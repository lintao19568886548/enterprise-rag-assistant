import csv
from copy import deepcopy
from pathlib import Path

from scripts.import_evaluation_labels import EDITABLE_FIELDS, import_rows


def _pending_case() -> dict:
    return {
        "dataset_version": "2.0.0-phase1",
        "id": "phase1-slot-031",
        "category": "fact",
        "label_status": "needs_human_label",
        "approval_status": "needs_human_label",
        "approved_by": None,
        "approved_at": None,
        "question": "",
    }


def _write_csv(path: Path, **overrides: str) -> None:
    row = {field: "" for field in EDITABLE_FIELDS}
    row.update(
        {
            "case_id": "phase1-slot-031",
            "category": "fact",
            "question": "设备的额定电压是多少？",
            "reference_material": "设备手册第 3 页",
            "expected_answer": "220V",
            "expected_answer_keywords": "220V|额定电压",
            "expected_sources": "设备手册",
            "expected_pages": "3",
            "tenant_id": "tenant-a",
            "knowledge_base_id": "kb-a",
            "should_refuse": "FALSE",
            "citation_requirements": "必须引用设备手册第 3 页",
            "approval_status": "approved",
            "approved_by": "business-expert",
            "approved_at": "2026-09-01T09:00:00+08:00",
        }
    )
    row.update(overrides)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "category", *EDITABLE_FIELDS])
        writer.writeheader()
        writer.writerow(row)


def test_valid_expert_row_can_be_imported(tmp_path):
    path = tmp_path / "labels.csv"
    _write_csv(path)
    rows, summary = import_rows([_pending_case()], path)
    assert summary["errors"] == []
    assert summary["newly_approved"] == 1
    assert rows[0]["approval_status"] == "approved"
    assert rows[0]["expected_answer_keywords"] == ["220V", "额定电压"]


def test_pending_row_cannot_fake_approval_metadata(tmp_path):
    path = tmp_path / "labels.csv"
    _write_csv(path, approval_status="needs_human_label", approved_by="robot")
    _, summary = import_rows([_pending_case()], path)
    assert any("pending row cannot contain approval metadata" in error for error in summary["errors"])


def test_existing_approved_case_is_read_only(tmp_path):
    path = tmp_path / "labels.csv"
    _write_csv(path)
    approved = deepcopy(_pending_case())
    approved["label_status"] = "approved"
    approved["approval_status"] = "approved"
    _, summary = import_rows([approved], path)
    assert any("not an editable pending case" in error for error in summary["errors"])
