from app.evaluation.run_eval import _percentile, evaluate_response, validate_cases


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
            {"id": "approved", "question": "问题", "label_status": "approved"},
            {"id": "pending", "question": "", "label_status": "needs_human_label"},
        ]
    )
    assert validation["approved"] == 1
    assert validation["pending"] == 1
    assert validation["errors"] == []


def test_approved_gold_slot_requires_a_question():
    validation = validate_cases(
        [{"id": "bad", "question": "", "label_status": "approved"}]
    )
    assert validation["errors"]
