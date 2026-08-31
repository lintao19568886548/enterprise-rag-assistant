"""Build deterministic scorer-contract fixtures; these are not model quality results."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "rag_cases.phase1.jsonl"
TARGET = ROOT / "offline_responses.phase1.jsonl"


def main() -> None:
    rows: list[dict] = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if case.get("approval_status") != "approved":
            continue
        should_refuse = bool(case["should_refuse"])
        source_terms = list(case.get("expected_sources") or [])
        rows.append(
            {
                "id": case["id"],
                "fixture_kind": "synthetic_scorer_contract",
                "response": {
                    "answer": "知识库资料不足。"
                    if should_refuse
                    else " ".join(case.get("expected_answer_keywords") or []) + " [1]",
                    "citations": []
                    if should_refuse
                    else [
                        {
                            "title": " ".join(source_terms) or "approved-evidence",
                            "chunk_id": f"fixture-{case['id']}",
                            "tenant_id": case["tenant_id"],
                            "knowledge_base_id": case["knowledge_base_id"],
                        }
                    ],
                    "has_sufficient_evidence": not should_refuse,
                    "latency_ms": 100,
                    "fixture_only": True,
                },
            }
        )
    TARGET.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"path": str(TARGET), "fixtures": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
