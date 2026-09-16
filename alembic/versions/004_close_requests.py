"""Durable exclusive close requests, including broker-only positions."""

import sqlalchemy as sa

from alembic import op


revision = "004_close_requests"
down_revision = "003_audit_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "close_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("execution_mode", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("broker_order_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_active_close_symbol",
        "close_requests",
        ["environment", "execution_mode", "symbol"],
        unique=True,
        postgresql_where=sa.text("status IN ('claimed', 'submitted', 'unknown')"),
        sqlite_where=sa.text("status IN ('claimed', 'submitted', 'unknown')"),
    )


def downgrade() -> None:
    op.drop_table("close_requests")
