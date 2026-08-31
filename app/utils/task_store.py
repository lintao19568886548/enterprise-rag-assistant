"""Persistent task-state abstraction with memory and Redis implementations."""

from __future__ import annotations

import threading
import time
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, cast

from redis import Redis

from app.clients.redis_utils import get_redis_client
from app.core.logger import logger
from app.core.settings import settings


class TaskStore(ABC):
    @abstractmethod
    def add_running(self, task_id: str, node_name: str) -> None: ...

    @abstractmethod
    def add_done(self, task_id: str, node_name: str) -> None: ...

    @abstractmethod
    def set_result(self, task_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    def get_result(self, task_id: str, key: str, default: Any = "") -> Any: ...

    @abstractmethod
    def set_status(self, task_id: str, status: str) -> None: ...

    @abstractmethod
    def get_status(self, task_id: str) -> str: ...

    @abstractmethod
    def get_done(self, task_id: str) -> list[str]: ...

    @abstractmethod
    def get_running(self, task_id: str) -> list[str]: ...

    @abstractmethod
    def clear(self, task_id: str) -> None: ...


@dataclass
class _MemoryTask:
    status: str = ""
    running: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)


class MemoryTaskStore(TaskStore):
    def __init__(self) -> None:
        self._tasks: dict[str, _MemoryTask] = {}
        self._lock = threading.RLock()

    def _cleanup_expired(self) -> None:
        threshold = time.monotonic() - settings.task_ttl_seconds
        expired = [task_id for task_id, task in self._tasks.items() if task.updated_at < threshold]
        for task_id in expired:
            self._tasks.pop(task_id, None)

    def _task(self, task_id: str) -> _MemoryTask:
        self._cleanup_expired()
        task = self._tasks.setdefault(task_id, _MemoryTask())
        task.updated_at = time.monotonic()
        return task

    def add_running(self, task_id: str, node_name: str) -> None:
        with self._lock:
            task = self._task(task_id)
            if node_name not in task.running:
                task.running.append(node_name)

    def add_done(self, task_id: str, node_name: str) -> None:
        with self._lock:
            task = self._task(task_id)
            task.running = [name for name in task.running if name != node_name]
            if node_name not in task.done:
                task.done.append(node_name)

    def set_result(self, task_id: str, key: str, value: Any) -> None:
        with self._lock:
            self._task(task_id).results[key] = value

    def get_result(self, task_id: str, key: str, default: Any = "") -> Any:
        with self._lock:
            return self._task(task_id).results.get(key, default)

    def set_status(self, task_id: str, status: str) -> None:
        with self._lock:
            self._task(task_id).status = status

    def get_status(self, task_id: str) -> str:
        with self._lock:
            return self._task(task_id).status

    def get_done(self, task_id: str) -> list[str]:
        with self._lock:
            return list(self._task(task_id).done)

    def get_running(self, task_id: str) -> list[str]:
        with self._lock:
            return list(self._task(task_id).running)

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)


class RedisTaskStore(TaskStore):
    _ADD_RUNNING_SCRIPT = """
    redis.call('LREM', KEYS[3], 0, ARGV[1])
    if not redis.call('LPOS', KEYS[2], ARGV[1]) then
        redis.call('RPUSH', KEYS[2], ARGV[1])
    end
    redis.call('HSET', KEYS[1], 'updated_at', ARGV[2])
    for i = 1, #KEYS do redis.call('EXPIRE', KEYS[i], ARGV[3]) end
    return 1
    """
    _ADD_DONE_SCRIPT = """
    redis.call('LREM', KEYS[2], 0, ARGV[1])
    if not redis.call('LPOS', KEYS[3], ARGV[1]) then
        redis.call('RPUSH', KEYS[3], ARGV[1])
    end
    redis.call('HSET', KEYS[1], 'updated_at', ARGV[2])
    for i = 1, #KEYS do redis.call('EXPIRE', KEYS[i], ARGV[3]) end
    return 1
    """

    def __init__(self, client: Redis) -> None:
        self.client = client
        self.ttl = settings.task_ttl_seconds

    @staticmethod
    def _keys(task_id: str) -> tuple[str, str, str, str]:
        prefix = f"kb:task:{task_id}"
        return prefix, f"{prefix}:running", f"{prefix}:done", f"{prefix}:results"

    def _expire(self, pipeline, keys: tuple[str, ...]) -> None:
        for key in keys:
            pipeline.expire(key, self.ttl)

    def add_running(self, task_id: str, node_name: str) -> None:
        meta, running, done, results = self._keys(task_id)
        self.client.eval(
            self._ADD_RUNNING_SCRIPT,
            4,
            meta,
            running,
            done,
            results,
            node_name,
            str(time.time()),
            str(self.ttl),
        )

    def add_done(self, task_id: str, node_name: str) -> None:
        meta, running, done, results = self._keys(task_id)
        self.client.eval(
            self._ADD_DONE_SCRIPT,
            4,
            meta,
            running,
            done,
            results,
            node_name,
            str(time.time()),
            str(self.ttl),
        )

    def set_result(self, task_id: str, key: str, value: Any) -> None:
        meta, running, done, results = self._keys(task_id)
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(results, key, json.dumps(value, ensure_ascii=False, default=str))
            pipe.hset(meta, mapping={"updated_at": str(time.time())})
            self._expire(pipe, (meta, running, done, results))
            pipe.execute()

    def get_result(self, task_id: str, key: str, default: Any = "") -> Any:
        value = cast(str | None, self.client.hget(self._keys(task_id)[3], key))
        if value is None:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def set_status(self, task_id: str, status: str) -> None:
        meta, running, done, results = self._keys(task_id)
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta, mapping={"status": status, "updated_at": str(time.time())})
            self._expire(pipe, (meta, running, done, results))
            pipe.execute()

    def get_status(self, task_id: str) -> str:
        return cast(str | None, self.client.hget(self._keys(task_id)[0], "status")) or ""

    def get_done(self, task_id: str) -> list[str]:
        return list(cast(list[str], self.client.lrange(self._keys(task_id)[2], 0, -1)))

    def get_running(self, task_id: str) -> list[str]:
        return list(cast(list[str], self.client.lrange(self._keys(task_id)[1], 0, -1)))

    def clear(self, task_id: str) -> None:
        self.client.delete(*self._keys(task_id))


@lru_cache(maxsize=1)
def get_task_store() -> TaskStore:
    if settings.task_backend == "redis":
        logger.info("任务状态后端：Redis")
        return RedisTaskStore(get_redis_client())
    if settings.is_production:
        raise RuntimeError("Production cannot use the in-memory task store")
    logger.warning("任务状态后端：内存（仅限开发/测试，服务重启后状态会丢失）")
    return MemoryTaskStore()
