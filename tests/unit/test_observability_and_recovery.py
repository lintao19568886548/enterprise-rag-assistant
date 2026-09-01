import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.logger import redact_log_text, redact_log_value
from app.import_process.api.file_import_service import app as import_app, settings as import_settings
from scripts.recovery_drill_sqlite import run_drill


def test_sensitive_credentials_are_redacted():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJlbXBsb3llZSJ9.signaturepayload"
    raw = (
        "Authorization: Bearer secret-token-value-123456 "
        "Cookie=session-cookie-123 API_KEY=secret-value-456 "
        f"jwt={jwt} postgresql://user:database-password@postgres/db"
    )
    redacted = redact_log_text(raw)
    assert "secret-token-value" not in redacted
    assert "session-cookie" not in redacted
    assert "secret-value" not in redacted
    assert jwt not in redacted
    assert "database-password" not in redacted
    assert redacted.count("***REDACTED***") >= 5


def test_nested_structured_credentials_are_redacted():
    bearer = "nested-" + "secret-token-123456"
    database_password = "nested-" + "database-password"
    api_secret = "nested-" + "api-secret-123456"
    structured = {
        "headers": {"Authorization": f"Bearer {bearer}"},
        "database_url": f"postgresql://user:{database_password}@postgres/db",
        "items": [{"message": f"API_KEY={api_secret}"}],
        "safe": "visible",
    }
    redacted = redact_log_value(structured)
    rendered = json.dumps(redacted)
    assert bearer not in rendered
    assert database_password not in rendered
    assert api_secret not in rendered
    assert redacted["safe"] == "visible"


def test_metrics_expose_enterprise_observability_contract(monkeypatch):
    monkeypatch.setattr(import_settings, "openai_api_key", SecretStr("sk-test-not-a-real-key"))
    monkeypatch.setattr(import_settings, "openai_base_url", "https://example.invalid/v1")
    monkeypatch.setattr(import_settings, "mineru_api_token", SecretStr("test-not-a-real-token"))
    with TestClient(import_app) as client:
        client.get("/health/live")
        response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    for name in (
        "kb_http_requests_total",
        "kb_http_request_duration_seconds_bucket",
        "kb_http_requests_in_flight",
        "kb_model_calls_total",
        "kb_model_tokens",
        "kb_model_estimated_cost_usd",
        "kb_milvus_retrieval_duration_seconds",
        "kb_workflow_node_duration_seconds",
        "kb_worker_queue_length",
        "kb_rag_answer_confidence",
        "kb_rag_citation_count",
        "kb_embedding_calls_total",
        "kb_embedding_duration_seconds",
        "kb_query_end_to_end_duration_seconds",
        "kb_database_pool_checked_out",
        "kb_database_pool_timeouts_total",
    ):
        assert name in body


def test_sanitized_recovery_drill(tmp_path):
    report_path = tmp_path / "recovery.json"
    report = run_drill(report_path)
    assert report["passed"] is True
    assert report["source_is_business_data"] is False
    assert report["foreign_key_error_count"] == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["integrity_check"] == "ok"
