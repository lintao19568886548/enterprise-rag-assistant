import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import app.query_process.api.query_service as query_service
from app.db.models import Tenant, User
from app.db.repositories import (
    create_import_task,
    create_knowledge_base,
    ensure_chat_session,
    register_document,
)
from app.db.session import session_scope
from app.utils.upload_utils import SavedUpload


def _other_tenant_document(tmp_path: Path):
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
    knowledge_base = create_knowledge_base(
        f"query-acl-{uuid.uuid4()}",
        "",
        "private",
        owner_id=user_id,
        tenant_id=tenant_id,
    )
    source = tmp_path / "source.md"
    source.write_text("# other tenant", encoding="utf-8")
    upload = SavedUpload(
        original_filename="source.md",
        stored_filename=f"{uuid.uuid4()}.md",
        path=source,
        content_type="text/markdown",
        size=source.stat().st_size,
        sha256=uuid.uuid4().hex * 2,
    )
    document, version, _ = register_document(knowledge_base.id, upload, None)
    task_id = str(uuid.uuid4())
    create_import_task(task_id, document.id, version.version)
    return tenant_id, user_id, knowledge_base.id, task_id


def test_cross_tenant_history_is_not_disclosed(tmp_path: Path):
    tenant_id, user_id, knowledge_base_id, _ = _other_tenant_document(tmp_path)
    session_id = f"other-session-{uuid.uuid4()}"
    ensure_chat_session(
        session_id,
        knowledge_base_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    with TestClient(query_service.app) as client:
        response = client.get(f"/history/{session_id}")

    assert response.status_code == 404


def test_cross_tenant_local_image_is_not_disclosed(tmp_path: Path, monkeypatch):
    _, _, _, task_id = _other_tenant_document(tmp_path)
    image_dir = tmp_path / "output" / "20260831" / task_id / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "private.png").write_bytes(b"not-a-real-png")
    monkeypatch.setattr(query_service, "PROJECT_ROOT", tmp_path)

    with TestClient(query_service.app) as client:
        response = client.get(f"/images/{task_id}/private.png")

    assert response.status_code == 404
