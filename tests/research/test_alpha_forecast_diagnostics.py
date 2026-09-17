"""Diagnostic evidence must expose fragile leads without creating new promotion gates."""

import json

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, benchmark_models
from agentic_trader.research.alpha.diagnostics import forecast_diagnostics, policy_diagnostics
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy
from agentic_trader.research.alpha.targets import ForecastTarget


@pytest.fixture
def concentrated_forecasts():
    return pd.DataFrame(
        {"prediction": [0.01, 0.01, 0.02], "training_mean": [0.001] * 3, "target": [-0.01, -0.01, 0.20]},
        index=pd.date_range("2024-01-01", periods=3, tz="UTC"),
    )


def test_influence_exposes_cancelling_errors_without_removing_observations(concentrated_forecasts):
    frame = concentrated_forecasts
    original = frame.copy(deep=True)
    result = forecast_diagnostics(frame)
    gain = (frame.target - frame.training_mean) ** 2 - (frame.target - frame.prediction) ** 2
    assert result["error_reduction"]["sum"] == pytest.approx(gain.sum())
    assert result["error_reduction"]["largest_positive_share_of_net"] > 1
    assert result["error_reduction"]["net_without_largest_positive"] < 0
    assert result["error_reduction"]["most_positive"][0]["decision_at"] == str(frame.index[-1])
    assert result["direction_accuracy"] == pytest.approx(1 / 3)
    assert result["paired_distributions"]["prediction"]["mean"] == pytest.approx(frame.prediction.mean())
    assert result["rows"] == result["paired_rows"] == 3
    assert result["authorizes_promotion"] is False
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("case", ["missing", "constant", "worse"])
def test_undefined_ratios_are_explicit_and_json_finite(case):
    frame = pd.DataFrame({"prediction": [0.0, 0.0], "target": [0.0, 0.0], "training_mean": [0.0, 0.0]})
    if case == "missing":
        frame.prediction = [np.nan, np.inf]
    if case == "worse":
        frame.prediction = 1.0
    result = forecast_diagnostics(frame)
    assert result["error_reduction"]["largest_positive_share_of_net"] is None
    assert result["paired_rows"] == (0 if case == "missing" else 2)
    assert result["unavailable_predictions"] == (2 if case == "missing" else 0)
    json.dumps(result, allow_nan=False)


def test_broad_signal_has_distributed_improvement():
    target = np.tile([-0.01, 0.01], 50)
    result = forecast_diagnostics(pd.DataFrame({"prediction": target, "target": target, "training_mean": 0.0}))
    assert result["direction_accuracy"] == 1
    assert result["error_reduction"]["largest_positive_share_of_net"] == pytest.approx(0.01)
    assert result["error_reduction"]["net_without_largest_positive"] > 0


def test_policy_attribution_counts_cash_and_costs_independently():
    frame = pd.DataFrame(
        {
            "position": [0.0, 1.0, 0.0, 1.0],
            "benchmark_position": [0.0, 1.0, 1.0, 1.0],
            "gross_return": [0.0, 0.1, 0.0, -0.05],
            "net_return": [0.0, 0.09, 0.0, -0.06],
            "benchmark_return": [0.0, 0.09, 0.02, -0.06],
            "turnover_legs": [0.0, 2.0, 0.0, 2.0],
        }
    )
    report = policy_diagnostics(frame)
    assert report["eligible_observations"] == 3
    assert report["entered_observations"] == 2
    assert report["skipped_observations"] == 1
    assert report["exposure_fraction"] == pytest.approx(2 / 3)
    assert report["entered_net_return_mean"] == pytest.approx(0.015)
    assert report["skipped_comparator_net_return_mean"] == pytest.approx(0.02)
    assert report["mean_excess_return"] == pytest.approx(-0.02 / 4)
    assert report["turnover_legs"] == 4
    assert report["gross_return_pct"] == pytest.approx(4.5)
    assert report["net_return_pct"] == pytest.approx(2.46)
    assert report["cost_drag_percentage_points"] == pytest.approx(2.04)


@pytest.mark.parametrize("method", ["single", "ridge", "boosted"])
def test_benchmarks_retain_fitted_evidence_and_fold_diagnostics(forecast_market, method):
    plan = ForecastBenchmarkPlan(
        ForecastTarget("1d"), method=method, budget=1, features=("open_gap",), execution=DailyLongFlatPolicy((0.0, 1.0))
    )
    trial = benchmark_models(forecast_market, plan).trials[0]
    assert trial.diagnostics["total_folds"] == 3
    assert trial.diagnostics["positive_skill_folds"] == 3
    for fold in trial.folds:
        evidence = fold["fitted_model"]
        assert len(evidence["training_input_hash"]) == 64
        assert evidence["features"] == ["open_gap"]
        assert evidence["estimator_parameters"]
        assert fold["diagnostics"]["paired_rows"] == fold["metrics"]["observations"]
        if method != "boosted":
            linear = evidence["linear_original_units"]
            selected = trial.predictions[trial.predictions.fold == fold["diagnostics"]["fold"]]
            feature = forecast_market.open / forecast_market.close.shift(1) - 1
            reconstructed = linear["intercept"] + linear["coefficients"][0] * feature.loc[selected.index]
            np.testing.assert_allclose(reconstructed, selected.prediction, rtol=0, atol=1e-12)
    assert trial.execution[1].document()["diagnostics"]["cost_drag_percentage_points"] > 0
    assert all("diagnostics" in fold for fold in trial.execution[1].folds)
    json.dumps(trial.document(), allow_nan=False)


@pytest.mark.parametrize("eligible", [0.0, 1.0])
def test_cash_only_policy_keeps_undefined_selected_statistics(eligible):
    frame = pd.DataFrame(
        {
            "position": [0.0],
            "benchmark_position": [eligible],
            "gross_return": [0.0],
            "net_return": [0.0],
            "benchmark_return": [0.0],
            "turnover_legs": [0.0],
        }
    )
    result = policy_diagnostics(frame)
    assert result["entered_net_return_mean"] is None
    assert result["exposure_fraction"] == (0 if eligible else None)
    assert result["observations"] == 1
    json.dumps(result, allow_nan=False)
    frame.loc[0, "benchmark_return"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        policy_diagnostics(frame)
