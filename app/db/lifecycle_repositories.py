"""Transactional document lifecycle, version activation and cleanup outbox operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update

from app.core.settings import settings
from app.db.models import ChunkRecord, Document, DocumentVersion, ImportTask, OutboxEvent
from app.db.session import session_scope


DOCUMENT_DELETE_EVENT = "document.cleanup.requested"
TERMINAL_CLEANUP_STATUSES = {"COMPLETED", "DEAD_LETTER"}


@dataclass(frozen=True)
class DocumentCleanupContext:
    document_id: str
    tenant_id: str
    knowledge_base_id: str
    source_object_paths: tuple[str, ...]
    task_ids: tuple[str, ...]
    local_directories: tuple[str, ...]


def request_document_deletion(
    document_id: str,
    tenant_id: str,
    requested_by: str,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[Document, OutboxEvent, bool]:
    """Atomically move a document to DELETING and enqueue one idempotent event."""
    deduplication_key = f"document:{document_id}:delete"
    with session_scope() as session:
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
        )
        if document is None:
            raise LookupError("document not found")
        existing = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.deduplication_key == deduplication_key,
            )
        )
        if existing is not None:
            return document, existing, False
        event = OutboxEvent(
            tenant_id=tenant_id,
            aggregate_type="document",
            aggregate_id=document_id,
            event_type=DOCUMENT_DELETE_EVENT,
            deduplication_key=deduplication_key,
            status="PENDING",
            payload={"completed_stages": []},
            max_attempts=settings.cleanup_max_retries,
            requested_by=requested_by,
            request_id=request_id,
            trace_id=trace_id,
        )
        document.lifecycle_status = "DELETING"
        session.add(event)
        session.flush()
        return document, event, True


def get_cleanup_event(event_id: str, tenant_id: str) -> OutboxEvent | None:
    with session_scope() as session:
        return session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.event_type == DOCUMENT_DELETE_EVENT,
            )
        )


def get_document_cleanup_event(document_id: str, tenant_id: str) -> OutboxEvent | None:
    with session_scope() as session:
        return session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.deduplication_key == f"document:{document_id}:delete",
                OutboxEvent.event_type == DOCUMENT_DELETE_EVENT,
            )
        )


def list_cleanup_events(tenant_id: str, limit: int = 100) -> list[OutboxEvent]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.tenant_id == tenant_id,
                    OutboxEvent.event_type == DOCUMENT_DELETE_EVENT,
                )
                .order_by(OutboxEvent.created_at.desc())
                .limit(limit)
            )
        )


def mark_cleanup_started(event_id: str, tenant_id: str) -> tuple[OutboxEvent, bool]:
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
            ).with_for_update()
        )
        if event is None:
            raise LookupError("cleanup event not found")
        if event.status == "COMPLETED":
            return event, False
        if event.status == "PROCESSING":
            return event, False
        event.status = "PROCESSING"
        event.attempts += 1
        event.next_retry_at = None
        event.last_error_code = None
        event.last_error_summary = None
        document = session.get(Document, event.aggregate_id)
        if document is not None and document.lifecycle_status == "CLEANUP_FAILED":
            document.lifecycle_status = "RETRYING"
        session.flush()
        return event, True


def get_document_cleanup_context(event_id: str, tenant_id: str) -> DocumentCleanupContext:
    """Load persisted cleanup pointers without exposing them through an API payload."""
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.event_type == DOCUMENT_DELETE_EVENT,
            )
        )
        if event is None:
            raise LookupError("cleanup event not found")
        document = session.scalar(
            select(Document).where(
                Document.id == event.aggregate_id,
                Document.tenant_id == tenant_id,
            )
        )
        if document is None:
            raise LookupError("document not found")
        versions = list(
            session.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.tenant_id == tenant_id,
                )
            )
        )
        tasks = list(
            session.scalars(
                select(ImportTask).where(
                    ImportTask.document_id == document.id,
                    ImportTask.tenant_id == tenant_id,
                )
            )
        )
        object_paths = {
            value
            for value in (
                document.object_storage_path,
                *(version.source_object_path for version in versions),
            )
            if value
        }
        local_directories = {
            value
            for value in (
                *(task.local_dir for task in tasks),
                *(version.source_local_path for version in versions),
            )
            if value
        }
        return DocumentCleanupContext(
            document_id=document.id,
            tenant_id=document.tenant_id,
            knowledge_base_id=document.knowledge_base_id,
            source_object_paths=tuple(sorted(object_paths)),
            task_ids=tuple(sorted(task.id for task in tasks)),
            local_directories=tuple(sorted(local_directories)),
        )


def record_cleanup_stage(event_id: str, tenant_id: str, stage: str) -> OutboxEvent:
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
            )
        )
        if event is None:
            raise LookupError("cleanup event not found")
        payload = dict(event.payload or {})
        stages = list(payload.get("completed_stages") or [])
        if stage not in stages:
            stages.append(stage)
        payload["completed_stages"] = stages
        event.payload = payload
        session.flush()
        return event


def mark_cleanup_failure(
    event_id: str,
    tenant_id: str,
    *,
    error_code: str,
    error_summary: str,
) -> OutboxEvent:
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
            )
        )
        if event is None:
            raise LookupError("cleanup event not found")
        exhausted = event.attempts >= event.max_attempts
        event.status = "DEAD_LETTER" if exhausted else "RETRYING"
        event.last_error_code = error_code[:128]
        event.last_error_summary = error_summary[:1024]
        if not exhausted:
            delay = min(
                settings.cleanup_retry_max_seconds,
                settings.cleanup_retry_base_seconds * (2 ** max(event.attempts - 1, 0)),
            )
            event.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        document = session.get(Document, event.aggregate_id)
        if document is not None:
            document.lifecycle_status = "CLEANUP_FAILED"
        session.flush()
        return event


def finalize_document_deletion(event_id: str, tenant_id: str) -> OutboxEvent:
    """Commit the SQL terminal state only after all external stages succeeded."""
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
            )
        )
        if event is None:
            raise LookupError("cleanup event not found")
        if event.status == "COMPLETED":
            return event
        document = session.scalar(
            select(Document).where(
                Document.id == event.aggregate_id,
                Document.tenant_id == tenant_id,
            )
        )
        if document is None:
            raise LookupError("document not found")
        now = datetime.now(UTC)
        document.lifecycle_status = "DELETED"
        document.status = "deleted"
        document.deleted_at = document.deleted_at or now
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .values(is_active=False)
        )
        session.execute(
            update(ImportTask)
            .where(
                ImportTask.document_id == document.id,
                ImportTask.status.in_(("pending", "processing")),
            )
            .values(status="cancelled", completed_at=now)
        )
        session.execute(delete(ChunkRecord).where(ChunkRecord.document_id == document.id))
        payload = dict(event.payload or {})
        stages = list(payload.get("completed_stages") or [])
        if "sql_metadata" not in stages:
            stages.append("sql_metadata")
        payload["completed_stages"] = stages
        event.payload = payload
        event.status = "COMPLETED"
        event.completed_at = now
        event.next_retry_at = None
        event.last_error_code = None
        event.last_error_summary = None
        session.flush()
        return event


def retry_cleanup_event(event_id: str, tenant_id: str) -> OutboxEvent:
    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.tenant_id == tenant_id,
            )
        )
        if event is None:
            raise LookupError("cleanup event not found")
        if event.status == "COMPLETED":
            return event
        if event.status in {"PENDING", "PROCESSING"}:
            return event
        if event.status == "DEAD_LETTER":
            event.attempts = 0
        event.status = "PENDING"
        event.next_retry_at = None
        event.last_error_code = None
        event.last_error_summary = None
        document = session.get(Document, event.aggregate_id)
        if document is not None:
            document.lifecycle_status = "RETRYING"
        session.flush()
        return event


def activate_document_version(
    document_id: str,
    version_number: int,
    tenant_id: str,
    activated_by: str,
) -> tuple[Document, DocumentVersion, int, int]:
    with session_scope() as session:
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None),
            )
        )
        if document is None or document.lifecycle_status != "ACTIVE":
            raise LookupError("active document not found")
        target = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.version == version_number,
            )
        )
        if target is None:
            raise LookupError("document version not found")
        previous_count = int(
            session.scalar(
                select(func.count(ChunkRecord.id)).where(
                    ChunkRecord.document_id == document_id,
                    ChunkRecord.document_version == document.current_version,
                )
            )
            or 0
        )
        target_count = int(
            session.scalar(
                select(func.count(ChunkRecord.id)).where(
                    ChunkRecord.document_id == document_id,
                    ChunkRecord.document_version == version_number,
                )
            )
            or 0
        )
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .values(is_active=False)
        )
        target.is_active = True
        target.activated_at = datetime.now(UTC)
        target.activated_by = activated_by
        document.current_version = version_number
        session.flush()
        return document, target, previous_count, target_count
