"""Add enterprise identity, memberships, grants and audit records.

Revision ID: b2c3d4e5f6a7
Revises: 8f1a2c3d4e5f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "8f1a2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000100"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _add_tenant_column(table_name: str) -> None:
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
            ondelete="CASCADE",
        )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("display_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("oidc_issuer", sa.String(length=1024), nullable=True))

    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_department_tenant_name"),
    )
    op.create_index("ix_departments_tenant_enabled", "departments", ["tenant_id", "enabled"])

    memberships = op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )
    op.create_index("ix_memberships_tenant_role", "memberships", ["tenant_id", "role"])
    op.bulk_insert(
        memberships,
        [
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "tenant_id": DEFAULT_TENANT_ID,
                "user_id": DEFAULT_USER_ID,
                "department_id": None,
                "role": "tenant_admin",
                "enabled": True,
            }
        ],
    )

    op.create_table(
        "service_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_service_account_tenant_name"),
    )
    op.create_index(
        "ix_service_accounts_tenant_enabled",
        "service_accounts",
        ["tenant_id", "enabled"],
    )

    op.create_table(
        "knowledge_base_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("granted_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "subject_type",
            "subject_id",
            "permission",
            name="uq_kb_grant_subject_permission",
        ),
    )
    op.create_index(
        "ix_kb_grants_tenant_kb",
        "knowledge_base_grants",
        ["tenant_id", "knowledge_base_id"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_audit_logs_event_outcome", "audit_logs", ["event_type", "outcome"])

    _add_tenant_column("document_versions")
    _add_tenant_column("chat_messages")
    _add_tenant_column("product_aliases")


def _drop_tenant_column(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(f"fk_{table_name}_tenant_id_tenants", type_="foreignkey")
        batch.drop_column("tenant_id")


def downgrade() -> None:
    _drop_tenant_column("product_aliases")
    _drop_tenant_column("chat_messages")
    _drop_tenant_column("document_versions")

    op.drop_index("ix_audit_logs_event_outcome", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_kb_grants_tenant_kb", table_name="knowledge_base_grants")
    op.drop_table("knowledge_base_grants")
    op.drop_index("ix_service_accounts_tenant_enabled", table_name="service_accounts")
    op.drop_table("service_accounts")
    op.drop_index("ix_memberships_tenant_role", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_departments_tenant_enabled", table_name="departments")
    op.drop_table("departments")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("oidc_issuer")
        batch.drop_column("display_name")
        batch.drop_column("email")
