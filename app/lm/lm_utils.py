"""Unified model gateway for OpenAI-compatible chat models."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

from langchain_openai import ChatOpenAI
from prometheus_client import Counter, Histogram
from pydantic import SecretStr

from app.core.logger import logger
from app.core.settings import settings


MODEL_CALLS = Counter(
    "kb_model_calls_total",
    "Model gateway calls",
    ("model", "mode", "status"),
)
MODEL_LATENCY = Histogram(
    "kb_model_call_duration_seconds",
    "Model gateway latency",
    ("model", "mode"),
)


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


_client_cache: dict[tuple[str, bool], ChatOpenAI] = {}
_circuits: dict[str, _CircuitState] = {}
_lock = threading.RLock()


def _get_raw_client(model: str, json_mode: bool) -> ChatOpenAI:
    cache_key = (model, json_mode)
    with _lock:
        cached = _client_cache.get(cache_key)
        if cached is not None:
            return cached

    api_key = settings.reveal(settings.openai_api_key)
    if not api_key or not settings.openai_base_url:
        raise ValueError("OPENAI_API_KEY and OPENAI_BASE_URL are required")

    model_kwargs: dict[str, Any] = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}
    extra_body = (
        {"enable_thinking": False}
        if "dashscope" in settings.openai_base_url.lower()
        else None
    )
    client = ChatOpenAI(
        model=model,
        temperature=settings.llm_temperature,
        api_key=SecretStr(api_key),
        base_url=settings.openai_base_url,
        timeout=settings.model_request_timeout_seconds,
        max_retries=settings.model_max_retries,
        extra_body=extra_body,
        model_kwargs=model_kwargs,
    )
    with _lock:
        return _client_cache.setdefault(cache_key, client)


def _circuit_allows(model: str) -> bool:
    with _lock:
        state = _circuits.setdefault(model, _CircuitState())
        if state.opened_at is None:
            return True
        if time.monotonic() - state.opened_at >= settings.model_circuit_breaker_reset_seconds:
            state.failures = 0
            state.opened_at = None
            return True
        return False


def _record_success(model: str) -> None:
    with _lock:
        _circuits[model] = _CircuitState()


def _record_failure(model: str) -> None:
    with _lock:
        state = _circuits.setdefault(model, _CircuitState())
        state.failures += 1
        if state.failures >= settings.model_circuit_breaker_failures:
            state.opened_at = time.monotonic()
            logger.warning("模型熔断器已打开，model={}，failures={}", model, state.failures)


class ModelGateway:
    """Small compatibility wrapper exposing the invoke/stream methods used by the graphs."""

    def __init__(self, primary_model: str, json_mode: bool = False) -> None:
        if primary_model not in settings.allowed_models:
            raise ValueError(f"model is not in LLM_ALLOWED_MODELS: {primary_model}")
        self.primary_model = primary_model
        self.json_mode = json_mode
        fallbacks = settings.fallback_models if primary_model == settings.llm_model else []
        self.models = list(dict.fromkeys([primary_model, *fallbacks]))
        unknown = set(self.models) - settings.allowed_models
        if unknown:
            raise ValueError(f"fallback models are not in LLM_ALLOWED_MODELS: {sorted(unknown)}")

    def invoke(self, input_data: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for model in self.models:
            if not _circuit_allows(model):
                logger.warning("跳过处于熔断状态的模型，model={}", model)
                continue
            started = time.perf_counter()
            try:
                response = _get_raw_client(model, self.json_mode).invoke(input_data, **kwargs)
                _record_success(model)
                MODEL_CALLS.labels(model, "invoke", "success").inc()
                return response
            except Exception as exc:
                last_error = exc
                _record_failure(model)
                MODEL_CALLS.labels(model, "invoke", "error").inc()
                logger.warning("模型调用失败，model={}，error={}", model, exc.__class__.__name__)
            finally:
                MODEL_LATENCY.labels(model, "invoke").observe(time.perf_counter() - started)
        raise RuntimeError("all configured models are unavailable") from last_error

    def stream(self, input_data: Any, **kwargs: Any) -> Iterator[Any]:
        last_error: Exception | None = None
        for model in self.models:
            if not _circuit_allows(model):
                continue
            started = time.perf_counter()
            emitted = False
            try:
                for chunk in _get_raw_client(model, self.json_mode).stream(input_data, **kwargs):
                    emitted = True
                    yield chunk
                _record_success(model)
                MODEL_CALLS.labels(model, "stream", "success").inc()
                return
            except Exception as exc:
                last_error = exc
                _record_failure(model)
                MODEL_CALLS.labels(model, "stream", "error").inc()
                logger.warning("模型流式调用失败，model={}，error={}", model, exc.__class__.__name__)
                if emitted:
                    raise RuntimeError("stream interrupted after partial output") from exc
            finally:
                MODEL_LATENCY.labels(model, "stream").observe(time.perf_counter() - started)
        raise RuntimeError("all configured models are unavailable") from last_error


def get_llm_client(model: str | None = None, json_mode: bool = False) -> ModelGateway:
    """Return a validated gateway without exposing provider secrets."""
    return ModelGateway(model or settings.llm_model, json_mode=json_mode)
