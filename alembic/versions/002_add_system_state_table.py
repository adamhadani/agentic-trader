"""Add system_state table.

Revision ID: 002_system_state
Revises: 001_initial
Create Date: 2026-09-14 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "002_system_state"
down_revision: str | Sequence[str] | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create system_state table if not exists."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "system_state" not in tables:
        op.create_table(
            "system_state",
            sa.Column("key", sa.String(), primary_key=True, nullable=False),
            sa.Column("value", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )


def downgrade() -> None:
    """Drop system_state table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "system_state" in tables:
        op.drop_table("system_state")
