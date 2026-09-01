"""Real PostgreSQL RLS acceptance tests.

Set both TEST_POSTGRES_OWNER_URL and TEST_POSTGRES_RUNTIME_URL. The owner URL
must point at the migrated test database; the runtime role must not own tables,
be a superuser, or have BYPASSRLS.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


OWNER_URL = os.environ.get("TEST_POSTGRES_OWNER_URL")
RUNTIME_URL = os.environ.get("TEST_POSTGRES_RUNTIME_URL")
pytestmark = pytest.mark.skipif(
    not OWNER_URL or not RUNTIME_URL,
    reason="real PostgreSQL owner/runtime test URLs are not configured",
)


def _set_context(connection, tenant_id: str, user_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


@pytest.fixture()
def rls_records():
    owner_engine = create_engine(OWNER_URL)
    suffix = uuid.uuid4().hex
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    kb_a = str(uuid.uuid4())
    kb_b = str(uuid.uuid4())
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, slug, name, enabled) VALUES "
                "(:a, :slug_a, 'RLS A', true), (:b, :slug_b, 'RLS B', true)"
            ),
            {"a": tenant_a, "b": tenant_b, "slug_a": f"rls-a-{suffix}", "slug_b": f"rls-b-{suffix}"},
        )
        _set_context(connection, tenant_a, user_a)
        connection.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, role, enabled) "
                "VALUES (:id, :tenant, :username, 'user', true)"
            ),
            {"id": user_a, "tenant": tenant_a, "username": f"rls-a-{suffix}"},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, description, owner_id, permission_scope, embedding_model, collection_name) "
                "VALUES (:id, :tenant, :name, '', :owner, 'private', 'test', 'test')"
            ),
            {"id": kb_a, "tenant": tenant_a, "name": f"rls-a-{suffix}", "owner": user_a},
        )
        _set_context(connection, tenant_b, user_b)
        connection.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, role, enabled) "
                "VALUES (:id, :tenant, :username, 'user', true)"
            ),
            {"id": user_b, "tenant": tenant_b, "username": f"rls-b-{suffix}"},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, description, owner_id, permission_scope, embedding_model, collection_name) "
                "VALUES (:id, :tenant, :name, '', :owner, 'private', 'test', 'test')"
            ),
            {"id": kb_b, "tenant": tenant_b, "name": f"rls-b-{suffix}", "owner": user_b},
        )
    yield {"tenant_a": tenant_a, "tenant_b": tenant_b, "user_a": user_a, "user_b": user_b, "kb_b": kb_b}
    with owner_engine.begin() as connection:
        for tenant_id, user_id in ((tenant_a, user_a), (tenant_b, user_b)):
            _set_context(connection, tenant_id, user_id)
            connection.execute(text("DELETE FROM knowledge_bases WHERE tenant_id = :tenant"), {"tenant": tenant_id})
            connection.execute(text("DELETE FROM users WHERE tenant_id = :tenant"), {"tenant": tenant_id})
        connection.execute(text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": tenant_a, "b": tenant_b})
    owner_engine.dispose()


def test_cross_tenant_select_update_delete_and_insert_are_blocked(rls_records):
    engine = create_engine(RUNTIME_URL, pool_size=1, max_overflow=0)
    with engine.connect() as connection:
        transaction = connection.begin()
        _set_context(connection, rls_records["tenant_a"], rls_records["user_a"])
        leaked = connection.scalar(
            text("SELECT count(1) FROM knowledge_bases WHERE id = :id"),
            {"id": rls_records["kb_b"]},
        )
        updated = connection.execute(
            text("UPDATE knowledge_bases SET description = 'forbidden' WHERE id = :id"),
            {"id": rls_records["kb_b"]},
        ).rowcount
        deleted = connection.execute(
            text("DELETE FROM knowledge_bases WHERE id = :id"),
            {"id": rls_records["kb_b"]},
        ).rowcount
        transaction.rollback()
    assert leaked == 0
    assert updated == 0
    assert deleted == 0

    with engine.connect() as connection:
        transaction = connection.begin()
        _set_context(connection, rls_records["tenant_a"], rls_records["user_a"])
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO users (id, tenant_id, username, role, enabled) "
                    "VALUES (:id, :tenant, :username, 'user', true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": rls_records["tenant_b"],
                    "username": f"forged-{uuid.uuid4().hex}",
                },
            )
        transaction.rollback()
    engine.dispose()


def test_runtime_role_is_least_privileged():
    engine = create_engine(RUNTIME_URL)
    with engine.connect() as connection:
        attributes = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    engine.dispose()
    assert tuple(attributes) == (False, False, False, False, False)


def test_missing_context_fails_closed_for_read_write_and_insert(rls_records):
    engine = create_engine(RUNTIME_URL)
    with engine.connect() as connection:
        transaction = connection.begin()
        assert connection.scalar(text("SELECT count(1) FROM knowledge_bases")) == 0
        assert (
            connection.execute(
                text("UPDATE knowledge_bases SET description = 'forbidden' WHERE id = :id"),
                {"id": rls_records["kb_b"]},
            ).rowcount
            == 0
        )
        assert (
            connection.execute(
                text("DELETE FROM knowledge_bases WHERE id = :id"),
                {"id": rls_records["kb_b"]},
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO users (id, tenant_id, username, role, enabled) "
                    "VALUES (:id, :tenant, :username, 'user', true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": rls_records["tenant_b"],
                    "username": f"missing-context-{uuid.uuid4().hex}",
                },
            )
        transaction.rollback()
    engine.dispose()


def test_pooled_connection_does_not_retain_previous_tenant(rls_records):
    engine = create_engine(RUNTIME_URL, pool_size=1, max_overflow=0)
    with engine.begin() as connection:
        _set_context(connection, rls_records["tenant_a"], rls_records["user_a"])
        assert connection.scalar(text("SELECT count(1) FROM knowledge_bases")) >= 1
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(1) FROM knowledge_bases")) == 0
        _set_context(connection, rls_records["tenant_b"], rls_records["user_b"])
        tenant_ids = connection.scalars(text("SELECT tenant_id FROM knowledge_bases")).all()
        assert tenant_ids and set(tenant_ids) == {rls_records["tenant_b"]}
    engine.dispose()
