"""Forecast selection measures labels, never a substitute bracket policy."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.baselines import (
    ForecastBenchmarkPlan,
    benchmark_models,
    forecast_metrics,
)
from agentic_trader.research.alpha.targets import ForecastTarget


@pytest.fixture
def forecast_plan():
    return ForecastBenchmarkPlan(target=ForecastTarget("1d"), method="ridge", budget=1, seed=17)


@pytest.mark.parametrize("method", ["single", "ridge", "boosted"])
@pytest.mark.parametrize("horizon", [1, 5])
def test_holdout_cannot_change_forecast_evidence(forecast_market, forecast_plan, method, horizon):
    plan = replace(forecast_plan, method=method, target=ForecastTarget("1d", horizon))
    initial = benchmark_models(forecast_market, plan)
    changed = forecast_market.copy()
    changed.iloc[480:] = np.nan
    other = benchmark_models(changed, plan)
    assert initial.document() == other.document()
    pd.testing.assert_frame_equal(initial.trials[0].predictions, other.trials[0].predictions)
    assert not initial.document()["authorizes_promotion"]
    assert initial.document()["target"]["horizon_bars"] == horizon
    assert "dsr" not in str(initial.trials[0].metrics).lower()
    assert "sharpe" not in str(initial.trials[0].metrics).lower()


@pytest.mark.parametrize("method", ["single", "ridge", "boosted"])
def test_future_observations_do_not_change_earlier_predictions(forecast_market, forecast_plan, method):
    plan = replace(forecast_plan, method=method)
    initial = benchmark_models(forecast_market, plan).trials[0]
    changed = forecast_market.copy()
    changed.iloc[300:, :4] *= 8
    other = benchmark_models(changed, plan).trials[0]
    before = forecast_market.index[300]
    pd.testing.assert_frame_equal(initial.predictions.loc[:before].iloc[:-2], other.predictions.loc[:before].iloc[:-2])
    # The last label before the perturbation is legitimately changed. Compare predictions only at that point.
    pd.testing.assert_series_equal(
        initial.predictions.prediction.loc[:before].iloc[:-1], other.predictions.prediction.loc[:before].iloc[:-1]
    )


def test_planted_forecast_is_detected_without_simulated_orders(forecast_market, forecast_plan):
    result = benchmark_models(forecast_market, forecast_plan)
    trial = result.trials[0]
    assert trial.metrics["skill_vs_training_mean"] > 0.9
    assert trial.metrics["rank_ic"] > 0.9
    assert all(fold["metrics"]["skill_vs_training_mean"] > 0.8 for fold in trial.folds)
    assert trial.predictions.index.is_unique
    assert trial.metrics["observations"] == len(trial.predictions.dropna())


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_training_labels_mature_before_validation_and_scored_labels_stay_in_fold(
    forecast_market, forecast_plan, horizon
):
    plan = replace(forecast_plan, target=ForecastTarget("1d", horizon))
    trial = benchmark_models(forecast_market, plan).trials[0]
    for fold in trial.folds:
        assert fold["last_training_label_position"] < fold["validation_start"] - plan.validation.embargo_bars
        assert fold["last_scored_label_position"] < fold["validation_end"]
        assert fold["last_training_label_position"] == fold["last_training_feature_position"] + horizon


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, 61])
def test_target_rejects_invalid_horizon(bad):
    with pytest.raises(ValueError, match="horizon"):
        ForecastTarget("1d", bad)


@pytest.mark.parametrize("bad", ["session", "weekly", "1m"])
def test_target_requires_declared_supported_clock(bad):
    with pytest.raises(ValueError, match="timeframe"):
        ForecastTarget(bad)


def test_constant_market_reports_undefined_skill_without_fabrication(forecast_market, forecast_plan):
    metrics = forecast_metrics(
        pd.DataFrame({"prediction": [0.0, 0.0], "target": [0.0, 0.0], "training_mean": [0.0, 0.0]})
    )
    assert metrics["rank_ic"] is None
    assert metrics["skill_vs_training_mean"] is None
    assert metrics["rmse"] == 0


def test_forecast_comparison_uses_identical_support_and_reports_missing_rows():
    observations = pd.DataFrame(
        {
            "prediction": [1.0, 2.0, np.nan, 100.0],
            "target": [2.0, 2.0, 9.0, np.nan],
            "training_mean": [0.0, 0.0, 0.0, 0.0],
        }
    )
    metrics = forecast_metrics(observations)
    assert metrics["observations"] == 2
    assert metrics["rmse"] == pytest.approx(np.sqrt(0.5))
    assert metrics["baseline_rmse"] == 2
    assert metrics["skill_vs_training_mean"] == 0.875
    assert metrics["rank_ic"] is None


def test_feature_holes_are_visible_and_never_imputed(forecast_market, forecast_plan):
    forecast_market.loc[forecast_market.index[300:305], "volume"] = np.nan
    trial = benchmark_models(forecast_market, forecast_plan).trials[0]
    assert trial.predictions.prediction.isna().sum() >= 5
    assert sum(f["validation_rows"] for f in trial.folds) > trial.metrics["observations"]


@pytest.mark.parametrize(
    "field,value", [("budget", True), ("budget", 101), ("seed", -1), ("seed", True), ("method", "unknown")]
)
def test_invalid_plans_fail_before_data_access(forecast_plan, field, value):
    with pytest.raises(ValueError):
        replace(forecast_plan, **{field: value})


def test_horizon_changes_protocol_identity(forecast_plan):
    assert forecast_plan.identity != replace(forecast_plan, target=ForecastTarget("1d", 5)).identity
