"""Run a safe, repeatable in-process Phase 2 concurrency acceptance harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 2)


def _summary(latencies: list[float], statuses: list[int]) -> dict[str, object]:
    successes = sum(1 for status in statuses if 200 <= status < 300)
    return {
        "requests": len(statuses),
        "successes": successes,
        "success_rate": round(successes / len(statuses), 4) if statuses else 0.0,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "error_code_distribution": dict(Counter(str(status) for status in statuses if status >= 300)),
    }


def run(report_path: Path) -> dict[str, object]:
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
            set_task_result(session_id, "latency_ms", 1)

        def query_once(client: TestClient, index: int) -> tuple[int, float]:
            started = time.perf_counter()
            response = client.post(
                "/query",
                json={"query": f"synthetic concurrent question {index}", "is_stream": False},
            )
            return response.status_code, (time.perf_counter() - started) * 1000

        def upload_once(client: TestClient, index: int) -> tuple[int, float]:
            started = time.perf_counter()
            response = client.post(
                "/upload",
                files={
                    "files": (
                        f"concurrent-{index}.md",
                        f"# synthetic performance document {index}\nunique-{time.time_ns()}".encode(),
                        "text/markdown",
                    )
                },
            )
            return response.status_code, (time.perf_counter() - started) * 1000

        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        tracemalloc.start()
        with patch.object(query_service, "run_query_graph", synthetic_query):
            with TestClient(query_service.app) as query_client:
                with ThreadPoolExecutor(max_workers=20) as executor:
                    query_results = list(executor.map(lambda i: query_once(query_client, i), range(20)))
        with patch.object(import_service, "run_import_graph", lambda *_args, **_kwargs: None):
            with TestClient(import_service.app) as import_client:
                with ThreadPoolExecutor(max_workers=5) as executor:
                    upload_results = list(executor.map(lambda i: upload_once(import_client, i), range(5)))
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
        query_statuses, query_latencies = map(list, zip(*query_results, strict=True))
        upload_statuses, upload_latencies = map(list, zip(*upload_results, strict=True))
        report: dict[str, Any] = {
            "executed_at": datetime.now(UTC).isoformat(),
            "mode": "in_process_api_and_database_with_mocked_external_model_and_parser",
            "uses_business_data": False,
            "query_concurrency": 20,
            "query": _summary(query_latencies, query_statuses),
            "upload_concurrency": 5,
            "upload": _summary(upload_latencies, upload_statuses),
            "large_file_boundary": {
                "configured_mb": 1,
                "status": oversized.status_code,
                "error_code": oversized.json().get("code"),
                "passed": oversized.status_code == 413,
            },
            "process_cpu_seconds": round(time.process_time() - cpu_started, 4),
            "wall_seconds": round(time.perf_counter() - wall_started, 4),
            "python_peak_memory_mib": round(peak_bytes / 1024 / 1024, 2),
            "queue_backlog": None,
            "model_timeout_rate": None,
            "external_services_exercised": False,
            "limitations": [
                "Model and parser calls are deterministic stubs; this measures API, validation and SQLite metadata paths.",
                "Docker, PostgreSQL server, Redis server and staging MinIO are unavailable on this host.",
                "Real provider concurrency, queue backlog and infrastructure interruption require staging acceptance.",
            ],
        }
        report["passed"] = bool(
            report["query"]["success_rate"] == 1.0
            and report["upload"]["success_rate"] == 1.0
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
