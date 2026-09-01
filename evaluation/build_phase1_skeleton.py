"""Build the 100-slot phase-one gold-set skeleton without inventing labels."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "rag_cases.v1.jsonl"
TARGET = ROOT / "rag_cases.phase1.jsonl"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000100"
DEFAULT_KNOWLEDGE_BASE_ID = "00000000-0000-0000-0000-000000000010"
PHASE1_APPROVER = "lintao19568886548"
PHASE1_APPROVED_AT = "2026-08-31T21:31:11+08:00"

PENDING_CATEGORY_COUNTS = {
    "fact": 5,
    "multi_document": 10,
    "table_image": 8,
    "temporal_version": 8,
    "ambiguous": 8,
    "unanswerable": 8,
    "permission_isolation": 8,
    "prompt_injection": 5,
    "bad_citation": 5,
    "long_conversation": 5,
}


def _seed_cases() -> list[dict]:
    cases: list[dict] = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        case["dataset_version"] = "2.0.0-phase1"
        case["label_status"] = "approved"
        case["approval_status"] = "approved"
        case["approved_by"] = PHASE1_APPROVER
        case["approved_at"] = PHASE1_APPROVED_AT
        case.setdefault("confirmation", "")
        case["category"] = "fact" if case.get("answerable", True) else "unanswerable"
        case["expected_answer_keywords"] = list(case.get("expected_keywords") or [])
        case["expected_sources"] = list(case.get("expected_source_terms") or [])
        case["should_refuse"] = not bool(case.get("answerable", True))
        case.setdefault("expected_document_ids", [])
        case.setdefault("expected_pages", [])
        case.setdefault("expected_chunk_ids", [])
        case.setdefault("allowed_knowledge_base_ids", [])
        case["tenant_id"] = DEFAULT_TENANT_ID
        case["knowledge_base_id"] = DEFAULT_KNOWLEDGE_BASE_ID
        case.setdefault("user_id", "")
        case.setdefault("max_cost", None)
        cases.append(case)
    return cases


def _pending_slots(start: int) -> list[dict]:
    rows: list[dict] = []
    index = start
    for category, count in PENDING_CATEGORY_COUNTS.items():
        for _ in range(count):
            rows.append(
                {
                    "dataset_version": "2.0.0-phase1",
                    "id": f"phase1-slot-{index:03d}",
                    "category": category,
                    "label_status": "needs_human_label",
                    "approval_status": "needs_human_label",
                    "approved_by": None,
                    "approved_at": None,
                    "question": "",
                    "confirmation": "",
                    "answerable": None,
                    "expected_answer": "",
                    "expected_keywords": [],
                    "expected_answer_keywords": [],
                    "expected_source_terms": [],
                    "expected_sources": [],
                    "expected_document_ids": [],
                    "expected_pages": [],
                    "expected_chunk_ids": [],
                    "allowed_knowledge_base_ids": [],
                    "tenant_id": "",
                    "knowledge_base_id": "",
                    "should_refuse": None,
                    "user_id": "",
                    "max_cost": None,
                    "notes": "等待业务专家填写问题、标准答案、证据页码和访问主体后改为 approved",
                }
            )
            index += 1
    return rows


def main() -> None:
    rows = _seed_cases()
    rows.extend(_pending_slots(len(rows) + 1))
    if len(rows) != 100:
        raise RuntimeError(f"expected 100 slots, got {len(rows)}")
    TARGET.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"path": str(TARGET), "total": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
