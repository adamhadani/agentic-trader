from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.forecasts import CombinedForecast
from agentic_trader.research.alpha.portfolio import PortfolioPolicy, PortfolioSnapshot, build_shadow_portfolio


@pytest.fixture
def portfolio_inputs():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    snapshot = PortfolioSnapshot(
        now,
        "account-1",
        "data-1",
        "config-1",
        100000,
        {"SPY": 10000},
        {"QQQ": 5000},
        frozenset({"SPY"}),
        frozenset({"SPY", "QQQ"}),
        frozenset({"SPY", "QQQ"}),
        {"SPY": 20000, "QQQ": 20000},
        {"SPY": "equity", "QQQ": "equity"},
        {"equity": 40000},
    )
    returns = pd.DataFrame(
        np.random.default_rng(4).normal(0, 0.01, (100, 2)),
        columns=["SPY", "QQQ"],
        index=pd.date_range(end=now, periods=100, freq="h"),
    )
    forecasts = (CombinedForecast("QQQ", "1h", now, 0.002, 0.01, ("alpha_a", "alpha_b")),)
    return now, snapshot, returns, forecasts


def test_shadow_portfolio_keeps_holdings_reservations_and_locks(portfolio_inputs):
    now, snapshot, returns, forecasts = portfolio_inputs
    result = build_shadow_portfolio(forecasts, returns, snapshot, now=now)
    assert result["mode"] == "shadow"
    assert result["symbols"] == ["QQQ", "SPY"]
    assert result["result"]["success"]
    assert result["result"]["weights"][1] == pytest.approx(0.1, abs=1e-7)
    assert result["result"]["gross_leverage"] <= 0.4 + 1e-7


@pytest.mark.parametrize("defect", ["missing_holding", "stale", "future", "liquidity", "groups"])
def test_missing_account_or_risk_evidence_blocks_targets(portfolio_inputs, defect):
    now, snapshot, returns, forecasts = portfolio_inputs
    if defect == "missing_holding":
        returns = returns[["QQQ"]]
    if defect == "stale":
        snapshot = replace(snapshot, as_of=now - timedelta(minutes=5))
    if defect == "future":
        snapshot = replace(snapshot, as_of=now + timedelta(minutes=5))
    if defect == "liquidity":
        snapshot = replace(snapshot, liquidity_caps={})
    if defect == "groups":
        snapshot = replace(snapshot, group_caps={})
    with pytest.raises(ValueError):
        build_shadow_portfolio(forecasts, returns, snapshot, now=now)


@pytest.mark.parametrize("defect", ["stale_forecast", "future_forecast", "netted_reservation", "too_many_positions"])
def test_portfolio_rejects_ambiguous_or_stale_execution_inputs(portfolio_inputs, defect):
    now, snapshot, returns, forecasts = portfolio_inputs
    policy = PortfolioPolicy()
    if defect == "stale_forecast":
        forecasts = (replace(forecasts[0], observed_at=now - timedelta(hours=2)),)
    elif defect == "future_forecast":
        forecasts = (replace(forecasts[0], observed_at=now + timedelta(seconds=1)),)
    elif defect == "netted_reservation":
        snapshot = replace(snapshot, reservations={"SPY": -5000, "QQQ": 5000})
    else:
        policy = PortfolioPolicy(max_positions=1)
    with pytest.raises(ValueError):
        build_shadow_portfolio(forecasts, returns, snapshot, now=now, policy=policy)


@pytest.mark.parametrize(
    "overrides", [{"gross_limit": -1}, {"covariance_shrinkage": 2}, {"min_observations": 2}, {"max_positions": 1.5}]
)
def test_invalid_portfolio_policy_is_rejected(overrides):
    with pytest.raises(ValueError):
        PortfolioPolicy(**overrides)


def test_factor_and_overlapping_sector_class_bounds(portfolio_inputs):
    now, snapshot, returns, forecasts = portfolio_inputs
    policy = PortfolioPolicy(max_factor_exposure=0.11)
    with pytest.raises(ValueError, match="factors"):
        build_shadow_portfolio(forecasts, returns, snapshot, now=now, policy=policy)
    snapshot = replace(
        snapshot,
        groups={"SPY": ("equity", "broad"), "QQQ": ("equity", "technology")},
        group_caps={"equity": 40000, "broad": 20000, "technology": 10000},
        factor_exposures={"SPY": {"beta": 1}, "QQQ": {"beta": 1.2}},
    )
    result = build_shadow_portfolio(forecasts, returns, snapshot, now=now, policy=policy)
    assert result["result"]["success"]
    qqq, spy = result["result"]["weights"]
    assert abs(qqq * 1.2 + spy) <= 0.11 + 1e-7
    assert abs(qqq) <= 0.1 + 1e-7
