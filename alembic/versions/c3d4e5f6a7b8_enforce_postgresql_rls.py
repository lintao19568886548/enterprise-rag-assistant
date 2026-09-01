"""Enforce tenant isolation with PostgreSQL Row-Level Security.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7

SQLite development remains unchanged. PostgreSQL policies fail closed when the
application has not installed a transaction-local identity context.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "users",
    "memberships",
    "departments",
    "knowledge_bases",
    "knowledge_base_grants",
    "documents",
    "document_versions",
    "chunks",
    "import_tasks",
    "chat_sessions",
    "chat_messages",
    "operation_logs",
    "audit_logs",
    "service_accounts",
    "product_aliases",
)

TENANT_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')"
USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')"
OIDC_SUBJECT_SETTING = "NULLIF(current_setting('app.oidc_subject', true), '')"
OIDC_ISSUER_SETTING = "NULLIF(current_setting('app.oidc_issuer', true), '')"


def _policy_sql(table_name: str) -> list[str]:
    select_expression = f"tenant_id = {TENANT_SETTING}"
    if table_name == "users":
        select_expression = (
            f"({select_expression}) OR "
            f"(external_identity_id = {OIDC_SUBJECT_SETTING} "
            f"AND oidc_issuer = {OIDC_ISSUER_SETTING})"
        )
    elif table_name == "memberships":
        select_expression = f"({select_expression}) OR (user_id = {USER_SETTING})"
    tenant_check = f"tenant_id = {TENANT_SETTING}"
    return [
        f'CREATE POLICY tenant_select ON "{table_name}" FOR SELECT USING ({select_expression})',
        f'CREATE POLICY tenant_insert ON "{table_name}" FOR INSERT WITH CHECK ({tenant_check})',
        (
            f'CREATE POLICY tenant_update ON "{table_name}" FOR UPDATE '
            f'USING ({tenant_check}) WITH CHECK ({tenant_check})'
        ),
        f'CREATE POLICY tenant_delete ON "{table_name}" FOR DELETE USING ({tenant_check})',
    ]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        for statement in _policy_sql(table_name):
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in reversed(TENANT_TABLES):
        for policy_name in ("tenant_delete", "tenant_update", "tenant_insert", "tenant_select"):
            op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
