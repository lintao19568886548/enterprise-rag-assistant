"""Celery application for durable import jobs."""

from celery import Celery

from app.core.settings import settings


celery_app = Celery(
    "knowledge_base",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.task_ttl_seconds,
    broker_connection_retry_on_startup=True,
    task_routes={
        "knowledge_base.import_document": {"queue": "import"},
        "knowledge_base.cleanup_document": {"queue": "cleanup"},
    },
)
