"""Signal provenance, reversible quarantine, and operational audit events.

Revision ID: 003_audit_provenance
Revises: 002_system_state
"""

import sqlalchemy as sa

from alembic import op


revision = "003_audit_provenance"
down_revision = "002_system_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("signals") as batch:
        batch.add_column(sa.Column("environment", sa.String(), nullable=False, server_default="production"))
        batch.add_column(sa.Column("execution_mode", sa.String(), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("run_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("executed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("broker_exit_order_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("is_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("execution_mode", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_signal_id", "audit_events", ["signal_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    with op.batch_alter_table("signals") as batch:
        for name in (
            "is_quarantined",
            "broker_exit_order_id",
            "executed_at",
            "run_id",
            "execution_mode",
            "environment",
        ):
            batch.drop_column(name)
