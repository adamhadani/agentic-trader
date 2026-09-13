import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from agentic_trader.config import AppConfig, load_config
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.models import OptimizationResult, ParameterCandidate
from agentic_trader.research.optimizer import ParameterGridOptimizer
from agentic_trader.research.reporting import format_optimization_report


@pytest.fixture
def config() -> AppConfig:
    return load_config()


def make_synthetic_screener_data(
    symbol: str = "SPY",
    n_bars: int = 60,
    base_price: float = 500.0,
) -> ContractMarketData:
    dates = pd.date_range("2025-01-01", periods=n_bars, freq="1D")
    prices = []
    current = base_price
    for i in range(n_bars):
        if 25 <= i <= 35:
            # Create a pullback
            current -= 1.0
        elif i > 35:
            # Rebound
            current += 1.5
        else:
            current += 0.8
        prices.append(current)

    closes = np.array(prices)
    highs = closes + 1.5
    lows = closes - 1.5
    opens = closes - 0.2

    ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values
    ema200 = pd.Series(closes).ewm(span=200, adjust=False).mean().values
    atr = np.full(n_bars, 2.5)

    rsi = np.full(n_bars, 55.0)
    for idx, i in enumerate(range(28, 35)):
        rsi[i] = 38.0 + idx * 2.0  # dip into oversold and bounce

    volume = np.full(n_bars, 1000000.0)
    # Volume surge on bar 40
    volume[40] = 2500000.0

    squeeze_count = np.zeros(n_bars, dtype=int)
    for i in range(20, 28):
        squeeze_count[i] = i - 19

    df_daily = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volume,
            "EMA_50": ema50,
            "EMA_200": ema200,
            "ATR_14": atr,
            "RSI_14": rsi,
            "EMA_20": closes,
            "BB_Upper": closes + 3.0,
            "BB_Lower": closes - 3.0,
            "KC_Upper": closes + 2.5,
            "KC_Lower": closes - 2.5,
            "Squeeze_On": np.full(n_bars, False),
            "Squeeze_Count": squeeze_count,
        },
        index=dates,
    )

    return ContractMarketData(
        contract=symbol,
        ticker=symbol,
        daily=df_daily,
        four_hour=df_daily.copy(),
        hourly=df_daily.copy(),
    )


# =====================================================================
# 1. Models and Reporting Tests
# =====================================================================


def test_optimization_models_and_report():
    c1 = ParameterCandidate(
        parameters={"rsi_threshold": 42.0, "ema_span": 20},
        total_return_pct=14.50,
        win_rate=66.67,
        profit_factor=2.40,
        sharpe_ratio=1.85,
        max_drawdown_pct=3.20,
        total_trades=12,
    )
    c2 = ParameterCandidate(
        parameters={"rsi_threshold": 45.0, "ema_span": 15},
        total_return_pct=8.20,
        win_rate=50.00,
        profit_factor=1.60,
        sharpe_ratio=1.10,
        max_drawdown_pct=5.40,
        total_trades=10,
    )

    result = OptimizationResult(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="2y",
        engine_used="VectorBT (JIT Tensor)",
        total_combinations_tested=21,
        ranked_candidates=[c1, c2],
    )

    report = format_optimization_report(result, top_n=2)
    assert "PARAMETER GRID OPTIMIZATION REPORT" in report
    assert "Asset Symbol:        SPY" in report
    assert "VectorBT (JIT Tensor)" in report
    assert "Total Configurations Tested: 21" in report
    assert "rsi_threshold=42.0" in report
    assert "RECOMMENDED CONFIGURATION TUNING" in report
    assert "+14.50%" in report


# =====================================================================
# 2. Optimization Engine Tests
# =====================================================================


def test_optimizer_trend_pullback_vbt_and_fallback(config):
    optimizer = ParameterGridOptimizer(config=config)
    data = make_synthetic_screener_data(symbol="SPY", n_bars=60)

    # Run with default engine (VectorBT if installed)
    res_vbt = optimizer.run(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="2y",
        market_data=data,
        force_fallback=False,
    )
    assert res_vbt.symbol == "SPY"
    assert res_vbt.strategy == "trend_pullback"
    assert res_vbt.total_combinations_tested == 21  # 7 RSI x 3 EMA
    assert len(res_vbt.ranked_candidates) == 21

    # Verify ranked descending by Sharpe and return
    top = res_vbt.ranked_candidates[0]
    assert "rsi_threshold" in top.parameters
    assert "ema_span" in top.parameters

    # Run with forced fallback (Pure Vectorized NumPy)
    res_fallback = optimizer.run(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="2y",
        market_data=data,
        force_fallback=True,
    )
    assert res_fallback.engine_used == "Vectorized NumPy"
    assert res_fallback.total_combinations_tested == 21
    assert len(res_fallback.ranked_candidates) == 21


def test_optimizer_squeeze_breakout(config):
    optimizer = ParameterGridOptimizer(config=config)
    data = make_synthetic_screener_data(symbol="QQQ", n_bars=60)

    res = optimizer.run(
        symbol="QQQ",
        strategy="squeeze_breakout",
        lookback="1y",
        market_data=data,
        force_fallback=True,
    )
    assert res.strategy == "squeeze_breakout"
    assert res.total_combinations_tested == 25  # 5 volume factors x 5 squeeze lengths
    assert len(res.ranked_candidates) == 25
    top = res.ranked_candidates[0]
    assert "volume_factor" in top.parameters
    assert "min_squeeze_bars" in top.parameters


def test_optimizer_invalid_strategy(config):
    optimizer = ParameterGridOptimizer(config=config)
    data = make_synthetic_screener_data(symbol="SPY", n_bars=60)
    with pytest.raises(ValueError, match="Unsupported strategy"):
        optimizer.run(symbol="SPY", strategy="invalid_strategy", market_data=data)


# =====================================================================
# 3. CLI Subcommand Tests
# =====================================================================


def test_cli_optimize_help():
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "optimize", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Run quantitative parameter grid search" in proc.stdout
    assert "--symbol" in proc.stdout
    assert "--strategy" in proc.stdout
    assert "--lookback" in proc.stdout
    assert "--top-n" in proc.stdout
