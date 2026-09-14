from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, Direction, SignalStatus, StrategyType
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.storage.db import SignalDatabase


@pytest.fixture(autouse=True, scope="session")
def isolate_test_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Ensure all unit test executions use an isolated test database and NEVER touch production signals.db."""
    test_db_dir = tmp_path_factory.mktemp("isolated_test_db")
    test_db_file = test_db_dir / "test_signals.db"
    os.environ["DB_NAME"] = "test_signals"
    os.environ["DB_PATH"] = str(test_db_file)


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
    notifier.send_signal_alert = AsyncMock(return_value=True)
    notifier.send_execution_alert = AsyncMock(return_value=True)
    notifier.send_trailing_stop_alert = AsyncMock(return_value=True)
    notifier.send_exit_alert = AsyncMock(return_value=True)
    notifier.is_configured = MagicMock(return_value=True)
    return notifier
