import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.forecasts import CombinedForecast
from agentic_trader.research.alpha.portfolio import PortfolioPolicy, PortfolioSnapshot, build_shadow_portfolio
from agentic_trader.research.alpha.targets import ForecastTarget


@pytest.fixture
def portfolio_inputs(forecast_contract):
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    snapshot = PortfolioSnapshot(
        now,
        "account-1",
        "data-1",
        "config-1",
        100000,
        {"SPY": 10000, "QQQ": 5000},
        {},
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
    forecasts = (
        CombinedForecast("QQQ", forecast_contract, now, 0.002, 0.0001, ("alpha_a",), ("family-a",), ("calibration-a",)),
    )
    return now, snapshot, returns, forecasts


def test_shadow_portfolio_keeps_filled_holdings_and_locks(portfolio_inputs):
    now, snapshot, returns, forecasts = portfolio_inputs
    result = build_shadow_portfolio(forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract)
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
        build_shadow_portfolio(forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract)


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
        build_shadow_portfolio(
            forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract, policy=policy
        )


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
        build_shadow_portfolio(
            forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract, policy=policy
        )
    snapshot = replace(
        snapshot,
        groups={"SPY": ("equity", "broad"), "QQQ": ("equity", "technology")},
        group_caps={"equity": 40000, "broad": 20000, "technology": 10000},
        factor_exposures={"SPY": {"beta": 1}, "QQQ": {"beta": 1.2}},
    )
    result = build_shadow_portfolio(
        forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract, policy=policy
    )
    assert result["result"]["success"]
    qqq, spy = result["result"]["weights"]
    assert abs(qqq * 1.2 + spy) <= 0.11 + 1e-7
    assert abs(qqq) <= 0.1 + 1e-7


@pytest.mark.parametrize(
    "holding,alpha,allowance", [(-10000, 0.02, 5000), (10000, -0.02, 5000), (10000, 0.02, 1000), (0, 0.02, 5000)]
)
def test_participation_bounds_traded_notional_not_final_holding(portfolio_inputs, holding, alpha, allowance):
    now, snapshot, returns, forecasts = portfolio_inputs
    snapshot = replace(
        snapshot,
        holdings={"QQQ": holding},
        reservations={},
        locked_symbols=frozenset(),
        liquidity_caps={"QQQ": allowance},
        groups={"QQQ": "equity"},
    )
    forecast = replace(forecasts[0], expected_return=alpha)
    result = build_shadow_portfolio(
        (forecast,), returns[["QQQ"]], snapshot, now=now, risk_contract=forecasts[0].contract
    )["result"]
    assert result["success"], result["message"]
    traded = result["weights"][0] * snapshot.equity - holding
    assert abs(traded) <= allowance + 0.01
    assert traded * alpha > 0


@pytest.mark.parametrize("reservation", [-5000, 5000])
def test_pending_orders_are_not_treated_as_filled_inventory(portfolio_inputs, reservation):
    now, snapshot, returns, forecasts = portfolio_inputs
    snapshot = replace(snapshot, reservations={"QQQ": reservation})
    with pytest.raises(ValueError, match="pending"):
        build_shadow_portfolio(forecasts, returns, snapshot, now=now, risk_contract=forecasts[0].contract)


@pytest.mark.parametrize("defect", ["daily_risk", "future_risk", "old_risk", "naive_risk", "nan_error"])
def test_risk_and_forecast_contracts_fail_closed(portfolio_inputs, defect):

    now, snapshot, returns, forecasts = portfolio_inputs
    contract = forecasts[0].contract
    if defect == "daily_risk":
        contract = replace(contract, target=ForecastTarget("1d"))
    elif defect == "future_risk":
        returns.index += pd.Timedelta(hours=1)
    elif defect == "old_risk":
        returns.index -= pd.Timedelta(days=5)
    elif defect == "naive_risk":
        returns.index = returns.index.tz_localize(None)
    else:
        forecasts = (replace(forecasts[0], standard_error=float("nan")),)
    with pytest.raises(ValueError):
        build_shadow_portfolio(forecasts, returns, snapshot, now=now, risk_contract=contract)


def test_portfolio_cli_persists_exact_input_and_solver_evidence(portfolio_inputs, tmp_path, monkeypatch):
    now, snapshot, returns, forecasts = portfolio_inputs
    payload = {
        "snapshot": {
            **asdict(snapshot),
            "as_of": now.isoformat(),
            **{k: sorted(getattr(snapshot, k)) for k in ("locked_symbols", "shortable", "tradable")},
        },
        "forecasts": [{**asdict(f), "observed_at": f.observed_at.isoformat()} for f in forecasts],
        "risk_contract": asdict(forecasts[0].contract),
        "returns": {
            "columns": returns.columns.tolist(),
            "index": [t.isoformat() for t in returns.index],
            "data": returns.to_numpy().tolist(),
        },
    }
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(json.dumps(payload))

    class Clock:
        fromisoformat = datetime.fromisoformat

        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.datetime", Clock)
    result = CliRunner().invoke(cli, ["alpha", "portfolio", str(source), "--output", str(output)])
    assert result.exit_code == 0, result.exception
    report = json.loads(output.read_text())
    assert report["input"] == json.loads(source.read_text())
    assert report["output"]["result"]["success"]
    assert (
        report["input_hash"]
        == hashlib.sha256(json.dumps(report["input"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    )
    assert report["output"]["result"]["diagnostics"]["constraint_margins"]
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    retry = CliRunner().invoke(cli, ["alpha", "portfolio", str(source), "--output", str(output)])
    assert retry.exit_code != 0
    assert output.read_bytes() == before
