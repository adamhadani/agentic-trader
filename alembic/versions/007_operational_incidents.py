"""Replayable operational incident state and observation watermarks."""

import sqlalchemy as sa

from alembic import op


revision = "007_operational_incidents"
down_revision = "006_account_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_projections",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("component", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("incident_projections")
