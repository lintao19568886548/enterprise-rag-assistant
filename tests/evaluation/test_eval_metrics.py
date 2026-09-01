import json
from pathlib import Path

from app.evaluation.run_eval import (
    _percentile,
    evaluate_gates,
    evaluate_response,
    validate_cases,
)


def _approved_case(**overrides):
    case = {
        "id": "approved",
        "category": "fact",
        "question": "问题",
        "confirmation": "",
        "expected_answer_keywords": [],
        "expected_sources": [],
        "expected_pages": [],
        "should_refuse": False,
        "tenant_id": "tenant-a",
        "knowledge_base_id": "kb-a",
        "label_status": "approved",
        "approval_status": "approved",
        "approved_by": "expert",
        "approved_at": "2026-08-31T00:00:00+08:00",
    }
    case.update(overrides)
    return case


def test_grounded_evaluation_metrics_pass_with_valid_citation():
    case = {
        "id": "case-1",
        "answerable": True,
        "expected_keywords": ["220V"],
        "expected_source_terms": ["设备手册"],
    }
    response = {
        "answer": "额定电压为 220V。[1]",
        "citations": [{"title": "设备手册", "chunk_id": "42"}],
        "has_sufficient_evidence": True,
    }
    assert evaluate_response(case, response)["passed"] is True


def test_refusal_case_requires_no_citations():
    case = {"id": "case-2", "answerable": False}
    response = {
        "answer": "知识库资料不足。",
        "citations": [],
        "has_sufficient_evidence": False,
    }
    assert evaluate_response(case, response)["passed"] is True


def test_percentile_uses_nearest_rank():
    assert _percentile([], 0.95) == 0
    assert _percentile([10, 20, 30, 40], 0.50) == 20
    assert _percentile([10, 20, 30, 40], 0.95) == 40


def test_pending_gold_slots_are_counted_but_need_no_fake_question():
    validation = validate_cases(
        [
            _approved_case(),
            {
                "id": "pending",
                "question": "",
                "label_status": "needs_human_label",
                "approval_status": "needs_human_label",
                "approved_by": None,
                "approved_at": None,
            },
        ]
    )
    assert validation["approved"] == 1
    assert validation["pending"] == 1
    assert validation["errors"] == []


def test_approved_gold_slot_requires_a_question():
    validation = validate_cases([_approved_case(id="bad", question="")])
    assert validation["errors"]


def test_hit_mrr_and_tenant_scope_are_measured():
    case = _approved_case(
        expected_sources=["manual"],
        expected_answer_keywords=["220v"],
    )
    result = evaluate_response(
        case,
        {
            "answer": "220V [1]",
            "has_sufficient_evidence": True,
            "citations": [
                {"title": "noise", "chunk_id": "1"},
                {
                    "title": "manual",
                    "chunk_id": "2",
                    "tenant_id": "tenant-a",
                    "knowledge_base_id": "kb-a",
                },
            ],
        },
    )
    assert result["hit_at_k"] == 1.0
    assert result["mrr"] == 0.5
    assert result["citation_validity"] == 1.0

    leaked = evaluate_response(
        case,
        {
            "answer": "220V [1]",
            "has_sufficient_evidence": True,
            "citations": [
                {
                    "title": "manual",
                    "chunk_id": "2",
                    "tenant_id": "tenant-b",
                    "knowledge_base_id": "kb-a",
                }
            ],
        },
    )
    assert leaked["citation_validity"] == 0.0
    assert leaked["passed"] is False


def test_release_gate_requires_approved_security_cases():
    summary = {
        "p95_latency_ms": 100,
        "metrics": {
            "citation_validity": 1.0,
            "answer_pass_rate": 1.0,
            "unanswerable_accuracy": 1.0,
            "phase1_approved_regression": 1.0,
            "model_failure_rate": 0.0,
            "permission_isolation": None,
            "prompt_injection_containment": None,
            "image_citation_correctness": None,
        },
        "dataset_validation": {"approved": 30},
        "approved_categories": ["fact", "unanswerable"],
    }
    assert evaluate_gates(summary, "pr")["passed"] is True
    assert evaluate_gates(summary, "release")["passed"] is False


def test_release_gate_stays_blocked_until_100_expert_approvals_and_category_coverage():
    summary = {
        "p95_latency_ms": 100,
        "dataset_validation": {"approved": 99},
        "approved_categories": [
            "permission_isolation",
            "prompt_injection",
            "unanswerable",
            "bad_citation",
            "table_image",
        ],
        "metrics": {
            "citation_validity": 1.0,
            "answer_pass_rate": 1.0,
            "unanswerable_accuracy": 1.0,
            "phase1_approved_regression": 1.0,
            "model_failure_rate": 0.0,
            "permission_isolation": 1.0,
            "prompt_injection_containment": 1.0,
            "image_citation_correctness": 1.0,
        },
    }
    gate = evaluate_gates(summary, "release")
    assert gate["checks"]["minimum_approved_cases"]["passed"] is False
    assert gate["passed"] is False


def test_phase1_dataset_keeps_30_approved_and_70_pending():
    dataset = Path("evaluation/rag_cases.phase1.jsonl")
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    validation = validate_cases(cases)
    assert validation["approved"] == 30
    assert validation["pending"] == 70
    assert validation["errors"] == []
