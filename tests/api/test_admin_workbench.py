import uuid

from fastapi.testclient import TestClient

from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    ensure_defaults,
    get_feedback_statistics,
    save_chat_message,
)
from app.db.session import init_database
from app.import_process.api.file_import_service import app as import_app
from app.query_process.api.query_service import app as query_app


def test_admin_workbench_is_safe_and_mobile_ready():
    with TestClient(import_app) as client:
        page = client.get("/admin.html")
        summary = client.get("/admin/workbench-summary")
    assert page.status_code == 200
    assert summary.status_code == 200
    body = page.text
    assert "企业 RAG 管理工作台" in body
    assert "@media(max-width:390px)" in body
    assert 'role="tablist"' in body
    assert 'aria-live="polite"' in body
    assert "innerHTML" not in body
    evaluation = summary.json()["evaluation"]
    assert evaluation["labels"]["approved"] == 30
    assert evaluation["labels"]["needs_human_label"] == 70


def test_user_feedback_is_tenant_and_user_scoped():
    init_database()
    ensure_defaults()
    session_id = f"feedback-{uuid.uuid4()}"
    message_id = save_chat_message(
        session_id=session_id,
        role="assistant",
        text="synthetic answer",
        knowledge_base_id=DEFAULT_KNOWLEDGE_BASE_ID,
        tenant_id=DEFAULT_TENANT_ID,
        user_id=DEFAULT_USER_ID,
    )
    before = get_feedback_statistics(DEFAULT_TENANT_ID)
    with TestClient(query_app) as client:
        response = client.post(
            f"/messages/{message_id}/feedback",
            json={"feedback": "helpful"},
        )
        invalid = client.post(
            f"/messages/{message_id}/feedback",
            json={"feedback": "inject-script"},
        )
    assert response.status_code == 200
    assert response.json()["feedback"] == "helpful"
    assert invalid.status_code == 422
    after = get_feedback_statistics(DEFAULT_TENANT_ID)
    assert after["helpful"] == before["helpful"] + 1
