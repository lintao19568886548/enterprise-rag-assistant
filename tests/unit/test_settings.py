import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def _production_settings(**overrides):
    values = {
        "app_env": "production",
        "auth_enabled": True,
        "oidc_enabled": True,
        "oidc_issuer_url": "https://id.example.com/realms/enterprise",
        "oidc_client_id": "enterprise-rag-assistant",
        "oidc_audience": "enterprise-rag-api",
        "oidc_allowed_algorithms": "RS256",
        "redis_enabled": True,
        "task_backend": "redis",
        "task_queue_enabled": True,
        "database_enabled": True,
        "database_url": "postgresql+psycopg://app:test@db/app",
        "langgraph_checkpointer": "postgres",
        "langgraph_database_url": "postgresql://app:test@db/app",
        "langgraph_aes_key": "k" * 32,
        "knowledge_base_filter_enabled": True,
        "llm_allowed_models": "qwen-plus",
        "minio_enabled": True,
        "minio_access_key": "minio-user",
        "minio_secret_key": "minio-secret",
        "minio_public_read": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_development_defaults_are_safe_and_explicit():
    config = Settings(_env_file=None, app_env="development")
    assert config.api_host == "127.0.0.1"
    assert "*" not in config.cors_origins
    assert config.task_backend == "memory"
    assert config.auth_enabled is False
    assert config.oidc_enabled is False
    assert config.knowledge_base_filter_enabled is True


def test_legacy_environment_aliases_remain_supported():
    config = Settings(
        _env_file=None,
        milvus_uri="http://127.0.0.1:19530",
        llm_model="test-model",
        rerank_model="test-reranker",
    )
    assert config.milvus_uri.endswith(":19530")
    assert config.llm_model == "test-model"
    assert config.rerank_model == "test-reranker"


def test_production_rejects_insecure_defaults():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", auth_enabled=False, task_backend="memory")


def test_production_accepts_explicit_enterprise_dependencies():
    config = _production_settings()
    assert config.is_production is True


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ({"database_url": "sqlite:///data/app.db"}, "SQLite is forbidden"),
        ({"oidc_enabled": False}, "OIDC_ENABLED must be true"),
        ({"oidc_audience": None}, "OIDC_ISSUER_URL, OIDC_CLIENT_ID"),
        ({"oidc_allowed_algorithms": "HS256"}, "secure asymmetric algorithms"),
        ({"minio_enabled": False}, "MINIO_ENABLED must be true"),
    ],
)
def test_production_rejects_unsafe_enterprise_overrides(override, expected_message):
    with pytest.raises(ValidationError, match=expected_message):
        _production_settings(**override)


def test_redis_backend_requires_redis_to_be_enabled():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, task_backend="redis", redis_enabled=False)


def test_secret_is_redacted_from_repr():
    config = Settings(_env_file=None, openai_api_key="sk-test-secret-value")
    assert "sk-test-secret-value" not in repr(config)
