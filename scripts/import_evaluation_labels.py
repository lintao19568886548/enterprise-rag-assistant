"""Validate and import business-expert labels into the versioned RAG dataset."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.run_eval import validate_cases  # noqa: E402


EDITABLE_FIELDS = (
    "question",
    "confirmation",
    "reference_material",
    "expected_answer",
    "expected_answer_keywords",
    "expected_sources",
    "expected_pages",
    "expected_document_ids",
    "expected_chunk_ids",
    "allowed_knowledge_base_ids",
    "tenant_id",
    "knowledge_base_id",
    "user_id",
    "should_refuse",
    "max_cost",
    "citation_requirements",
    "permission_scope",
    "injection_attack_type",
    "notes",
    "approval_status",
    "approved_by",
    "approved_at",
    "reviewer_comment",
)
LIST_FIELDS = {
    "expected_answer_keywords",
    "expected_sources",
    "expected_pages",
    "expected_document_ids",
    "expected_chunk_ids",
    "allowed_knowledge_base_ids",
}
ALLOWED_APPROVAL_STATUSES = {"needs_human_label", "approved"}


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace("；", "|").split("|") if item.strip()]


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes", "y", "是"}:
        return True
    if normalized in {"false", "0", "no", "n", "否"}:
        return False
    raise ValueError("should_refuse must be TRUE or FALSE")


def _parse_cost(value: str) -> float | None:
    if not value.strip():
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError("max_cost cannot be negative")
    return parsed


def _validate_approved_row(row: dict[str, str], line: int) -> list[str]:
    errors: list[str] = []
    required_text = (
        "question",
        "reference_material",
        "citation_requirements",
        "tenant_id",
        "knowledge_base_id",
        "approved_by",
        "approved_at",
    )
    for field in required_text:
        if not row.get(field, "").strip():
            errors.append(f"line {line}: approved row missing {field}")
    try:
        parsed = datetime.fromisoformat(row.get("approved_at", ""))
        if parsed.tzinfo is None:
            errors.append(f"line {line}: approved_at must include timezone")
    except ValueError:
        errors.append(f"line {line}: approved_at must be ISO-8601")
    should_refuse = row.get("should_refuse", "").strip().lower()
    if should_refuse not in {"true", "false", "1", "0", "yes", "no", "y", "n", "是", "否"}:
        errors.append(f"line {line}: approved row must set should_refuse")
    if should_refuse in {"false", "0", "no", "n", "否"}:
        if not _parse_list(row.get("expected_answer_keywords", "")):
            errors.append(f"line {line}: answerable row needs expected_answer_keywords")
        if not _parse_list(row.get("expected_sources", "")):
            errors.append(f"line {line}: answerable row needs expected_sources")
    category = row.get("category", "").strip()
    if category == "prompt_injection" and not row.get("injection_attack_type", "").strip():
        errors.append(f"line {line}: prompt_injection row needs injection_attack_type")
    if category == "permission_isolation" and not row.get("permission_scope", "").strip():
        errors.append(f"line {line}: permission_isolation row needs permission_scope")
    return errors


def import_rows(dataset: list[dict[str, Any]], csv_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {str(case["id"]): case for case in dataset}
    pending_ids = {
        case_id
        for case_id, case in by_id.items()
        if str(case.get("approval_status") or case.get("label_status")) == "needs_human_label"
    }
    errors: list[str] = []
    seen: set[str] = set()
    imported = 0
    newly_approved = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required_headers = {"case_id", "category", *EDITABLE_FIELDS}
        missing_headers = sorted(required_headers - set(reader.fieldnames or []))
        if missing_headers:
            return dataset, {"errors": [f"missing CSV columns: {', '.join(missing_headers)}"]}
        for line, raw in enumerate(reader, start=2):
            row = {key: str(value or "") for key, value in raw.items()}
            case_id = row["case_id"].strip()
            if not case_id:
                errors.append(f"line {line}: missing case_id")
                continue
            if case_id in seen:
                errors.append(f"line {line}: duplicate case_id {case_id}")
                continue
            seen.add(case_id)
            if case_id not in pending_ids:
                errors.append(f"line {line}: {case_id} is not an editable pending case")
                continue
            case = by_id[case_id]
            if row["category"].strip() != str(case.get("category") or ""):
                errors.append(f"line {line}: category for {case_id} is read-only")
                continue
            status = row["approval_status"].strip() or "needs_human_label"
            if status not in ALLOWED_APPROVAL_STATUSES:
                errors.append(f"line {line}: invalid approval_status {status}")
                continue
            if status == "approved":
                errors.extend(_validate_approved_row(row, line))
            elif row["approved_by"].strip() or row["approved_at"].strip():
                errors.append(f"line {line}: pending row cannot contain approval metadata")
            if errors and errors[-1].startswith(f"line {line}:"):
                continue
            try:
                for field in EDITABLE_FIELDS:
                    value: Any = row[field].strip()
                    if field in LIST_FIELDS:
                        value = _parse_list(value)
                    elif field == "should_refuse":
                        value = _parse_bool(value)
                    elif field == "max_cost":
                        value = _parse_cost(value)
                    elif field in {"approved_by", "approved_at"} and not value:
                        value = None
                    case[field] = value
                case["answerable"] = None if case["should_refuse"] is None else not case["should_refuse"]
                case["label_status"] = status
                case["approval_status"] = status
                case["expected_keywords"] = list(case["expected_answer_keywords"])
                case["expected_source_terms"] = list(case["expected_sources"])
            except ValueError as exc:
                errors.append(f"line {line}: {exc}")
                continue
            imported += 1
            newly_approved += int(status == "approved")
    validation = validate_cases(dataset)
    errors.extend(validation["errors"])
    return dataset, {
        "rows_seen": len(seen),
        "rows_imported": imported,
        "newly_approved": newly_approved,
        "approved_after_import": validation["approved"],
        "pending_after_import": validation["pending"],
        "errors": errors,
    }


def _atomic_write(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n"
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and import expert RAG labels from UTF-8 CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/rag_cases.phase1.jsonl"))
    parser.add_argument("--apply", action="store_true", help="Write validated labels; default is dry-run")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    updated, summary = import_rows(dataset, args.csv_path)
    summary["mode"] = "apply" if args.apply else "dry-run"
    if summary.get("errors"):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    if args.apply:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        backup = Path("backups") / f"evaluation_labels_{stamp}" / args.dataset.name
        backup.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(args.dataset, backup)
        _atomic_write(args.dataset, updated)
        summary["backup_created"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
