from datetime import UTC, datetime

from agentic_trader.backtest.models import (
    BacktestResult,
    BacktestTrade,
    MonteCarloResult,
)
from agentic_trader.backtest.monte_carlo import run_monte_carlo_simulation
from agentic_trader.backtest.reporting import format_backtest_report
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType


def make_trade(symbol: str, pnl: float, pnl_pct: float) -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        entry_price=100.0,
        quantity=10.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        exit_timestamp=datetime(2025, 1, 5, tzinfo=UTC),
        exit_price=100.0 + (pnl / 10.0),
        exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
        pnl_dollars=pnl,
        pnl_pct=pnl_pct,
        duration_bars=4,
    )


def test_monte_carlo_insufficient_trades():
    trades = [make_trade("SPY", 100.0, 1.0), make_trade("SPY", -50.0, -0.5)]
    res = run_monte_carlo_simulation(trades, starting_cash=100000.0, n_simulations=100)
    assert res is None


def test_monte_carlo_simulation_metrics():
    # 6 trades: 4 wins, 2 losses
    trades = [
        make_trade("SPY", 500.0, 0.5),
        make_trade("QQQ", -250.0, -0.25),
        make_trade("SPY", 800.0, 0.8),
        make_trade("/MES", -300.0, -0.3),
        make_trade("QQQ", 600.0, 0.6),
        make_trade("SPY", 400.0, 0.4),
    ]

    mc = run_monte_carlo_simulation(
        trades,
        starting_cash=100000.0,
        n_simulations=500,
        random_seed=42,
    )

    assert mc is not None
    assert mc.n_simulations == 500
    assert mc.median_equity >= mc.ci_5th_equity
    assert mc.ci_95th_equity >= mc.median_equity
    assert mc.ci_95th_drawdown_pct >= mc.median_drawdown_pct
    assert mc.median_sharpe >= mc.ci_5th_sharpe
    assert 0.0 <= mc.risk_of_ruin_10pct <= 100.0
    assert 0.0 <= mc.risk_of_ruin_20pct <= 100.0
    assert mc.var_95_pct >= 0.0
    assert mc.cvar_95_pct >= mc.var_95_pct


def test_monte_carlo_determinism():
    trades = [
        make_trade("SPY", 400.0, 0.4),
        make_trade("QQQ", -200.0, -0.2),
        make_trade("SPY", 300.0, 0.3),
        make_trade("/MES", 500.0, 0.5),
    ]

    mc1 = run_monte_carlo_simulation(trades, starting_cash=100000.0, n_simulations=200, random_seed=123)
    mc2 = run_monte_carlo_simulation(trades, starting_cash=100000.0, n_simulations=200, random_seed=123)
    mc3 = run_monte_carlo_simulation(trades, starting_cash=100000.0, n_simulations=200, random_seed=999)

    assert mc1 is not None and mc2 is not None and mc3 is not None
    assert mc1.median_equity == mc2.median_equity
    assert mc1.median_drawdown_pct == mc2.median_drawdown_pct
    # Different seed should give slight variance
    assert mc1.median_equity != mc3.median_equity or mc1.median_drawdown_pct != mc3.median_drawdown_pct


def test_monte_carlo_reporting_output():
    mc = MonteCarloResult(
        n_simulations=1000,
        median_equity=108500.0,
        ci_5th_equity=102100.0,
        ci_95th_equity=114200.0,
        median_drawdown_pct=3.45,
        ci_95th_drawdown_pct=6.80,
        median_sharpe=1.75,
        ci_5th_sharpe=0.85,
        risk_of_ruin_10pct=0.20,
        risk_of_ruin_20pct=0.00,
        var_95_pct=0.45,
        cvar_95_pct=0.62,
    )

    result = BacktestResult(
        starting_cash=100000.0,
        ending_equity=108500.0,
        strategy_pnl=4000.0,
        strategy_return_pct=4.0,
        cash_yield_pnl=4500.0,
        combined_total_pnl=8500.0,
        combined_return_pct=8.5,
        total_trades=15,
        winning_trades=10,
        losing_trades=5,
        win_rate=66.67,
        profit_factor=2.20,
        max_drawdown_pct=3.50,
        sharpe_ratio=1.80,
        sortino_ratio=2.50,
        annualized_return_pct=8.50,
        avg_trade_duration_bars=5.2,
        trades=[],
        equity_curve=[],
        monte_carlo=mc,
    )

    report = format_backtest_report(result, symbols=["SPY", "QQQ"], lookback="1y")
    assert "MONTE CARLO RISK RESAMPLING (1,000 Bootstrap Iterations)" in report
    assert "Final Portfolio Equity (Median):" in report
    assert "108,500.00" in report
    assert "90% Confidence Interval (Equity): [$102,100.00 .. $114,200.00]" in report
    assert "95th Pctile Worst Drawdown:" in report
    assert "6.80%" in report
    assert "Risk of Ruin (Drawdown >= 10%):" in report
    assert "0.20%" in report
    assert "95% Value at Risk (VaR):" in report
    assert "0.45%" in report
    assert "95% Conditional VaR (CVaR):" in report
    assert "0.62%" in report
