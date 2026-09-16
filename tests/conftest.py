from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import psycopg2
import pytest
from curl_cffi import AsyncCurl, Curl
from psycopg2.extensions import make_dsn, parse_dsn
from pytest_socket import SocketBlockedError
from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url

from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, Direction, SignalStatus, StrategyType
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.runtime import validate_test_database
from agentic_trader.storage.db import SignalDatabase


# No application imports read dotenv. Remove inherited credentials before collection,
# including for subprocess tests; each test gets independent database/config state.
for _key in list(os.environ):
    if any(part in _key for part in ("API_KEY", "API_SECRET", "TOKEN", "PASSWORD")) or _key.startswith(
        ("ALPACA_", "APCA_", "TRADOVATE_", "LANGCHAIN_", "LANGSMITH_")
    ):
        os.environ.pop(_key, None)
os.environ.update(COPILOT_ENV="test", COPILOT_ENV_FILE="", EXECUTION_MODE="paper")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("DB_PATH", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
TEST_CONFIG = Path(__file__).parent / "fixtures" / "config.yaml"
os.environ["COPILOT_CONFIG"] = str(TEST_CONFIG)


def pytest_addoption(parser):
    parser.addoption(
        "--alpaca-transport",
        choices=("socket", "memory"),
        default="socket",
        help="Real SDK contract harness transport; memory does not replace TCP/WebSocket verification.",
    )
    parser.addoption(
        "--run-postgres",
        action="store_true",
        default=False,
        help="Run integration tests on TEST_POSTGRES_URL (disposable test_ database only).",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-postgres"):
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="PostgreSQL integration tests require --run-postgres."))


@pytest.fixture(autouse=True)
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    root = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("COPILOT_ENV", "test")
    monkeypatch.setenv("COPILOT_TEST_ROOT", str(root))
    monkeypatch.setenv("COPILOT_CONFIG", str(TEST_CONFIG))
    monkeypatch.setenv("COPILOT_ENV_FILE", "")
    monkeypatch.setenv("DB_NAME", "test_signals")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "signals.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PROMOTED_ALPHAS_PATH", str(tmp_path / "promoted_alphas.yaml"))

    # SQLAlchemy's native drivers (notably libpq) can bypass Python socket guards.
    # Protect even explicit URLs and code that clears os.environ mid-test.
    def guard_connect(dialect, conn_rec, cargs, cparams):
        if dialect.name == "sqlite":
            validate_test_database(f"sqlite:///{cargs[0]}", root=root)
        elif dialect.name == "postgresql":
            if cargs:
                dsn = parse_dsn(cargs[0])
                url = make_url("postgresql://").set(
                    username=dsn.get("user"),
                    password=dsn.get("password"),
                    host=dsn.get("host"),
                    port=int(dsn["port"]) if dsn.get("port") else None,
                    database=dsn.get("dbname"),
                )
            else:
                url = make_url("postgresql://").set(
                    username=cparams.get("user"),
                    password=cparams.get("password"),
                    host=cparams.get("host"),
                    port=cparams.get("port"),
                    database=cparams.get("database") or cparams.get("dbname"),
                )
            validate_test_database(url.render_as_string(hide_password=False), root=root)
        else:
            raise AssertionError("Unit tests cannot use external database drivers.")

    # yfinance uses libcurl, which bypasses Python sockets. Block its native I/O too.
    def block_native_network(*args, **kwargs):
        raise SocketBlockedError("Native libcurl network access is disabled in unit tests.")

    monkeypatch.setattr(Curl, "perform", block_native_network)
    monkeypatch.setattr(AsyncCurl, "add_handle", block_native_network)

    original_pg_connect = psycopg2.connect

    def guarded_pg_connect(dsn=None, *args, **kwargs):
        values = parse_dsn(
            make_dsn(
                dsn or "", **{k: v for k, v in kwargs.items() if k not in ("connection_factory", "cursor_factory")}
            )
        )
        url = make_url("postgresql://").set(
            username=values.get("user"),
            password=values.get("password"),
            host=values.get("host"),
            port=int(values["port"]) if values.get("port") else None,
            database=values.get("dbname"),
        )
        validate_test_database(url.render_as_string(hide_password=False), root=root)
        return original_pg_connect(dsn, *args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", guarded_pg_connect)
    original_sqlite_connect = sqlite3.connect

    def guarded_sqlite_connect(database, *args, **kwargs):
        validate_test_database(f"sqlite:///{database}", root=root)
        return original_sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_sqlite_connect)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", guarded_sqlite_connect)
    event.listen(Engine, "do_connect", guard_connect)
    yield
    event.remove(Engine, "do_connect", guard_connect)


@pytest.fixture
def app_config() -> AppConfig:
    """Provide a standard AppConfig instance loaded from defaults (isolated to test DB)."""
    return load_config()


@pytest.fixture
def config(app_config: AppConfig) -> AppConfig:
    """Alias for app_config for backward-compatible test fixtures."""
    return app_config


@pytest.fixture
def temp_db(tmp_path: Any) -> SignalDatabase:
    """Provide a temporary SQLite SignalDatabase instance isolated to tmp_path."""
    db_file = tmp_path / "test_signals.db"
    return SignalDatabase(str(db_file))


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """Generate a clean synthetic 60-bar 4h OHLCV DataFrame for indicator/backtest tests."""
    base_date = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    dates = [base_date + timedelta(hours=4 * i) for i in range(60)]

    np.random.seed(42)
    closes = 5000.0 + np.cumsum(np.random.normal(1.5, 5.0, 60))
    highs = closes + np.random.uniform(2.0, 8.0, 60)
    lows = closes - np.random.uniform(2.0, 8.0, 60)
    opens = (highs + lows) / 2.0
    volumes = np.random.randint(1000, 50000, 60)

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=pd.DatetimeIndex(dates),
    )
    return df


@pytest.fixture
def sample_contract_market_data(sample_ohlcv_df: pd.DataFrame) -> ContractMarketData:
    """Provide a ContractMarketData object populated with synthetic data."""
    return ContractMarketData(
        symbol="/MES",
        daily=sample_ohlcv_df.resample("1D")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(),
        four_hour=sample_ohlcv_df,
        one_hour=sample_ohlcv_df,
    )


@pytest.fixture
def sample_signal_dict() -> dict[str, Any]:
    """Provide a canonical trade signal dictionary."""
    return {
        "id": 1,
        "contract": "/MES",
        "strategy": StrategyType.TREND_PULLBACK.value,
        "direction": Direction.LONG.value,
        "entry_price": 5800.0,
        "stop_loss": 5750.0,
        "take_profit": 5900.0,
        "risk_dollars": 250.0,
        "reward_dollars": 500.0,
        "notional_value": 29000.0,
        "effective_leverage": 0.29,
        "status": SignalStatus.PENDING.value,
        "quantity": 1.0,
        "asset_class": AssetClass.FUTURES.value,
        "timestamp": "2026-09-13 12:00:00",
    }


@pytest.fixture
def mock_notifier() -> MagicMock:
    """Mock TelegramNotifier with AsyncMock coroutine methods."""
    notifier = MagicMock()
    notifier.send_message = AsyncMock(return_value=True)
    notifier.send_signal_alert = AsyncMock(return_value=True)
    notifier.send_execution_alert = AsyncMock(return_value=True)
    notifier.send_trailing_stop_alert = AsyncMock(return_value=True)
    notifier.send_exit_alert = AsyncMock(return_value=True)
    notifier.is_configured = MagicMock(return_value=True)
    return notifier
