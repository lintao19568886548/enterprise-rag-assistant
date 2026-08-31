"""FastAPI query service for the enterprise knowledge base."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.clients.mongo_history_utils import clear_history, get_recent_messages
from app.api.admin_router import router as admin_router
from app.api.knowledge_router import router as knowledge_router
from app.core.errors import AppError, ErrorCode, classify_exception
from app.core.health import create_health_router
from app.core.logger import logger
from app.core.middleware import install_common_api_features
from app.core.security import RequireAdmin, RequireReadonly, RequireUser
from app.core.settings import settings
from app.core.tenant_context import identity_context
from app.db.repositories import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    ensure_chat_session,
    ensure_defaults,
    get_accessible_knowledge_base,
    get_chat_session,
    get_import_task_document,
)
from app.db.session import init_database
from app.query_process.agent.kb_query_workflow import get_default_query_workflow
from app.query_process.agent.state import create_default_state
from app.utils.path_util import PROJECT_ROOT
from app.utils.sse_utils import SSEEvent, create_sse_queue, push_to_session, sse_generator
from app.utils.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PROCESSING,
    get_task_result,
    set_task_result,
    update_task_status,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_for_service("query")
    init_database()
    ensure_defaults()
    get_default_query_workflow()
    logger.info("问答服务配置校验及 LangGraph 预编译完成，环境={}", settings.app_env)
    yield


app = FastAPI(
    title="Query Service",
    description="Enterprise knowledge-base retrieval and grounded answer service",
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
install_common_api_features(app, "query")
app.include_router(create_health_router("query"))
app.include_router(knowledge_router)
app.include_router(admin_router)


@app.get("/chat.html", response_class=FileResponse)
async def chat():
    chat_html_path = Path(__file__).resolve().parent.parent / "page" / "chat.html"
    if not chat_html_path.exists():
        raise HTTPException(status_code=404, detail="chat.html page not found")
    return FileResponse(chat_html_path)


def _safe_image_name(filename: str) -> str | None:
    safe_name = Path(filename).name
    allowed_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
    if safe_name != filename or Path(safe_name).suffix.lower() not in allowed_suffixes:
        return None
    return safe_name


def _principal_can_read_task(task_id: str, principal) -> bool:
    context = get_import_task_document(task_id)
    if context is None:
        return False
    _task, document = context
    return get_accessible_knowledge_base(
        document.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is not None


@app.get("/images/{task_id}/{filename}", response_class=FileResponse)
async def local_output_image_for_task(
    task_id: str,
    filename: str,
    principal: RequireReadonly,
):
    safe_name = _safe_image_name(filename)
    if safe_name is None or not _principal_can_read_task(task_id, principal):
        raise HTTPException(status_code=404, detail="image not found")
    output_root = PROJECT_ROOT / "output"
    task_roots = [path for path in output_root.glob(f"*/{task_id}") if path.is_dir()]
    matches = [
        path
        for task_root in task_roots
        for path in task_root.rglob(safe_name)
        if path.is_file()
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(max(matches, key=lambda path: path.stat().st_mtime))


@app.get("/images/{filename}", response_class=FileResponse)
async def local_output_image(filename: str, principal: RequireReadonly):
    """Authenticated compatibility route for image URLs imported before task scoping."""
    safe_name = _safe_image_name(filename)
    if safe_name is None:
        raise HTTPException(status_code=404, detail="image not found")
    output_root = (PROJECT_ROOT / "output").resolve()
    matches = sorted(
        (path for path in output_root.rglob(safe_name) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for match in matches:
        for parent in match.parents:
            if parent == output_root:
                break
            if _principal_can_read_task(parent.name, principal):
                return FileResponse(match)
    raise HTTPException(status_code=404, detail="image not found")


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000, description="查询内容")
    session_id: str | None = Field(default=None, max_length=128, description="会话 ID")
    knowledge_base_id: str = Field(
        default=DEFAULT_KNOWLEDGE_BASE_ID,
        min_length=1,
        max_length=128,
        description="知识库 ID",
    )
    is_stream: bool = Field(default=False, description="是否流式返回")

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query cannot be blank")
        return value


@app.post("/query")
async def query(
    background_tasks: BackgroundTasks,
    principal: RequireUser,
    request: QueryRequest,
):
    session_id = request.session_id or str(uuid.uuid4())
    if get_accessible_knowledge_base(
        request.knowledge_base_id,
        principal.tenant_id,
        principal.user_id,
        is_admin=principal.is_admin,
    ) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识库不存在", status_code=404)
    try:
        ensure_chat_session(
            session_id,
            request.knowledge_base_id,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
    except PermissionError as exc:
        raise AppError(
            ErrorCode.PERMISSION_DENIED,
            "会话不属于当前用户或知识库",
            status_code=403,
        ) from exc
    if request.is_stream:
        create_sse_queue(session_id)
    update_task_status(session_id, TASK_STATUS_PROCESSING, request.is_stream)
    logger.info(
        "[{}] 开始问答，stream={}，query_length={}",
        session_id,
        request.is_stream,
        len(request.query),
    )

    if request.is_stream:
        background_tasks.add_task(
            run_query_graph,
            session_id,
            request.query,
            request.knowledge_base_id,
            True,
            principal.user_id,
            principal.tenant_id,
        )
        return {
            "message": "结果正在处理中",
            "session_id": session_id,
            "knowledge_base_id": request.knowledge_base_id,
        }

    await run_in_threadpool(
        run_query_graph,
        session_id,
        request.query,
        request.knowledge_base_id,
        False,
        principal.user_id,
        principal.tenant_id,
    )
    task_error = get_task_result(session_id, "error", "")
    if task_error:
        try:
            error_code = ErrorCode(task_error)
        except ValueError:
            error_code = ErrorCode.INTERNAL_ERROR
        status_code = 503 if error_code != ErrorCode.INTERNAL_ERROR else 500
        raise AppError(error_code, "问答流程执行失败", status_code=status_code)
    return {
        "message": "处理完成",
        "session_id": session_id,
        "knowledge_base_id": request.knowledge_base_id,
        "answer": get_task_result(session_id, "answer", ""),
        "citations": get_task_result(session_id, "citations", []),
        "image_urls": get_task_result(session_id, "image_urls", []),
        "confidence": get_task_result(session_id, "confidence", 0.0),
        "has_sufficient_evidence": get_task_result(
            session_id,
            "has_sufficient_evidence",
            False,
        ),
        "model": get_task_result(session_id, "model", ""),
        "latency_ms": get_task_result(session_id, "latency_ms", 0),
        "done_list": [],
    }


def run_query_graph(
    session_id: str,
    user_query: str,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    is_stream: bool = True,
    user_id: str = DEFAULT_USER_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    with identity_context(tenant_id=tenant_id, user_id=user_id):
        with logger.contextualize(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            knowledge_base_id=knowledge_base_id,
        ):
            _run_query_graph_in_context(
                session_id,
                user_query,
                knowledge_base_id,
                is_stream,
                user_id,
                tenant_id,
            )


def _run_query_graph_in_context(
    session_id: str,
    user_query: str,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    is_stream: bool = True,
    user_id: str = DEFAULT_USER_ID,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    state = create_default_state(
        original_query=user_query,
        session_id=session_id,
        knowledge_base_id=knowledge_base_id,
        is_stream=is_stream,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    try:
        get_default_query_workflow().run(state)
        update_task_status(session_id, TASK_STATUS_COMPLETED, is_stream)
    except Exception as exc:
        error_code = classify_exception(exc)
        logger.opt(exception=True).error("[{}] 问答工作流失败：{}", session_id, exc)
        set_task_result(session_id, "error", str(error_code))
        update_task_status(session_id, TASK_STATUS_FAILED, is_stream)
        if is_stream:
            push_to_session(
                session_id,
                SSEEvent.ERROR,
                {"code": error_code, "error": "问答流程执行失败"},
            )


@app.get("/stream/{session_id}")
async def stream(session_id: str, request: Request, principal: RequireUser):
    chat_session = get_chat_session(session_id)
    if (
        chat_session is None
        or chat_session.tenant_id != principal.tenant_id
        or chat_session.user_id != principal.user_id
    ):
        raise AppError(ErrorCode.TASK_NOT_FOUND, "会话不存在", status_code=404)
    return StreamingResponse(
        sse_generator(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health", include_in_schema=False)
async def legacy_health():
    return {"ok": True, "deprecated": "use /health/live and /health/ready"}


@app.get("/history/{session_id}")
async def history(
    session_id: str,
    principal: RequireReadonly,
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        chat_session = get_chat_session(session_id)
        if (
            chat_session is None
            or chat_session.tenant_id != principal.tenant_id
            or chat_session.user_id != principal.user_id
        ):
            raise AppError(ErrorCode.TASK_NOT_FOUND, "会话不存在", status_code=404)
        records = get_recent_messages(
            session_id,
            limit=limit,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        return {
            "session_id": session_id,
            "items": [
                {
                    "_id": str(record.get("_id")) if record.get("_id") is not None else "",
                    "session_id": record.get("session_id", ""),
                    "role": record.get("role", ""),
                    "text": record.get("text", ""),
                    "rewritten_query": record.get("rewritten_query", ""),
                    "item_names": record.get("item_names", []),
                    "image_urls": record.get("image_urls", []),
                    "citations": record.get("citations", []),
                    "model": record.get("model"),
                    "latency_ms": record.get("latency_ms"),
                    "ts": record.get("ts"),
                }
                for record in records
            ],
        }
    except AppError:
        raise
    except Exception as exc:
        logger.opt(exception=True).error("[{}] 历史记录读取失败：{}", session_id, exc)
        raise AppError(ErrorCode.INTERNAL_ERROR, "历史记录读取失败", status_code=503) from exc


@app.delete("/history/{session_id}")
async def clear_chat_history(session_id: str, principal: RequireAdmin):
    try:
        count = clear_history(
            session_id,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
    except PermissionError as exc:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "会话不存在", status_code=404) from exc
    return {"message": "History cleared", "deleted_count": count}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.api_host, port=settings.query_service_port)
