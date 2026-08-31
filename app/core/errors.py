"""Stable application error contract shared by all APIs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logger import logger


class ErrorCode(StrEnum):
    FILE_TYPE_NOT_ALLOWED = "FILE_TYPE_NOT_ALLOWED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_SIGNATURE_INVALID = "FILE_SIGNATURE_INVALID"
    FILE_NAME_INVALID = "FILE_NAME_INVALID"
    TOO_MANY_FILES = "TOO_MANY_FILES"
    PARSER_FAILED = "PARSER_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    MILVUS_UNAVAILABLE = "MILVUS_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    RERANK_FAILED = "RERANK_FAILED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def classify_exception(exc: Exception) -> ErrorCode:
    """Map provider/library exceptions to a stable public error code."""
    if isinstance(exc, AppError):
        return exc.code
    text = f"{exc.__class__.__name__} {exc}".lower()
    if any(marker in text for marker in ("401", "403", "unauthorized", "invalid api key", "authentication")):
        return ErrorCode.MODEL_AUTH_FAILED
    if any(marker in text for marker in ("timeout", "timed out", "rate limit", "too many requests")):
        return ErrorCode.MODEL_TIMEOUT
    if any(marker in text for marker in ("milvus", "grpc", "goaway", "channel")):
        return ErrorCode.MILVUS_UNAVAILABLE
    if any(marker in text for marker in ("embedding", "bge", "vector encode")):
        return ErrorCode.EMBEDDING_FAILED
    if any(marker in text for marker in ("mineru", "pdf", "parse", "markdown")):
        return ErrorCode.PARSER_FAILED
    if "rerank" in text:
        return ErrorCode.RERANK_FAILED
    return ErrorCode.INTERNAL_ERROR


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": "请求参数校验失败",
                "details": exc.errors(),
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.opt(exception=True).error("[{}] 未处理的接口异常：{}", request_id, exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "服务器内部错误，请使用 request_id 查询日志",
                "request_id": request_id,
            },
        )
