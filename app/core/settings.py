"""Application settings with validation and backwards-compatible environment aliases."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WEAK_SECRET_MARKERS = (
    "change-me",
    "example",
    "minioadmin",
    "password",
    "placeholder",
    "replace",
    "secret",
    "test",
)


def _url_password(url: str) -> str | None:
    try:
        password = urlsplit(url).password
    except ValueError:
        return None
    return unquote(password) if password else None


def _validate_deployed_secret(name: str, value: str | None, *, minimum_length: int) -> None:
    if not value:
        raise ValueError(f"{name} must be configured in staging/production")
    normalized = value.casefold()
    if len(value) < minimum_length or any(marker in normalized for marker in _WEAK_SECRET_MARKERS):
        raise ValueError(f"{name} is weak or still uses a placeholder")
    if len(set(value)) < 6:
        raise ValueError(f"{name} does not have enough character diversity")


class Settings(BaseSettings):
    """Single source of truth for application configuration.

    Secrets use ``SecretStr`` so accidental logging or model dumps redact their
    values. Legacy variable names remain accepted while the project migrates to
    the normalized names documented in ``.env.example``.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Enterprise Knowledge Base"
    api_host: str = "127.0.0.1"
    import_service_port: int = Field(default=8000, ge=1, le=65535)
    query_service_port: int = Field(default=8001, ge=1, le=65535)

    log_level: str = "INFO"
    log_console_enable: bool = True
    log_console_level: str = "INFO"
    log_file_enable: bool = True
    log_file_level: str = "INFO"
    log_file_retention: str = "7 days"
    log_json: bool = False
    log_sensitive_content: bool = False

    cors_allowed_origins: str = (
        "http://127.0.0.1:8000,http://127.0.0.1:8001,"
        "http://localhost:8000,http://localhost:8001"
    )
    cors_allow_credentials: bool = False

    auth_enabled: bool = False
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_cache_seconds: int = Field(default=300, ge=30, le=86400)
    oidc_allowed_algorithms: str = "RS256"
    oidc_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    admin_api_keys: SecretStr | None = None
    user_api_keys: SecretStr | None = None
    readonly_api_keys: SecretStr | None = None
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    llm_model: str = Field(
        default="qwen-plus",
        validation_alias=AliasChoices("LLM_MODEL", "LLM_DEFAULT_MODEL"),
    )
    vl_model: str = "qwen-vl-plus"
    llm_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        validation_alias=AliasChoices("LLM_TEMPERATURE", "LLM_DEFAULT_TEMPERATURE"),
    )
    model_request_timeout_seconds: float = Field(default=60.0, gt=0)
    model_max_retries: int = Field(default=2, ge=0, le=10)
    model_circuit_breaker_failures: int = Field(default=5, ge=1, le=100)
    model_circuit_breaker_reset_seconds: int = Field(default=30, ge=1, le=3600)
    model_input_cost_per_1m_tokens: float = Field(default=0.0, ge=0.0)
    model_output_cost_per_1m_tokens: float = Field(default=0.0, ge=0.0)
    llm_allowed_models: str = ""
    llm_fallback_models: str = ""

    embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "BGE_M3"),
    )
    bge_m3_path: str | None = None
    bge_device: str = "cpu"
    bge_fp16: bool = False
    embedding_dimension: int = Field(
        default=1024,
        ge=1,
        validation_alias=AliasChoices("EMBEDDING_DIMENSION", "EMBEDDING_DIM"),
    )
    rerank_model: str = Field(
        default="gte-rerank-v2",
        validation_alias=AliasChoices("RERANK_MODEL", "TEXT_RERANK_MODEL"),
    )
    rerank_instruct: str = Field(
        default="",
        validation_alias=AliasChoices("RERANK_INSTRUCT", "TEXT_RERANK_INSTRUCT"),
    )
    rerank_enabled: bool = True
    rerank_top_n: int = Field(default=30, ge=1, le=200)
    hyde_enabled: bool = True
    retrieval_candidate_limit: int = Field(default=10, ge=1, le=200)
    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    knowledge_base_filter_enabled: bool = True
    answer_min_evidence_chunks: int = Field(default=1, ge=1, le=20)
    answer_min_relevance_score: float = Field(default=0.2, ge=0.0, le=1.0)
    citation_max_count: int = Field(default=5, ge=1, le=20)
    answer_context_max_chars: int = Field(default=12000, ge=1000, le=200000)

    redis_enabled: bool = False
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    task_backend: Literal["memory", "redis"] = "memory"
    task_ttl_seconds: int = Field(default=86400, ge=60)
    task_queue_enabled: bool = False
    celery_broker_url: SecretStr | None = None
    celery_result_backend: SecretStr | None = None
    celery_task_max_retries: int = Field(default=3, ge=0, le=20)
    cleanup_max_retries: int = Field(default=5, ge=1, le=50)
    cleanup_retry_base_seconds: int = Field(default=5, ge=1, le=3600)
    cleanup_retry_max_seconds: int = Field(default=600, ge=1, le=86400)

    database_enabled: bool = True
    database_url: SecretStr = SecretStr(
        f"sqlite:///{(PROJECT_ROOT / 'data' / 'knowledge_base.db').as_posix()}"
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)

    langgraph_checkpointer: Literal["memory", "sqlite", "postgres"] = "sqlite"
    langgraph_checkpoint_path: str = str(PROJECT_ROOT / "data" / "langgraph_checkpoints.sqlite")
    langgraph_database_url: SecretStr | None = None
    langgraph_aes_key: SecretStr | None = None

    milvus_uri: str = Field(
        default="http://127.0.0.1:19530",
        validation_alias=AliasChoices("MILVUS_URI", "MILVUS_URL"),
    )
    milvus_token: SecretStr | None = None
    milvus_required: bool = True
    milvus_collection: str = Field(
        default="chunks_collection",
        validation_alias=AliasChoices("MILVUS_COLLECTION", "CHUNKS_COLLECTION"),
    )
    entity_name_collection: str = "entity_name_collection"
    item_name_collection: str = "item_name_collection"
    milvus_metric_type: str = "COSINE"
    milvus_min_cosine_score: float = Field(default=0.6, ge=-1.0, le=1.0)
    milvus_keepalive_time_ms: int = Field(default=300000, ge=10000)
    milvus_keepalive_timeout_ms: int = Field(default=20000, ge=1000)
    milvus_keepalive_permit_without_calls: bool = False

    minio_enabled: bool = False
    minio_endpoint: str = "127.0.0.1:9000"
    minio_public_endpoint: str = "127.0.0.1:9000"
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket_name: str = "kb-import-bucket"
    minio_img_dir: str = "images"
    minio_pdf_dir: str = "pdf_files"
    minio_secure: bool = False
    minio_public_secure: bool = False
    minio_public_read: bool = False
    minio_presigned_expiry_seconds: int = Field(default=3600, ge=60, le=604800)

    mineru_base_url: str | None = None
    mineru_api_token: SecretStr | None = None
    mineru_model_source: str = "modelscope"

    web_search_enabled: bool = False
    mcp_dashscope_base_url: str | None = None

    upload_allowed_extensions: str = ".pdf,.md,.markdown"
    upload_max_file_size_mb: int = Field(default=50, ge=1, le=1024)
    upload_max_files_per_request: int = Field(default=10, ge=1, le=100)
    upload_max_filename_length: int = Field(default=180, ge=16, le=255)
    upload_chunk_size_bytes: int = Field(default=1024 * 1024, ge=64 * 1024)

    health_dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_deployed(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def oidc_algorithms(self) -> list[str]:
        return [
            value.strip().upper()
            for value in self.oidc_allowed_algorithms.split(",")
            if value.strip()
        ]

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_allowed_origins.split(",") if value.strip()]

    @property
    def allowed_upload_extensions(self) -> set[str]:
        return {
            value.strip().lower() if value.strip().startswith(".") else f".{value.strip().lower()}"
            for value in self.upload_allowed_extensions.split(",")
            if value.strip()
        }

    @property
    def upload_max_file_size_bytes(self) -> int:
        return self.upload_max_file_size_mb * 1024 * 1024

    @property
    def redis_dsn(self) -> str:
        return self.reveal(self.redis_url) or ""

    @property
    def database_dsn(self) -> str:
        return self.reveal(self.database_url) or ""

    @property
    def langgraph_database_dsn(self) -> str | None:
        return self.reveal(self.langgraph_database_url)

    @property
    def effective_celery_broker_url(self) -> str:
        return self.reveal(self.celery_broker_url) or self.redis_dsn

    @property
    def effective_celery_result_backend(self) -> str:
        return self.reveal(self.celery_result_backend) or self.redis_dsn

    @property
    def allowed_models(self) -> set[str]:
        configured = {
            value.strip()
            for value in self.llm_allowed_models.split(",")
            if value.strip()
        }
        return configured or {self.llm_model, self.vl_model, *self.fallback_models}

    @property
    def fallback_models(self) -> list[str]:
        return [
            value.strip()
            for value in self.llm_fallback_models.split(",")
            if value.strip()
        ]

    @staticmethod
    def reveal(secret: SecretStr | None) -> str | None:
        return secret.get_secret_value() if secret is not None else None

    def api_keys_for_role(self, role: str) -> set[str]:
        secret = {
            "admin": self.admin_api_keys,
            "user": self.user_api_keys,
            "readonly": self.readonly_api_keys,
        }.get(role)
        raw = self.reveal(secret) or ""
        return {value.strip() for value in raw.split(",") if value.strip()}

    def validate_for_service(self, service: Literal["import", "query"]) -> None:
        missing: list[str] = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.openai_base_url:
            missing.append("OPENAI_BASE_URL")
        if not self.milvus_uri:
            missing.append("MILVUS_URI (or legacy MILVUS_URL)")
        if service == "import" and not self.mineru_api_token:
            missing.append("MINERU_API_TOKEN")
        if self.minio_enabled:
            if not self.minio_access_key:
                missing.append("MINIO_ACCESS_KEY")
            if not self.minio_secret_key:
                missing.append("MINIO_SECRET_KEY")
        if self.auth_enabled:
            if self.oidc_enabled:
                if not self.oidc_issuer_url:
                    missing.append("OIDC_ISSUER_URL")
                if not self.oidc_client_id:
                    missing.append("OIDC_CLIENT_ID")
                if not self.oidc_audience:
                    missing.append("OIDC_AUDIENCE")
            elif not any((self.admin_api_keys, self.user_api_keys, self.readonly_api_keys)):
                missing.append("OIDC configuration or at least one development API key")
        if missing:
            raise ValueError(f"{service} service missing required configuration: {', '.join(missing)}")

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.task_backend == "redis" and not self.redis_enabled:
            raise ValueError("REDIS_ENABLED must be true when TASK_BACKEND=redis")
        if self.task_queue_enabled and not self.redis_enabled:
            raise ValueError("REDIS_ENABLED must be true when TASK_QUEUE_ENABLED=true")
        if self.langgraph_aes_key:
            raw_checkpoint_key = self.reveal(self.langgraph_aes_key) or ""
            key_length = len(raw_checkpoint_key.encode("utf-8"))
            if key_length not in {16, 24, 32}:
                raise ValueError("LANGGRAPH_AES_KEY must be 16, 24, or 32 UTF-8 bytes")
        secure_oidc_algorithms = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if self.oidc_enabled:
            if not self.oidc_issuer_url or not self.oidc_client_id or not self.oidc_audience:
                raise ValueError(
                    "OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_AUDIENCE are required "
                    "when OIDC_ENABLED=true"
                )
            if not self.oidc_algorithms or not set(self.oidc_algorithms).issubset(
                secure_oidc_algorithms
            ):
                raise ValueError("OIDC_ALLOWED_ALGORITHMS must contain only secure asymmetric algorithms")
        if self.is_deployed:
            if "*" in self.cors_origins:
                raise ValueError("CORS wildcard is forbidden in staging/production")
            if not self.auth_enabled:
                raise ValueError("AUTH_ENABLED must be true in staging/production")
            if not self.oidc_enabled:
                raise ValueError("OIDC_ENABLED must be true in staging/production")
            if not self.oidc_issuer_url or not self.oidc_issuer_url.lower().startswith("https://"):
                raise ValueError("OIDC_ISSUER_URL must use HTTPS in staging/production")
            if self.log_sensitive_content:
                raise ValueError("LOG_SENSITIVE_CONTENT must be false in staging/production")
            if self.task_backend == "memory":
                raise ValueError("TASK_BACKEND=memory is forbidden in staging/production")
            if not self.redis_enabled:
                raise ValueError("REDIS_ENABLED must be true in staging/production")
            _validate_deployed_secret(
                "REDIS_URL password",
                _url_password(self.redis_dsn),
                minimum_length=24,
            )
            if not self.task_queue_enabled:
                raise ValueError("TASK_QUEUE_ENABLED must be true in staging/production")
            if not self.database_enabled:
                raise ValueError("DATABASE_ENABLED must be true in staging/production")
            if self.database_dsn.lower().startswith("sqlite"):
                raise ValueError("SQLite is forbidden in staging/production; use PostgreSQL")
            _validate_deployed_secret(
                "DATABASE_URL password",
                _url_password(self.database_dsn),
                minimum_length=24,
            )
            if self.langgraph_checkpointer != "postgres":
                raise ValueError("LANGGRAPH_CHECKPOINTER=postgres is required in staging/production")
            if not self.langgraph_database_dsn:
                raise ValueError("LANGGRAPH_DATABASE_URL is required in staging/production")
            _validate_deployed_secret(
                "LANGGRAPH_DATABASE_URL password",
                _url_password(self.langgraph_database_dsn),
                minimum_length=24,
            )
            if not self.langgraph_aes_key:
                raise ValueError("LANGGRAPH_AES_KEY is required to encrypt deployed checkpoints")
            _validate_deployed_secret(
                "LANGGRAPH_AES_KEY",
                self.reveal(self.langgraph_aes_key),
                minimum_length=32,
            )
            if not self.knowledge_base_filter_enabled:
                raise ValueError("KNOWLEDGE_BASE_FILTER_ENABLED must be true in staging/production")
            if not self.llm_allowed_models.strip():
                raise ValueError("LLM_ALLOWED_MODELS must define an explicit deployed whitelist")
            if self.openai_base_url and not self.openai_base_url.lower().startswith("https://"):
                raise ValueError("OPENAI_BASE_URL must use HTTPS in staging/production")
            if self.openai_api_key:
                _validate_deployed_secret(
                    "OPENAI_API_KEY",
                    self.reveal(self.openai_api_key),
                    minimum_length=20,
                )
            if self.mineru_api_token:
                _validate_deployed_secret(
                    "MINERU_API_TOKEN",
                    self.reveal(self.mineru_api_token),
                    minimum_length=20,
                )
            for role_name in ("admin", "user", "readonly"):
                for api_key in self.api_keys_for_role(role_name):
                    _validate_deployed_secret(
                        f"{role_name.upper()}_API_KEYS entry",
                        api_key,
                        minimum_length=32,
                    )
            if not self.minio_enabled:
                raise ValueError("MINIO_ENABLED must be true in staging/production")
            if not self.minio_access_key or not self.minio_secret_key:
                raise ValueError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required when deployed")
            _validate_deployed_secret(
                "MINIO_ACCESS_KEY",
                self.reveal(self.minio_access_key),
                minimum_length=16,
            )
            _validate_deployed_secret(
                "MINIO_SECRET_KEY",
                self.reveal(self.minio_secret_key),
                minimum_length=32,
            )
            if self.minio_public_read:
                raise ValueError("MINIO_PUBLIC_READ is forbidden in staging/production")
            if not self.minio_public_secure:
                raise ValueError("MINIO_PUBLIC_SECURE=true is required in staging/production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
