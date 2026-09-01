import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.import_process.api.file_import_service import app
from app.db.models import Tenant, User
from app.db.repositories import DEFAULT_KNOWLEDGE_BASE_ID, create_knowledge_base, register_document
from app.db.repositories import create_import_task
from app.db.session import session_scope
import app.services.lifecycle as lifecycle_service
from app.utils.upload_utils import SavedUpload


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


def test_document_delete_api_returns_same_cleanup_job_on_repeat(tmp_path: Path, monkeypatch):
    token = uuid.uuid4().hex
    task_id = str(uuid.uuid4())
    task_dir = tmp_path / "output" / "20260831" / task_id
    task_dir.mkdir(parents=True)
    source = task_dir / f"cleanup-{token}.md"
    source.write_text("# lifecycle", encoding="utf-8")
    document, _, _ = register_document(
        DEFAULT_KNOWLEDGE_BASE_ID,
        SavedUpload(
            original_filename=source.name,
            stored_filename=source.name,
            path=source,
            content_type="text/markdown",
            size=source.stat().st_size,
            sha256=token * 2,
        ),
        None,
    )
    create_import_task(
        task_id,
        document.id,
        1,
        local_dir=str(task_dir),
        local_file_path=str(source),
    )
    monkeypatch.setattr(lifecycle_service, "PROJECT_ROOT", tmp_path)

    with TestClient(app) as client:
        first = client.delete(f"/documents/{document.id}?confirm=true")
        second = client.delete(f"/documents/{document.id}?confirm=true")

    assert first.status_code == second.status_code == 202
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["cleanup_job"]["id"] == second.json()["cleanup_job"]["id"]
