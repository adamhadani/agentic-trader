"""Replayable account activity evidence and fenced reconciliation checkpoints."""

import sqlalchemy as sa

from alembic import op


revision = "006_account_ledger"
down_revision = "005_execution_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_projections",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("activity_id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_table(
        "ledger_checkpoints",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ledger_checkpoints")
    op.drop_table("activity_projections")
