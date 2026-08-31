"""Engine/session lifecycle and development schema bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.settings import PROJECT_ROOT, settings
from app.core.tenant_context import (
    ContextTokens,
    current_identity_context,
    reset_identity_context,
    set_identity_context,
)
from app.db.models import Base


def normalized_database_url() -> str:
    url = settings.database_url
    prefix = "sqlite:///./"
    if url.startswith(prefix):
        relative = url[len(prefix) :]
        return f"sqlite:///{(PROJECT_ROOT / relative).resolve().as_posix()}"
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = normalized_database_url()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
        )
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def apply_postgres_rls_context(
    connection: Connection,
    context: dict[str, str | None] | None = None,
) -> None:
    """Install fail-closed transaction-local values consumed by RLS policies."""
    if connection.dialect.name != "postgresql":
        return
    values = context or current_identity_context()
    for setting_name, context_name in (
        ("app.tenant_id", "tenant_id"),
        ("app.user_id", "user_id"),
        ("app.oidc_subject", "oidc_subject"),
        ("app.oidc_issuer", "oidc_issuer"),
    ):
        connection.execute(
            text("SELECT set_config(:setting_name, :setting_value, true)"),
            {
                "setting_name": setting_name,
                "setting_value": values.get(context_name) or "",
            },
        )


@event.listens_for(Session, "after_begin")
def _set_session_rls_context(
    _session: Session,
    _transaction: Any,
    connection: Connection,
) -> None:
    apply_postgres_rls_context(connection)


@contextmanager
def session_scope(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
):
    tokens: ContextTokens | None = None
    if tenant_id is not None or user_id is not None:
        current = current_identity_context()
        tokens = set_identity_context(
            tenant_id=tenant_id,
            user_id=user_id,
            oidc_subject=current["oidc_subject"],
            oidc_issuer=current["oidc_issuer"],
        )
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if tokens is not None:
            reset_identity_context(tokens)


def init_database() -> None:
    if not settings.database_enabled:
        return
    if settings.is_production:
        database_ping()
    else:
        Base.metadata.create_all(get_engine())


def database_ping() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
