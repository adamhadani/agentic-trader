from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.backtest.models import (
    BacktestResult,
    BacktestTrade,
    MonteCarloResult,
)
from agentic_trader.backtest.monte_carlo import run_monte_carlo_simulation
from agentic_trader.backtest.reporting import format_backtest_report
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType
from agentic_trader.presentation.formatters import TelegramHtmlFormatter


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
    assert mc.median_sharpe is None and mc.ci_5th_sharpe is None
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


@pytest.mark.parametrize("sharpe", [1.75, None])
def test_monte_carlo_reporting_output(sharpe):
    mc = MonteCarloResult(
        n_simulations=1000,
        median_equity=108500.0,
        ci_5th_equity=102100.0,
        ci_95th_equity=114200.0,
        median_drawdown_pct=3.45,
        ci_95th_drawdown_pct=6.80,
        median_sharpe=sharpe,
        ci_5th_sharpe=0.85 if sharpe is not None else None,
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
    assert "IID CLOSED-TRADE RESAMPLING (1,000 Iterations)" in report
    assert "Final Resampled Equity (Median):" in report
    assert "108,500.00" in report
    assert "5th–95th Equity Percentiles: [$102,100.00 .. $114,200.00]" in report
    assert "95th Percentile Drawdown:" in report
    assert "6.80%" in report
    assert "Paths with Drawdown >= 10%:" in report
    assert "0.20%" in report
    assert "95% Per-Trade Loss VaR:" in report
    assert "0.45%" in report
    assert "95% Per-Trade Loss CVaR:" in report
    assert "0.62%" in report
    assert "starting cash" in report
    assert "serial dependence" in report
    if sharpe is None:
        assert "unavailable" in report and "observed portfolio-return clock" in report
    telegram = TelegramHtmlFormatter.format_backtest_html(result)
    assert "IID trade paths" in telegram and "loss / starting cash" in telegram


@pytest.mark.parametrize(
    "pnls,var,cvar",
    [
        ([1000.0] * 30, 0.0, 0.0),
        ([0.0] * 30, 0.0, 0.0),
        ([-1000.0] * 30, 1.0, 1.0),
        ([-3000.0, -1000.0] + [1000.0] * 28, 1.0, 2.33),
    ],
    ids=["all_positive", "all_zero", "all_losses", "fractional_tail_mass"],
)
def test_loss_metrics_have_consistent_nonnegative_loss_signs(pnls, var, cvar):
    trades = [make_trade("SPY", pnl, pnl / 1000) for pnl in pnls]
    result = run_monte_carlo_simulation(trades, 100_000.0, n_simulations=25)
    assert result.var_95_pct == var
    assert result.cvar_95_pct == cvar
    assert result.median_sharpe is None and result.ci_5th_sharpe is None


@pytest.mark.parametrize("percentages", [[-90.0, None, 500.0], [None, None, None]])
def test_loss_units_always_use_starting_capital_not_optional_position_percentages(percentages):
    trades = [
        make_trade("SPY", pnl, percent) for pnl, percent in zip([-1000.0, 500.0, 100.0], percentages, strict=True)
    ]
    result = run_monte_carlo_simulation(trades, 100_000.0, n_simulations=25)
    assert result.var_95_pct == result.cvar_95_pct == 1.0


@pytest.mark.parametrize("days", [1, 365, 3650])
def test_trade_timestamps_do_not_invent_an_observed_portfolio_return_clock(days):
    trades = [make_trade("SPY", pnl, 1.0) for pnl in [100.0, 200.0, -100.0, 50.0]]
    for trade in trades:
        trade.exit_timestamp = trade.entry_timestamp + timedelta(days=days)
    result = run_monte_carlo_simulation(trades, 100_000.0, n_simulations=25)
    assert result.median_sharpe is None and result.ci_5th_sharpe is None
    assert result.sharpe_unavailable_reason == "missing_observed_portfolio_return_clock"


@pytest.mark.parametrize(
    "cash,simulations,pnl",
    [
        (0.0, 10, 1.0),
        (-1.0, 10, 1.0),
        (float("inf"), 10, 1.0),
        (100.0, 0, 1.0),
        (100.0, 1.5, 1.0),
        (100.0, 10, float("nan")),
        (100.0, 10, float("inf")),
    ],
)
def test_invalid_resampling_inputs_fail_explicitly(cash, simulations, pnl):
    trades = [make_trade("SPY", pnl, 0.0) for _ in range(3)]
    with pytest.raises(ValueError):
        run_monte_carlo_simulation(trades, cash, n_simulations=simulations)
