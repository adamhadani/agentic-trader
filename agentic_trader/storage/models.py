from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agentic_trader.constants import AssetClass, SignalStatus


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


class SignalRecord(Base):
    """ORM representation of a trade signal and its lifecycle."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    contract: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    risk_dollars: Mapped[float] = mapped_column(Float, nullable=False)
    reward_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default=SignalStatus.PENDING, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(String, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String, default=AssetClass.FUTURES, nullable=True)

    __table_args__ = (Index("idx_recent_signals", "contract", "strategy", "timestamp"),)

    def to_dict(self) -> dict[str, Any]:
        """Convert ORM record into a dictionary compatible with existing callers."""
        ts = (
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(self.timestamp, datetime)
            else str(self.timestamp)
        )
        exit_ts = (
            self.exit_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(self.exit_timestamp, datetime)
            else (str(self.exit_timestamp) if self.exit_timestamp else None)
        )
        return {
            "id": self.id,
            "timestamp": ts,
            "contract": self.contract,
            "strategy": self.strategy,
            "direction": self.direction,
            "entry_price": float(self.entry_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "risk_dollars": float(self.risk_dollars),
            "reward_dollars": float(self.reward_dollars) if self.reward_dollars is not None else None,
            "notional_value": float(self.notional_value) if self.notional_value is not None else None,
            "status": self.status,
            "telegram_message_id": self.telegram_message_id,
            "raw_response": self.raw_response,
            "exit_price": float(self.exit_price) if self.exit_price is not None else None,
            "exit_timestamp": exit_ts,
            "realized_pnl": float(self.realized_pnl) if self.realized_pnl is not None else None,
            "exit_reason": self.exit_reason,
            "broker_order_id": self.broker_order_id,
            "asset_class": self.asset_class or AssetClass.FUTURES,
        }
