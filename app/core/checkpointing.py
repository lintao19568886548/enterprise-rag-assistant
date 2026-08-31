"""LangGraph checkpoint backend factory."""

from __future__ import annotations

import atexit
import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.logger import logger
from app.core.settings import PROJECT_ROOT, settings


def _serializer():
    serializer = JsonPlusSerializer(pickle_fallback=False)
    key = settings.reveal(settings.langgraph_aes_key)
    if key:
        return EncryptedSerializer.from_pycryptodome_aes(
            serde=serializer,
            key=key.encode("utf-8"),
        )
    return serializer


def _sqlite_path() -> Path:
    path = Path(settings.langgraph_checkpoint_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@lru_cache(maxsize=4)
def get_checkpoint_saver(namespace: str) -> BaseCheckpointSaver:
    backend = settings.langgraph_checkpointer
    serde = _serializer()
    if backend == "memory":
        logger.warning("LangGraph checkpointer：内存（namespace={}）", namespace)
        return InMemorySaver(serde=serde)
    if backend == "sqlite":
        path = _sqlite_path()
        sqlite_connection = sqlite3.connect(path, check_same_thread=False)
        sqlite_connection.execute("PRAGMA journal_mode=WAL")
        sqlite_connection.execute("PRAGMA busy_timeout=5000")
        sqlite_saver = SqliteSaver(sqlite_connection, serde=serde)
        sqlite_saver.setup()
        atexit.register(sqlite_connection.close)
        logger.info("LangGraph checkpointer：SQLite（namespace={}）", namespace)
        return sqlite_saver
    if backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row

        database_url = settings.langgraph_database_url or settings.database_url
        database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        postgres_connection = Connection.connect(
            database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        postgres_saver = PostgresSaver(postgres_connection, serde=serde)
        postgres_saver.setup()
        atexit.register(postgres_connection.close)
        logger.info("LangGraph checkpointer：PostgreSQL（namespace={}）", namespace)
        return postgres_saver
    raise ValueError(f"Unsupported LangGraph checkpointer: {backend}")


def checkpoint_config(thread_id: str, namespace: str) -> dict:
    # ``checkpoint_ns`` is reserved for LangGraph subgraphs. Prefixing the
    # thread ID isolates import/query workflows without interfering with the
    # top-level checkpoint lookup semantics.
    return {"configurable": {"thread_id": f"{namespace}:{thread_id}"}}
