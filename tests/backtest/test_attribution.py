from datetime import UTC, datetime

import pandas as pd

from agentic_trader.backtest.attribution import calculate_performance_attribution
from agentic_trader.backtest.models import BacktestResult, BacktestTrade
from agentic_trader.backtest.reporting import format_backtest_report
from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType


def _make_trade(
    symbol: str,
    asset_class: AssetClass,
    strategy: StrategyType,
    direction: Direction,
    entry_dt: datetime,
    entry_price: float,
    exit_price: float,
    pnl: float,
) -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol,
        asset_class=asset_class,
        strategy=strategy,
        direction=direction,
        entry_timestamp=entry_dt,
        entry_price=entry_price,
        quantity=1.0,
        stop_loss=entry_price - 10.0,
        take_profit=entry_price + 20.0,
        risk_dollars=50.0,
        exit_price=exit_price,
        pnl_dollars=pnl,
        pnl_pct=round((pnl / (entry_price * 1.0)) * 100.0, 2),
        exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
        duration_bars=4,
    )


def test_factor_and_asset_class_attribution():
    t1 = _make_trade(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 1, 10, tzinfo=UTC),
        entry_price=5800.0,
        exit_price=5850.0,
        pnl=250.0,
    )
    t2 = _make_trade(
        symbol="/MNQ",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 2, 5, tzinfo=UTC),
        entry_price=19000.0,
        exit_price=19200.0,
        pnl=400.0,
    )
    t3 = _make_trade(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 3, 1, tzinfo=UTC),
        entry_price=500.0,
        exit_price=490.0,
        pnl=-100.0,
    )

    res = BacktestResult(
        starting_cash=100000.0,
        ending_equity=105050.0,
        strategy_pnl=550.0,
        strategy_return_pct=0.55,
        cash_yield_pnl=4500.0,
        combined_total_pnl=5050.0,
        combined_return_pct=5.05,
        total_trades=3,
        winning_trades=2,
        losing_trades=1,
        win_rate=66.7,
        profit_factor=6.5,
        max_drawdown_pct=1.2,
        sharpe_ratio=1.65,
        sortino_ratio=2.1,
        annualized_return_pct=5.05,
        avg_trade_duration_bars=4.0,
        trades=[t1, t2, t3],
    )

    attr = calculate_performance_attribution(res)
    assert attr is not None
    assert len(attr.factors) == 3

    # Trend-Pullback factor: t1 (+250) + t3 (-100) = +150
    trend_f = next(f for f in attr.factors if "Trend-Pullback" in f.factor_name)
    assert trend_f.pnl_dollars == 150.0
    assert trend_f.trade_count == 2
    assert trend_f.win_rate == 50.0

    # Squeeze Breakout factor: t2 (+400)
    sqz_f = next(f for f in attr.factors if "Squeeze Breakout" in f.factor_name)
    assert sqz_f.pnl_dollars == 400.0
    assert sqz_f.trade_count == 1
    assert sqz_f.win_rate == 100.0

    # Cash Carry Yield factor: 4500.0
    carry_f = next(f for f in attr.factors if "Cash Carry" in f.factor_name)
    assert carry_f.pnl_dollars == 4500.0
    assert carry_f.return_contribution_pct == 4.5

    # Asset class attribution: Futures (+650) vs Equity (-100)
    assert len(attr.asset_classes) >= 2
    fut_ac = next(a for a in attr.asset_classes if a.name == "Futures")
    assert fut_ac.pnl_dollars == 650.0
    assert fut_ac.trade_count == 2

    eq_ac = next(a for a in attr.asset_classes if a.name == "Equity")
    assert eq_ac.pnl_dollars == -100.0
    assert eq_ac.trade_count == 1


def test_regime_attribution_with_vix():
    # Build synthetic VIX historical series
    dates = pd.date_range("2025-01-01", periods=100, freq="1D", tz="UTC")
    # Low VIX for first 30 days, normal for next 40, elevated for last 30
    vix_values = [13.5] * 30 + [18.0] * 40 + [26.0] * 30
    vix_df = pd.DataFrame({"Close": vix_values}, index=dates)

    t_compressed = _make_trade(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 1, 15, tzinfo=UTC),
        entry_price=5800.0,
        exit_price=5900.0,
        pnl=500.0,
    )
    t_normal = _make_trade(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 2, 10, tzinfo=UTC),
        entry_price=500.0,
        exit_price=510.0,
        pnl=100.0,
    )
    t_elevated = _make_trade(
        symbol="/MNQ",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 3, 20, tzinfo=UTC),
        entry_price=19000.0,
        exit_price=18800.0,
        pnl=-400.0,
    )

    res = BacktestResult(
        starting_cash=100000.0,
        ending_equity=100200.0,
        strategy_pnl=200.0,
        strategy_return_pct=0.20,
        cash_yield_pnl=0.0,
        combined_total_pnl=200.0,
        combined_return_pct=0.20,
        total_trades=3,
        winning_trades=2,
        losing_trades=1,
        win_rate=66.7,
        profit_factor=1.5,
        max_drawdown_pct=0.5,
        sharpe_ratio=1.1,
        sortino_ratio=1.4,
        annualized_return_pct=1.0,
        avg_trade_duration_bars=4.0,
        trades=[t_compressed, t_normal, t_elevated],
    )

    attr = calculate_performance_attribution(res, vix_df=vix_df)

    comp_reg = next(r for r in attr.regimes if r.regime == "COMPRESSED")
    assert comp_reg.trade_count == 1
    assert comp_reg.pnl_dollars == 500.0
    assert comp_reg.win_rate == 100.0

    norm_reg = next(r for r in attr.regimes if r.regime == "NORMAL")
    assert norm_reg.trade_count == 1
    assert norm_reg.pnl_dollars == 100.0
    assert norm_reg.win_rate == 100.0

    elev_reg = next(r for r in attr.regimes if r.regime == "ELEVATED")
    assert elev_reg.trade_count == 1
    assert elev_reg.pnl_dollars == -400.0
    assert elev_reg.win_rate == 0.0


def test_format_backtest_report_with_attribution():
    t1 = _make_trade(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        entry_dt=datetime(2025, 1, 10, tzinfo=UTC),
        entry_price=5800.0,
        exit_price=5850.0,
        pnl=250.0,
    )
    res = BacktestResult(
        starting_cash=100000.0,
        ending_equity=104750.0,
        strategy_pnl=250.0,
        strategy_return_pct=0.25,
        cash_yield_pnl=4500.0,
        combined_total_pnl=4750.0,
        combined_return_pct=4.75,
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=100.0,
        profit_factor=float("inf"),
        max_drawdown_pct=0.1,
        sharpe_ratio=2.0,
        sortino_ratio=3.0,
        annualized_return_pct=4.75,
        avg_trade_duration_bars=4.0,
        trades=[t1],
    )
    res.attribution = calculate_performance_attribution(res)

    report = format_backtest_report(res, symbols=["/MES"], lookback="1y")
    assert "FACTOR & REGIME ATTRIBUTION" in report
    assert "Quantitative Factor Breakdown:" in report
    assert "Trend-Pullback (Momentum)" in report
    assert "Cash Carry Yield" in report
    assert "Macro Volatility Regime Breakdown:" in report
    assert "Asset Class Exposure:" in report
    assert "Futures" in report
