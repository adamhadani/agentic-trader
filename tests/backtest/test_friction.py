from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.models import BacktestResult, BacktestTrade
from agentic_trader.backtest.reporting import format_backtest_report
from agentic_trader.config import AppConfig, FrictionConfig, load_config
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType
from agentic_trader.data.market_data import ContractMarketData


@pytest.fixture
def config() -> AppConfig:
    cfg = load_config()
    cfg.friction = FrictionConfig(
        enabled=True,
        futures_commission_per_contract=0.62,
        equity_commission_per_share=0.005,
        futures_slippage_points=0.25,
        equity_slippage_pct=0.0002,
    )
    return cfg


def test_friction_config_defaults():
    fc = FrictionConfig()
    assert fc.enabled is True
    assert fc.futures_commission_per_contract == 0.62
    assert fc.equity_commission_per_share == 0.005
    assert fc.futures_slippage_points == 0.25
    assert fc.equity_slippage_pct == 0.0002


def test_futures_execution_with_friction(config: AppConfig):
    engine = BacktestEngine(
        config=config,
        initial_cash=100000.0,
        risk_free_rate=0.0,
        apply_friction=True,
    )

    # 25 daily bars where on bar 24 price reaches 5100.0 (TP at 5050.0)
    dates = pd.date_range("2026-01-01", periods=25, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [5000.0] * 25,
            "High": [5020.0] * 24 + [5100.0],
            "Low": [4980.0] * 25,
            "Close": [5000.0] * 24 + [5090.0],
            "Volume": [1000.0] * 25,
            "EMA_50": [4900.0] * 25,
            "EMA_200": [4800.0] * 25,
            "ATR_14": [10.0] * 25,
            "RSI_14": [50.0] * 25,
            "EMA_20": [5000.0] * 25,
            "BB_Upper": [5050.0] * 25,
            "BB_Lower": [4950.0] * 25,
            "KC_Upper": [5040.0] * 25,
            "KC_Lower": [4960.0] * 25,
            "Squeeze_On": [False] * 25,
            "Squeeze_Count": [0] * 25,
        },
        index=dates,
    )
    md = ContractMarketData(contract="/MES", ticker="/MES", daily=df, four_hour=df, hourly=df)

    trade = BacktestTrade(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_timestamp=dates[20],
        entry_price=5000.0,  # nominal entry
        quantity=2.0,  # 2 contracts
        stop_loss=4950.0,
        take_profit=5050.0,
        risk_dollars=500.0,
        commission=0.0,
        slippage_dollars=0.0,
    )

    data_map = {"/MES": md}
    res = engine.run(symbols=["/MES"], market_data_map=data_map, initial_trades=[trade])

    assert res.total_trades == 1
    closed_t = res.trades[0]
    assert closed_t.exit_reason == ExitReason.TAKE_PROFIT
    # 2 contracts: round-trip commission = $0.62 * 2 = $1.24 per trade (exit leg added)
    assert closed_t.commission == 1.24
    # Exit slippage = 0.25 points * 5.0 multiplier * 2 contracts = $2.50
    assert closed_t.slippage_dollars == 2.50
    # Nominal exit was 5050.0; slippage reduced it to 5049.75
    assert closed_t.exit_price == 5049.75
    # Nominal profit = (5050 - 5000) * 5 * 2 = $500.0
    # Net profit = (5049.75 - 5000) * 5 * 2 - 1.24 = 497.50 - 1.24 = $496.26
    assert closed_t.pnl_dollars == 496.26
    assert res.total_commissions == 1.24
    assert res.total_slippage == 2.50


def test_frictionless_mode_preserves_pure_alpha(config: AppConfig):
    engine = BacktestEngine(
        config=config,
        initial_cash=100000.0,
        risk_free_rate=0.0,
        apply_friction=False,
    )

    dates = pd.date_range("2026-01-01", periods=25, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 25,
            "High": [102.0] * 24 + [115.0],
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
    md = ContractMarketData(contract="SPY", ticker="SPY", daily=df, four_hour=df, hourly=df)

    trade = BacktestTrade(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_timestamp=dates[20],
        entry_price=100.0,
        quantity=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=500.0,
    )

    res = engine.run(symbols=["SPY"], market_data_map={"SPY": md}, initial_trades=[trade])
    assert res.total_trades == 1
    assert res.trades[0].commission == 0.0
    assert res.trades[0].slippage_dollars == 0.0
    assert res.trades[0].pnl_dollars == 1000.0
    assert res.total_commissions == 0.0
    assert res.total_slippage == 0.0


def test_reporting_friction_breakdown():
    trade = BacktestTrade(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        entry_price=100.0,
        quantity=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=500.0,
        commission=1.00,
        slippage_dollars=4.00,
        pnl_dollars=995.00,
        pnl_pct=9.95,
        duration_bars=3,
    )

    result = BacktestResult(
        starting_cash=100000.0,
        ending_equity=100995.0,
        strategy_pnl=995.0,
        strategy_return_pct=1.0,
        cash_yield_pnl=0.0,
        combined_total_pnl=995.0,
        combined_return_pct=1.0,
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=100.0,
        profit_factor=999.99,
        max_drawdown_pct=0.0,
        sharpe_ratio=2.5,
        sortino_ratio=3.0,
        annualized_return_pct=12.0,
        avg_trade_duration_bars=3.0,
        trades=[trade],
        equity_curve=[],
        gross_strategy_pnl=1000.00,
        total_commissions=1.00,
        total_slippage=4.00,
    )

    report = format_backtest_report(result, symbols=["SPY"], lookback="1y")
    assert "• Gross Strategy Alpha:         $   +1,000.00" in report
    assert "• Execution Commissions:        $       -1.00" in report
    assert "• Bid-Ask Slippage Drag:        $       -4.00" in report
    assert "• Net Strategy Alpha P&L:       $     +995.00" in report
