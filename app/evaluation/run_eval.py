"""Versioned black-box RAG evaluation runner."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import math
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def evaluate_response(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    answer = str(response.get("answer") or "")
    citations = list(response.get("citations") or [])
    answerable = bool(case.get("answerable", True))
    expected_keywords = [str(value).lower() for value in case.get("expected_keywords", [])]
    expected_sources = [str(value).lower() for value in case.get("expected_source_terms", [])]
    answer_lower = answer.lower()
    source_text = " ".join(str(item.get("title") or "") for item in citations).lower()

    keyword_recall = (
        sum(value in answer_lower for value in expected_keywords) / len(expected_keywords)
        if expected_keywords
        else 1.0
    )
    source_recall = (
        sum(value in source_text for value in expected_sources) / len(expected_sources)
        if expected_sources
        else 1.0
    )
    refused = response.get("has_sufficient_evidence") is False
    refusal_accuracy = float(refused == (not answerable))
    citation_validity = (
        float(all(item.get("chunk_id") or item.get("url") for item in citations))
        if citations
        else float(not answerable)
    )
    citation_coverage = float(bool(citations)) if answerable else float(not citations)
    passed = all(
        metric >= 1.0
        for metric in (keyword_recall, source_recall, refusal_accuracy, citation_validity, citation_coverage)
    )
    return {
        "id": case.get("id"),
        "keyword_recall": keyword_recall,
        "source_recall_at_k": source_recall,
        "refusal_accuracy": refusal_accuracy,
        "citation_validity": citation_validity,
        "citation_coverage": citation_coverage,
        "confidence": response.get("confidence"),
        "latency_ms": response.get("latency_ms"),
        "passed": passed,
    }


def _post_query(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(
        f"{base_url.rstrip('/')}/query",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def validate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    ids: set[str] = set()
    approved = 0
    pending = 0
    categories: dict[str, int] = {}
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            errors.append(f"line {index}: missing id")
        elif case_id in ids:
            errors.append(f"line {index}: duplicate id {case_id}")
        ids.add(case_id)
        label_status = str(case.get("label_status") or "approved").strip()
        is_approved = label_status in {"approved", "approved_seed"}
        if is_approved:
            approved += 1
            if not str(case.get("question") or "").strip():
                errors.append(f"line {index}: approved case {case_id} has no question")
        else:
            pending += 1
        category = str(case.get("category") or "legacy_seed")
        categories[category] = categories.get(category, 0) + 1
    return {
        "total": len(cases),
        "approved": approved,
        "pending": pending,
        "categories": categories,
        "errors": errors,
    }


def _percentile(values: list[int], percentile: float) -> int:
    """Return a deterministic nearest-rank percentile without extra dependencies."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def run(args: argparse.Namespace) -> int:
    cases = _load_cases(args.dataset)
    validation = validate_cases(cases)
    if validation["errors"]:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 2
    if args.validate_only:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0
    if not args.include_unapproved:
        cases = [
            case
            for case in cases
            if str(case.get("label_status") or "approved") in {"approved", "approved_seed"}
        ]
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.get("id") in selected]
    if args.limit:
        cases = cases[: args.limit]
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            session_id = f"eval-{case['id']}-{int(time.time() * 1000)}"
            response = _post_query(
                args.base_url,
                args.api_key,
                {
                    "query": case["question"],
                    "session_id": session_id,
                    "knowledge_base_id": args.knowledge_base_id,
                    "is_stream": False,
                },
            )
            attempts = 1
            confirmation = str(case.get("confirmation") or "").strip()
            if (
                confirmation
                and (
                    response.get("has_sufficient_evidence") is False
                    or not response.get("citations")
                )
            ):
                response = _post_query(
                    args.base_url,
                    args.api_key,
                    {
                        "query": f"{confirmation} 原问题：{case['question']}",
                        "session_id": session_id,
                        "knowledge_base_id": args.knowledge_base_id,
                        "is_stream": False,
                    },
                )
                attempts = 2
            result = evaluate_response(case, response)
            result["attempts"] = attempts
            result["wall_latency_ms"] = int((time.perf_counter() - started) * 1000)
        except (HTTPError, OSError, ValueError) as exc:
            result = {
                "id": case.get("id"),
                "passed": False,
                "error": exc.__class__.__name__,
                "wall_latency_ms": int((time.perf_counter() - started) * 1000),
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))

    latencies = [item["wall_latency_ms"] for item in results]
    passed_count = sum(bool(item.get("passed")) for item in results)
    metric_names = (
        "keyword_recall",
        "source_recall_at_k",
        "refusal_accuracy",
        "citation_validity",
        "citation_coverage",
    )
    summary = {
        "dataset": str(args.dataset),
        "dataset_version": cases[0].get("dataset_version") if cases else None,
        "total": len(results),
        "passed": passed_count,
        "pass_rate": passed_count / len(results) if results else 0,
        "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "metrics": {
            name: round(
                statistics.mean(
                    float(item[name]) for item in results if name in item
                ),
                4,
            )
            if any(name in item for item in results)
            else 0.0
            for name in metric_names
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))
    return 0 if summary["passed"] == summary["total"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the versioned RAG acceptance dataset")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--api-key", default=os.getenv("KB_EVAL_API_KEY", ""))
    parser.add_argument(
        "--knowledge-base-id",
        default="00000000-0000-0000-0000-000000000010",
    )
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/rag_cases.v1.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/reports/latest.json"))
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N cases; 0 runs all cases")
    parser.add_argument("--case-id", action="append", default=[], help="Run a specific case ID; may be repeated")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate IDs, labels and category counts without calling a model",
    )
    parser.add_argument(
        "--include-unapproved",
        action="store_true",
        help="Include pending human-label slots; normally they are skipped",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
