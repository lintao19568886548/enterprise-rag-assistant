"""Shared synchronous and asynchronous Redis clients."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.core.settings import settings


class RedisUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    if not settings.redis_enabled:
        raise RedisUnavailableError("Redis is disabled by REDIS_ENABLED=False")
    client = Redis.from_url(
        settings.redis_dsn,
        decode_responses=True,
        socket_connect_timeout=settings.health_dependency_timeout_seconds,
        socket_timeout=settings.health_dependency_timeout_seconds,
        health_check_interval=30,
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        raise RedisUnavailableError("Redis is configured but unavailable") from exc
    return client


@lru_cache(maxsize=1)
def get_async_redis_client() -> AsyncRedis:
    if not settings.redis_enabled:
        raise RedisUnavailableError("Redis is disabled by REDIS_ENABLED=False")
    return AsyncRedis.from_url(
        settings.redis_dsn,
        decode_responses=True,
        socket_connect_timeout=settings.health_dependency_timeout_seconds,
        socket_timeout=settings.health_dependency_timeout_seconds,
        health_check_interval=30,
    )
