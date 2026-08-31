"""Celery task definitions."""

from __future__ import annotations

from app.core.logger import logger
from app.core.settings import settings
from app.core.tenant_context import identity_context
from app.db.repositories import DEFAULT_TENANT_ID, DEFAULT_USER_ID
from app.db.repositories import increment_import_retry
from app.import_process.services.import_runner import is_retryable_import_exception, run_import_graph
from app.utils.task_utils import TASK_STATUS_PENDING, update_task_status
from app.worker.celery_app import celery_app


@celery_app.task(bind=True, name="knowledge_base.import_document")
def import_document_task(
    self,
    task_id: str,
    local_dir: str,
    local_file_path: str,
    tenant_id: str = DEFAULT_TENANT_ID,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    try:
        run_import_graph(task_id, local_dir, local_file_path, tenant_id, user_id)
    except Exception as exc:
        if is_retryable_import_exception(exc) and self.request.retries < settings.celery_task_max_retries:
            with identity_context(tenant_id=tenant_id, user_id=user_id):
                increment_import_retry(task_id)
                update_task_status(task_id, TASK_STATUS_PENDING)
            countdown = min(60, 2 ** (self.request.retries + 1))
            logger.warning(
                "[{}] 可重试导入错误，{} 秒后执行第 {} 次重试",
                task_id,
                countdown,
                self.request.retries + 1,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise
