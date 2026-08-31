"""Crash-resumable document cleanup orchestration."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from minio.deleteobjects import DeleteObject

from app.clients.milvus_utils import get_milvus_client
from app.clients.minio_utils import get_minio_client
from app.core.logger import logger
from app.core.metrics import CLEANUP_EVENTS
from app.core.settings import PROJECT_ROOT, settings
from app.core.tenant_context import identity_context
from app.db.identity_repositories import add_audit_log
from app.db.lifecycle_repositories import (
    DocumentCleanupContext,
    finalize_document_deletion,
    get_cleanup_event,
    get_document_cleanup_context,
    mark_cleanup_failure,
    mark_cleanup_started,
    record_cleanup_stage,
)
from app.db.models import OutboxEvent
from app.utils.milvus_utils import build_scope_filter


CLEANUP_STAGES = ("vectors", "object_storage", "local_output", "sql_metadata")


class CleanupStageError(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause_type = cause.__class__.__name__
        super().__init__(f"cleanup stage {stage} failed")


def validate_minio_object_name(object_name: str) -> str:
    """Accept only normalized relative MinIO object names."""
    if not object_name or object_name.startswith(("/", "\\")) or "\\" in object_name:
        raise ValueError("invalid object name")
    path = PurePosixPath(object_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid object name")
    return path.as_posix()


def _validated_source_object(context: DocumentCleanupContext, object_name: str) -> str:
    normalized = validate_minio_object_name(object_name)
    expected_prefix = PurePosixPath(
        settings.minio_pdf_dir,
        context.tenant_id,
        context.knowledge_base_id,
    ).as_posix()
    if normalized != expected_prefix and not normalized.startswith(f"{expected_prefix}/"):
        raise ValueError("object is outside document source prefix")
    return normalized


def validated_task_output_directories(context: DocumentCleanupContext) -> tuple[Path, ...]:
    """Resolve only output/YYYYMMDD/<persisted-task-id>, never arbitrary paths."""
    output_root = (PROJECT_ROOT / "output").resolve()
    validated: set[Path] = set()
    task_ids = set(context.task_ids)
    for pointer in context.local_directories:
        resolved = Path(pointer).resolve()
        try:
            relative = resolved.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("local output is outside project output root") from exc
        parts = relative.parts
        if len(parts) < 2 or not re.fullmatch(r"\d{8}", parts[0]) or parts[1] not in task_ids:
            raise ValueError("local output does not match a persisted task directory")
        target = (output_root / parts[0] / parts[1]).resolve()
        if target == output_root or target.parent.parent != output_root:
            raise ValueError("unsafe local output target")
        validated.add(target)
    return tuple(sorted(validated, key=str))


def _cleanup_vectors(context: DocumentCleanupContext) -> None:
    if not settings.milvus_required:
        return
    client = get_milvus_client()
    if client is None:
        raise RuntimeError("Milvus unavailable")
    expression = build_scope_filter(
        context.tenant_id,
        context.knowledge_base_id,
        document_id=context.document_id,
        active_only=False,
    )
    for collection_name in (settings.milvus_collection, settings.item_name_collection):
        if client.has_collection(collection_name=collection_name):
            client.delete(collection_name=collection_name, filter=expression)


def _remove_minio_prefix(client, prefix: str) -> None:
    normalized = validate_minio_object_name(prefix)
    objects = client.list_objects(
        settings.minio_bucket_name,
        prefix=f"{normalized.rstrip('/')}/",
        recursive=True,
    )
    errors = list(
        client.remove_objects(
            settings.minio_bucket_name,
            (DeleteObject(item.object_name) for item in objects),
        )
    )
    if errors:
        raise RuntimeError("MinIO batch removal failed")


def _cleanup_object_storage(context: DocumentCleanupContext) -> None:
    if not settings.minio_enabled:
        if context.source_object_paths:
            raise RuntimeError("MinIO disabled while source objects remain")
        return
    client = get_minio_client()
    if client is None:
        raise RuntimeError("MinIO unavailable")
    for object_name in context.source_object_paths:
        client.remove_object(
            settings.minio_bucket_name,
            _validated_source_object(context, object_name),
        )
    for task_id in context.task_ids:
        prefix = PurePosixPath(
            settings.minio_img_dir,
            context.tenant_id,
            context.knowledge_base_id,
            task_id,
        ).as_posix()
        _remove_minio_prefix(client, prefix)


def _cleanup_local_output(context: DocumentCleanupContext) -> None:
    for target in validated_task_output_directories(context):
        if target.exists():
            if not target.is_dir():
                raise ValueError("task output target is not a directory")
            shutil.rmtree(target)


def _audit_stage(
    context: DocumentCleanupContext,
    event: OutboxEvent,
    stage: str,
    outcome: str,
) -> None:
    add_audit_log(
        tenant_id=context.tenant_id,
        actor_id=event.requested_by,
        actor_type="user" if event.requested_by else "system",
        event_type=f"document.cleanup.{stage}",
        outcome=outcome,
        resource_type="document",
        resource_id=context.document_id,
        metadata={"cleanup_job_id": event.id, "attempt": event.attempts},
        request_id=event.request_id,
        trace_id=event.trace_id,
    )


def _safe_error(stage: str, exc: Exception) -> tuple[str, str]:
    error_type = exc.__class__.__name__[:80]
    return (
        f"CLEANUP_{stage.upper()}_{error_type.upper()}",
        f"{stage} cleanup failed ({error_type})",
    )


def process_cleanup_event(
    event_id: str,
    tenant_id: str,
    *,
    stage_handlers: dict[str, Callable[[DocumentCleanupContext], None]] | None = None,
) -> OutboxEvent:
    """Execute or resume a cleanup event; completed stages are never repeated."""
    with logger.contextualize(tenant_id=tenant_id, cleanup_job_id=event_id):
        return _process_cleanup_event(event_id, tenant_id, stage_handlers=stage_handlers)


def _process_cleanup_event(
    event_id: str,
    tenant_id: str,
    *,
    stage_handlers: dict[str, Callable[[DocumentCleanupContext], None]] | None = None,
) -> OutboxEvent:
    current = get_cleanup_event(event_id, tenant_id)
    if current is None:
        raise LookupError("cleanup event not found")
    if current.status in {"COMPLETED", "DEAD_LETTER", "PROCESSING"}:
        return current

    event, acquired = mark_cleanup_started(event_id, tenant_id)
    if not acquired:
        return event
    context = get_document_cleanup_context(event_id, tenant_id)
    handlers: dict[str, Callable[[DocumentCleanupContext], None]] = {
        "vectors": _cleanup_vectors,
        "object_storage": _cleanup_object_storage,
        "local_output": _cleanup_local_output,
    }
    if stage_handlers:
        handlers.update(stage_handlers)
    completed = set((event.payload or {}).get("completed_stages") or [])

    for stage in CLEANUP_STAGES:
        if stage in completed:
            continue
        try:
            if stage == "sql_metadata":
                event = finalize_document_deletion(event_id, tenant_id)
            else:
                handlers[stage](context)
                event = record_cleanup_stage(event_id, tenant_id, stage)
            _audit_stage(context, event, stage, "success")
            CLEANUP_EVENTS.labels("success", stage).inc()
        except Exception as exc:
            code, summary = _safe_error(stage, exc)
            failed = mark_cleanup_failure(
                event_id,
                tenant_id,
                error_code=code,
                error_summary=summary,
            )
            _audit_stage(context, failed, stage, "failure")
            CLEANUP_EVENTS.labels("failure", stage).inc()
            logger.error(
                "Cleanup job {} stage {} failed with {}",
                event_id,
                stage,
                exc.__class__.__name__,
            )
            raise CleanupStageError(stage, exc) from exc
    return event


def process_cleanup_event_safely(
    event_id: str,
    tenant_id: str,
    user_id: str,
) -> OutboxEvent | None:
    """Run cleanup for FastAPI background mode without failing the accepted response."""
    with identity_context(tenant_id=tenant_id, user_id=user_id):
        try:
            return process_cleanup_event(event_id, tenant_id)
        except CleanupStageError:
            return get_cleanup_event(event_id, tenant_id)
