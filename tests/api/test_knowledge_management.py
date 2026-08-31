import uuid

from fastapi.testclient import TestClient

from app.import_process.api.file_import_service import app
from app.db.models import Tenant, User
from app.db.repositories import create_knowledge_base
from app.db.session import session_scope


def test_default_knowledge_base_is_available():
    with TestClient(app) as client:
        response = client.get("/knowledge-bases")
    assert response.status_code == 200
    assert any(item["name"] == "默认知识库" for item in response.json()["items"])


def test_create_and_confirm_delete_knowledge_base():
    name = f"test-kb-{uuid.uuid4()}"
    with TestClient(app) as client:
        created = client.post(
            "/knowledge-bases",
            json={"name": name, "description": "test", "permission_scope": "private"},
        )
        assert created.status_code == 201
        knowledge_base_id = created.json()["id"]

        not_confirmed = client.delete(f"/knowledge-bases/{knowledge_base_id}")
        assert not_confirmed.status_code == 409

        deleted = client.delete(f"/knowledge-bases/{knowledge_base_id}?confirm=true")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_cross_tenant_knowledge_base_is_not_disclosed():
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(Tenant(id=tenant_id, slug=f"tenant-{tenant_id}", name="其他租户"))
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                username=f"user-{user_id}",
                role="admin",
                enabled=True,
            )
        )
    record = create_knowledge_base(
        f"cross-tenant-{uuid.uuid4()}",
        "",
        "private",
        owner_id=user_id,
        tenant_id=tenant_id,
    )

    with TestClient(app) as client:
        response = client.get(f"/knowledge-bases/{record.id}")

    assert response.status_code == 404
