"""FastAPI service for validated document uploads and knowledge-base imports."""

from __future__ import annotations

import uuid
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.knowledge_router import router as knowledge_router
from app.api.admin_router import router as admin_router
from app.clients.minio_utils import get_minio_client
from app.core.errors import AppError, ErrorCode
from app.core.health import create_health_router
from app.core.logger import logger
from app.core.middleware import install_common_api_features
from app.core.security import RequireAdmin, RequireEditor, RequireReadonly
from app.core.settings import settings
from app.import_process.agent.kb_import_workflow import get_default_import_workflow
from app.import_process.services.import_runner import run_import_graph
from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    create_import_task,
    ensure_defaults,
    get_document,
    get_import_task,
    get_import_task_document,
    get_latest_import_task_for_document,
    get_accessible_knowledge_base,
    get_knowledge_base,
    register_document,
    reset_import_task_for_retry,
    set_document_object_path,
    update_import_task,
)
from app.db.identity_repositories import add_audit_log
from app.db.session import init_database
from app.utils.path_util import PROJECT_ROOT
from app.utils.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_PROCESSING,
    add_done_task,
    add_running_task,
    clear_task,
    get_done_task_list,
    get_running_task_list,
    get_task_status,
    get_task_result,
    set_task_result,
    update_task_status,
)
from app.utils.upload_utils import SavedUpload, save_validated_upload, validate_upload_metadata


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_for_service("import")
    init_database()
    ensure_defaults()
    get_default_import_workflow()
    logger.info("文件导入服务配置校验通过，环境={}", settings.app_env)
    yield


app = FastAPI(
    title="File Import Service",
    description="PDF/Markdown validation, parsing, chunking, embedding and Milvus import",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Request-ID",
        "X-Trace-ID",
    ],
)
install_common_api_features(app, "import")
app.include_router(create_health_router("import"))
app.include_router(knowledge_router)
app.include_router(admin_router)


@app.get("/import.html", response_class=FileResponse)
async def get_import_page():
    html_path = PROJECT_ROOT / "app" / "import_process" / "page" / "import.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="import.html page not found")
    return FileResponse(path=html_path, media_type="text/html")


@app.get("/admin.html", response_class=FileResponse)
async def get_admin_page():
    html_path = PROJECT_ROOT / "app" / "import_process" / "page" / "admin.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="admin.html page not found")
    return FileResponse(path=html_path, media_type="text/html")


def _upload_to_minio(
    tenant_id: str,
    knowledge_base_id: str,
    task_id: str,
    saved: SavedUpload,
) -> str | None:
    client = get_minio_client()
    if client is None:
        return None
    object_name = (
        f"{settings.minio_pdf_dir}/{tenant_id}/{knowledge_base_id}/{datetime.now():%Y%m%d}/"
        f"{task_id}/{saved.stored_filename}"
    )
    client.fput_object(
        bucket_name=settings.minio_bucket_name,
        object_name=object_name,
        file_path=str(saved.path),
        content_type=saved.content_type,
    )
    return object_name


def _audit_request(
    request: Request,
    principal,
    event_type: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    add_audit_log(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_type="service_account" if principal.service_account_id else "user",
        event_type=event_type,
        outcome="success",
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
    )


@app.post(
    "/upload",
    summary="安全上传文件并启动导入",
    description="支持 PDF/Markdown 多文件上传；服务端执行大小、MIME 和文件签名校验。",
)
async def upload_files(
    background_tasks: BackgroundTasks,
    request: Request,
    principal: RequireEditor,
    files: list[UploadFile] = File(...),
    knowledge_base_id: str = Form(default=DEFAULT_KNOWLEDGE_BASE_ID),
):
    if not files:
        raise AppError(ErrorCode.VALIDATION_ERROR, "至少上传一个文件")
    if get_accessible_knowledge_base(
        knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识库不存在", status_code=404)
    if len(files) > settings.upload_max_files_per_request:
        raise AppError(
            ErrorCode.TOO_MANY_FILES,
            f"单次最多上传 {settings.upload_max_files_per_request} 个文件",
            status_code=413,
        )

    for upload in files:
        validate_upload_metadata(upload)

    saved_request: list[tuple[str, Path, SavedUpload]] = []
    try:
        for upload in files:
            task_id = str(uuid.uuid4())
            task_dir = PROJECT_ROOT / "output" / datetime.now().strftime("%Y%m%d") / task_id
            update_task_status(task_id, TASK_STATUS_PENDING)
            add_running_task(task_id, "upload_file")
            saved = await save_validated_upload(upload, task_dir)
            saved_request.append((task_id, task_dir, saved))
    except Exception:
        for task_id, task_dir, saved in saved_request:
            saved.path.unlink(missing_ok=True)
            clear_task(task_id)
            try:
                task_dir.rmdir()
            except OSError:
                pass
        raise

    queued: list[tuple[str, Path, SavedUpload, str, int]] = []
    response_files: list[dict[str, Any]] = []
    for task_id, task_dir, saved in saved_request:
        object_name = None
        try:
            document, version, created = register_document(knowledge_base_id, saved, None)
        except LookupError as exc:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "知识库不存在", status_code=404) from exc
        if not created:
            saved.path.unlink(missing_ok=True)
            clear_task(task_id)
            object_name = None
            try:
                task_dir.rmdir()
            except OSError:
                pass
            response_files.append(
                {
                    "task_id": None,
                    "document_id": document.id,
                    "document_version": version.version,
                    "original_filename": saved.original_filename,
                    "size": saved.size,
                    "sha256": saved.sha256,
                    "duplicate": True,
                }
            )
            continue
        try:
            object_name = _upload_to_minio(
                principal.tenant_id,
                knowledge_base_id,
                task_id,
                saved,
            )
            if object_name:
                set_document_object_path(document.id, object_name)
        except Exception as exc:
            if settings.minio_enabled:
                logger.opt(exception=True).warning(
                    "[{}] MinIO 上传失败，保留本地文件继续处理：{}", task_id, exc
                )
        create_import_task(
            task_id,
            document.id,
            version.version,
            local_dir=str(task_dir),
            local_file_path=str(saved.path),
        )
        add_done_task(task_id, "upload_file")
        queued.append((task_id, task_dir, saved, document.id, version.version))
        logger.info(
            "[{}] 文件校验并登记成功，原文件名={}，大小={}，sha256={}...，document_id={}",
            task_id,
            saved.original_filename,
            saved.size,
            saved.sha256[:12],
            document.id,
        )

    for task_id, task_dir, saved, document_id, document_version in queued:
        set_task_result(task_id, "local_dir", str(task_dir))
        set_task_result(task_id, "local_file_path", str(saved.path))
        set_task_result(task_id, "sha256", saved.sha256)
        if settings.task_queue_enabled:
            from app.worker.tasks import import_document_task

            import_document_task.apply_async(
                args=[
                    task_id,
                    str(task_dir),
                    str(saved.path),
                    principal.tenant_id,
                    principal.user_id,
                ],
                task_id=task_id,
            )
        else:
            background_tasks.add_task(
                run_import_graph,
                task_id,
                str(task_dir),
                str(saved.path),
                principal.tenant_id,
                principal.user_id,
            )
        response_files.append(
            {
                "task_id": task_id,
                "document_id": document_id,
                "document_version": document_version,
                "original_filename": saved.original_filename,
                "stored_filename": saved.stored_filename,
                "size": saved.size,
                "sha256": saved.sha256,
                "duplicate": False,
            }
        )
        _audit_request(
            request,
            principal,
            "document.import_requested",
            "document",
            document_id,
            {"task_id": task_id, "document_version": document_version},
        )

    return {
        "code": 200,
        "message": f"Files accepted, total: {len(response_files)}",
        "task_ids": [item["task_id"] for item in response_files if item["task_id"]],
        "files": response_files,
    }


@app.get("/status/{task_id}", summary="任务状态查询")
async def get_task_progress(task_id: str, principal: RequireReadonly):
    context = get_import_task_document(task_id)
    if context is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    task_record, document = context
    if get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    status = get_task_status(task_id) or (task_record.status if task_record else None)
    if not status or task_record is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    return {
        "code": 200,
        "task_id": task_id,
        "status": status,
        "done_list": get_done_task_list(task_id),
        "running_list": get_running_task_list(task_id),
        "progress": task_record.progress if task_record else None,
        "current_node": task_record.current_node if task_record else None,
        "retry_count": task_record.retry_count if task_record else 0,
        "error_code": task_record.error_code if task_record else None,
        "error_summary": task_record.error_summary if task_record else None,
    }


@app.post("/tasks/{task_id}/cancel", summary="取消导入任务")
async def cancel_task(task_id: str, principal: RequireAdmin):
    context = get_import_task_document(task_id)
    if context is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    task_record, document = context
    if get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    current = get_task_status(task_id) or (task_record.status if task_record else None)
    if not current or task_record is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    if current in {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED}:
        return {"task_id": task_id, "status": current, "cancelled": False}
    from app.utils.task_utils import TASK_STATUS_CANCELLED

    update_task_status(task_id, TASK_STATUS_CANCELLED)
    update_import_task(task_id, TASK_STATUS_CANCELLED, current_node="cancelled", progress=0)
    if settings.task_queue_enabled:
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(task_id, terminate=False)
    return {"task_id": task_id, "status": TASK_STATUS_CANCELLED, "cancelled": True}


@app.post("/tasks/{task_id}/retry", summary="重试失败的导入任务")
async def retry_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: RequireAdmin,
):
    context = get_import_task_document(task_id)
    if context is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    task_record, task_document = context
    if get_accessible_knowledge_base(
        task_document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    current = get_task_status(task_id) or (task_record.status if task_record else None)
    if not current or task_record is None:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务不存在", status_code=404)
    if current not in {TASK_STATUS_FAILED}:
        raise AppError(ErrorCode.VALIDATION_ERROR, "只有失败任务可以重新执行", status_code=409)
    local_dir = get_task_result(task_id, "local_dir", "") or task_record.local_dir or ""
    local_file_path = (
        get_task_result(task_id, "local_file_path", "") or task_record.local_file_path or ""
    )
    output_root = (PROJECT_ROOT / "output").resolve()
    resolved_file = Path(local_file_path).resolve() if local_file_path else None
    if resolved_file is None or not resolved_file.is_file():
        document = get_document(task_record.document_id)
        if document is not None:
            matches = list(output_root.glob(f"*/{task_id}/{document.stored_filename}"))
            if matches:
                resolved_file = matches[0].resolve()
                local_file_path = str(resolved_file)
                local_dir = str(resolved_file.parent)
            elif settings.minio_enabled and document.object_storage_path:
                client = get_minio_client()
                if client is not None:
                    retry_dir = output_root / datetime.now().strftime("%Y%m%d") / task_id
                    retry_dir.mkdir(parents=True, exist_ok=True)
                    resolved_file = (retry_dir / document.stored_filename).resolve()
                    client.fget_object(
                        settings.minio_bucket_name,
                        document.object_storage_path,
                        str(resolved_file),
                    )
                    local_file_path = str(resolved_file)
                    local_dir = str(retry_dir)
    if (
        resolved_file is None
        or not resolved_file.is_file()
        or output_root not in resolved_file.parents
    ):
        raise AppError(ErrorCode.TASK_NOT_FOUND, "任务原文件不存在，无法重试", status_code=410)
    reset_import_task_for_retry(task_id, local_dir, local_file_path)
    update_task_status(task_id, TASK_STATUS_PENDING)
    set_task_result(task_id, "local_dir", local_dir)
    set_task_result(task_id, "local_file_path", local_file_path)
    if settings.task_queue_enabled:
        from app.worker.tasks import import_document_task

        import_document_task.apply_async(
            args=[task_id, local_dir, local_file_path, principal.tenant_id, principal.user_id],
            task_id=task_id,
        )
    else:
        background_tasks.add_task(
            run_import_graph,
            task_id,
            local_dir,
            local_file_path,
            principal.tenant_id,
            principal.user_id,
        )
    _audit_request(request, principal, "document.import_retry_requested", "import_task", task_id)
    return {"task_id": task_id, "status": TASK_STATUS_PENDING, "retried": True}


@app.post("/documents/{document_id}/rebuild", summary="重建文档向量")
async def rebuild_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: RequireAdmin,
):
    document = get_document(document_id)
    if document is None or document.deleted_at is not None or get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
        write=True,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", status_code=404)
    previous_task = get_latest_import_task_for_document(document_id)
    source = (
        Path(previous_task.local_file_path).resolve()
        if previous_task and previous_task.local_file_path
        else None
    )
    output_root = (PROJECT_ROOT / "output").resolve()
    if (
        source is None
        or not source.is_file()
        or output_root not in source.parents
    ):
        source = None

    task_id = str(uuid.uuid4())
    task_dir = output_root / datetime.now().strftime("%Y%m%d") / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    local_file_path = (task_dir / document.stored_filename).resolve()
    try:
        if source is not None:
            shutil.copy2(source, local_file_path)
        elif settings.minio_enabled and document.object_storage_path:
            client = get_minio_client()
            if client is None:
                raise RuntimeError("MinIO unavailable")
            client.fget_object(
                settings.minio_bucket_name,
                document.object_storage_path,
                str(local_file_path),
            )
        else:
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "原始文件不存在，无法重建向量",
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
        document.current_version,
        local_dir=str(task_dir),
        local_file_path=str(local_file_path),
    )
    update_task_status(task_id, TASK_STATUS_PENDING)
    set_task_result(task_id, "local_dir", str(task_dir))
    set_task_result(task_id, "local_file_path", str(local_file_path))
    if settings.task_queue_enabled:
        from app.worker.tasks import import_document_task

        import_document_task.apply_async(
            args=[
                task_id,
                str(task_dir),
                str(local_file_path),
                principal.tenant_id,
                principal.user_id,
            ],
            task_id=task_id,
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
    _audit_request(
        request,
        principal,
        "document.rebuild_requested",
        "document",
        document.id,
        {"task_id": task_id, "document_version": document.current_version},
    )
    return {
        "task_id": task_id,
        "document_id": document.id,
        "document_version": document.current_version,
        "status": TASK_STATUS_PENDING,
    }


if __name__ == "__main__":
    uvicorn.run(app=app, host=settings.api_host, port=settings.import_service_port)
