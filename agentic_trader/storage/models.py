from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from agentic_trader.constants import UNKNOWN_EXECUTION_MODE, AssetClass, RuntimeEnvironment, SignalStatus


class UTCDatetime(TypeDecorator):
    """Platform-independent UTC DateTime: stores timezone-naive UTC in Postgres/SQLite while exposing UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if hasattr(value, "tzinfo") and value.tzinfo is not None:
                return value.astimezone(UTC).replace(tzinfo=None)
            return value
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        return None


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


class SignalRecord(Base):
    """ORM representation of a trade signal and its lifecycle."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDatetime, default=lambda: datetime.now(UTC), nullable=False)
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
    exit_timestamp: Mapped[datetime | None] = mapped_column(UTCDatetime, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String, default=AssetClass.FUTURES, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=True)
    environment: Mapped[str] = mapped_column(String, default=RuntimeEnvironment.PRODUCTION, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String, default=UNKNOWN_EXECUTION_MODE, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(UTCDatetime, nullable=True)
    broker_exit_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
            "quantity": float(self.quantity) if self.quantity is not None else 1.0,
            "environment": self.environment,
            "execution_mode": self.execution_mode,
            "run_id": self.run_id,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "broker_exit_order_id": self.broker_exit_order_id,
            "is_quarantined": self.is_quarantined,
        }


class AuditEventRecord(Base):
    """Append-only operational evidence, without credentials or destination identifiers."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDatetime, default=lambda: datetime.now(UTC), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class SystemStateRecord(Base):
    """ORM representation of system-level key-value configuration and operational flags."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDatetime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": (
                self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at)
            ),
        }


class CloseRequestRecord(Base):
    """Durable, exclusive close intent; terminal requests remain as history."""

    __tablename__ = "close_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    signal_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDatetime, default=lambda: datetime.now(UTC), nullable=False)
    __table_args__ = (
        Index(
            "uq_active_close_symbol",
            "environment",
            "execution_mode",
            "symbol",
            unique=True,
            postgresql_where=text("status IN ('claimed', 'submitted', 'unknown')"),
            sqlite_where=text("status IN ('claimed', 'submitted', 'unknown')"),
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class WorkflowLockRecord(Base):
    """One transaction mutex per environment/account; never held during network I/O."""

    __tablename__ = "workflow_locks"
    scope: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DomainEventRecord(Base):
    __tablename__ = "domain_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    event_key: Mapped[str] = mapped_column(String, nullable=False)
    stream: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDatetime, nullable=False, default=lambda: datetime.now(UTC))
    __table_args__ = (
        Index("uq_domain_event_key", "scope", "event_key", unique=True),
        Index("ix_domain_event_stream", "scope", "stream", "id"),
    )


class WorkItemRecord(Base):
    __tablename__ = "work_items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDatetime, nullable=True)
    available_at: Mapped[datetime] = mapped_column(UTCDatetime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index("uq_work_dedup", "scope", "kind", "dedup_key", unique=True),
        Index("ix_work_dispatch", "scope", "kind", "status", "sequence"),
    )


class OrderProjectionRecord(Base):
    __tablename__ = "order_projections"
    scope: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
