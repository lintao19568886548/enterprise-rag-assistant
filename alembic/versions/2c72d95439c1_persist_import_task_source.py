"""Persist task source pointers for restart-safe retries.

Revision ID: 2c72d95439c1
Revises: 9e7567baa0f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "2c72d95439c1"
down_revision: str | None = "9e7567baa0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_tasks", sa.Column("local_dir", sa.String(length=1024), nullable=True))
    op.add_column(
        "import_tasks",
        sa.Column("local_file_path", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_tasks", "local_file_path")
    op.drop_column("import_tasks", "local_dir")
