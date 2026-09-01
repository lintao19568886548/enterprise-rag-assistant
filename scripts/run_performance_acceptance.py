"""Run a safe, repeatable in-process load profile without provider calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import tracemalloc
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

CONCURRENCY_LEVELS = (1, 10, 30, 50)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 2)


def _summary(latencies: list[float], statuses: list[int], elapsed: float) -> dict[str, Any]:
    successes = sum(1 for status in statuses if 200 <= status < 300)
    timeouts = sum(status == 0 for status in statuses)
    return {
        "requests": len(statuses),
        "successes": successes,
        "success_rate": round(successes / len(statuses), 4) if statuses else 0.0,
        "error_rate": round((len(statuses) - successes) / len(statuses), 4) if statuses else 1.0,
        "timeout_rate": round(timeouts / len(statuses), 4) if statuses else 0.0,
        "throughput_requests_per_second": round(len(statuses) / elapsed, 2) if elapsed else 0.0,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "status_distribution": dict(Counter(str(status) for status in statuses)),
    }


def _run_profile(concurrency: int, action: Callable[[int], int]) -> dict[str, Any]:
    request_count = max(5, concurrency)

    def measured(index: int) -> tuple[int, float]:
        started = time.perf_counter()
        try:
            status = action(index)
        except TimeoutError:
            status = 0
        except Exception:
            status = 599
        return status, (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(measured, range(request_count)))
    elapsed = time.perf_counter() - started
    statuses, latencies = map(list, zip(*results, strict=True))
    return {"concurrency": concurrency, **_summary(latencies, statuses, elapsed)}


def _run_scenario(action: Callable[[int], int]) -> list[dict[str, Any]]:
    return [_run_profile(concurrency, action) for concurrency in CONCURRENCY_LEVELS]


def run(report_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="enterprise-rag-performance-") as raw_dir:
        temp_dir = Path(raw_dir)
        os.environ.update(
            {
                "APP_ENV": "test",
                "AUTH_ENABLED": "false",
                "DATABASE_ENABLED": "true",
                "DATABASE_URL": f"sqlite:///{(temp_dir / 'performance.db').as_posix()}",
                "MILVUS_REQUIRED": "false",
                "MINIO_ENABLED": "false",
                "TASK_QUEUE_ENABLED": "false",
                "TASK_BACKEND": "memory",
                "LANGGRAPH_CHECKPOINTER": "memory",
                "RATE_LIMIT_ENABLED": "false",
                "LOG_FILE_ENABLE": "false",
                "LOG_CONSOLE_LEVEL": "ERROR",
            }
        )
        from fastapi.testclient import TestClient

        import app.import_process.api.file_import_service as import_service
        import app.query_process.api.query_service as query_service
        from app.core.settings import settings
        from app.db.session import get_engine, get_session_factory
        from app.utils.task_utils import set_task_result

        import_service.PROJECT_ROOT = temp_dir
        session_ids: list[str] = []
        session_lock = threading.Lock()

        def synthetic_query(
            session_id: str,
            _query: str,
            _knowledge_base_id: str,
            _is_stream: bool,
            _user_id: str,
            _tenant_id: str,
        ) -> None:
            set_task_result(session_id, "answer", "synthetic grounded response")
            set_task_result(session_id, "citations", [{"document_id": "synthetic-doc"}])
            set_task_result(session_id, "confidence", 0.9)
            set_task_result(session_id, "has_sufficient_evidence", True)
            set_task_result(session_id, "model", "synthetic-no-provider-call")
            set_task_result(session_id, "model_latency_ms", 0)
            set_task_result(session_id, "local_latency_ms", 1)
            set_task_result(session_id, "total_latency_ms", 1)

        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        tracemalloc.start()
        with patch.object(query_service, "run_query_graph", synthetic_query):
            with TestClient(query_service.app) as query_client:
                health = _run_scenario(lambda _index: query_client.get("/health/live").status_code)
                knowledge_bases = _run_scenario(
                    lambda _index: query_client.get("/knowledge-bases").status_code
                )

                def query_action(index: int) -> int:
                    response = query_client.post(
                        "/query",
                        json={"query": f"synthetic concurrent question {index}", "is_stream": False},
                    )
                    if response.status_code == 200:
                        with session_lock:
                            session_ids.append(str(response.json()["session_id"]))
                    return response.status_code

                query = _run_scenario(query_action)
                if not session_ids:
                    raise RuntimeError("query load produced no session for history acceptance")
                history_session = session_ids[0]
                history = _run_scenario(
                    lambda _index: query_client.get(f"/history/{history_session}").status_code
                )

        with patch.object(import_service, "run_import_graph", lambda *_args, **_kwargs: None):
            with TestClient(import_service.app) as import_client:

                def upload_action(index: int) -> int:
                    response = import_client.post(
                        "/upload",
                        files={
                            "files": (
                                f"concurrent-{index}-{time.time_ns()}.md",
                                f"# synthetic document {index}\nnonce-{time.time_ns()}".encode(),
                                "text/markdown",
                            )
                        },
                    )
                    return response.status_code

                upload = _run_scenario(upload_action)
                original_limit = settings.upload_max_file_size_mb
                settings.upload_max_file_size_mb = 1
                try:
                    oversized = import_client.post(
                        "/upload",
                        files={"files": ("oversized.md", b"#" + b"x" * (1024 * 1024 + 1), "text/markdown")},
                    )
                finally:
                    settings.upload_max_file_size_mb = original_limit

        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        cpu_seconds = time.process_time() - cpu_started
        wall_seconds = time.perf_counter() - wall_started
        scenarios = {
            "health_check": health,
            "knowledge_base_list": knowledge_bases,
            "session_history": history,
            "ordinary_query_local_path": query,
            "document_import_api_local_path": upload,
        }
        all_profiles = [profile for profiles in scenarios.values() for profile in profiles]
        report: dict[str, Any] = {
            "executed_at": datetime.now(UTC).isoformat(),
            "mode": "in_process_fastapi_sqlite_with_mocked_external_model_parser_and_milvus",
            "uses_business_data": False,
            "concurrency_levels": list(CONCURRENCY_LEVELS),
            "scenarios": scenarios,
            "large_file_boundary": {
                "configured_mb": 1,
                "status": oversized.status_code,
                "error_code": oversized.json().get("code"),
                "passed": oversized.status_code == 413,
            },
            "process_cpu_seconds": round(cpu_seconds, 4),
            "wall_seconds": round(wall_seconds, 4),
            "client_process_cpu_single_core_percent": round(cpu_seconds / wall_seconds * 100, 2),
            "python_peak_memory_mib": round(peak_bytes / 1024 / 1024, 2),
            "database_pool_status": get_engine().pool.status(),
            "online_model_latency": "not_exercised",
            "milvus_latency": "not_exercised",
            "streaming_query": "not_exercised",
            "authentication": "not_exercised_auth_disabled_in_local_harness",
            "queue_backlog": "not_exercised_redis_disabled",
            "external_services_exercised": False,
            "limitations": [
                "Provider, parser and Milvus calls are deterministic stubs or disabled; results measure local API and SQLite paths only.",
                "The shared FastAPI TestClient adds in-process test transport overhead and is not a production-capacity claim.",
                "Real OIDC, streaming, PostgreSQL, Redis, MinIO, Milvus and provider concurrency remain staging acceptance items.",
            ],
        }
        report["passed"] = bool(
            all(profile["success_rate"] == 1.0 for profile in all_profiles)
            and report["large_file_boundary"]["passed"]
        )
        get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.report)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
