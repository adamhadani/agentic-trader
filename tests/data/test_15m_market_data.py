from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from agentic_trader.data.market_data import ContractMarketData, MarketDataFetcher


def _generate_synthetic_candles(n_bars: int = 50, start_dt: datetime | None = None) -> pd.DataFrame:
    base = start_dt or datetime(2026, 9, 15, 9, 30, tzinfo=UTC)
    dates = [base + timedelta(minutes=15 * i) for i in range(n_bars)]
    np.random.seed(42)
    closes = 500.0 + np.cumsum(np.random.normal(0.2, 1.0, n_bars))
    highs = closes + np.random.uniform(0.5, 1.5, n_bars)
    lows = closes - np.random.uniform(0.5, 1.5, n_bars)
    opens = (highs + lows) / 2.0
    vols = np.random.randint(5000, 25000, n_bars)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=pd.DatetimeIndex(dates),
    )


def test_contract_market_data_backward_compatibility():
    """Verify ContractMarketData supports 15m data while maintaining backward compatibility."""
    df_15m = _generate_synthetic_candles(30)
    df_1h = _generate_synthetic_candles(20)
    df_4h = _generate_synthetic_candles(15)
    df_daily = _generate_synthetic_candles(10)

    # 1. Standard construction with fifteen_minute
    cmd = ContractMarketData(
        contract="SPY",
        ticker="SPY",
        daily=df_daily,
        four_hour=df_4h,
        hourly=df_1h,
        fifteen_minute=df_15m,
    )
    assert not cmd.fifteen_minute.empty
    assert len(cmd.fifteen_minute) == 30
    assert cmd.one_hour is cmd.hourly

    # 2. Legacy construction with symbol and one_hour
    cmd_legacy = ContractMarketData(
        symbol="QQQ",
        daily=df_daily,
        four_hour=df_4h,
        one_hour=df_1h,
    )
    assert cmd_legacy.contract == "QQQ"
    assert cmd_legacy.ticker == "QQQ"
    assert cmd_legacy.hourly is cmd_legacy.one_hour
    assert cmd_legacy.fifteen_minute.empty


def test_compute_intraday_indicators_on_15m():
    """Verify indicators (EMA, ATR, RSI, Squeeze, Bands) compute properly on 15m bars."""
    fetcher = MarketDataFetcher()
    raw_15m = _generate_synthetic_candles(60)

    df_ind = fetcher.compute_intraday_indicators(raw_15m)
    assert not df_ind.empty
    assert "EMA_20" in df_ind.columns
    assert "EMA_50" in df_ind.columns
    assert "ATR_14" in df_ind.columns
    assert "RSI_14" in df_ind.columns
    assert "BB_Upper" in df_ind.columns
    assert "KC_Upper" in df_ind.columns
    assert "Squeeze" in df_ind.columns
    assert "Squeeze_Count" in df_ind.columns
    assert "Volume_SMA_20" in df_ind.columns

    # Verify ATR is positive and reasonable
    assert (df_ind["ATR_14"].dropna() > 0).all()
    # Verify RSI is within [0, 100]
    valid_rsi = df_ind["RSI_14"].dropna()
    assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()


def test_fetch_data_populates_fifteen_minute():
    """Verify MarketDataFetcher.fetch_data fetches and computes 15m data alongside 1h, 4h, and daily."""
    mock_provider = MagicMock()
    df_bars = _generate_synthetic_candles(50)
    mock_provider.fetch_bars.return_value = df_bars

    fetcher = MarketDataFetcher(provider=mock_provider)
    data = fetcher.fetch_data(contract="NVDA", ticker="NVDA", include_fifteen_min=True)

    assert not data.daily.empty
    assert not data.four_hour.empty
    assert not data.hourly.empty
    assert not data.fifteen_minute.empty
    assert len(data.fifteen_minute) == 50
    assert "ATR_14" in data.fifteen_minute.columns
