"""Request tracing, basic local rate limiting and Prometheus metrics."""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Awaitable, cast

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.clients.redis_utils import get_async_redis_client
from app.core.errors import ErrorCode, install_exception_handlers
from app.core.logger import logger
from app.core.settings import settings

REQUEST_COUNT = Counter(
    "kb_http_requests_total",
    "HTTP requests handled by the knowledge-base services",
    ("service", "method", "path", "status"),
)
REQUEST_LATENCY = Histogram(
    "kb_http_request_duration_seconds",
    "HTTP request latency",
    ("service", "method", "path"),
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUEST_COUNT.labels(
            self.service_name, request.method, path, str(response.status_code)
        ).inc()
        REQUEST_LATENCY.labels(self.service_name, request.method, path).observe(elapsed)
        logger.info(
            "[{}] {} {} -> {} ({:.1f} ms)",
            request_id,
            request.method,
            path,
            response.status_code,
            elapsed * 1000,
        )
        return response


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed production limiter with an explicit development fallback."""

    _REDIS_SCRIPT = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return current
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled or request.url.path.startswith(("/health", "/metrics")):
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        limited = False
        if settings.redis_enabled:
            window = int(time.time()) // settings.rate_limit_window_seconds
            key = f"kb:rate:{client}:{window}"
            try:
                count = await cast(
                    Awaitable[Any],
                    get_async_redis_client().eval(
                        self._REDIS_SCRIPT,
                        1,
                        key,
                        settings.rate_limit_window_seconds + 1,
                    ),
                )
                limited = int(count) > settings.rate_limit_requests
            except Exception as exc:
                logger.warning("Redis 限流检查失败：{}", exc.__class__.__name__)
                if settings.is_production:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "code": ErrorCode.INTERNAL_ERROR,
                            "message": "限流服务暂不可用",
                            "request_id": getattr(request.state, "request_id", ""),
                        },
                    )
                limited = self._memory_limited(client)
        else:
            limited = self._memory_limited(client)
        if limited:
            return JSONResponse(
                status_code=429,
                content={
                    "code": ErrorCode.RATE_LIMITED,
                    "message": "请求过于频繁，请稍后重试",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        return await call_next(request)

    def _memory_limited(self, client: str) -> bool:
        now = time.monotonic()
        threshold = now - settings.rate_limit_window_seconds
        with self._lock:
            timestamps = self._requests[client]
            while timestamps and timestamps[0] <= threshold:
                timestamps.popleft()
            if len(timestamps) >= settings.rate_limit_requests:
                return True
            timestamps.append(now)
            if len(self._requests) > 10_000:
                empty_keys = [key for key, value in self._requests.items() if not value]
                for key in empty_keys:
                    self._requests.pop(key, None)
        return False


def install_common_api_features(app: FastAPI, service_name: str) -> None:
    install_exception_handlers(app)
    app.add_middleware(InMemoryRateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware, service_name=service_name)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
