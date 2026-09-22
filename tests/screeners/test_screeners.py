import pandas as pd
import pytest

from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.screeners.strategies import StrategyEngine


@pytest.fixture
def config():
    return load_config()


def create_mock_market_data(
    daily_close=5800.0,
    daily_ema50=5700.0,
    daily_ema200=5500.0,
    four_h_close=5800.0,
    four_h_ema20=5800.0,
    four_h_rsi_seq=None,
    four_h_atr=20.0,
    squeeze_bars=0,
    breakout=False,
):
    if four_h_rsi_seq is None:
        four_h_rsi_seq = [43.0, 42.0, 41.0, 44.0]
    # Daily DataFrame
    dates_daily = pd.date_range("2026-01-01", periods=20, freq="1D")

    df_daily = pd.DataFrame(
        {
            "Open": [daily_close] * 20,
            "High": [daily_close + 10] * 20,
            "Low": [daily_close - 10] * 20,
            "Close": [daily_close] * 20,
            "Volume": [10000.0] * 20,
            "EMA_50": [daily_ema50] * 20,
            "EMA_200": [daily_ema200] * 20,
            "ATR_14": [25.0] * 20,
        },
        index=dates_daily,
    )

    # 4H DataFrame
    dates_4h = pd.date_range("2026-01-01", periods=30, freq="4h")
    closes = [four_h_close] * 30
    df_4h = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 5 for c in closes],
            "Low": [c - 5 for c in closes],
            "Close": closes,
            "Volume": [5000.0] * 30,
            "EMA_20": [four_h_ema20] * 30,
            "EMA_50": [daily_ema50] * 30,
            "EMA_200": [daily_ema200] * 30,
            "RSI_14": [50.0] * 26 + four_h_rsi_seq,
            "ATR_14": [four_h_atr] * 30,
            "BB_Upper": [four_h_close + 10] * 30,
            "BB_Middle": [four_h_close] * 30,
            "BB_Lower": [four_h_close - 10] * 30,
            "KC_Upper": [four_h_close + 15] * 30,
            "KC_Middle": [four_h_close] * 30,
            "KC_Lower": [four_h_close - 15] * 30,
            "Squeeze": [False] * 30,
            "Squeeze_Count": [0] * 30,
            "Volume_SMA_20": [5000.0] * 30,
        },
        index=dates_4h,
    )

    if squeeze_bars > 0:
        for i in range(squeeze_bars):
            row_idx = -2 - i
            df_4h.iloc[row_idx, df_4h.columns.get_loc("Squeeze")] = True
            df_4h.iloc[row_idx, df_4h.columns.get_loc("Squeeze_Count")] = squeeze_bars - i

    if breakout:
        # Breakout on last candle
        df_4h.iloc[-1, df_4h.columns.get_loc("Close")] = four_h_close + 15.0  # Above BB_Upper
        df_4h.iloc[-1, df_4h.columns.get_loc("Volume")] = 10000.0  # 2x Volume SMA

    return ContractMarketData(
        contract="/MES",
        ticker="MES=F",
        daily=df_daily,
        four_hour=df_4h,
        hourly=df_4h,
    )


def test_trend_pullback_long_trigger(config):
    engine = StrategyEngine(config)
    # Bullish trend: Daily Close (5800) > EMA50 (5700) > EMA200 (5500)
    # RSI dipped to 41 and recovered to 44. Price is right at EMA20 (5800)
    data = create_mock_market_data(
        daily_close=5800.0,
        daily_ema50=5700.0,
        daily_ema200=5500.0,
        four_h_close=5800.0,
        four_h_ema20=5800.0,
        four_h_rsi_seq=[48.0, 42.0, 41.0, 44.0],
        four_h_atr=20.0,
    )
    candidate = engine.check_trend_pullback(data)
    assert candidate is not None
    assert candidate.direction == "LONG"
    assert candidate.strategy == "TREND_PULLBACK"
    assert candidate.contract == "/MES"
    assert 0.0 < candidate.setup_quality <= 1.0


def test_trend_pullback_no_trigger_when_far_from_ema(config):
    engine = StrategyEngine(config)
    # Price 5850 is 50 pts away from EMA 5800, which is > 0.5 * ATR(20) = 10 pts
    data = create_mock_market_data(
        daily_close=5800.0,
        daily_ema50=5700.0,
        daily_ema200=5500.0,
        four_h_close=5850.0,
        four_h_ema20=5800.0,
        four_h_rsi_seq=[48.0, 42.0, 41.0, 44.0],
        four_h_atr=20.0,
    )
    candidate = engine.check_trend_pullback(data)
    assert candidate is None


def test_squeeze_breakout_trigger(config):
    engine = StrategyEngine(config)
    data = create_mock_market_data(
        squeeze_bars=6,
        breakout=True,
    )
    candidate = engine.check_squeeze_breakout(data, timeframe="4h")
    assert candidate is not None
    assert candidate.strategy == "SQUEEZE_BREAKOUT"
    assert candidate.direction == "LONG"
    assert 0.0 < candidate.setup_quality <= 1.0


def test_equity_screener_candidate(config):
    engine = StrategyEngine(config)
    data = create_mock_market_data(
        daily_close=500.0,
        daily_ema50=490.0,
        daily_ema200=470.0,
        four_h_close=500.0,
        four_h_ema20=500.0,
        four_h_rsi_seq=[48.0, 42.0, 41.0, 44.0],
        four_h_atr=5.0,
    )
    data.contract = "SPY"
    data.ticker = "SPY"

    candidates = engine.scan_contract(data, asset_class=AssetClass.EQUITY)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.contract == "SPY"
    assert c.symbol == "SPY"
    assert c.asset_class == AssetClass.EQUITY
    assert c.direction == "LONG"
    assert 0.0 < c.setup_quality <= 1.0
