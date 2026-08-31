# app/core/logger.py

"""
项目日志工具类
基于loguru实现，支持.env配置控制台/文件双输出，自动生成logs/app_年月日.log
特性：
1. 配置驱动：通过.env开关输出、修改日志级别
2. 自动路径：文件日志默认输出到 项目根/logs/app_YYYYMMDD.log
3. 自动清理：按配置保留日志，自动删除过期文件
4. 中文友好：utf-8编码，彻底解决中文乱码
5. 异步安全：开启异步入队，支持多线程/异步场景，避免日志错乱
6. 开箱即用：项目所有模块直接导入logger即可使用
7. 位置终极精准：穿透loguru内部+工具类自身，完美显示业务模块实际调用位置
"""
import inspect
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from loguru import logger as loguru_logger

from app.core.settings import settings

LOG_CONSOLE_ENABLE = settings.log_console_enable
LOG_CONSOLE_LEVEL = settings.log_console_level.upper()
LOG_FILE_ENABLE = settings.log_file_enable
LOG_FILE_LEVEL = settings.log_file_level.upper()
LOG_FILE_RETENTION = settings.log_file_retention

# -------------------------- 第三步：定义日志路径（自动推导项目根） --------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE_NAME = "app_{time:YYYYMMDD}.log"
LOG_FILE_PATH = LOG_DIR / LOG_FILE_NAME

# -------------------------- 第四步：定义日志格式（彩色、结构化、易读） --------------------------
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name: <30}</cyan>:<cyan>{line: <4}</cyan> - "
    "<level>{message}</level>"
)


_SECRET_VALUES = tuple(
    value
    for value in (
        settings.reveal(settings.openai_api_key),
        settings.reveal(settings.mineru_api_token),
        settings.reveal(settings.minio_access_key),
        settings.reveal(settings.minio_secret_key),
        settings.reveal(settings.admin_api_keys),
        settings.reveal(settings.user_api_keys),
        settings.reveal(settings.readonly_api_keys),
        settings.reveal(settings.langgraph_aes_key),
        settings.reveal(settings.milvus_token),
        settings.redis_dsn,
        settings.database_dsn,
        settings.langgraph_database_dsn,
        settings.effective_celery_broker_url,
        settings.effective_celery_result_backend,
    )
    if value
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._~+/=-]{12,}|eyj[a-z0-9._~-]{20,})"
)
_NAMED_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|x-api-key|api[_-]?key|token|password|secret|"
    r"database[_-]?url|redis[_-]?url|dsn|connection[_-]?string)"
    r"(\s*[:=]\s*)([^\s,;}&]+)"
)
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|token|password|secret|database[_-]?url|"
    r"redis[_-]?url|dsn|connection[_-]?string)"
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)")


def redact_log_text(value: object) -> str:
    message = str(value)
    for secret in _SECRET_VALUES:
        message = message.replace(secret, "***REDACTED***")
    message = _SECRET_PATTERN.sub("***REDACTED***", message)
    message = _NAMED_SECRET_PATTERN.sub(r"\1\2***REDACTED***", message)
    return _URL_CREDENTIAL_PATTERN.sub(r"\1***REDACTED***\3", message)


def redact_log_value(value: object, *, field_name: str | None = None) -> object:
    """Recursively redact credentials in structured log extras."""
    if field_name and _SENSITIVE_FIELD_PATTERN.search(field_name):
        return "***REDACTED***"
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, Mapping):
        return {
            key: redact_log_value(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [redact_log_value(item) for item in value]
    return value


def _redact_record(record):
    """Remove configured credentials and common token forms before writing."""
    record["message"] = redact_log_text(record["message"])
    record["extra"] = redact_log_value(record["extra"])
    if settings.app_env in {"staging", "production"} and not settings.log_sensitive_content:
        record["exception"] = None
    return True

# -------------------------- 第五步：初始化日志配置（核心方法） --------------------------
def init_logger():
    """
    初始化全局日志配置
    1. 移除loguru默认控制台输出（避免重复打印）
    2. 根据.env配置开启/关闭控制台输出
    3. 根据.env配置开启/关闭文件输出（自动创建logs文件夹）
    4. 配置日志格式、级别、分割、保留策略
    :return: 配置完成的loguru logger实例
    """
    # 1. 移除loguru默认的控制台输出
    loguru_logger.remove()

    # 2. 配置控制台输出（若.env开启）
    if LOG_CONSOLE_ENABLE:
        loguru_logger.add(
            sink=sys.stdout,
            level=LOG_CONSOLE_LEVEL,
            format=LOG_FORMAT,
            colorize=True,
            enqueue=True,
            filter=_redact_record,
            serialize=settings.log_json,
        )

    # 3. 配置文件输出（若.env开启）
    if LOG_FILE_ENABLE:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        loguru_logger.add(
            sink=LOG_FILE_PATH,
            level=LOG_FILE_LEVEL,
            format=LOG_FORMAT,
            rotation="00:00",
            retention=LOG_FILE_RETENTION,
            encoding="utf-8",
            enqueue=True,
            backtrace=not settings.is_production,
            diagnose=False,
            filter=_redact_record,
            serialize=settings.log_json,
        )

    return loguru_logger

# -------------------------- 第六步：初始化并终极修正全局logger --------------------------
base_logger = init_logger()

def fix_log_position(record):
    """遍历调用栈，跳过loguru内部帧+工具类自身帧，提取业务代码实际调用位置"""
    for frame in inspect.stack():
        # 终极过滤：排除loguru内部 + 排除工具类logger.py自身，直接定位业务模块
        if ("_logger.py" in frame.filename or frame.function == "_log") or "logger.py" in frame.filename:
            continue
        # 更新日志字段为业务代码实际位置
        record.update(
            name=frame.filename.split("/")[-1].split("\\")[-1],
            function=frame.function,
            line=frame.lineno
        )
        break

# 应用终极修复，导出全局可用的logger
logger = base_logger.patch(fix_log_position)
