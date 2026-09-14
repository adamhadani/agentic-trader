import numpy as np
import pandas as pd
import pytest

from agentic_trader.screeners.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_keltner_channels,
    calculate_rsi,
    consecutive_squeeze_count,
    detect_squeeze,
)


@pytest.fixture
def sample_ohlcv():
    dates = pd.date_range("2026-01-01", periods=50, freq="4h")
    # Upward trending series
    base = np.linspace(100, 150, 50)
    high = base + 2.0
    low = base - 2.0
    close = base + 0.5
    volume = np.full(50, 1000.0)
    return pd.DataFrame(
        {"High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def test_calculate_ema(sample_ohlcv):
    ema = calculate_ema(sample_ohlcv["Close"], span=20)
    assert len(ema) == 50
    assert not ema.isna().any()
    # In an upward trend, EMA lags behind current close
    assert ema.iloc[-1] < sample_ohlcv["Close"].iloc[-1]


def test_calculate_rsi(sample_ohlcv):
    rsi = calculate_rsi(sample_ohlcv["Close"], period=14)
    assert len(rsi) == 50
    # Consistently increasing prices should give high RSI (> 70)
    assert rsi.iloc[-1] > 70.0


def test_calculate_atr(sample_ohlcv):
    atr = calculate_atr(sample_ohlcv["High"], sample_ohlcv["Low"], sample_ohlcv["Close"], period=14)
    assert len(atr) == 50
    # High - Low is 4.0, so ATR should be around 4.0
    assert abs(atr.iloc[-1] - 4.0) < 0.5


def test_bollinger_and_keltner(sample_ohlcv):
    bb_u, bb_m, bb_l = calculate_bollinger_bands(sample_ohlcv["Close"], period=20, num_std=2.0)
    kc_u, kc_m, kc_l = calculate_keltner_channels(
        sample_ohlcv["High"], sample_ohlcv["Low"], sample_ohlcv["Close"], period=20, atr_multiplier=1.5
    )
    assert bb_u.iloc[-1] > bb_m.iloc[-1] > bb_l.iloc[-1]
    assert kc_u.iloc[-1] > kc_m.iloc[-1] > kc_l.iloc[-1]


def test_detect_squeeze():
    bb_u = pd.Series([105, 108, 110])
    bb_l = pd.Series([95, 92, 90])
    kc_u = pd.Series([110, 110, 109])
    kc_l = pd.Series([90, 90, 91])

    # Bar 0: bb_u(105) < kc_u(110) and bb_l(95) > kc_l(90) -> True
    # Bar 1: bb_u(108) < kc_u(110) and bb_l(92) > kc_l(90) -> True
    # Bar 2: bb_u(110) not < kc_u(109) -> False
    squeeze = detect_squeeze(bb_u, bb_l, kc_u, kc_l)
    assert squeeze.tolist() == [True, True, False]

    counts = consecutive_squeeze_count(squeeze)
    assert counts.tolist() == [1, 2, 0]
