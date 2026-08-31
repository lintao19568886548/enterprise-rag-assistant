import uuid
from pathlib import Path

import pytest

from app.db.models import Tenant, User
from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    create_import_task,
    create_knowledge_base,
    ensure_chat_session,
    ensure_defaults,
    get_accessible_knowledge_base,
    list_import_tasks,
    list_knowledge_bases,
    register_document,
    soft_delete_knowledge_base,
)
from app.db.session import init_database, session_scope
from app.utils.upload_utils import SavedUpload


def _other_principal() -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(Tenant(id=tenant_id, slug=f"tenant-{tenant_id}", name="隔离租户"))
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                username=f"user-{user_id}",
                role="admin",
                enabled=True,
            )
        )
    return tenant_id, user_id


def test_knowledge_base_access_is_tenant_scoped():
    init_database()
    ensure_defaults()
    other_tenant_id, other_user_id = _other_principal()
    record = create_knowledge_base(
        f"隔离知识库-{uuid.uuid4()}",
        "",
        "private",
        owner_id=other_user_id,
        tenant_id=other_tenant_id,
    )

    assert get_accessible_knowledge_base(
        record.id,
        DEFAULT_TENANT_ID,
        DEFAULT_USER_ID,
        is_admin=True,
    ) is None
    assert get_accessible_knowledge_base(
        record.id,
        other_tenant_id,
        other_user_id,
        is_admin=True,
    ) is not None
    assert record.id not in {item.id for item in list_knowledge_bases(DEFAULT_TENANT_ID)}


def test_chat_session_cannot_be_reclaimed_by_another_principal():
    init_database()
    ensure_defaults()
    session_id = f"acl-session-{uuid.uuid4()}"
    ensure_chat_session(
        session_id,
        DEFAULT_KNOWLEDGE_BASE_ID,
        user_id=DEFAULT_USER_ID,
        tenant_id=DEFAULT_TENANT_ID,
    )
    other_tenant_id, other_user_id = _other_principal()

    with pytest.raises(PermissionError):
        ensure_chat_session(
            session_id,
            DEFAULT_KNOWLEDGE_BASE_ID,
            user_id=other_user_id,
            tenant_id=other_tenant_id,
        )


def test_deleted_knowledge_base_tasks_are_hidden_by_default(tmp_path: Path):
    init_database()
    ensure_defaults()
    knowledge_base = create_knowledge_base(
        f"生命周期-{uuid.uuid4()}",
        "",
        "private",
    )
    source = tmp_path / "manual.md"
    source.write_text("# 测试", encoding="utf-8")
    upload = SavedUpload(
        original_filename="manual.md",
        stored_filename=f"{uuid.uuid4()}.md",
        path=source,
        content_type="text/markdown",
        size=source.stat().st_size,
        sha256=uuid.uuid4().hex * 2,
    )
    document, version, _ = register_document(knowledge_base.id, upload, None)
    task_id = str(uuid.uuid4())
    create_import_task(task_id, document.id, version.version)

    assert task_id in {task.id for task in list_import_tasks(tenant_id=DEFAULT_TENANT_ID)}
    assert soft_delete_knowledge_base(knowledge_base.id) is True
    assert task_id not in {task.id for task in list_import_tasks(tenant_id=DEFAULT_TENANT_ID)}
    assert task_id in {
        task.id
        for task in list_import_tasks(
            tenant_id=DEFAULT_TENANT_ID,
            include_deleted=True,
        )
    }
