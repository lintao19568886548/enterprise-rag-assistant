"""Liveness/readiness checks without leaking credentials."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Response, status

from app.core.settings import settings
from app.db.session import database_ping

async def _database_component():
    if not settings.database_enabled:
        return "database", {"status": "disabled", "required": False}
    started = time.perf_counter()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(database_ping),
            timeout=settings.health_dependency_timeout_seconds,
        )
        return "database", {
            "status": "ok",
            "required": True,
            "latency": f"{(time.perf_counter() - started) * 1000:.1f} ms",
        }
    except Exception as exc:
        return "database", {
            "status": "unavailable",
            "required": True,
            "error": exc.__class__.__name__,
        }


async def _redis_component():
    if not settings.redis_enabled:
        return "redis", {"status": "disabled", "required": False}
    started = time.perf_counter()
    try:
        from app.clients.redis_utils import get_redis_client

        await asyncio.wait_for(
            asyncio.to_thread(get_redis_client().ping),
            timeout=settings.health_dependency_timeout_seconds,
        )
        return "redis", {
            "status": "ok",
            "required": True,
            "latency": f"{(time.perf_counter() - started) * 1000:.1f} ms",
        }
    except Exception as exc:
        return "redis", {"status": "unavailable", "required": True, "error": exc.__class__.__name__}


async def _milvus_component():
    if not settings.milvus_required:
        return "milvus", {"status": "disabled", "required": False}
    started = time.perf_counter()
    try:
        from app.clients.milvus_utils import get_milvus_client

        client = await asyncio.wait_for(
            asyncio.to_thread(get_milvus_client),
            timeout=settings.health_dependency_timeout_seconds,
        )
        await asyncio.wait_for(
            asyncio.to_thread(client.list_collections),
            timeout=settings.health_dependency_timeout_seconds,
        )
        return "milvus", {
            "status": "ok",
            "required": True,
            "latency": f"{(time.perf_counter() - started) * 1000:.1f} ms",
        }
    except Exception as exc:
        return "milvus", {"status": "unavailable", "required": True, "error": exc.__class__.__name__}


async def _minio_component():
    if not settings.minio_enabled:
        return "minio", {"status": "disabled", "required": False}
    started = time.perf_counter()
    try:
        from app.clients.minio_utils import get_minio_client

        client = await asyncio.wait_for(
            asyncio.to_thread(get_minio_client),
            timeout=settings.health_dependency_timeout_seconds,
        )
        if client is None:
            raise RuntimeError("MinIO unavailable")
        await asyncio.wait_for(
            asyncio.to_thread(client.bucket_exists, settings.minio_bucket_name),
            timeout=settings.health_dependency_timeout_seconds,
        )
        return "minio", {
            "status": "ok",
            "required": True,
            "latency": f"{(time.perf_counter() - started) * 1000:.1f} ms",
        }
    except Exception as exc:
        return "minio", {"status": "unavailable", "required": True, "error": exc.__class__.__name__}
def create_health_router(service: Literal["import", "query"]) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    async def live():
        return {"status": "ok", "service": service, "environment": settings.app_env}

    @router.get("/health/ready")
    async def ready(response: Response):
        checks = [
            _milvus_component(),
            _redis_component(),
            _database_component(),
            _minio_component(),
        ]
        results = dict(await asyncio.gather(*checks))
        config_ok = bool(settings.openai_api_key and settings.openai_base_url)
        if service == "import":
            config_ok = config_ok and bool(settings.mineru_api_token)
        results["model_configuration"] = {
            "status": "ok" if config_ok else "invalid",
            "required": True,
        }
        ready_ok = all(
            value["status"] in {"ok", "disabled", "configured"}
            for value in results.values()
            if value.get("required")
        )
        if not ready_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready_ok else "not_ready", "components": results}

    return router
