"""Add the default tenant boundary to persisted resources.

Revision ID: 8f1a2c3d4e5f
Revises: 2c72d95439c1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "8f1a2c3d4e5f"
down_revision: str | None = "2c72d95439c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000100"


def _add_tenant_column(table_name: str, *, ondelete: str = "CASCADE") -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(
            sa.Column(
                "tenant_id",
                sa.String(length=36),
                nullable=False,
                server_default=DEFAULT_TENANT_ID,
            )
        )
        batch.create_foreign_key(
            f"fk_{table_name}_tenant_id_tenants",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    tenants = op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.bulk_insert(
        tenants,
        [
            {
                "id": DEFAULT_TENANT_ID,
                "slug": "default",
                "name": "默认租户",
                "enabled": True,
            }
        ],
    )

    _add_tenant_column("users", ondelete="RESTRICT")
    _add_tenant_column("knowledge_bases")
    _add_tenant_column("documents")
    _add_tenant_column("chunks")
    _add_tenant_column("import_tasks")
    _add_tenant_column("chat_sessions")
    _add_tenant_column("operation_logs")

    op.create_index(
        "ix_knowledge_bases_tenant_deleted",
        "knowledge_bases",
        ["tenant_id", "deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_tenant_status",
        "documents",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_chunks_tenant_kb",
        "chunks",
        ["tenant_id", "knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_import_tasks_tenant_created",
        "import_tasks",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_tenant_user",
        "chat_sessions",
        ["tenant_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "ix_operation_logs_tenant_created",
        "operation_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )


def _drop_tenant_column(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(
            f"fk_{table_name}_tenant_id_tenants",
            type_="foreignkey",
        )
        batch.drop_column("tenant_id")


def downgrade() -> None:
    op.drop_index("ix_operation_logs_tenant_created", table_name="operation_logs")
    op.drop_index("ix_chat_sessions_tenant_user", table_name="chat_sessions")
    op.drop_index("ix_import_tasks_tenant_created", table_name="import_tasks")
    op.drop_index("ix_chunks_tenant_kb", table_name="chunks")
    op.drop_index("ix_documents_tenant_status", table_name="documents")
    op.drop_index("ix_knowledge_bases_tenant_deleted", table_name="knowledge_bases")

    _drop_tenant_column("operation_logs")
    _drop_tenant_column("chat_sessions")
    _drop_tenant_column("import_tasks")
    _drop_tenant_column("chunks")
    _drop_tenant_column("documents")
    _drop_tenant_column("knowledge_bases")
    _drop_tenant_column("users")
    op.drop_table("tenants")
