import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.migrations import (
    downgrade_migrations,
    get_alembic_config,
    get_current_revision,
    get_history,
    run_migrations_head,
    to_sync_url,
)


def test_to_sync_url():
    assert to_sync_url("sqlite+aiosqlite:///data/signals.db") == "sqlite:///data/signals.db"
    assert to_sync_url("postgresql+asyncpg://user:pass@localhost/db") == "postgresql://user:pass@localhost/db"
    assert to_sync_url("sqlite:///data/signals.db") == "sqlite:///data/signals.db"


def test_get_alembic_config(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    cfg = get_alembic_config(db_url)
    assert cfg.get_main_option("sqlalchemy.url") == db_url
    assert "alembic" in (cfg.get_main_option("script_location") or "")


def test_migrations_fresh_lifecycle(tmp_path: Path):
    db_file = tmp_path / "fresh_signals.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    # Initially before migration
    assert get_current_revision(db_url) is None

    # Upgrade to head
    run_migrations_head(db_url)
    assert get_current_revision(db_url) == "001_initial"

    # Verify SQLite schema inspection
    with sqlite3.connect(db_file) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "signals" in tables
        assert "alembic_version" in tables

        cursor = conn.execute("PRAGMA table_info(signals)")
        cols = {row[1] for row in cursor.fetchall()}
        expected_cols = {
            "id",
            "timestamp",
            "contract",
            "strategy",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "risk_dollars",
            "reward_dollars",
            "notional_value",
            "status",
            "telegram_message_id",
            "raw_response",
            "exit_price",
            "exit_timestamp",
            "realized_pnl",
            "exit_reason",
            "broker_order_id",
            "asset_class",
            "quantity",
        }
        assert expected_cols.issubset(cols)

    # Downgrade to base
    downgrade_migrations("base", db_url)
    assert get_current_revision(db_url) is None

    with sqlite3.connect(db_file) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "signals" not in tables

    # Re-upgrade to head
    run_migrations_head(db_url)
    assert get_current_revision(db_url) == "001_initial"


def test_get_history(tmp_path: Path):
    db_file = tmp_path / "hist.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    history = get_history(db_url)
    assert len(history) >= 1
    revisions = [h["revision"] for h in history]
    assert "001_initial" in revisions


@pytest.mark.asyncio
async def test_signal_database_auto_migration(tmp_path: Path):
    db_file = tmp_path / "auto_migrated.db"
    db = SignalDatabase(db_path=str(db_file))

    # Verify migration stamped
    assert get_current_revision(db.db_url) == "001_initial"

    # Verify write and read operations
    sig_id = await db.record_signal(
        contract="SPY",
        strategy="TEST_MIGRATION",
        direction="LONG",
        entry_price=500.0,
        stop_loss=495.0,
        take_profit=510.0,
        risk_dollars=250.0,
        reward_dollars=500.0,
        notional_value=25000.0,
        quantity=50.0,
        asset_class="EQUITY",
    )
    assert sig_id > 0

    record = await db.get_signal_by_id(sig_id)
    assert record is not None
    assert record["contract"] == "SPY"
    assert record["quantity"] == 50.0
    assert record["asset_class"] == "EQUITY"


def test_cli_db_commands(tmp_path: Path):
    db_file = tmp_path / "cli_test.db"
    env = {"DB_PATH": str(db_file)}

    run_env = os.environ.copy()
    run_env.update(env)

    # Test copilot db current (fresh DB will be migrated or checked)
    res = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "current"],
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Current database revision:" in res.stdout

    # Test copilot db history
    res_hist = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "history"],
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_hist.returncode == 0
    assert "001_initial" in res_hist.stdout

    # Test copilot db upgrade
    res_up = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "upgrade"],
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_up.returncode == 0
    assert "Database upgraded successfully" in res_up.stdout
