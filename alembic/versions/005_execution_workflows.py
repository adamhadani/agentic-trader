"""Transactional command queue, event journal, order projection and outbox."""

import sqlalchemy as sa

from alembic import op


revision = "005_execution_workflows"
down_revision = "004_close_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_locks",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "domain_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("stream", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("uq_domain_event_key", "domain_events", ["scope", "event_key"], unique=True)
    op.create_index("ix_domain_event_stream", "domain_events", ["scope", "stream", "id"])
    op.create_table(
        "work_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("uq_work_dedup", "work_items", ["scope", "kind", "dedup_key"], unique=True)
    op.create_index("ix_work_dispatch", "work_items", ["scope", "kind", "status", "sequence"])
    op.create_table(
        "order_projections",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    for table in ("order_projections", "work_items", "domain_events", "workflow_locks"):
        op.drop_table(table)
