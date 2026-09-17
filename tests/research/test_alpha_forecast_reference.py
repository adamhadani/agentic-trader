"""Independent numeric references for forecast timing and policy accounting.

These use scalar cash flows and closed-form OLS, not the production label builder,
fold generator, estimator, DSL evaluator or return-statistics helper.
"""

from math import prod

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, benchmark_models, forecast_metrics
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.mark.parametrize("label", list(ForecastLabel))
@pytest.mark.parametrize("horizon", [1, 5])
def test_forecasts_and_daily_cash_flows_match_independent_reference(forecast_market, label, horizon):
    policy = DailyLongFlatPolicy((0.0, 1.0, 5.0)) if horizon == 1 else None
    plan = ForecastBenchmarkPlan(
        ForecastTarget("1d", horizon, label),
        method="single",
        budget=1,
        features=("open_gap",),
        execution=policy,
    )
    trial = benchmark_models(forecast_market, plan).trials[0]
    # Fixture: 600 observations; 480 discovery; three 80-bar validation folds.
    close = forecast_market.close.to_numpy()[:480]
    opening = forecast_market.open.to_numpy()[:480]
    feature = np.array([np.nan, *[opening[t] / close[t - 1] - 1 for t in range(1, 480)]])
    labels = np.array(
        [
            close[t + horizon] / (close[t] if label == ForecastLabel.CLOSE_TO_CLOSE else opening[t + 1]) - 1
            for t in range(480 - horizon)
        ]
    )
    all_predictions = []
    for fold_number, start in enumerate((240, 320, 400)):
        train_end = start - horizon - 1  # One embargo bar; end is exclusive.
        x, y = feature[1:train_end], labels[1:train_end]
        slope = sum((x - x.mean()) * (y - y.mean())) / sum((x - x.mean()) ** 2)
        intercept = y.mean() - slope * x.mean()
        expected = intercept + slope * feature[start : start + 80 - horizon]
        actual = trial.predictions.loc[forecast_market.index[start : start + 80 - horizon]]
        np.testing.assert_allclose(actual.prediction, expected, rtol=0, atol=1e-12)
        np.testing.assert_allclose(actual.target, labels[start : start + 80 - horizon], rtol=0, atol=1e-12)
        np.testing.assert_allclose(actual.training_mean, y.mean(), rtol=0, atol=1e-12)
        assert trial.folds[fold_number]["last_training_label_position"] < start
        all_predictions.extend(expected)
        for scenario in trial.execution:
            cost = scenario.cost_bps / 10000
            expected_returns, comparator, entries = [0.0], [0.0], 0
            for offset, prediction in enumerate(expected, start=1):
                t = start + offset
                # Buy with one unit of wealth, paying entry cost; sell all shares net of exit cost.
                shares = 1 / (opening[t] * (1 + cost))
                payoff = shares * close[t] * (1 - cost) - 1
                comparator.append(payoff)
                expected_returns.append(payoff if prediction > 0 else 0.0)
                entries += int(prediction > 0)
            observed = scenario.observations.iloc[fold_number * 80 : (fold_number + 1) * 80]
            np.testing.assert_allclose(observed.net_return, expected_returns, rtol=0, atol=1e-12)
            np.testing.assert_allclose(observed.benchmark_return, comparator, rtol=0, atol=1e-12)
            metrics = scenario.folds[fold_number]["metrics"]
            assert metrics["total_trades"] == entries
            assert metrics["total_return_pct"] == pytest.approx(100 * (prod(1 + r for r in expected_returns) - 1))
    assert len(all_predictions) == 3 * (80 - horizon)


def test_positive_squared_error_skill_does_not_establish_directional_or_trading_skill():
    # One large move improves squared error more than two wrong-sign errors lose.
    # All forecasts and the baseline are positive: their long/cash decisions coincide.
    observations = pd.DataFrame(
        {"prediction": [0.01, 0.01, 0.02], "training_mean": [0.001] * 3, "target": [-0.01, -0.01, 0.20]}
    )
    assert forecast_metrics(observations)["skill_vs_training_mean"] > 0
    assert (observations.prediction.gt(0) == observations.target.gt(0)).mean() == pytest.approx(1 / 3)
    assert observations.prediction.gt(0).equals(observations.training_mean.gt(0))
    gains = (observations.target - observations.training_mean) ** 2 - (
        observations.target - observations.prediction
    ) ** 2
    assert gains.iloc[-1] > gains.sum() > 0
