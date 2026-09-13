import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agentic_trader.constants import AssetClass, ExitReason, SignalStatus
from agentic_trader.storage.models import Base, SignalRecord


class SignalDatabase:
    """
    SQLAlchemy 2.0 Async ORM Persistence Layer.
    Provides database-agnostic operations (SQLite, PostgreSQL, MySQL)
    with Alembic readiness and automatic schema initialization.
    """

    def __init__(self, db_path: str | None = None, db_url: str | None = None):
        if db_url:
            self.db_url = db_url
            self.db_path = None
        elif db_path:
            self.db_path = str(Path(db_path).resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        else:
            raise ValueError("Either db_path or db_url must be provided.")

        self.engine: AsyncEngine = create_async_engine(self.db_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # Synchronously initialize tables and perform SQLite column migrations if needed
        if self.db_path:
            self.init_sync()

    def init_sync(self):
        """Synchronous migration check to ensure existing SQLite database has required columns."""
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            # Let sqlite create tables if schema is not yet present
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    contract TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    risk_dollars REAL NOT NULL,
                    reward_dollars REAL,
                    notional_value REAL,
                    status TEXT DEFAULT 'PENDING',
                    telegram_message_id INTEGER,
                    raw_response TEXT,
                    exit_price REAL,
                    exit_timestamp DATETIME,
                    realized_pnl REAL,
                    exit_reason TEXT,
                    broker_order_id TEXT,
                    asset_class TEXT DEFAULT 'FUTURES',
                    quantity REAL DEFAULT 1.0
                );
                """
            )
            cursor = conn.execute("PRAGMA table_info(signals)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            for col, col_type in [
                ("exit_price", "REAL"),
                ("exit_timestamp", "DATETIME"),
                ("realized_pnl", "REAL"),
                ("exit_reason", "TEXT"),
                ("broker_order_id", "TEXT"),
                ("asset_class", "TEXT"),
                ("quantity", "REAL DEFAULT 1.0"),
            ]:
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {col_type}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recent_signals ON signals(contract, strategy, timestamp);")
            conn.commit()

    async def init_db(self):
        """Asynchronously initialize all ORM tables and indexes."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def is_duplicate_recent(self, contract: str, strategy: str, hours: int = 12) -> bool:
        """Check if an active or recent signal was emitted for this contract and strategy within `hours`."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        async with self.session_factory() as session:
            stmt = (
                select(SignalRecord.id)
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
            await session.commit()
            return rec.id

    async def update_telegram_message_id(self, signal_id: int, message_id: int):
        """Associate the Telegram alert message ID with the signal record."""
        async with self.session_factory() as session:
            stmt = update(SignalRecord).where(SignalRecord.id == signal_id).values(telegram_message_id=message_id)
            await session.execute(stmt)
            await session.commit()

    async def update_signal_status(self, signal_id: int, status: str):
        """Update signal status (e.g. SUBMITTING, EXECUTED, DISMISSED, FAILED)."""
        async with self.session_factory() as session:
            stmt = update(SignalRecord).where(SignalRecord.id == signal_id).values(status=str(status))
            await session.execute(stmt)
            await session.commit()

    async def update_signal_execution(
        self,
        signal_id: int,
        broker_order_id: str | None,
        fill_price: float | None = None,
        status: str = SignalStatus.EXECUTED,
    ):
        """Record broker order ID and execution status, optionally updating entry price to actual fill."""
        async with self.session_factory() as session:
            vals: dict[str, Any] = {
                "status": str(status),
                "broker_order_id": broker_order_id,
            }
            if fill_price is not None:
                vals["entry_price"] = float(fill_price)
            stmt = update(SignalRecord).where(SignalRecord.id == signal_id).values(**vals)
            await session.execute(stmt)
            await session.commit()

    async def get_signal_by_id(self, signal_id: int) -> dict[str, Any] | None:
        """Fetch signal record by primary key."""
        async with self.session_factory() as session:
            stmt = select(SignalRecord).where(SignalRecord.id == signal_id)
            res = await session.execute(stmt)
            rec = res.scalar_one_or_none()
            return rec.to_dict() if rec else None

    async def get_active_notional_exposure(self) -> float:
        """Sum of notional_value of currently EXECUTED (active) positions."""
        async with self.session_factory() as session:
            stmt = select(func.sum(SignalRecord.notional_value)).where(SignalRecord.status == SignalStatus.EXECUTED)
            res = await session.execute(stmt)
            val = res.scalar()
            return float(val) if val is not None else 0.0

    async def get_active_contract_count(self) -> int:
        """Count of active EXECUTED positions."""
        async with self.session_factory() as session:
            stmt = select(func.count(SignalRecord.id)).where(SignalRecord.status == SignalStatus.EXECUTED)
            res = await session.execute(stmt)
            val = res.scalar()
            return int(val) if val is not None else 0

    async def get_active_position_count(self) -> int:
        """Count of active EXECUTED positions across all asset classes."""
        return await self.get_active_contract_count()

    async def get_recent_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent signals ordered by timestamp descending."""
        async with self.session_factory() as session:
            stmt = select(SignalRecord).order_by(SignalRecord.timestamp.desc()).limit(limit)
            res = await session.execute(stmt)
            records = res.scalars().all()
            return [r.to_dict() for r in records]

    async def get_active_positions(self) -> list[dict[str, Any]]:
        """Fetch all currently active (EXECUTED) positions."""
        async with self.session_factory() as session:
            stmt = (
                select(SignalRecord)
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
    ) -> bool:
        """Close an active position and record exit metrics."""
        now_utc = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = (
                update(SignalRecord)
                .where(
                    SignalRecord.id == signal_id,
                    SignalRecord.status == SignalStatus.EXECUTED,
                )
                .values(
                    status=str(status),
                    exit_price=float(exit_price),
                    exit_timestamp=now_utc,
                    realized_pnl=float(realized_pnl),
                    exit_reason=str(exit_reason),
                )
            )
            res = await session.execute(stmt)
            await session.commit()
            rowcount = getattr(res, "rowcount", 0)
            return bool(rowcount > 0)
