"""Knowledge-base, document and import-task lifecycle endpoints."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.clients.milvus_utils import get_milvus_client
from app.clients.minio_utils import get_minio_client
from app.core.errors import AppError, ErrorCode
from app.core.logger import logger
from app.core.security import RequireAdmin, RequireReadonly
from app.core.settings import PROJECT_ROOT, settings
from app.db.lifecycle_repositories import (
    get_cleanup_event,
    get_document_cleanup_event,
    list_cleanup_events,
    request_document_deletion,
    retry_cleanup_event,
)
from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    add_operation_log,
    create_knowledge_base,
    get_document,
    get_import_task,
    get_import_task_document,
    get_accessible_knowledge_base,
    get_knowledge_base,
    list_document_versions,
    list_documents,
    list_import_tasks,
    list_knowledge_bases,
    create_import_task,
    soft_delete_knowledge_base,
)
from app.import_process.services.import_runner import run_import_graph
from app.services.lifecycle import process_cleanup_event_safely, validate_minio_object_name
from app.utils.task_utils import TASK_STATUS_PENDING, set_task_result, update_task_status
from app.utils.milvus_utils import build_scope_filter


router = APIRouter(tags=["knowledge-base-management"])


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    permission_scope: Literal["private", "department", "public"] = "private"


def _knowledge_base_payload(record) -> dict:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "name": record.name,
        "description": record.description,
        "owner_id": record.owner_id,
        "permission_scope": record.permission_scope,
        "embedding_model": record.embedding_model,
        "collection_name": record.collection_name,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _document_payload(record) -> dict:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "knowledge_base_id": record.knowledge_base_id,
        "original_filename": record.original_filename,
        "content_hash": record.content_hash,
        "mime_type": record.mime_type,
        "file_size": record.file_size,
        "current_version": record.current_version,
        "status": record.status,
        "lifecycle_status": record.lifecycle_status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _cleanup_event_payload(record) -> dict:
    return {
        "id": record.id,
        "document_id": record.aggregate_id,
        "status": record.status,
        "completed_stages": list((record.payload or {}).get("completed_stages") or []),
        "attempts": record.attempts,
        "max_attempts": record.max_attempts,
        "error_code": record.last_error_code,
        "error_summary": record.last_error_summary,
        "next_retry_at": record.next_retry_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
    }


def _dispatch_cleanup(
    background_tasks: BackgroundTasks,
    event_id: str,
    tenant_id: str,
    user_id: str,
) -> None:
    if settings.task_queue_enabled:
        from app.worker.tasks import cleanup_document_task

        cleanup_document_task.apply_async(
            args=[event_id, tenant_id, user_id],
            task_id=event_id,
            queue="cleanup",
        )
    else:
        background_tasks.add_task(process_cleanup_event_safely, event_id, tenant_id, user_id)


def _task_payload(record) -> dict:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "document_id": record.document_id,
        "document_version": record.document_version,
        "status": record.status,
        "progress": record.progress,
        "current_node": record.current_node,
        "retry_count": record.retry_count,
        "error_code": record.error_code,
        "error_summary": record.error_summary,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "created_at": record.created_at,
    }


@router.post("/knowledge-bases", status_code=201)
async def create_kb(payload: KnowledgeBaseCreate, request: Request, principal: RequireAdmin):
    try:
        record = create_knowledge_base(
            payload.name.strip(),
            payload.description.strip(),
            payload.permission_scope,
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
    except IntegrityError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "知识库名称已经存在", status_code=409) from exc
    add_operation_log(
        principal.subject,
        "create",
        "knowledge_base",
        record.id,
        request_id=getattr(request.state, "request_id", None),
        tenant_id=principal.tenant_id,
    )
    return _knowledge_base_payload(record)


@router.get("/knowledge-bases")
async def get_knowledge_bases(principal: RequireReadonly):
    records = list_knowledge_bases(
        principal.tenant_id,
        user_id=None if principal.is_admin else principal.user_id,
    )
    return {"items": [_knowledge_base_payload(record) for record in records]}


@router.get("/knowledge-bases/{knowledge_base_id}")
async def get_kb(knowledge_base_id: str, principal: RequireReadonly):
    record = get_accessible_knowledge_base(
        knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    )
    if record is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "知识库不存在", status_code=404)
    return _knowledge_base_payload(record)


@router.delete("/knowledge-bases/{knowledge_base_id}")
async def delete_kb(
    knowledge_base_id: str,
    request: Request,
    principal: RequireAdmin,
    confirm: bool = Query(default=False),
):
    if not confirm:
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除知识库必须传入 confirm=true", status_code=409)
    if knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID:
        raise AppError(ErrorCode.VALIDATION_ERROR, "默认知识库受保护，不能删除", status_code=409)
    record = get_accessible_knowledge_base(
        knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    )
    if record is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识库不存在", status_code=404)
    documents = list_documents(knowledge_base_id)
    try:
        if settings.milvus_required:
            client = get_milvus_client()
            if client is None:
                raise RuntimeError("Milvus unavailable")
            client.delete(
                collection_name=settings.milvus_collection,
                filter=build_scope_filter(
                    principal.tenant_id,
                    knowledge_base_id,
                    active_only=False,
                ),
            )
            if client.has_collection(collection_name=settings.item_name_collection):
                client.delete(
                    collection_name=settings.item_name_collection,
                    filter=build_scope_filter(
                        principal.tenant_id,
                        knowledge_base_id,
                        active_only=False,
                    ),
            )
        if settings.minio_enabled:
            minio_client = get_minio_client()
            if minio_client is None:
                raise RuntimeError("MinIO unavailable")
            for document in documents:
                if document.object_storage_path:
                    minio_client.remove_object(
                        settings.minio_bucket_name,
                        document.object_storage_path,
                    )
    except Exception as exc:
        logger.opt(exception=True).error("知识库 {} 外部资源清理失败：{}", knowledge_base_id, exc)
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "知识库尚未删除：外部资源清理失败，请稍后重试",
            status_code=503,
        ) from exc
    if not soft_delete_knowledge_base(knowledge_base_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识库不存在", status_code=404)
    add_operation_log(
        principal.subject,
        "delete",
        "knowledge_base",
        knowledge_base_id,
        request_id=getattr(request.state, "request_id", None),
        tenant_id=principal.tenant_id,
    )
    return {
        "id": knowledge_base_id,
        "deleted": True,
        "cleanup": "metadata, vectors, aliases, stored source objects",
    }


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
async def get_documents(knowledge_base_id: str, principal: RequireReadonly):
    if get_accessible_knowledge_base(
        knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "知识库不存在", status_code=404)
    return {"items": [_document_payload(record) for record in list_documents(knowledge_base_id)]}


@router.get("/documents/{document_id}")
async def get_document_detail(document_id: str, principal: RequireReadonly):
    record = get_document(document_id)
    if record is None or get_accessible_knowledge_base(
        record.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "文档不存在", status_code=404)
    return _document_payload(record)


@router.get("/documents/{document_id}/versions")
async def get_versions(document_id: str, principal: RequireReadonly):
    document = get_document(document_id)
    if document is None or get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "文档不存在", status_code=404)
    return {
        "items": [
            {
                "id": version.id,
                "document_id": version.document_id,
                "version": version.version,
                "content_hash": version.content_hash,
                "parser_version": version.parser_version,
                "chunk_strategy_version": version.chunk_strategy_version,
                "embedding_model": version.embedding_model,
                "is_active": version.is_active,
                "chunk_count": version.chunk_count,
                "activated_at": version.activated_at,
                "activated_by": version.activated_by,
                "created_at": version.created_at,
            }
            for version in list_document_versions(document_id)
        ]
    }


@router.delete("/documents/{document_id}", status_code=202)
async def delete_document_endpoint(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: RequireAdmin,
    confirm: bool = Query(default=False),
):
    if not confirm:
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除文档必须传入 confirm=true", status_code=409)
    record = get_document(document_id)
    if record is None:
        existing = get_document_cleanup_event(document_id, principal.tenant_id)
        if existing is not None:
            return {
                "id": document_id,
                "accepted": True,
                "created": False,
                "cleanup_job": _cleanup_event_payload(existing),
            }
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    if get_accessible_knowledge_base(
        record.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    _, cleanup_event, created = request_document_deletion(
        document_id,
        principal.tenant_id,
        principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
    )
    if created:
        _dispatch_cleanup(
            background_tasks,
            cleanup_event.id,
            principal.tenant_id,
            principal.user_id,
        )
    add_operation_log(
        principal.subject,
        "delete",
        "document",
        document_id,
        request_id=getattr(request.state, "request_id", None),
        tenant_id=principal.tenant_id,
    )
    return {
        "id": document_id,
        "accepted": True,
        "created": created,
        "cleanup_job": _cleanup_event_payload(cleanup_event),
    }


@router.get("/cleanup-jobs")
async def get_cleanup_jobs(
    principal: RequireAdmin,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": [
            _cleanup_event_payload(event)
            for event in list_cleanup_events(principal.tenant_id, limit)
        ]
    }


@router.get("/cleanup-jobs/{event_id}")
async def get_cleanup_job(event_id: str, principal: RequireAdmin):
    event = get_cleanup_event(event_id, principal.tenant_id)
    if event is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "清理任务不存在", status_code=404)
    return _cleanup_event_payload(event)


@router.post("/cleanup-jobs/{event_id}/retry", status_code=202)
async def retry_cleanup_job(
    event_id: str,
    background_tasks: BackgroundTasks,
    principal: RequireAdmin,
):
    existing = get_cleanup_event(event_id, principal.tenant_id)
    if existing is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "清理任务不存在", status_code=404)
    if existing.status not in {"RETRYING", "DEAD_LETTER"}:
        return _cleanup_event_payload(existing)
    try:
        event = retry_cleanup_event(event_id, principal.tenant_id)
    except LookupError as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "清理任务不存在", status_code=404) from exc
    if event.status != "COMPLETED":
        _dispatch_cleanup(background_tasks, event.id, principal.tenant_id, principal.user_id)
    return _cleanup_event_payload(event)


def _version_source_path(document, version) -> Path | None:
    if not version.source_local_path:
        return None
    source = Path(version.source_local_path).resolve()
    output_root = (PROJECT_ROOT / "output").resolve()
    if not source.is_file() or output_root not in source.parents:
        return None
    return source


@router.post("/documents/{document_id}/versions/{version_number}/activate", status_code=202)
@router.post("/documents/{document_id}/versions/{version_number}/rollback", status_code=202)
async def rebuild_document_version(
    document_id: str,
    version_number: int,
    background_tasks: BackgroundTasks,
    principal: RequireAdmin,
):
    document = get_document(document_id)
    if document is None or get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    versions = list_document_versions(document_id)
    target = next((item for item in versions if item.version == version_number), None)
    if target is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档版本不存在", status_code=404)
    if target.is_active:
        return {
            "document_id": document.id,
            "document_version": target.version,
            "already_active": True,
            "chunk_count_before": target.chunk_count,
            "chunk_count_after": target.chunk_count,
        }

    source = _version_source_path(document, target)
    task_id = str(uuid.uuid4())
    output_root = (PROJECT_ROOT / "output").resolve()
    task_dir = output_root / datetime.now().strftime("%Y%m%d") / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    local_file_path = (task_dir / document.stored_filename).resolve()
    try:
        if source is not None:
            shutil.copy2(source, local_file_path)
        elif settings.minio_enabled and target.source_object_path:
            object_name = validate_minio_object_name(target.source_object_path)
            required_prefix = PurePosixPath(
                settings.minio_pdf_dir,
                principal.tenant_id,
                document.knowledge_base_id,
            ).as_posix()
            if not object_name.startswith(f"{required_prefix}/"):
                raise ValueError("source object is outside document prefix")
            client = get_minio_client()
            if client is None:
                raise RuntimeError("MinIO unavailable")
            client.fget_object(settings.minio_bucket_name, object_name, str(local_file_path))
        else:
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "该历史版本的原始文件不存在，无法重建",
                status_code=410,
            )
    except Exception:
        local_file_path.unlink(missing_ok=True)
        try:
            task_dir.rmdir()
        except OSError:
            pass
        raise

    create_import_task(
        task_id,
        document.id,
        target.version,
        local_dir=str(task_dir),
        local_file_path=str(local_file_path),
    )
    update_task_status(task_id, TASK_STATUS_PENDING)
    set_task_result(task_id, "local_dir", str(task_dir))
    set_task_result(task_id, "local_file_path", str(local_file_path))
    if settings.task_queue_enabled:
        from app.worker.tasks import import_document_task

        import_document_task.apply_async(
            args=[task_id, str(task_dir), str(local_file_path), principal.tenant_id, principal.user_id],
            task_id=task_id,
            queue="import",
        )
    else:
        background_tasks.add_task(
            run_import_graph,
            task_id,
            str(task_dir),
            str(local_file_path),
            principal.tenant_id,
            principal.user_id,
        )
    current = next((item for item in versions if item.is_active), None)
    return {
        "task_id": task_id,
        "document_id": document.id,
        "document_version": target.version,
        "status": TASK_STATUS_PENDING,
        "activation": "after_successful_rebuild",
        "chunk_count_before": current.chunk_count if current else None,
        "target_previous_chunk_count": target.chunk_count,
        "chunk_count_after": None,
    }


@router.get("/tasks")
async def get_tasks(
    principal: RequireAdmin,
    limit: int = Query(default=100, ge=1, le=500),
    include_deleted: bool = Query(default=False),
):
    records = list_import_tasks(
        limit,
        tenant_id=principal.tenant_id,
        include_deleted=include_deleted,
    )
    return {"items": [_task_payload(record) for record in records]}


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: str, principal: RequireReadonly):
    context = get_import_task_document(task_id)
    if context is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    record, document = context
    if get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    return _task_payload(record)
