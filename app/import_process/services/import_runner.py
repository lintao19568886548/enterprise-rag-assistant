"""Queue-independent import workflow runner."""

from __future__ import annotations

from app.core.logger import logger
from app.core.metrics import IMPORT_TASKS
from app.core.errors import classify_exception
from app.core.settings import settings
from app.db.repositories import (
    get_document,
    get_import_task,
    replace_chunk_metadata,
    update_import_task,
)
from app.import_process.agent.kb_import_workflow import get_default_import_workflow
from app.import_process.agent.state import get_default_state
from app.utils.task_utils import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PROCESSING,
    add_done_task,
    get_task_status,
    set_task_result,
    update_task_status,
)


def is_retryable_import_exception(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    retryable_markers = (
        "timeout",
        "connection",
        "temporarily unavailable",
        "too many requests",
        "rate limit",
        "unavailable",
        "goaway",
    )
    return any(marker in name or marker in message for marker in retryable_markers)


def _safe_error_summary(exc: Exception) -> str:
    message = str(exc)
    for secret in (
        settings.reveal(settings.openai_api_key),
        settings.reveal(settings.mineru_api_token),
        settings.reveal(settings.minio_access_key),
        settings.reveal(settings.minio_secret_key),
    ):
        if secret:
            message = message.replace(secret, "***REDACTED***")
    return message[:1024]


def run_import_graph(task_id: str, local_dir: str, local_file_path: str) -> None:
    try:
        if get_task_status(task_id) == TASK_STATUS_CANCELLED:
            return
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        update_import_task(task_id, TASK_STATUS_PROCESSING, current_node="node_entry", progress=5)
        logger.info("[{}] 开始执行导入工作流", task_id)
        initial_state = get_default_state()
        initial_state["task_id"] = task_id
        initial_state["local_dir"] = local_dir
        initial_state["local_file_path"] = local_file_path
        task_record = get_import_task(task_id)
        document = get_document(task_record.document_id) if task_record else None
        if task_record and document:
            initial_state["tenant_id"] = document.tenant_id
            initial_state["knowledge_base_id"] = document.knowledge_base_id
            initial_state["document_id"] = document.id
            initial_state["document_version"] = task_record.document_version
            initial_state["original_filename"] = document.original_filename
            initial_state["permission_scope"] = "private"
        progress_by_node = {
            "node_entry": 10,
            "node_pdf_to_md": 30,
            "node_md_img": 45,
            "node_document_split": 60,
            "node_item_name_recognition": 70,
            "node_bge_embedding": 85,
            "node_import_milvus": 98,
        }
        final_state = initial_state
        for event in get_default_import_workflow().run(initial_state, stream=True):
            for node_name, node_state in event.items():
                if isinstance(node_state, dict):
                    final_state = node_state
                add_done_task(task_id, node_name)
                update_import_task(
                    task_id,
                    TASK_STATUS_PROCESSING,
                    current_node=node_name,
                    progress=progress_by_node.get(node_name, 50),
                )
                logger.info("[{}] 导入节点执行完成：{}", task_id, node_name)
            if get_task_status(task_id) == TASK_STATUS_CANCELLED:
                update_import_task(task_id, TASK_STATUS_CANCELLED, progress=0)
                logger.warning("[{}] 导入任务已取消，在节点边界安全停止", task_id)
                IMPORT_TASKS.labels("cancelled", "false").inc()
                return
        if task_record and document:
            replace_chunk_metadata(
                document.id,
                task_record.document_version,
                document.knowledge_base_id,
                list(final_state.get("chunks") or []),
            )
        update_task_status(task_id, TASK_STATUS_COMPLETED)
        update_import_task(task_id, TASK_STATUS_COMPLETED, current_node="completed", progress=100)
        logger.info("[{}] 导入工作流执行完成", task_id)
        IMPORT_TASKS.labels("completed", "false").inc()
    except Exception as exc:
        retryable = is_retryable_import_exception(exc)
        error_code = classify_exception(exc)
        set_task_result(task_id, "error_type", exc.__class__.__name__)
        set_task_result(task_id, "retryable", retryable)
        update_task_status(task_id, TASK_STATUS_FAILED)
        update_import_task(
            task_id,
            TASK_STATUS_FAILED,
            error_code=str(error_code),
            error_summary=_safe_error_summary(exc),
        )
        logger.opt(exception=True).error("[{}] 导入工作流失败：{}", task_id, exc)
        IMPORT_TASKS.labels("failed", str(retryable).lower()).inc()
        raise
