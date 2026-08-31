from app.query_process.agent.nodes.node_answer_output import NodeAnswerOutput
from app.query_process.agent.nodes.node_rerank import NodeRerank
from app.query_process.agent.state import create_default_state
from app.utils.milvus_utils import build_chunk_filter


def test_milvus_filter_escapes_user_values_and_scopes_knowledge_base():
    expression = build_chunk_filter(
        ['device "A"'],
        "tenant-123",
        "kb-123",
    )
    assert 'item_name in ["device \\"A\\""]' in expression
    assert 'tenant_id == "tenant-123"' in expression
    assert 'knowledge_base_id == "kb-123"' in expression
    assert "is_active == true" in expression


def test_grounded_answer_builds_structured_citations():
    state = create_default_state(
        reranked_docs=[
            {
                "content": "设备额定电压是 220V。",
                "score": 0.91,
                "source": "local",
                "file_title": "设备手册",
                "chunk_id": 42,
                "document_id": "doc-1",
                "document_version": 2,
                "page_number": 7,
            }
        ]
    )
    NodeAnswerOutput()._prepare_evidence(state)
    assert state["has_sufficient_evidence"] is True
    assert state["confidence"] > 0.8
    assert state["citations"] == [
        {
            "id": 1,
            "label": "[1]",
            "source": "local",
            "title": "设备手册",
            "chunk_id": "42",
            "document_id": "doc-1",
            "document_version": 2,
            "page_number": 7,
            "url": "",
            "score": 0.91,
        }
    ]


def test_low_score_evidence_is_rejected():
    state = create_default_state(
        reranked_docs=[{"content": "无关段落", "score": 0.01, "source": "local"}]
    )
    NodeAnswerOutput()._prepare_evidence(state)
    assert state["has_sufficient_evidence"] is False
    assert state["citations"] == []


def test_model_refusal_reconciles_structured_evidence_state():
    state = create_default_state(
        answer="参考资料未明确说明该设备的内置电池容量。",
        has_sufficient_evidence=True,
        confidence=0.87,
        citations=[{"document_id": "doc-1"}],
    )

    NodeAnswerOutput()._reconcile_generated_evidence(state)

    assert state["has_sufficient_evidence"] is False
    assert state["confidence"] == 0.2
    assert state["citations"] == []


def test_grounded_answer_with_limited_scope_keeps_evidence_state():
    state = create_default_state(
        answer="执行 display wlan ap all，State 为 R/M 表示已连通 [2]。参考资料未明确说明其余部分。",
        has_sufficient_evidence=True,
        confidence=0.91,
        citations=[{"document_id": "doc-1"}],
    )

    NodeAnswerOutput()._reconcile_generated_evidence(state)

    assert state["has_sufficient_evidence"] is True
    assert state["confidence"] == 0.91
    assert state["citations"] == [{"document_id": "doc-1"}]


def test_rerank_failure_preserves_documents(monkeypatch):
    monkeypatch.setattr(
        "app.query_process.agent.nodes.node_rerank.rerank_documents",
        lambda *_: (_ for _ in ()).throw(TimeoutError()),
    )
    docs = [{"content": "手册内容", "chunk_id": "1", "retrieval_score": 0.7}]
    result = NodeRerank()._rerank_merged_docs("问题", docs)
    assert result == [{**docs[0], "score": 0.7}]
