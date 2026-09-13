"""Initial signals schema baseline.

Revision ID: 001_initial
Revises:
Create Date: 2026-09-13 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema to initial baseline."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "signals" not in tables:
        op.create_table(
            "signals",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("contract", sa.String(), nullable=False),
            sa.Column("strategy", sa.String(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("stop_loss", sa.Float(), nullable=False),
            sa.Column("take_profit", sa.Float(), nullable=False),
            sa.Column("risk_dollars", sa.Float(), nullable=False),
            sa.Column("reward_dollars", sa.Float(), nullable=True),
            sa.Column("notional_value", sa.Float(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
            sa.Column("telegram_message_id", sa.Integer(), nullable=True),
            sa.Column("raw_response", sa.String(), nullable=True),
            sa.Column("exit_price", sa.Float(), nullable=True),
            sa.Column("exit_timestamp", sa.DateTime(), nullable=True),
            sa.Column("realized_pnl", sa.Float(), nullable=True),
            sa.Column("exit_reason", sa.String(), nullable=True),
            sa.Column("broker_order_id", sa.String(), nullable=True),
            sa.Column("asset_class", sa.String(), nullable=True, server_default="FUTURES"),
            sa.Column("quantity", sa.Float(), nullable=True, server_default="1.0"),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # Check and add any columns that might be missing on an existing legacy table
        existing_cols = {col["name"] for col in inspector.get_columns("signals")}
        with op.batch_alter_table("signals") as batch_op:
            if "exit_price" not in existing_cols:
                batch_op.add_column(sa.Column("exit_price", sa.Float(), nullable=True))
            if "exit_timestamp" not in existing_cols:
                batch_op.add_column(sa.Column("exit_timestamp", sa.DateTime(), nullable=True))
            if "realized_pnl" not in existing_cols:
                batch_op.add_column(sa.Column("realized_pnl", sa.Float(), nullable=True))
            if "exit_reason" not in existing_cols:
                batch_op.add_column(sa.Column("exit_reason", sa.String(), nullable=True))
            if "broker_order_id" not in existing_cols:
                batch_op.add_column(sa.Column("broker_order_id", sa.String(), nullable=True))
            if "asset_class" not in existing_cols:
                batch_op.add_column(sa.Column("asset_class", sa.String(), nullable=True, server_default="FUTURES"))
            if "quantity" not in existing_cols:
                batch_op.add_column(sa.Column("quantity", sa.Float(), nullable=True, server_default="1.0"))

    # Create index if it does not exist
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("signals")}
    if "idx_recent_signals" not in existing_indexes:
        op.create_index("idx_recent_signals", "signals", ["contract", "strategy", "timestamp"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "signals" in tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("signals")}
        if "idx_recent_signals" in existing_indexes:
            op.drop_index("idx_recent_signals", table_name="signals")
        op.drop_table("signals")
