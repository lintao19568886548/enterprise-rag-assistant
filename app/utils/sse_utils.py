"""SSE transport using an in-process queue or Redis Streams."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, AsyncGenerator

from fastapi import Request

from app.clients.redis_utils import get_async_redis_client, get_redis_client
from app.core.logger import logger
from app.core.settings import settings


class SSEEvent:
    READY = "ready"
    PROGRESS = "progress"
    DELTA = "delta"
    FINAL = "final"
    ERROR = "error"
    CLOSE = "__close__"


_session_stream: dict[str, queue.Queue] = {}
_session_lock = threading.RLock()


def _uses_redis() -> bool:
    return settings.task_backend == "redis"


def _redis_stream_key(session_id: str) -> str:
    return f"kb:sse:{session_id}"


def get_sse_queue(session_id: str) -> queue.Queue | None:
    with _session_lock:
        return _session_stream.get(session_id)


def create_sse_queue(session_id: str):
    if _uses_redis():
        client = get_redis_client()
        key = _redis_stream_key(session_id)
        with client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.xadd(key, {"event": "__init__", "data": "{}"})
            pipe.expire(key, settings.task_ttl_seconds)
            pipe.execute()
        return key
    with _session_lock:
        stream_queue: queue.Queue = queue.Queue()
        _session_stream[session_id] = stream_queue
        return stream_queue


def remove_sse_queue(session_id: str) -> None:
    if _uses_redis():
        # Keep the stream for reconnect/resume and let the configured TTL remove it.
        get_redis_client().expire(_redis_stream_key(session_id), settings.task_ttl_seconds)
        return
    with _session_lock:
        _session_stream.pop(session_id, None)


def _sse_pack(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def push_to_session(session_id: str, event: str, data: dict[str, Any]) -> None:
    if _uses_redis():
        client = get_redis_client()
        key = _redis_stream_key(session_id)
        with client.pipeline(transaction=True) as pipe:
            pipe.xadd(
                key,
                {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)},
                maxlen=1000,
                approximate=True,
            )
            pipe.expire(key, settings.task_ttl_seconds)
            pipe.execute()
        return
    stream_queue = get_sse_queue(session_id)
    if stream_queue is None:
        logger.warning("[{}] SSE 会话不存在，丢弃事件 {}", session_id, event)
        return
    stream_queue.put({"event": event, "data": data})


async def _memory_messages(session_id: str) -> AsyncGenerator[dict[str, Any], None]:
    stream_queue = get_sse_queue(session_id)
    if stream_queue is None:
        return
    while True:
        try:
            yield await asyncio.to_thread(stream_queue.get, True, 1.0)
        except queue.Empty:
            yield {"event": "__heartbeat__", "data": {}}


async def _redis_messages(session_id: str) -> AsyncGenerator[dict[str, Any], None]:
    client = get_async_redis_client()
    key = _redis_stream_key(session_id)
    last_id = "0-0"
    while True:
        messages = await client.xread({key: last_id}, count=50, block=1000)
        if not messages:
            yield {"event": "__heartbeat__", "data": {}}
            continue
        for _, entries in messages:
            for message_id, fields in entries:
                last_id = message_id
                event = fields.get("event", "message")
                if event == "__init__":
                    continue
                try:
                    data = json.loads(fields.get("data", "{}"))
                except json.JSONDecodeError:
                    data = {"message": "invalid SSE payload"}
                yield {"event": event, "data": data}


async def sse_generator(session_id: str, request: Request):
    yield _sse_pack(SSEEvent.READY, {})
    source = _redis_messages(session_id) if _uses_redis() else _memory_messages(session_id)
    try:
        async for message in source:
            if await request.is_disconnected():
                break
            event = str(message.get("event") or "message")
            if event == "__heartbeat__":
                yield ": heartbeat\n\n"
                continue
            if event == SSEEvent.CLOSE:
                break
            yield _sse_pack(event, message.get("data") or {})
            if event in {SSEEvent.FINAL, SSEEvent.ERROR}:
                break
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        return
    except Exception as exc:
        logger.opt(exception=True).error("[{}] SSE 流异常：{}", session_id, exc)
        yield _sse_pack(SSEEvent.ERROR, {"error": "SSE stream unavailable"})
    finally:
        remove_sse_queue(session_id)
