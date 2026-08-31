"""Engine/session lifecycle and development schema bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.settings import PROJECT_ROOT, settings
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


@contextmanager
def session_scope():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
