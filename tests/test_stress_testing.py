import numpy as np
import pandas as pd
import pytest

from agentic_trader.backtest.reporting import (
    format_instantaneous_shock_report,
    format_stress_test_report,
)
from agentic_trader.backtest.stress import (
    CRISIS_CATALOG,
    CrisisReplayEngine,
    InstantaneousShockResult,
    ScenarioStressResult,
)
from agentic_trader.config import AppConfig, ContractConfig
from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData


def _make_synthetic_crisis_market_data(
    symbol: str = "SPY",
    start_date: str = "2020-02-15",
    n_bars: int = 40,
    base_price: float = 330.0,
) -> ContractMarketData:
    dates = pd.date_range(start=start_date, periods=n_bars, freq="1D")
    prices = []
    current = base_price
    for i in range(n_bars):
        if i < 15:
            # Crash phase
            current -= 4.0
        elif i == 15:
            # Rebound pullback setup bar
            current -= 1.0
        else:
            # Recovery phase
            current += 2.0
        prices.append(current)

    closes = np.array(prices)
    highs = closes + 2.0
    lows = closes - 2.0
    opens = closes - 0.5

    df_daily = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(n_bars, 2_000_000.0),
            "EMA_50": pd.Series(closes).ewm(span=50, adjust=False).mean().values,
            "EMA_200": pd.Series(closes).ewm(span=200, adjust=False).mean().values,
            "ATR_14": np.full(n_bars, 3.5),
            "RSI_14": np.full(n_bars, 45.0),
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
        four_hour=df_daily,
        hourly=df_daily,
    )


def test_crisis_catalog_integrity():
    assert "2008_GFC" in CRISIS_CATALOG
    assert "2020_COVID" in CRISIS_CATALOG
    assert "2022_INFLATION" in CRISIS_CATALOG

    gfc = CRISIS_CATALOG["2008_GFC"]
    assert gfc.start_date == "2008-09-01"
    assert gfc.end_date == "2009-03-31"
    assert gfc.vix_peak is not None and gfc.vix_peak > 70.0

    covid = CRISIS_CATALOG["2020_COVID"]
    assert covid.start_date == "2020-02-15"
    assert covid.vix_peak is not None and covid.vix_peak > 80.0


def test_resolve_proxy_symbols():
    # In 2008: micro-futures don't exist -> map to ETF proxies
    resolved_2008 = CrisisReplayEngine.resolve_proxy_symbols(["/MES", "/MNQ", "/MGC", "SPY"], start_year=2008)
    assert resolved_2008 == ["SPY", "QQQ", "GLD"]

    # In 2020: micro-futures exist -> keep as-is
    resolved_2020 = CrisisReplayEngine.resolve_proxy_symbols(["/MES", "/MNQ", "SPY"], start_year=2020)
    assert resolved_2020 == ["/MES", "/MNQ", "SPY"]


def test_replay_scenario_execution():
    config = AppConfig(
        contracts={
            "SPY": ContractConfig(
                ticker="SPY",
                name="SPY ETF",
                multiplier=1.0,
                tick_size=0.01,
                asset_class=AssetClass.EQUITY,
            )
        }
    )

    engine = CrisisReplayEngine(config=config)
    covid_scenario = CRISIS_CATALOG["2020_COVID"]
    md = _make_synthetic_crisis_market_data("SPY", start_date=covid_scenario.start_date, n_bars=35)

    result = engine.replay_scenario(
        scenario_id="2020_COVID",
        symbols=["SPY"],
        market_data_map={"SPY": md},
        initial_cash=100000.0,
    )

    assert result.scenario.scenario_id == "2020_COVID"
    assert result.starting_cash == 100000.0
    assert result.ending_equity > 0.0
    assert result.pass_fail in ("PASS", "WARNING", "FAIL")
    assert result.backtest_result is not None


def test_replay_unknown_scenario_raises():
    config = AppConfig()
    engine = CrisisReplayEngine(config=config)
    with pytest.raises(ValueError, match="Unknown crisis scenario"):
        engine.replay_scenario("1929_GREAT_DEPRESSION")


def test_simulate_instantaneous_shock():
    positions = [
        {"symbol": "SPY", "quantity": 100.0, "entry_price": 500.0, "multiplier": 1.0, "direction": "LONG"},
        {"symbol": "/MES", "quantity": 1.0, "entry_price": 5000.0, "multiplier": 5.0, "direction": "LONG"},
        {"symbol": "GLD", "quantity": 50.0, "entry_price": 200.0, "multiplier": 1.0, "direction": "LONG"},
    ]
    cash = 100000.0

    # Shocks: SPY drops 10%, /MES drops 10%, GLD gains 3%
    # SPY notional = $50,000 -> P&L = -$5,000
    # /MES notional = 5000 * 5 * 1 = $25,000 -> P&L = -$2,500
    # GLD notional = 50 * 200 = $10,000 -> P&L = +$300
    # Total P&L impact = -$7,200
    res = CrisisReplayEngine.simulate_instantaneous_shock(positions, cash=cash)

    assert res.current_open_positions == 3
    assert res.total_open_notional == 85000.0
    assert res.immediate_pnl_impact == -7200.0
    assert res.post_shock_equity == 92800.0
    assert res.post_shock_drawdown_pct == 7.20
    assert res.margin_call_risk is False
    assert res.shock_breakdown["SPY"] == -5000.0
    assert res.shock_breakdown["/MES"] == -2500.0
    assert res.shock_breakdown["GLD"] == 300.0


def test_format_stress_test_report():
    sc1 = CRISIS_CATALOG["2008_GFC"]
    res1 = ScenarioStressResult(
        scenario=sc1,
        starting_cash=100000.0,
        ending_equity=104200.0,
        net_pnl=4200.0,
        net_return_pct=4.20,
        max_drawdown_pct=6.50,
        worst_trade_pnl=-350.0,
        total_trades=8,
        winning_trades=5,
        losing_trades=3,
        win_rate=62.50,
        profit_factor=1.85,
        cash_yield_pnl=1200.0,
        pass_fail="PASS",
    )

    report = format_stress_test_report([res1])
    assert "PORTFOLIO STRESS TESTING: HISTORICAL MACRO CRISIS REPLAY" in report
    assert "2008 Global Financial Crisis" in report
    assert "+4.20%" in report
    assert "6.50%" in report
    assert "PASS" in report


def test_format_instantaneous_shock_report():
    shock = InstantaneousShockResult(
        current_open_positions=2,
        total_open_notional=75000.0,
        immediate_pnl_impact=-4500.0,
        post_shock_equity=95500.0,
        post_shock_drawdown_pct=4.50,
        margin_call_risk=False,
        shock_breakdown={"SPY": -3000.0, "/MES": -1500.0},
    )

    report = format_instantaneous_shock_report(shock)
    assert "INSTANTANEOUS FACTOR SHOCK SIMULATION" in report
    assert "-$4,500.00" in report or "-$4500.00" in report or "-4,500.00" in report
    assert "4.50%" in report
    assert "ACCEPTABLE" in report
