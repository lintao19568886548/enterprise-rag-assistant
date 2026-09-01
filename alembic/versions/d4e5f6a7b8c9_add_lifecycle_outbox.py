"""Add document version activation and lifecycle cleanup outbox.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column(
                "lifecycle_status",
                sa.String(length=32),
                nullable=False,
                server_default="ACTIVE",
            )
        )
        batch.create_index(
            "ix_documents_tenant_lifecycle",
            ["tenant_id", "lifecycle_status"],
            unique=False,
        )

    with op.batch_alter_table("document_versions") as batch:
        batch.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("chunk_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_object_path", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("source_local_path", sa.String(length=2048), nullable=True))
        batch.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("activated_by", sa.String(length=128), nullable=True))
    op.execute(
        """
        UPDATE document_versions
        SET is_active = true,
            activated_at = created_at
        WHERE EXISTS (
            SELECT 1 FROM documents
            WHERE documents.id = document_versions.document_id
              AND documents.current_version = document_versions.version
        )
        """
    )
    op.create_index(
        "uq_document_active_version",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_summary", sa.String(length=1024), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "deduplication_key", name="uq_outbox_tenant_dedup"),
    )
    op.create_index(
        "ix_outbox_status_next_retry",
        "outbox_events",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_outbox_tenant_created",
        "outbox_events",
        ["tenant_id", "created_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        tenant_setting = "NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_select ON outbox_events FOR SELECT "
            f"USING (tenant_id = {tenant_setting})"
        )
        op.execute(
            f"CREATE POLICY tenant_insert ON outbox_events FOR INSERT "
            f"WITH CHECK (tenant_id = {tenant_setting})"
        )
        op.execute(
            f"CREATE POLICY tenant_update ON outbox_events FOR UPDATE "
            f"USING (tenant_id = {tenant_setting}) WITH CHECK (tenant_id = {tenant_setting})"
        )
        op.execute(
            f"CREATE POLICY tenant_delete ON outbox_events FOR DELETE "
            f"USING (tenant_id = {tenant_setting})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for policy_name in ("tenant_delete", "tenant_update", "tenant_insert", "tenant_select"):
            op.execute(f"DROP POLICY IF EXISTS {policy_name} ON outbox_events")
        op.execute("ALTER TABLE outbox_events NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE outbox_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_outbox_tenant_created", table_name="outbox_events")
    op.drop_index("ix_outbox_status_next_retry", table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index("uq_document_active_version", table_name="document_versions")
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_column("activated_by")
        batch.drop_column("activated_at")
        batch.drop_column("source_local_path")
        batch.drop_column("source_object_path")
        batch.drop_column("chunk_count")
        batch.drop_column("is_active")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_tenant_lifecycle")
        batch.drop_column("lifecycle_status")
