from fastapi.testclient import TestClient

from app.db.identity_repositories import get_service_account
from app.import_process.api.file_import_service import app


def test_service_account_can_be_revoked_only_with_confirmation():
    with TestClient(app) as client:
        created = client.post(
            "/admin/service-accounts",
            json={"name": "phase2-observability-test", "role": "viewer"},
        )
        assert created.status_code == 201
        account_id = created.json()["id"]
        refused = client.delete(f"/admin/service-accounts/{account_id}")
        assert refused.status_code == 409
        revoked = client.delete(f"/admin/service-accounts/{account_id}?confirm=true")
        assert revoked.status_code == 200
        assert revoked.json()["revoked"] is True
        assert get_service_account(account_id) is None
