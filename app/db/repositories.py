"""Transactional repositories for metadata, tasks and chat history."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.core.settings import settings
from app.db.models import (
    ChatMessage,
    ChatSession,
    ChunkRecord,
    Document,
    DocumentVersion,
    ImportTask,
    KnowledgeBase,
    Membership,
    OperationLog,
    Tenant,
    User,
)
from app.db.session import session_scope
from app.utils.upload_utils import SavedUpload


DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_KNOWLEDGE_BASE_ID = "00000000-0000-0000-0000-000000000010"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000100"


def ensure_defaults() -> None:
    if not settings.database_enabled or settings.is_production:
        return
    with session_scope() as session:
        tenant = session.get(Tenant, DEFAULT_TENANT_ID)
        if tenant is None:
            session.add(
                Tenant(
                    id=DEFAULT_TENANT_ID,
                    slug="default",
                    name="默认租户",
                    enabled=True,
                )
            )
            session.flush()
        user = session.get(User, DEFAULT_USER_ID)
        if user is None:
            session.add(
                User(
                    id=DEFAULT_USER_ID,
                    tenant_id=DEFAULT_TENANT_ID,
                    username="local-admin",
                    role="admin",
                    enabled=True,
                )
            )
            session.flush()
        membership = session.scalar(
            select(Membership).where(
                Membership.tenant_id == DEFAULT_TENANT_ID,
                Membership.user_id == DEFAULT_USER_ID,
            )
        )
        if membership is None:
            session.add(
                Membership(
                    id="00000000-0000-0000-0000-000000000002",
                    tenant_id=DEFAULT_TENANT_ID,
                    user_id=DEFAULT_USER_ID,
                    role="tenant_admin",
                    enabled=True,
                )
            )
            session.flush()
        knowledge_base = session.get(KnowledgeBase, DEFAULT_KNOWLEDGE_BASE_ID)
        if knowledge_base is None:
            session.add(
                KnowledgeBase(
                    id=DEFAULT_KNOWLEDGE_BASE_ID,
                    tenant_id=DEFAULT_TENANT_ID,
                    name="默认知识库",
                    description="本机兼容知识库",
                    owner_id=DEFAULT_USER_ID,
                    permission_scope="private",
                    embedding_model=settings.embedding_model,
                    collection_name=settings.milvus_collection,
                )
            )


def get_user(user_id: str, tenant_id: str | None = None) -> User | None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or (tenant_id is not None and user.tenant_id != tenant_id):
            return None
        return user


def create_knowledge_base(
    name: str,
    description: str,
    permission_scope: str,
    owner_id: str = DEFAULT_USER_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> KnowledgeBase:
    with session_scope() as session:
        record = KnowledgeBase(
            tenant_id=tenant_id,
            name=name,
            description=description,
            owner_id=owner_id,
            permission_scope=permission_scope,
            embedding_model=settings.embedding_model,
            collection_name=settings.milvus_collection,
        )
        session.add(record)
        session.flush()
        return record


def list_knowledge_bases(
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    user_id: str | None = None,
) -> list[KnowledgeBase]:
    with session_scope() as session:
        statement = select(KnowledgeBase).where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        records = list(
            session.scalars(
                statement.order_by(KnowledgeBase.created_at.desc())
            )
        )
    if user_id is None:
        return records
    from app.db.identity_repositories import get_active_membership, has_knowledge_base_grant

    membership = get_active_membership(user_id, tenant_id)
    if membership is None:
        return []
    if membership.role in {"platform_admin", "tenant_admin"}:
        return records
    return [
        record
        for record in records
        if record.owner_id == user_id
        or record.permission_scope in {"department", "public"}
        or has_knowledge_base_grant(
            tenant_id=tenant_id,
            knowledge_base_id=record.id,
            user_id=user_id,
            role=membership.role,
            department_id=membership.department_id,
            required_permission="read",
        )
    ]


def get_knowledge_base(knowledge_base_id: str) -> KnowledgeBase | None:
    with session_scope() as session:
        record = session.get(KnowledgeBase, knowledge_base_id)
        return record if record and record.deleted_at is None else None


def get_accessible_knowledge_base(
    knowledge_base_id: str,
    tenant_id: str,
    user_id: str,
    *,
    is_admin: bool = False,
    write: bool = False,
) -> KnowledgeBase | None:
    record = get_knowledge_base(knowledge_base_id)
    if record is None or record.tenant_id != tenant_id:
        return None
    if is_admin or record.owner_id == user_id:
        return record
    from app.db.identity_repositories import get_active_membership, has_knowledge_base_grant

    membership = get_active_membership(user_id, tenant_id)
    if membership is None:
        return None
    required_permission = "write" if write else "read"
    if has_knowledge_base_grant(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        role=membership.role,
        department_id=membership.department_id,
        required_permission=required_permission,
    ):
        return record
    if not write and record.permission_scope in {"department", "public"}:
        return record
    return None


def soft_delete_knowledge_base(knowledge_base_id: str) -> bool:
    with session_scope() as session:
        record = session.get(KnowledgeBase, knowledge_base_id)
        if record is None or record.deleted_at is not None:
            return False
        deleted_at = datetime.now(UTC)
        record.deleted_at = deleted_at
        documents = list(
            session.scalars(
                select(Document).where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.deleted_at.is_(None),
                )
            )
        )
        for document in documents:
            document.deleted_at = deleted_at
            document.status = "deleted"
        return True


def register_document(
    knowledge_base_id: str,
    saved: SavedUpload,
    object_storage_path: str | None,
) -> tuple[Document, DocumentVersion, bool]:
    with session_scope() as session:
        knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or knowledge_base.deleted_at is not None:
            raise LookupError("knowledge base not found")
        existing = session.scalar(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.content_hash == saved.sha256,
                Document.deleted_at.is_(None),
            )
        )
        if existing is not None:
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == existing.id,
                    DocumentVersion.version == existing.current_version,
                )
            )
            if version is None:
                raise RuntimeError("document version metadata is missing")
            return existing, version, False

        previous_version = session.scalar(
            select(Document)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.original_filename == saved.original_filename,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.current_version.desc())
        )
        if previous_version is not None:
            session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.document_id == previous_version.id)
                .values(is_active=False)
            )
            previous_version.current_version += 1
            previous_version.content_hash = saved.sha256
            previous_version.stored_filename = saved.stored_filename
            previous_version.mime_type = saved.content_type
            previous_version.file_size = saved.size
            previous_version.status = "pending"
            previous_version.object_storage_path = object_storage_path
            version = DocumentVersion(
                tenant_id=knowledge_base.tenant_id,
                document_id=previous_version.id,
                version=previous_version.current_version,
                content_hash=saved.sha256,
                parser_version="mineru-v4",
                chunk_strategy_version="heading-v1",
                embedding_model=settings.embedding_model,
                is_active=True,
                source_object_path=object_storage_path,
                source_local_path=str(saved.path),
                activated_at=datetime.now(UTC),
            )
            session.add(version)
            session.flush()
            return previous_version, version, True

        document = Document(
            tenant_id=knowledge_base.tenant_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=saved.original_filename,
            stored_filename=saved.stored_filename,
            content_hash=saved.sha256,
            mime_type=saved.content_type,
            file_size=saved.size,
            current_version=1,
            status="pending",
            object_storage_path=object_storage_path,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            tenant_id=knowledge_base.tenant_id,
            document_id=document.id,
            version=1,
            content_hash=saved.sha256,
            parser_version="mineru-v4",
            chunk_strategy_version="heading-v1",
            embedding_model=settings.embedding_model,
            is_active=True,
            source_object_path=object_storage_path,
            source_local_path=str(saved.path),
            activated_at=datetime.now(UTC),
        )
        session.add(version)
        session.flush()
        return document, version, True


def create_import_task(
    task_id: str,
    document_id: str,
    document_version: int,
    *,
    local_dir: str | None = None,
    local_file_path: str | None = None,
) -> ImportTask:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise LookupError(document_id)
        record = ImportTask(
            id=task_id,
            tenant_id=document.tenant_id,
            document_id=document_id,
            document_version=document_version,
            status="pending",
            progress=0,
            current_node="upload_file",
            local_dir=local_dir,
            local_file_path=local_file_path,
        )
        session.add(record)
        session.flush()
        return record


def set_document_object_path(document_id: str, object_storage_path: str) -> None:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is not None:
            document.object_storage_path = object_storage_path
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version == document.current_version,
                )
            )
            if version is not None:
                version.source_object_path = object_storage_path


def update_import_task(
    task_id: str,
    status: str,
    *,
    current_node: str | None = None,
    progress: int | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> None:
    if not settings.database_enabled:
        return
    with session_scope() as session:
        task = session.get(ImportTask, task_id)
        if task is None:
            return
        task.status = status
        if current_node is not None:
            task.current_node = current_node
        if progress is not None:
            task.progress = max(0, min(100, progress))
        if error_code is not None:
            task.error_code = error_code
        if error_summary is not None:
            task.error_summary = error_summary[:1024]
        now = datetime.now(UTC)
        if status == "processing" and task.started_at is None:
            task.started_at = now
        if status in {"completed", "failed", "cancelled"}:
            task.completed_at = now
        document = session.get(Document, task.document_id)
        if document is not None:
            document.status = status


def increment_import_retry(task_id: str) -> None:
    with session_scope() as session:
        task = session.get(ImportTask, task_id)
        if task is not None:
            task.retry_count += 1


def reset_import_task_for_retry(task_id: str, local_dir: str, local_file_path: str) -> None:
    """Atomically prepare a failed persisted task for another execution."""
    with session_scope() as session:
        task = session.get(ImportTask, task_id)
        if task is None:
            raise LookupError(task_id)
        task.status = "pending"
        task.progress = 0
        task.current_node = "retry_pending"
        task.retry_count += 1
        task.error_code = None
        task.error_summary = None
        task.completed_at = None
        task.local_dir = local_dir
        task.local_file_path = local_file_path
        document = session.get(Document, task.document_id)
        if document is not None:
            document.status = "pending"


def get_import_task(task_id: str) -> ImportTask | None:
    with session_scope() as session:
        return session.get(ImportTask, task_id)


def get_import_task_document(task_id: str) -> tuple[ImportTask, Document] | None:
    with session_scope() as session:
        row = session.execute(
            select(ImportTask, Document)
            .join(Document, Document.id == ImportTask.document_id)
            .where(ImportTask.id == task_id)
        ).one_or_none()
        return (row[0], row[1]) if row else None


def get_latest_import_task_for_document(document_id: str) -> ImportTask | None:
    with session_scope() as session:
        return session.scalar(
            select(ImportTask)
            .where(ImportTask.document_id == document_id)
            .order_by(ImportTask.created_at.desc())
            .limit(1)
        )


def list_import_tasks(
    limit: int = 100,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    include_deleted: bool = False,
) -> list[ImportTask]:
    with session_scope() as session:
        statement = (
            select(ImportTask)
            .join(Document, Document.id == ImportTask.document_id)
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .where(ImportTask.tenant_id == tenant_id)
        )
        if not include_deleted:
            statement = statement.where(
                Document.deleted_at.is_(None),
                KnowledgeBase.deleted_at.is_(None),
            )
        return list(
            session.scalars(statement.order_by(ImportTask.created_at.desc()).limit(limit))
        )


def list_documents(knowledge_base_id: str) -> list[Document]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Document)
                .where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.created_at.desc())
            )
        )


def get_document(document_id: str) -> Document | None:
    with session_scope() as session:
        record = session.get(Document, document_id)
        return record if record and record.deleted_at is None else None


def list_document_versions(document_id: str) -> list[DocumentVersion]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version.desc())
            )
        )


def soft_delete_document(document_id: str) -> Document | None:
    with session_scope() as session:
        record = session.get(Document, document_id)
        if record is None or record.deleted_at is not None:
            return None
        record.deleted_at = datetime.now(UTC)
        record.status = "deleted"
        return record


def replace_chunk_metadata(
    document_id: str,
    document_version: int,
    knowledge_base_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    if not settings.database_enabled:
        return
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise LookupError(document_id)
        session.execute(
            delete(ChunkRecord).where(
                ChunkRecord.document_id == document_id,
                ChunkRecord.document_version == document_version,
            )
        )
        for index, chunk in enumerate(chunks):
            milvus_id = str(chunk.get("chunk_id", ""))
            if not milvus_id:
                continue
            content = str(chunk.get("content", ""))
            session.add(
                ChunkRecord(
                    tenant_id=document.tenant_id,
                    chunk_id=f"{document_id}:{document_version}:{index}",
                    document_id=document_id,
                    document_version=document_version,
                    knowledge_base_id=knowledge_base_id,
                    page_number=chunk.get("page_number") or chunk.get("page"),
                    section_title=str(chunk.get("title") or chunk.get("parent_title") or ""),
                    parent_chunk_id=chunk.get("parent_chunk_id"),
                    chunk_index=index,
                    item_name=str(chunk.get("item_name") or ""),
                    item_aliases=list(chunk.get("item_aliases") or []),
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    parser_version="mineru-v4",
                    permission_scope="private",
                    collection_name=settings.milvus_collection,
                    milvus_chunk_id=milvus_id,
                )
            )
        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version == document_version,
            )
        )
        if version is not None:
            version.chunk_count = len(chunks)


def save_chat_message(
    session_id: str,
    role: str,
    text: str,
    rewritten_query: str = "",
    item_names: list[str] | None = None,
    image_urls: list[str] | None = None,
    message_id: str | None = None,
    knowledge_base_id: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    user_id: str = DEFAULT_USER_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> str:
    with session_scope() as session:
        chat = session.get(ChatSession, session_id)
        if chat is None:
            chat = ChatSession(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id or DEFAULT_KNOWLEDGE_BASE_ID,
            )
            session.add(chat)
            session.flush()
        else:
            if chat.tenant_id != tenant_id or chat.user_id != user_id:
                raise PermissionError("session_id belongs to another principal")
            if knowledge_base_id and chat.knowledge_base_id not in {None, knowledge_base_id}:
                raise ValueError("session_id already belongs to another knowledge base")
        message = session.get(ChatMessage, message_id) if message_id else None
        if message is None:
            kwargs = {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "role": role,
                "text": text,
            }
            if message_id:
                kwargs["id"] = message_id
            message = ChatMessage(**kwargs)
            session.add(message)
        elif message.tenant_id != tenant_id:
            raise PermissionError("message belongs to another tenant")
        message.role = role
        message.text = text
        message.rewritten_query = rewritten_query or ""
        message.item_names = item_names or []
        message.image_urls = image_urls or []
        message.citations = citations or []
        message.model = model
        message.latency_ms = latency_ms
        session.flush()
        return message.id


def ensure_chat_session(
    session_id: str,
    knowledge_base_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> ChatSession:
    with session_scope() as session:
        chat = session.get(ChatSession, session_id)
        if chat is None:
            chat = ChatSession(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
            )
            session.add(chat)
            session.flush()
            return chat
        if (
            chat.tenant_id != tenant_id
            or chat.user_id != user_id
            or chat.knowledge_base_id != knowledge_base_id
        ):
            raise PermissionError("session_id belongs to another principal or knowledge base")
        return chat


def get_chat_session(session_id: str) -> ChatSession | None:
    with session_scope() as session:
        return session.get(ChatSession, session_id)


def update_message_item_names(ids: list[str], item_names: list[str]) -> int:
    with session_scope() as session:
        records = list(session.scalars(select(ChatMessage).where(ChatMessage.id.in_(ids))))
        for record in records:
            record.item_names = item_names
        return len(records)


def get_recent_messages(
    session_id: str,
    limit: int = 10,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        statement = (
            select(ChatMessage)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(ChatMessage.session_id == session_id)
        )
        if tenant_id is not None:
            statement = statement.where(ChatSession.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(ChatSession.user_id == user_id)
        records = list(
            session.scalars(
                statement.order_by(ChatMessage.created_at.desc()).limit(limit)
            )
        )
        records.reverse()
        return [
            {
                "_id": record.id,
                "session_id": record.session_id,
                "role": record.role,
                "text": record.text,
                "rewritten_query": record.rewritten_query,
                "item_names": record.item_names,
                "image_urls": record.image_urls,
                "citations": record.citations,
                "model": record.model,
                "latency_ms": record.latency_ms,
                "ts": record.created_at.timestamp() if record.created_at else None,
            }
            for record in records
        ]


def clear_history(
    session_id: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> int:
    with session_scope() as session:
        chat = session.get(ChatSession, session_id)
        if chat is None:
            return 0
        if tenant_id is not None and chat.tenant_id != tenant_id:
            raise PermissionError("session belongs to another tenant")
        if user_id is not None and chat.user_id != user_id:
            raise PermissionError("session belongs to another user")
        records = list(session.scalars(select(ChatMessage.id).where(ChatMessage.session_id == session_id)))
        session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
        session.execute(delete(ChatSession).where(ChatSession.id == session_id))
        return len(records)


def add_operation_log(
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    with session_scope() as session:
        session.add(
            OperationLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
                request_id=request_id,
            )
        )
