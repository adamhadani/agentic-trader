import subprocess
import sys
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.metrics import (
    calculate_drawdown,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_win_rate,
)
from agentic_trader.backtest.models import BacktestResult, BacktestTrade, EquityPoint
from agentic_trader.backtest.reporting import format_backtest_report
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType
from agentic_trader.data.market_data import ContractMarketData


@pytest.fixture
def config() -> AppConfig:
    return load_config()


def create_trade(
    symbol: str = "SPY",
    direction: Direction = Direction.LONG,
    pnl: float = 100.0,
    duration: int = 5,
    exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
) -> BacktestTrade:
    entry_dt = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    exit_dt = datetime(2026, 1, 6, 16, 0, tzinfo=UTC)
    return BacktestTrade(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=direction,
        entry_timestamp=entry_dt,
        entry_price=500.0,
        quantity=10.0,
        stop_loss=490.0,
        take_profit=520.0,
        risk_dollars=100.0,
        exit_timestamp=exit_dt,
        exit_price=510.0 if pnl > 0 else 490.0,
        exit_reason=exit_reason,
        pnl_dollars=pnl,
        pnl_pct=(pnl / 5000.0) * 100.0,
        duration_bars=duration,
    )


# =====================================================================
# 1. Metric Calculation Tests
# =====================================================================


def test_calculate_win_rate():
    assert calculate_win_rate([]) == 0.0

    trades = [
        create_trade(pnl=150.0),
        create_trade(pnl=200.0),
        create_trade(pnl=-100.0),
        create_trade(pnl=-50.0),
    ]
    assert calculate_win_rate(trades) == 50.0

    winners_only = [create_trade(pnl=100.0), create_trade(pnl=50.0)]
    assert calculate_win_rate(winners_only) == 100.0


def test_calculate_profit_factor():
    assert calculate_profit_factor([]) == 0.0

    winners_only = [create_trade(pnl=100.0), create_trade(pnl=50.0)]
    assert calculate_profit_factor(winners_only) == 999.99

    losers_only = [create_trade(pnl=-100.0)]
    assert calculate_profit_factor(losers_only) == 0.0

    mixed = [
        create_trade(pnl=300.0),
        create_trade(pnl=-100.0),
    ]
    assert calculate_profit_factor(mixed) == 3.0


def test_calculate_drawdown():
    empty_s = pd.Series(dtype=float)
    max_dd, dd_series = calculate_drawdown(empty_s)
    assert max_dd == 0.0
    assert dd_series.empty

    # Equity goes 100 -> 120 -> 90 -> 110
    equity = pd.Series([100.0, 120.0, 90.0, 110.0])
    max_dd, dd_series = calculate_drawdown(equity)
    # Peak is 120, trough is 90 -> dd = (90 - 120) / 120 = -25%
    assert max_dd == 25.0
    assert len(dd_series) == 4


def test_calculate_sharpe_ratio():
    assert calculate_sharpe_ratio(pd.Series(dtype=float)) == 0.0
    assert calculate_sharpe_ratio(pd.Series([0.01])) == 0.0

    # Constant returns -> std is 0 -> Sharpe is 0.0
    constant_returns = pd.Series([0.01, 0.01, 0.01, 0.01])
    assert calculate_sharpe_ratio(constant_returns) == 0.0

    # Healthy positive returns with modest volatility
    np.random.seed(42)
    daily_returns = pd.Series(np.random.normal(0.001, 0.005, 252))
    sharpe = calculate_sharpe_ratio(daily_returns, risk_free_rate=0.045)
    assert isinstance(sharpe, float)
    assert sharpe > 0.0


def test_calculate_sortino_ratio():
    assert calculate_sortino_ratio(pd.Series(dtype=float)) == 0.0
    assert calculate_sortino_ratio(pd.Series([0.01])) == 0.0

    # All positive returns above risk-free rate -> no downside volatility -> 999.99
    positive_returns = pd.Series([0.02, 0.03, 0.025, 0.04, 0.02])
    assert calculate_sortino_ratio(positive_returns, risk_free_rate=0.01) == 999.99

    # Mixed returns
    mixed_returns = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015, -0.002])
    sortino = calculate_sortino_ratio(mixed_returns, risk_free_rate=0.0)
    assert isinstance(sortino, float)
    assert sortino > 0.0


# =====================================================================
# 2. Model & Reporting Tests
# =====================================================================


def test_backtest_models_and_report():
    t1 = create_trade(symbol="SPY", pnl=350.0, duration=4)
    t2 = create_trade(symbol="QQQ", pnl=-120.0, duration=2, exit_reason=ExitReason.STOP_LOSS)
    ep1 = EquityPoint(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        portfolio_equity=100000.0,
        cash_reserve=100000.0,
        drawdown_pct=0.0,
    )
    ep2 = EquityPoint(
        timestamp=datetime(2026, 1, 2, tzinfo=UTC),
        portfolio_equity=100230.0,
        cash_reserve=100230.0,
        drawdown_pct=0.0,
    )

    result = BacktestResult(
        starting_cash=100000.0,
        ending_equity=100230.0,
        strategy_pnl=230.0,
        strategy_return_pct=0.23,
        cash_yield_pnl=45.0,
        combined_total_pnl=275.0,
        combined_return_pct=0.28,
        total_trades=2,
        winning_trades=1,
        losing_trades=1,
        win_rate=50.0,
        profit_factor=2.92,
        max_drawdown_pct=0.12,
        sharpe_ratio=1.45,
        sortino_ratio=2.10,
        annualized_return_pct=7.50,
        avg_trade_duration_bars=3.0,
        trades=[t1, t2],
        equity_curve=[ep1, ep2],
    )

    report = format_backtest_report(result, symbols=["SPY", "QQQ"], lookback="1y", strategy="all")
    assert "CASH-PLUS TRADING COPILOT: QUANTITATIVE BACKTEST REPORT" in report
    assert "Universe:            SPY, QQQ" in report
    assert "Pure Strategy Alpha P&L:      $     +230.00" in report
    assert "Treasury/Cash Reserve Yield:  $      +45.00" in report
    assert "Win Rate:" in report
    assert "50.00%" in report
    assert "Top Performing Trades:" in report
    assert "SPY" in report


# =====================================================================
# 3. BacktestEngine Execution Tests
# =====================================================================


def make_synthetic_market_data(
    symbol: str = "SPY",
    n_bars: int = 50,
    base_price: float = 500.0,
    bullish_pullback: bool = True,
) -> ContractMarketData:
    dates = pd.date_range("2025-01-01", periods=n_bars, freq="1D")
    prices = []
    current = base_price
    for i in range(n_bars):
        if bullish_pullback and i == 30:
            # Create a pullback bar
            current -= 5.0
        elif bullish_pullback and i > 30:
            # Rebound
            current += 2.0
        else:
            current += 0.5
        prices.append(current)

    closes = np.array(prices)
    highs = closes + 1.5
    lows = closes - 1.5
    opens = closes - 0.2

    # Precalculate technical indicators expected by strategy engine
    ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values
    ema200 = pd.Series(closes).ewm(span=200, adjust=False).mean().values
    atr = np.full(n_bars, 2.0)
    rsi = np.full(n_bars, 50.0)
    if bullish_pullback and n_bars > 32:
        rsi[30] = 42.0
        rsi[31] = 44.0

    df_daily = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(n_bars, 1000000.0),
            "EMA_50": ema50,
            "EMA_200": ema200,
            "ATR_14": atr,
            "RSI_14": rsi,
            "EMA_20": closes,
            "BB_Upper": closes + 4.0,
            "BB_Lower": closes - 4.0,
            "KC_Upper": closes + 3.0,
            "KC_Lower": closes - 3.0,
            "Squeeze_On": np.full(n_bars, False),
            "Squeeze_Count": np.zeros(n_bars, dtype=int),
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


def test_backtest_engine_run_synthetic(config):
    engine = BacktestEngine(
        config=config,
        initial_cash=100000.0,
        risk_free_rate=0.045,
        max_concurrent_positions=2,
    )

    md_spy = make_synthetic_market_data(symbol="SPY", n_bars=40, base_price=500.0)
    data_map = {"SPY": md_spy}

    result = engine.run(
        symbols=["SPY"],
        market_data_map=data_map,
        strategy_filter="all",
    )

    assert result.starting_cash == 100000.0
    assert result.ending_equity > 0.0
    assert result.cash_yield_pnl > 0.0  # Earned treasury yield
    assert len(result.equity_curve) == 40


def test_backtest_engine_bracket_exits(config):
    engine = BacktestEngine(
        config=config,
        initial_cash=100000.0,
        risk_free_rate=0.0,
    )

    # Manually execute trade exit logic across a known bar sequence
    dates = pd.date_range("2026-01-01", periods=25, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 25,
            "High": [102.0] * 24 + [115.0],  # Bar 25 spikes to 115 (triggers TP)
            "Low": [98.0] * 25,
            "Close": [100.0] * 24 + [114.0],
            "Volume": [1000.0] * 25,
            "EMA_50": [90.0] * 25,
            "EMA_200": [80.0] * 25,
            "ATR_14": [2.0] * 25,
            "RSI_14": [50.0] * 25,
            "EMA_20": [100.0] * 25,
            "BB_Upper": [105.0] * 25,
            "BB_Lower": [95.0] * 25,
            "KC_Upper": [104.0] * 25,
            "KC_Lower": [96.0] * 25,
            "Squeeze_On": [False] * 25,
            "Squeeze_Count": [0] * 25,
        },
        index=dates,
    )
    md = ContractMarketData(contract="XYZ", ticker="XYZ", daily=df, four_hour=df, hourly=df)

    # Inject an active long trade with TP at 110.0 and SL at 95.0
    trade = BacktestTrade(
        symbol="XYZ",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_timestamp=dates[20],
        entry_price=100.0,
        quantity=50.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=250.0,
    )

    data_map = {"XYZ": md}
    # Run engine with data_map and injected long trade
    res = engine.run(symbols=["XYZ"], market_data_map=data_map, initial_trades=[trade])
    assert res.starting_cash == 100000.0
    assert res.total_trades == 1
    assert res.winning_trades == 1
    assert res.trades[0].exit_reason == ExitReason.TAKE_PROFIT
    assert res.trades[0].exit_price == 110.0
    assert res.trades[0].pnl_dollars == (110.0 - 100.0) * 1.0 * 50.0  # 500.0

    # Inject an active short trade where price spikes past SL
    short_trade = BacktestTrade(
        symbol="XYZ",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.SHORT,
        entry_timestamp=dates[20],
        entry_price=100.0,
        quantity=50.0,
        stop_loss=108.0,
        take_profit=90.0,
        risk_dollars=400.0,
    )
    res_short = engine.run(symbols=["XYZ"], market_data_map=data_map, initial_trades=[short_trade])
    assert res_short.total_trades == 1
    assert res_short.losing_trades == 1
    assert res_short.trades[0].exit_reason == ExitReason.STOP_LOSS
    assert res_short.trades[0].exit_price == 108.0


def test_cli_backtest_help():
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "backtest", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Run offline historical backtest" in proc.stdout or "backtest" in proc.stdout
    assert "--symbols" in proc.stdout
    assert "--strategy" in proc.stdout
    assert "--lookback" in proc.stdout
    assert "--cash" in proc.stdout
    assert "--risk-free-rate" in proc.stdout
    assert "--monte-carlo" in proc.stdout
    assert "--mc-sims" in proc.stdout
