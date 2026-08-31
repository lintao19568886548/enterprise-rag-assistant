import uuid

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.security import hash_service_account_secret
from app.core.settings import settings
from app.db.identity_repositories import create_service_account
from app.db.repositories import DEFAULT_TENANT_ID, ensure_defaults
from app.db.session import init_database
from app.import_process.api.file_import_service import app


def _service_key(role: str) -> str:
    raw = f"phase2-{role}-" + "x" * 40
    record = create_service_account(
        DEFAULT_TENANT_ID,
        f"security-{role}-{uuid.uuid4()}",
        hash_service_account_secret(raw, iterations=100_000),
        role,
    )
    return f"sa_{record.id}_{raw}"


def test_viewer_cannot_write_and_editor_cannot_manage_tenant(monkeypatch):
    init_database()
    ensure_defaults()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_api_keys", SecretStr("test-only-validation-key"))
    viewer = _service_key("viewer")
    editor = _service_key("editor")
    with TestClient(app) as client:
        viewer_write = client.post(
            "/knowledge-bases",
            headers={"X-API-Key": viewer},
            json={"name": "must-not-exist", "description": "", "permission_scope": "private"},
        )
        editor_admin = client.get("/admin/tenants", headers={"X-API-Key": editor})
    assert viewer_write.status_code == 403
    assert editor_admin.status_code == 403
