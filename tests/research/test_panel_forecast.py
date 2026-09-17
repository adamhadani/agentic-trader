"""Causal forecast support, fitting and frozen decisions over incomplete cohorts."""

import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.market.quality import BarSourceQuality
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


def synthetic_case(*, seed=21, effect=0.02):
    clock = pd.date_range("2023-01-02", periods=170, freq="B", tz="America/New_York")
    symbols = tuple(f"A{i}" for i in range(6))
    rng = np.random.default_rng(seed)
    pulse = rng.uniform(-1, 1, (len(clock), len(symbols)))
    noise = rng.normal(0, 0.001, pulse.shape)
    returns = noise + effect * np.vstack([np.zeros(len(symbols)), pulse[:-1]])
    close = 100 * np.cumprod(1 + returns, axis=0)
    opened = np.vstack([np.full(len(symbols), 100), close[:-1]])
    frames = {}
    for j, symbol in enumerate(symbols):
        frame = pd.DataFrame(
            {
                "open": opened[:, j],
                "high": np.maximum(opened[:, j], close[:, j]) * 1.001,
                "low": np.minimum(opened[:, j], close[:, j]) * 0.999,
                "close": close[:, j],
                "volume": 1000 + 100 * pulse[:, j],
            },
            index=clock,
        )
        frame.attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
        frames[symbol] = frame
    plan = PanelForecastPlan(
        campaign_id="fixture",
        selection=ForecastSelection("stocks", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2024-01-01T12:00Z")),
        cohorts=(ForecastCohort("stocks", symbols, top_k=1, min_assets=3),),
        start=clock[0].date(),
        end=clock[-1].date(),
        feed="alpaca:iex",
        folds=(PanelFold("test", clock[80].date(), clock[-1].date()),),
        features=(
            PanelHypothesis("pulse", "volume"),
            PanelHypothesis("reversal", "-roc(close,2)"),
            PanelHypothesis("volatility", "realized_vol(returns,3)"),
        ),
        economic_features=("pulse", "reversal"),
        train_sessions=40,
        min_train_sessions=10,
        min_train_rows=20,
        refit_sessions=5,
        history_sessions=5,
        ridge_alpha=1.0,
        target=ForecastTarget("1d", 2, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        ic=ICPolicy(min_assets=3, min_observations=10, hac_lags=2, observations_per_year=252),
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    return SimpleNamespace(plan=plan, frames=frames, clock=clock, sessions=sessions)


@pytest.fixture
def forecast_case():
    return synthetic_case()


def compute(case):
    return compute_panel_forecast(DailyStudyInputs(case.frames), case.clock, case.plan, case.sessions)


def trial(result, model="pulse"):
    return next(row for row in result["trials"] if row["model"] == model)


def test_future_observations_cannot_change_prior_predictions_eligibility_or_fits(forecast_case):
    c = forecast_case
    before = compute(c)
    cutoff = c.clock[130]
    for frame in c.frames.values():
        frame.loc[cutoff:, ["open", "high", "low", "close"]] *= 3
        frame.loc[cutoff:, "volume"] *= 2
    after = compute(c)
    for first, second in zip(before["trials"], after["trials"], strict=True):
        count = sum(pd.Timestamp(t) < cutoff for t in first["predictions"]["dates"])
        assert first["predictions"]["values"][:count] == second["predictions"]["values"][:count]
    for first, second in zip(before["supports"], after["supports"], strict=True):
        count = sum(pd.Timestamp(t) < cutoff for t in first["dates"])
        assert first["eligible"][:count] == second["eligible"][:count]
        assert [f for f in first["fits"] if pd.Timestamp(f["decision_bar"]) < cutoff] == [
            f for f in second["fits"] if pd.Timestamp(f["decision_bar"]) < cutoff
        ]


def test_training_endpoints_are_strictly_prior_and_scaler_uses_training_only(forecast_case):
    result = compute(forecast_case)
    fits = result["supports"][0]["fits"]
    assert fits and all(f["status"] == "fitted" for f in fits)
    for fit in fits:
        assert pd.Timestamp(fit["last_training_label_available_at"]) < pd.Timestamp(fit["decision_at"])
        assert fit["training_dates"] <= forecast_case.plan.train_sessions
        assert fit["training_rows"] >= forecast_case.plan.min_train_rows
        evidence = fit["fitted_model"]
        assert len(evidence["standardization"]["mean"]) == 3
        assert len(evidence["linear_original_units"]["coefficients"]) == 3
    # First refit is index 80; horizon 2 excludes training decisions 78 and later.
    expected_pulse_mean = np.mean([frame["volume"].iloc[38:78].mean() for frame in forecast_case.frames.values()])
    assert fits[0]["fitted_model"]["standardization"]["mean"][0] == pytest.approx(expected_pulse_mean)
    assert trial(result, "ridge")["forecast_metrics"]["skill_vs_training_mean"] > 0
    assert trial(result)["forecast_metrics"] is None
    json.dumps(result, allow_nan=False)


def test_late_listing_becomes_eligible_without_full_history_intersection(forecast_case):
    c = forecast_case
    symbol = c.plan.cohorts[0].symbols[-1]
    c.frames[symbol] = c.frames[symbol].iloc[100:]
    result = compute(c)
    support = result["supports"][0]
    j = support["symbols"].index(symbol)
    observed = {pd.Timestamp(t): row[j] for t, row in zip(support["dates"], support["eligible"], strict=True)}
    assert not observed[c.clock[103]] and observed[c.clock[104]]
    assert result["status"] == "completed"
    assert len(result["coverage"][symbol]["missing_dates"]) == 100
    assert support["stage_counts"]["eligible_symbol_decisions"] < support["stage_counts"]["expected_symbol_decisions"]


@pytest.mark.parametrize("selected", [False, True])
def test_missing_future_outcome_never_changes_frozen_membership(forecast_case, selected):
    c = forecast_case
    baseline = trial(compute(c))
    basket = baseline["baskets"][0]
    symbol = next(s for s, weight in basket["weights"].items() if bool(weight) == selected)
    c.frames[symbol] = c.frames[symbol].drop(pd.Timestamp(basket["entry_bar"]))
    changed = trial(compute(c))
    assert changed["baskets"][0]["weights"] == basket["weights"]
    assert basket["decision_bar"] in changed["ic_missing_label_dates"]
    assert changed["stage_counts"]["predicted_missing_labels"] >= 1
    assert changed["baskets"][0]["status"] == ("unavailable" if selected else "observed")
    if selected:
        assert changed["cost_summaries"][0]["net_return"] is None
        assert changed["cost_summaries"][0]["missing_baskets"] >= 1
    else:
        assert changed["baskets"][0]["gross_return"] == basket["gross_return"]


def test_insufficient_training_does_not_erase_economic_scores(forecast_case):
    c = forecast_case
    c.plan = replace(c.plan, min_train_rows=1000)
    result = compute(c)
    assert all(f["status"] == "unavailable" for f in result["supports"][0]["fits"])
    assert trial(result)["stage_counts"]["available_predictions"] > 0
    assert trial(result, "ridge")["stage_counts"]["available_predictions"] == 0


@pytest.mark.parametrize("effect", [0.0, 0.02])
def test_small_null_and_planted_controls_exercise_the_same_pipeline(effect):
    values = []
    for seed in (11, 23, 35):
        c = synthetic_case(seed=seed, effect=effect)
        c.plan = replace(c.plan, target=ForecastTarget("1d", 1, ForecastLabel.NEXT_OPEN_TO_CLOSE))
        result = compute(c)
        values.append(trial(result)["ic"]["folds"]["test"]["mean_ic"])
        assert not result["authorizes_promotion"]
    if effect:
        assert min(values) > 0.8
    else:
        assert abs(np.mean(values)) < 0.1  # Mechanical negative control, not a calibrated false-positive bound.


@pytest.mark.parametrize("fault", ["source_loss", "negative_price", "failed_member"])
def test_invalid_acquisition_cannot_become_historical_ineligibility(forecast_case, fault):
    c = forecast_case
    batch = DailyStudyInputs(c.frames)
    first = c.plan.cohorts[0].symbols[0]
    if fault == "source_loss":
        c.frames[first].attrs["source_quality"] = BarSourceQuality(
            len(c.clock) + 1, len(c.clock), len(c.clock)
        ).document()
    elif fault == "negative_price":
        c.frames[first].iloc[5, 3] = -1
    else:
        del c.frames[first]
        batch.failures[first] = {"error_type": "APIError"}
    with pytest.raises(ValueError):
        compute_panel_forecast(batch, c.clock, c.plan, c.sessions)


def test_cohorts_fit_independently(forecast_case):
    c = forecast_case
    c.plan = replace(
        c.plan,
        cohorts=(
            ForecastCohort("stocks", c.plan.cohorts[0].symbols[:3], top_k=1, min_assets=3),
            ForecastCohort("control", c.plan.cohorts[0].symbols[3:], top_k=1, min_assets=3),
        ),
    )
    before = compute(c)
    for symbol in c.plan.cohorts[0].symbols:
        c.frames[symbol].loc[:, "volume"] *= 5
    after = compute(c)
    for section in ("supports", "trials"):
        assert [row for row in before[section] if row["cohort"] == "control"] == [
            row for row in after[section] if row["cohort"] == "control"
        ]
