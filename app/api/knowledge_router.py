"""Knowledge-base, document and import-task lifecycle endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.clients.milvus_utils import get_milvus_client
from app.clients.minio_utils import get_minio_client
from app.core.errors import AppError, ErrorCode
from app.core.logger import logger
from app.core.security import RequireAdmin, RequireReadonly
from app.core.settings import settings
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
    soft_delete_document,
    soft_delete_knowledge_base,
)
from app.utils.milvus_utils import escape_milvus_string


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
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


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
            safe_id = escape_milvus_string(knowledge_base_id)
            client.delete(
                collection_name=settings.milvus_collection,
                filter=f'knowledge_base_id == "{safe_id}"',
            )
            if client.has_collection(collection_name=settings.item_name_collection):
                client.delete(
                collection_name=settings.item_name_collection,
                filter=f'knowledge_base_id == "{safe_id}"',
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
                "created_at": version.created_at,
            }
            for version in list_document_versions(document_id)
        ]
    }


@router.delete("/documents/{document_id}")
async def delete_document_endpoint(
    document_id: str,
    request: Request,
    principal: RequireAdmin,
    confirm: bool = Query(default=False),
):
    if not confirm:
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除文档必须传入 confirm=true", status_code=409)
    record = get_document(document_id)
    if record is None or get_accessible_knowledge_base(
        record.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    client = get_milvus_client() if settings.milvus_required else None
    if settings.milvus_required and client is None:
        raise AppError(ErrorCode.MILVUS_UNAVAILABLE, "Milvus 当前不可用", status_code=503)
    if client is not None:
        try:
            safe_id = escape_milvus_string(document_id)
            client.delete(
                collection_name=settings.milvus_collection,
                filter=f'document_id == "{safe_id}"',
            )
        except Exception as exc:
            logger.opt(exception=True).error("文档 {} 的 Milvus 清理失败：{}", document_id, exc)
            raise AppError(
                ErrorCode.MILVUS_UNAVAILABLE,
                "文档尚未删除：向量清理失败，请稍后重试",
                status_code=503,
            ) from exc
    if settings.minio_enabled and record.object_storage_path:
        try:
            minio_client = get_minio_client()
            if minio_client is None:
                raise RuntimeError("MinIO unavailable")
            minio_client.remove_object(settings.minio_bucket_name, record.object_storage_path)
        except Exception as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "文档尚未删除：源文件清理失败，请稍后重试",
                status_code=503,
            ) from exc
    if soft_delete_document(document_id) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    add_operation_log(
        principal.subject,
        "delete",
        "document",
        document_id,
        request_id=getattr(request.state, "request_id", None),
        tenant_id=principal.tenant_id,
    )
    return {"id": document_id, "deleted": True}


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
