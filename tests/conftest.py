import os

import pytest
from pydantic import SecretStr

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FILE_ENABLE", "False")
os.environ.setdefault("LOG_CONSOLE_LEVEL", "WARNING")
os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")
os.environ.setdefault("DATABASE_ENABLED", "True")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MILVUS_REQUIRED", "False")

from app.core.settings import settings  # noqa: E402


@pytest.fixture(autouse=True)
def configure_test_service_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give service lifespans inert provider values without polluting Settings tests."""
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-test-not-a-real-key"))
    monkeypatch.setattr(settings, "openai_base_url", "https://example.invalid/v1")
    monkeypatch.setattr(settings, "mineru_api_token", SecretStr("test-not-a-real-token"))
