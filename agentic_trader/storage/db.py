from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentic_trader.config import AppConfig, DatabaseConfig, normalize_db_url
from agentic_trader.constants import (
    DEFAULT_DB_MAX_RETRIES,
    DEFAULT_DB_RETRY_DELAY,
    DEFAULT_DUPLICATE_SIGNAL_WINDOW_HOURS,
    DEFAULT_RECENT_SIGNALS_LIMIT,
    UNKNOWN_EXECUTION_MODE,
    AssetClass,
    AuditEventType,
    ExecutionMode,
    ExitReason,
    RuntimeEnvironment,
    SignalStatus,
)
from agentic_trader.runtime import RUN_ID, validate_test_database
from agentic_trader.storage.migrations import run_migrations_head
from agentic_trader.storage.models import AuditEventRecord, SignalRecord, SystemStateRecord


logger = logging.getLogger(__name__)


class SignalDatabase:
    """
    SQLAlchemy 2.0 Async ORM Persistence Layer.
    Provides database-agnostic operations (SQLite, PostgreSQL, MySQL)
    with Alembic readiness and automatic schema initialization.
    """

    def __init__(
        self,
        db_path: str | None = None,
        db_url: str | None = None,
        config: AppConfig | DatabaseConfig | None = None,
    ):
        if isinstance(config, DatabaseConfig):
            db_cfg = config
        elif isinstance(config, AppConfig):
            db_cfg = config.database
        else:
            db_cfg = DatabaseConfig()

        if db_url:
            self.db_url = db_url
            self.db_path = None
        elif db_path:
            self.db_path = str(Path(db_path).resolve())
            self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        elif isinstance(config, AppConfig):
            self.db_url = config.resolved_db_url
            self.db_path = config.db_path or db_cfg.path
        elif db_cfg.path or db_cfg.url:
            self.db_url = normalize_db_url(db_cfg.path or db_cfg.url or "")
            self.db_path = db_cfg.path
        else:
            raise ValueError("SignalDatabase requires an explicit database path, URL, or AppConfig.")

        self.environment = (
            config.environment
            if isinstance(config, AppConfig)
            else os.environ.get("COPILOT_ENV", RuntimeEnvironment.PRODUCTION)
        )
        self.execution_mode = str(config.execution_mode) if isinstance(config, AppConfig) else UNKNOWN_EXECUTION_MODE
        if isinstance(config, AppConfig) and self.execution_mode == ExecutionMode.ALPACA:
            self.execution_mode += ":paper" if config.alpaca_paper else ":live"
        validate_test_database(self.db_url)
        if self.db_path:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_cfg = db_cfg
        engine_kwargs: dict[str, Any] = {"echo": db_cfg.echo}
        if "sqlite" in self.db_url and ":memory:" not in self.db_url:
            engine_kwargs["poolclass"] = NullPool
        if "postgresql" in self.db_url:
            engine_kwargs["pool_size"] = db_cfg.pool_size
            engine_kwargs["max_overflow"] = db_cfg.max_overflow
            engine_kwargs["pool_timeout"] = db_cfg.pool_timeout
            engine_kwargs["pool_recycle"] = db_cfg.pool_recycle

        self.engine: AsyncEngine = create_async_engine(self.db_url, **engine_kwargs)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # Synchronously initialize tables and perform migrations
        if self.db_path or self.db_url:
            self.init_sync(max_retries=db_cfg.max_retries, retry_delay=db_cfg.retry_delay)

    def _scope(self):
        return (
            SignalRecord.is_quarantined.is_(False),
            SignalRecord.environment == self.environment,
            SignalRecord.execution_mode.in_([self.execution_mode, UNKNOWN_EXECUTION_MODE])
            if self.execution_mode != UNKNOWN_EXECUTION_MODE
            else True,
        )

    def _audit(self, event_type: str, signal_id: int | None, payload: dict[str, Any]) -> AuditEventRecord:
        return AuditEventRecord(
            event_type=event_type,
            signal_id=signal_id,
            environment=self.environment,
            execution_mode=self.execution_mode,
            run_id=RUN_ID,
            payload=json.dumps(payload, default=str, sort_keys=True),
        )

    async def record_audit(self, event_type: str, payload: dict[str, Any], signal_id: int | None = None) -> None:
        async with self.session_factory() as session:
            session.add(self._audit(event_type, signal_id, payload))
            await session.commit()

    async def record_exit_request(self, signal_id: int, broker_exit_order_id: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(SignalRecord)
                .where(*self._scope(), SignalRecord.id == signal_id)
                .values(broker_exit_order_id=broker_exit_order_id)
            )
            session.add(
                self._audit(
                    AuditEventType.EXIT_ORDER_SUBMITTED, signal_id, {"broker_exit_order_id": broker_exit_order_id}
                )
            )
            await session.commit()

    async def get_audit_events(self, signal_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            stmt = select(AuditEventRecord).where(AuditEventRecord.environment == self.environment)
            if signal_id is not None:
                stmt = stmt.where(AuditEventRecord.signal_id == signal_id)
            records = (await session.execute(stmt.order_by(AuditEventRecord.id.desc()).limit(limit))).scalars()
            return [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat(),
                    "event_type": r.event_type,
                    "signal_id": r.signal_id,
                    "run_id": r.run_id,
                    "payload": json.loads(r.payload),
                }
                for r in records
            ]

    async def quarantine_signal(self, signal_id: int, reason: str, expected: dict[str, Any]) -> bool:
        """Preserve a confirmed contaminant and its evidence, excluding it from operational queries."""
        async with self.session_factory() as session:
            rec = await session.get(SignalRecord, signal_id, with_for_update=True)
            if rec is None or rec.is_quarantined:
                return False
            before = rec.to_dict()
            if not expected or any(before.get(k) != v for k, v in expected.items()):
                raise ValueError(f"Signal #{signal_id} does not match quarantine evidence")
            session.add(self._audit(AuditEventType.SIGNAL_QUARANTINED, signal_id, {"reason": reason, "before": before}))
            rec.is_quarantined = True
            await session.commit()
            return True

    def init_sync(
        self,
        max_retries: int = DEFAULT_DB_MAX_RETRIES,
        retry_delay: float = DEFAULT_DB_RETRY_DELAY,
    ):
        """Synchronous migration check ensuring schema has required tables, columns, and Alembic revisions."""
        for attempt in range(1, max_retries + 1):
            try:
                run_migrations_head(self.db_url)
                return
            except Exception as e:
                if attempt == max_retries:
                    logger.error("Failed to apply migrations after %d attempts: %s", max_retries, e)
                    raise
                logger.warning(
                    "Database migration connection attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt,
                    max_retries,
                    e,
                    retry_delay,
                )
                time.sleep(retry_delay)

    async def init_db(self):
        """Initialize all ORM tables and indexes via Alembic migrations."""
        run_migrations_head(self.db_url)

    async def is_duplicate_recent(
        self,
        contract: str,
        strategy: str,
        hours: int = DEFAULT_DUPLICATE_SIGNAL_WINDOW_HOURS,
    ) -> bool:
        """Check if an active or recent signal was emitted for this contract and strategy within `hours`."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        async with self.session_factory() as session:
            stmt = (
                select(SignalRecord.id)
                .where(*self._scope())
                .where(
                    SignalRecord.contract == contract,
                    SignalRecord.strategy == strategy,
                    SignalRecord.timestamp >= cutoff,
                )
                .order_by(SignalRecord.timestamp.desc())
                .limit(1)
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none() is not None

    async def record_signal(
        self,
        contract: str,
        strategy: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_dollars: float,
        reward_dollars: float | None = None,
        notional_value: float | None = None,
        raw_response: str | None = None,
        status: str = SignalStatus.PENDING,
        asset_class: str = AssetClass.FUTURES,
        quantity: float = 1.0,
    ) -> int:
        """Insert a new trade signal record into the database."""
        async with self.session_factory() as session:
            rec = SignalRecord(
                environment=self.environment,
                execution_mode=self.execution_mode,
                run_id=RUN_ID,
                timestamp=datetime.now(UTC),
                contract=contract,
                strategy=strategy,
                direction=str(direction),
                entry_price=float(entry_price),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_dollars=float(risk_dollars),
                reward_dollars=float(reward_dollars) if reward_dollars is not None else None,
                notional_value=float(notional_value) if notional_value is not None else None,
                raw_response=raw_response,
                status=str(status),
                asset_class=str(asset_class),
                quantity=float(quantity),
            )
            session.add(rec)
            await session.flush()
            session.add(self._audit(AuditEventType.SIGNAL_CREATED, rec.id, rec.to_dict()))
            await session.commit()
            return rec.id

    async def update_telegram_message_id(self, signal_id: int, message_id: int):
        """Associate the Telegram alert message ID with the signal record."""
        async with self.session_factory() as session:
            stmt = (
                update(SignalRecord)
                .where(*self._scope())
                .where(SignalRecord.id == signal_id)
                .values(telegram_message_id=message_id)
            )
            await session.execute(stmt)
            await session.commit()

    async def claim_signal(self, signal_id: int) -> bool:
        """Atomically claim a pending signal before any broker submission."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(SignalRecord)
                .where(*self._scope(), SignalRecord.id == signal_id, SignalRecord.status == SignalStatus.PENDING)
                .values(status=SignalStatus.SUBMITTING)
            )
            claimed = bool(getattr(result, "rowcount", 0) > 0)
            if claimed:
                session.add(self._audit(AuditEventType.EXECUTION_CLAIMED, signal_id, {}))
            await session.commit()
            return claimed

    async def update_signal_status(self, signal_id: int, status: str):
        """Update signal status (e.g. SUBMITTING, EXECUTED, DISMISSED, FAILED)."""
        async with self.session_factory() as session:
            stmt = (
                update(SignalRecord)
                .where(*self._scope())
                .where(SignalRecord.id == signal_id)
                .values(status=str(status))
            )
            await session.execute(stmt)
            await session.commit()

    async def update_signal_execution(
        self,
        signal_id: int,
        broker_order_id: str | None,
        fill_price: float | None = None,
        status: str = SignalStatus.EXECUTED,
        quantity: float | None = None,
        notional_value: float | None = None,
        risk_dollars: float | None = None,
        executed_at: datetime | None = None,
    ):
        """Record broker order ID and execution status, optionally updating fill price, quantity, and notional."""
        async with self.session_factory() as session:
            vals: dict[str, Any] = {
                "status": str(status),
                "broker_order_id": broker_order_id,
            }
            if executed_at is not None:
                vals["executed_at"] = executed_at
            if fill_price is not None:
                vals["entry_price"] = float(fill_price)
            if quantity is not None:
                vals["quantity"] = float(quantity)
            if notional_value is not None:
                vals["notional_value"] = float(notional_value)
            if risk_dollars is not None:
                vals["risk_dollars"] = float(risk_dollars)
            before = await session.get(SignalRecord, signal_id)
            session.add(
                self._audit(
                    AuditEventType.ENTRY_EXECUTION_UPDATED,
                    signal_id,
                    {"before": before.to_dict() if before else None, "after": vals},
                )
            )
            stmt = (
                update(SignalRecord)
                .where(*self._scope())
                .where(
                    SignalRecord.id == signal_id,
                    SignalRecord.status.in_([SignalStatus.PENDING, SignalStatus.SUBMITTING, SignalStatus.EXECUTED]),
                )
                .values(**vals)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_signal_by_id(self, signal_id: int) -> dict[str, Any] | None:
        """Fetch signal record by primary key."""
        async with self.session_factory() as session:
            stmt = select(SignalRecord).where(*self._scope()).where(SignalRecord.id == signal_id)
            res = await session.execute(stmt)
            rec = res.scalar_one_or_none()
            return rec.to_dict() if rec else None

    async def get_active_notional_exposure(self) -> float:
        """Sum of notional_value of currently EXECUTED (active) positions."""
        async with self.session_factory() as session:
            stmt = (
                select(func.sum(SignalRecord.notional_value))
                .where(*self._scope())
                .where(SignalRecord.status == SignalStatus.EXECUTED)
            )
            res = await session.execute(stmt)
            val = res.scalar()
            return float(val) if val is not None else 0.0

    async def get_active_contract_count(self) -> int:
        """Count of active EXECUTED positions."""
        async with self.session_factory() as session:
            stmt = (
                select(func.count(SignalRecord.id))
                .where(*self._scope())
                .where(SignalRecord.status == SignalStatus.EXECUTED)
            )
            res = await session.execute(stmt)
            val = res.scalar()
            return int(val) if val is not None else 0

    async def get_active_position_count(self) -> int:
        """Count of active EXECUTED positions across all asset classes."""
        return await self.get_active_contract_count()

    async def get_recent_signals(self, limit: int = DEFAULT_RECENT_SIGNALS_LIMIT) -> list[dict[str, Any]]:
        """Return the most recent signals ordered by timestamp descending."""
        async with self.session_factory() as session:
            stmt = select(SignalRecord).where(*self._scope()).order_by(SignalRecord.timestamp.desc()).limit(limit)
            res = await session.execute(stmt)
            records = res.scalars().all()
            return [r.to_dict() for r in records]

    async def get_active_positions(self) -> list[dict[str, Any]]:
        """Fetch all currently active (EXECUTED) positions."""
        async with self.session_factory() as session:
            stmt = (
                select(SignalRecord)
                .where(*self._scope())
                .where(SignalRecord.status == SignalStatus.EXECUTED)
                .order_by(SignalRecord.timestamp.asc())
            )
            res = await session.execute(stmt)
            records = res.scalars().all()
            return [r.to_dict() for r in records]

    async def close_position(
        self,
        signal_id: int,
        exit_price: float,
        exit_reason: str | ExitReason,
        realized_pnl: float,
        status: str | SignalStatus,
        broker_exit_order_id: str | None = None,
        exit_timestamp: datetime | None = None,
    ) -> bool:
        """Close an active position and record exit metrics."""
        now_utc = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = (
                update(SignalRecord)
                .where(*self._scope())
                .where(
                    SignalRecord.id == signal_id,
                    SignalRecord.status == SignalStatus.EXECUTED,
                )
                .values(
                    status=str(status),
                    exit_price=float(exit_price),
                    exit_timestamp=exit_timestamp or now_utc,
                    broker_exit_order_id=broker_exit_order_id,
                    realized_pnl=float(realized_pnl),
                    exit_reason=str(exit_reason),
                )
            )
            res = await session.execute(stmt)
            rowcount = getattr(res, "rowcount", 0)
            if rowcount > 0:
                session.add(
                    self._audit(
                        AuditEventType.POSITION_CLOSED,
                        signal_id,
                        {
                            "exit_price": exit_price,
                            "exit_reason": str(exit_reason),
                            "realized_pnl": realized_pnl,
                            "broker_exit_order_id": broker_exit_order_id,
                            "exit_timestamp": exit_timestamp or now_utc,
                        },
                    )
                )
            await session.commit()
            return bool(rowcount > 0)

    async def update_position_stop(
        self,
        signal_id: int,
        new_stop: float,
        raw_response: str | None = None,
    ) -> bool:
        """Update stop loss price for an active position (e.g. breakeven or trailing stop)."""
        async with self.session_factory() as session:
            vals: dict[str, Any] = {"stop_loss": float(new_stop)}
            if raw_response:
                vals["raw_response"] = raw_response
            stmt = (
                update(SignalRecord)
                .where(*self._scope())
                .where(
                    SignalRecord.id == signal_id,
                    SignalRecord.status == SignalStatus.EXECUTED,
                )
                .values(**vals)
            )
            res = await session.execute(stmt)
            await session.commit()
            rowcount = getattr(res, "rowcount", 0)
            return bool(rowcount > 0)

    async def get_closed_positions_stats(self) -> dict[str, Any]:
        """Aggregate closed trade statistics (P&L, win rate, profit factor, trade list)."""
        async with self.session_factory() as session:
            stmt = (
                select(SignalRecord)
                .where(*self._scope())
                .where(SignalRecord.status.in_([SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS]))
                .order_by(SignalRecord.exit_timestamp.desc())
            )
            res = await session.execute(stmt)
            closed = res.scalars().all()

            unverified_count = 0
            if self.execution_mode.startswith(ExecutionMode.ALPACA):
                confirmed = [r for r in closed if r.executed_at and r.broker_exit_order_id]
                unverified_count = len(closed) - len(confirmed)
                closed = confirmed

            total_trades = len(closed)
            wins = sum(
                1 for r in closed if r.status == SignalStatus.CLOSED_WIN or (r.realized_pnl and r.realized_pnl > 0)
            )
            losses = total_trades - wins
            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

            total_pnl = sum(r.realized_pnl or 0.0 for r in closed)
            gross_profit = sum((r.realized_pnl or 0.0) for r in closed if (r.realized_pnl or 0.0) > 0)
            gross_loss = abs(sum((r.realized_pnl or 0.0) for r in closed if (r.realized_pnl or 0.0) < 0))

            if gross_loss > 0:
                profit_factor = round(gross_profit / gross_loss, 2)
            elif gross_profit > 0:
                profit_factor = 999.99
            else:
                profit_factor = 0.0

            return {
                "unverified_closed_count": unverified_count,
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 2),
                "total_pnl": round(total_pnl, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "profit_factor": profit_factor,
                "trades": [r.to_dict() for r in closed],
            }

    async def get_state(self, key: str) -> str | None:
        """Retrieve system state value for given key."""
        async with self.session_factory() as session:
            stmt = select(SystemStateRecord.value).where(SystemStateRecord.key == key)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def set_state(self, key: str, value: str) -> None:
        """Insert or update system state key-value pair."""
        async with self.session_factory() as session:
            stmt = select(SystemStateRecord).where(SystemStateRecord.key == key)
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(UTC)
            else:
                new_rec = SystemStateRecord(key=key, value=value, updated_at=datetime.now(UTC))
                session.add(new_rec)
            await session.commit()

    async def clear_all_signals(self) -> int:
        """Clear all signal and position history from the database, resetting autoincrement sequence."""
        async with self.session_factory() as session:
            count_res = await session.execute(select(func.count()).select_from(SignalRecord))
            total_records = count_res.scalar() or 0
            await session.execute(delete(SignalRecord))
            if "postgresql" in self.db_url:
                with contextlib.suppress(Exception):
                    await session.execute(text("ALTER SEQUENCE signals_id_seq RESTART WITH 1"))
            elif self.db_path or "sqlite" in self.db_url:
                with contextlib.suppress(Exception):
                    await session.execute(text("DELETE FROM sqlite_sequence WHERE name='signals'"))
            await session.commit()
            logger.info("Cleared %d signal records from database.", total_records)
            return total_records
