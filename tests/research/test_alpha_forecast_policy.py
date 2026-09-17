"""Timing and transaction-cost screens must not become fictitious broker fills."""

from dataclasses import replace
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, benchmark_models
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy, evaluate_daily_policy
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget, forecast_labels


@pytest.mark.parametrize(
    "label,expected",
    [
        (ForecastLabel.CLOSE_TO_CLOSE, [0.2, -0.1, np.nan]),
        (ForecastLabel.NEXT_OPEN_TO_CLOSE, [0.0, 0.08, np.nan]),
    ],
)
def test_label_endpoints_are_explicit_and_do_not_capture_untradeable_overnight_move(label, expected):
    frame = pd.DataFrame({"open": [90.0, 120.0, 100.0], "close": [100.0, 120.0, 108.0]})
    result = forecast_labels(frame, ForecastTarget("1d", label=label))
    np.testing.assert_allclose(result, expected, equal_nan=True)


@pytest.fixture
def policy_case():
    index = pd.date_range("2024-01-02", periods=5, freq="B", tz="UTC")
    bars = pd.DataFrame(
        {"open": [100.0, 120.0, 90.0, 100.0, 100.0], "close": [100.0, 120.0, 99.0, 90.0, 110.0]}, index=index
    )
    forecasts = pd.DataFrame({"prediction": [0.1, 0.02, np.nan, -0.01]}, index=index[:-1])
    folds = [{"validation_start": 0, "validation_end": 5}]
    bars.attrs["timeframe"] = "1d"
    return bars, forecasts, folds


@pytest.mark.parametrize("cost", [0.0, 1.0, 100.0])
def test_long_flat_cash_flows_use_next_open_and_both_cost_legs(policy_case, cost):
    bars, forecasts, folds = policy_case
    result = evaluate_daily_policy(bars, forecasts, folds, DailyLongFlatPolicy((cost,)))
    observations = result[0].observations
    slip = cost / 10000
    assert list(observations.position) == [0.0, 1.0, 1.0, 0.0, 0.0]
    np.testing.assert_allclose(
        observations.net_return, [0.0, (1 - slip) / (1 + slip) - 1, 1.1 * (1 - slip) / (1 + slip) - 1, 0.0, 0.0]
    )
    assert result[0].metrics["sample_length"] == 5  # Cash/warmup/missing scores are observations.
    assert result[0].metrics["total_trades"] == 2
    assert result[0].missing_forecasts == 1


def test_cost_sensitivity_preserves_decisions_and_reduces_each_payoff(policy_case):
    bars, forecasts, folds = policy_case
    results = evaluate_daily_policy(bars, forecasts, folds, DailyLongFlatPolicy((0.0, 1.0, 5.0, 10.0)))
    for cheaper, dearer in pairwise(results):
        pd.testing.assert_series_equal(cheaper.observations.position, dearer.observations.position)
        assert (cheaper.observations.net_return >= dearer.observations.net_return).all()


def test_first_bar_of_each_fold_starts_flat_and_cannot_replay_previous_forecast(policy_case):
    bars, forecasts, _ = policy_case
    forecasts.prediction = 0.1
    folds = [{"validation_start": 0, "validation_end": 3}, {"validation_start": 3, "validation_end": 5}]
    # A forecast whose exit lies outside its originating fold is not eligible.
    forecasts = forecasts.drop(bars.index[2])
    result = evaluate_daily_policy(bars, forecasts, folds, DailyLongFlatPolicy((0.0,)))[0]
    assert list(result.observations.position) == [0.0, 1.0, 1.0, 0.0, 1.0]


@pytest.mark.parametrize("bad", [np.nan, np.inf, 0.0, -1.0])
def test_unknown_entry_or_exit_prices_invalidate_policy_even_when_forecast_missing(policy_case, bad):
    bars, forecasts, folds = policy_case
    bars.loc[bars.index[3], "open"] = bad
    with pytest.raises(ValueError, match="price"):
        evaluate_daily_policy(bars, forecasts, folds, DailyLongFlatPolicy((0.0,)))


@pytest.mark.parametrize("costs", [(), (0.0, 0.0), (5.0, 1.0), (-1.0,), (np.nan,), (True,), (10000.0,)])
def test_cost_policy_rejects_ambiguous_or_unbounded_scenarios(costs):
    with pytest.raises(ValueError):
        DailyLongFlatPolicy(costs)


def test_selected_feature_and_target_are_identity_and_trial_budget(forecast_market):
    plan = ForecastBenchmarkPlan(
        ForecastTarget("1d", label=ForecastLabel.NEXT_OPEN_TO_CLOSE),
        method="single",
        budget=1,
        features=("open_gap",),
        execution=DailyLongFlatPolicy((0.0, 1.0, 5.0, 10.0)),
    )
    result = benchmark_models(forecast_market, plan)
    assert plan.trial_count == result.document()["trial_count"] == 5
    assert result.trials[0].parameters["features"] == ["open_gap"]
    assert len(result.trials[0].execution) == 4
    assert plan.identity != replace(plan, target=ForecastTarget("1d")).identity
    assert plan.identity != replace(plan, features=("roc(close,20)",)).identity


@pytest.mark.parametrize("target", [ForecastTarget("1h"), ForecastTarget("1d", 5)])
def test_nonoverlap_screen_cannot_claim_multi_bar_or_intraday_execution(target):
    with pytest.raises(ValueError, match="daily|horizon"):
        ForecastBenchmarkPlan(target, execution=DailyLongFlatPolicy((0.0,)))


def test_holdout_and_future_prices_cannot_change_past_decisions(forecast_market):
    plan = ForecastBenchmarkPlan(
        ForecastTarget("1d", label=ForecastLabel.NEXT_OPEN_TO_CLOSE),
        method="single",
        budget=1,
        features=("open_gap",),
        execution=DailyLongFlatPolicy((1.0,)),
    )
    initial = benchmark_models(forecast_market, plan)
    changed = forecast_market.copy()
    changed.iloc[480:] = np.nan
    assert initial.document() == benchmark_models(changed, plan).document()
    changed.iloc[300:480, :4] *= 4
    other = benchmark_models(changed, plan)
    cutoff = forecast_market.index[300]
    pd.testing.assert_series_equal(
        initial.trials[0].predictions.prediction.loc[:cutoff].iloc[:-1],
        other.trials[0].predictions.prediction.loc[:cutoff].iloc[:-1],
    )
    pd.testing.assert_frame_equal(
        initial.trials[0].execution[0].observations.loc[:cutoff].iloc[:-1],
        other.trials[0].execution[0].observations.loc[:cutoff].iloc[:-1],
    )


@pytest.mark.parametrize("edge_scope", ["overnight", "intraday"])
def test_control_distinguishes_predictive_overnight_edge_from_executable_daytime_edge(edge_scope):
    rng = np.random.default_rng(903)
    shocks = rng.normal(0, 0.01, 600)
    opening, close, previous_gap = [], [], 0.0
    for shock in shocks:
        gap = 0.8 * previous_gap + shock if edge_scope == "overnight" else shock
        o = (close[-1] if close else 100.0) * (1 + gap)
        c = o if edge_scope == "overnight" else o * (1 + 0.8 * previous_gap)
        opening.append(o)
        close.append(c)
        previous_gap = gap
    frame = pd.DataFrame({"open": opening, "close": close}, index=pd.date_range("2020-01-01", periods=600, tz="UTC"))
    frame.attrs["timeframe"] = "1d"
    plan = ForecastBenchmarkPlan(
        ForecastTarget("1d"),
        method="single",
        budget=1,
        features=("open_gap",),
        execution=DailyLongFlatPolicy((0.0, 1.0)),
    )
    historical = benchmark_models(frame, plan).trials[0]
    delayed = benchmark_models(
        frame, replace(plan, target=ForecastTarget("1d", label=ForecastLabel.NEXT_OPEN_TO_CLOSE))
    ).trials[0]
    if edge_scope == "overnight":
        assert historical.metrics["skill_vs_training_mean"] > 0.4
        assert historical.execution[0].metrics["total_return_pct"] == 0
        assert historical.execution[1].metrics["total_return_pct"] < 0
        assert delayed.metrics["skill_vs_training_mean"] is None  # Zero daytime target variance.
    else:
        assert delayed.metrics["skill_vs_training_mean"] > 0.99
        assert all(f["metrics"]["total_return_pct"] > 0 for f in delayed.execution[1].folds)


@pytest.mark.parametrize("features", [(), ["open_gap"], ("roc(close,5)", "roc(close, 5)"), ("delay(close,-1)",)])
def test_feature_sets_are_immutable_causal_and_deduplicated(features):
    with pytest.raises(ValueError):
        ForecastBenchmarkPlan(ForecastTarget("1d"), features=features)


@pytest.mark.parametrize("defect", ["hourly", "session"])
def test_payoff_screen_requires_fixed_daily_clock(policy_case, defect):
    bars, forecasts, folds = policy_case
    if defect == "hourly":
        bars.attrs["timeframe"] = "1h"
    else:
        bars.attrs["bar_layout"] = "rth_open_v1"
    with pytest.raises(ValueError):
        evaluate_daily_policy(bars, forecasts, folds, DailyLongFlatPolicy((0.0,)))
