import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite


SCHEMA = """
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
    broker_order_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_recent_signals
ON signals(contract, strategy, timestamp);
"""


class SignalDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_sync()

    def init_sync(self):
        """Synchronously initialize schema if not present and migrate new columns."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            cursor = conn.execute("PRAGMA table_info(signals)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            for col, col_type in [
                ("exit_price", "REAL"),
                ("exit_timestamp", "DATETIME"),
                ("realized_pnl", "REAL"),
                ("exit_reason", "TEXT"),
                ("broker_order_id", "TEXT"),
            ]:
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {col_type}")
            conn.commit()

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            cursor = await db.execute("PRAGMA table_info(signals)")
            rows = await cursor.fetchall()
            existing_cols = {row[1] for row in rows}
            for col, col_type in [
                ("exit_price", "REAL"),
                ("exit_timestamp", "DATETIME"),
                ("realized_pnl", "REAL"),
                ("exit_reason", "TEXT"),
                ("broker_order_id", "TEXT"),
            ]:
                if col not in existing_cols:
                    await db.execute(f"ALTER TABLE signals ADD COLUMN {col} {col_type}")
            await db.commit()

    async def is_duplicate_recent(self, contract: str, strategy: str, hours: int = 12) -> bool:
        """Check if a signal for the same contract and strategy was emitted within the last `hours`."""
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id FROM signals
                WHERE contract = ? AND strategy = ? AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (contract, strategy, cutoff),
            )
            row = await cursor.fetchone()
            return row is not None

    async def record_signal(
        self,
        contract: str,
        strategy: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_dollars: float,
        reward_dollars: float,
        notional_value: float,
        raw_response: str | None = None,
        status: str = "PENDING",
    ) -> int:
        utc_now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO signals (
                    timestamp, contract, strategy, direction,
                    entry_price, stop_loss, take_profit,
                    risk_dollars, reward_dollars, notional_value,
                    status, raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now,
                    contract,
                    strategy,
                    direction,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_dollars,
                    reward_dollars,
                    notional_value,
                    status,
                    raw_response,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def update_telegram_message_id(self, signal_id: int, message_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE signals SET telegram_message_id = ? WHERE id = ?",
                (message_id, signal_id),
            )
            await db.commit()

    async def update_signal_status(self, signal_id: int, status: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE signals SET status = ? WHERE id = ?",
                (status, signal_id),
            )
            await db.commit()

    async def update_signal_execution(
        self,
        signal_id: int,
        broker_order_id: str | None,
        fill_price: float | None = None,
        status: str = "EXECUTED",
    ):
        """Record broker order ID and execution status, optionally updating entry price to actual fill."""
        async with aiosqlite.connect(self.db_path) as db:
            if fill_price is not None:
                await db.execute(
                    """
                    UPDATE signals
                    SET status = ?, broker_order_id = ?, entry_price = ?
                    WHERE id = ?
                    """,
                    (status, broker_order_id, fill_price, signal_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE signals
                    SET status = ?, broker_order_id = ?
                    WHERE id = ?
                    """,
                    (status, broker_order_id, signal_id),
                )
            await db.commit()

    async def get_signal_by_id(self, signal_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_active_notional_exposure(self) -> float:
        """Sum of notional_value of currently EXECUTED (active) positions."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT SUM(notional_value) FROM signals WHERE status = 'EXECUTED'")
            row = await cursor.fetchone()
            if row and row[0] is not None:
                return float(row[0])
            return 0.0

    async def get_active_contract_count(self) -> int:
        """Count of active EXECUTED positions."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE status = 'EXECUTED'")
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def get_recent_signals(self, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_active_positions(self) -> list[dict]:
        """Fetch all currently active (EXECUTED) positions."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM signals WHERE status = 'EXECUTED' ORDER BY timestamp ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def close_position(
        self,
        signal_id: int,
        exit_price: float,
        exit_reason: str,
        realized_pnl: float,
        status: str,
    ) -> bool:
        """Close an active position and record exit metrics."""
        utc_now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE signals
                SET status = ?,
                    exit_price = ?,
                    exit_timestamp = ?,
                    realized_pnl = ?,
                    exit_reason = ?
                WHERE id = ? AND status = 'EXECUTED'
                """,
                (status, exit_price, utc_now, realized_pnl, exit_reason, signal_id),
            )
            await db.commit()
            return bool(cursor.rowcount > 0)
