"""Replayable alpha research/registry projections and signal version attribution."""

import sqlalchemy as sa

from alembic import op


revision = "008_alpha_pipeline"
down_revision = "007_operational_incidents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alpha_projections",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    for name in ("timeframe", "alpha_version", "alpha_policy", "decision_provenance"):
        op.add_column("signals", sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    for name in ("timeframe", "alpha_version", "alpha_policy", "decision_provenance"):
        op.drop_column("signals", name)
    op.drop_table("alpha_projections")
