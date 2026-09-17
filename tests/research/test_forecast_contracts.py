from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.forecasts import (
    AlphaForecast,
    ForecastCalibration,
    combine_forecasts,
    validate_forecast,
)
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.fixture
def forecast(forecast_contract):
    return AlphaForecast(
        "a",
        "SPY",
        forecast_contract,
        datetime(2026, 9, 17, 12, tzinfo=UTC),
        0.01,
        0.002,
        1,
        "family-a",
        "calibration-a",
    )


@pytest.mark.parametrize("change", ["horizon", "label", "feed", "adjustment", "currency"])
def test_pooling_rejects_incompatible_return_contracts(forecast, change):

    contract = forecast.contract
    updates = {
        "horizon": {"target": ForecastTarget("1h", 5)},
        "label": {"target": ForecastTarget("1h", 1, ForecastLabel.NEXT_OPEN_TO_CLOSE)},
        "feed": {"feed": "alpaca:iex"},
        "adjustment": {"adjustment": "all"},
        "currency": {"currency": "EUR"},
    }
    other = replace(
        forecast,
        version_id="b",
        family_id="family-b",
        calibration_id="calibration-b",
        contract=replace(contract, **updates[change]),
    )
    with pytest.raises(ValueError, match="contract"):
        combine_forecasts([forecast, other], as_of=forecast.observed_at)


def test_version_rename_does_not_create_an_independent_forecast(forecast):
    copy = replace(forecast, version_id="renamed-version")
    with pytest.raises(ValueError, match="family"):
        combine_forecasts([forecast, copy], as_of=forecast.observed_at)


@pytest.fixture
def calibration_inputs(forecast_contract):
    index = pd.date_range("2025-01-01", periods=100, freq="h", tz="UTC")
    rng = np.random.default_rng(91)
    scores = pd.Series(rng.normal(size=100), index=index)
    returns = 0.003 * scores + pd.Series(rng.normal(0, 0.01, 100), index=index)
    observed = pd.Series(index + pd.Timedelta(hours=1), index=index)
    return (
        scores,
        returns,
        {"contract": forecast_contract, "trained_until": observed.iloc[-1].isoformat(), "label_observed_at": observed},
    )


def test_calibration_checks_label_availability_at_its_boundary(calibration_inputs):
    scores, returns, args = calibration_inputs
    args["trained_until"] = scores.index[-1].isoformat()
    with pytest.raises(ValueError, match="available"):
        ForecastCalibration.fit(scores, returns, **args)


def test_calibration_estimates_mean_error_separately_from_return_volatility(calibration_inputs):
    scores, returns, args = calibration_inputs
    model = ForecastCalibration.fit(scores, returns, **args)
    assert 0 < model.standard_error(0) < model.return_volatility
    assert model.standard_error(5) > model.standard_error(0)
    assert ForecastCalibration.from_document(model.document()) == model
    assert model.contract == args["contract"]


def test_uncertainty_changes_allocations_without_assuming_independence():
    optimizer = ConvexAlphaPortfolioOptimizer(uncertainty_aversion=1, risk_aversion=1)
    low = optimizer.optimize(np.array([0.002]), np.array([[0.01]]), alpha_standard_error=[0.0001])
    high = optimizer.optimize(np.array([0.002]), np.array([[0.01]]), alpha_standard_error=[0.01])
    assert low.success and high.success
    assert low.weights[0] > 0.1
    assert abs(high.weights[0]) < 1e-6
    assert low.uncertainty_penalty > 0
    assert low.objective == pytest.approx(
        low.alpha_capture - low.risk_penalty - low.transaction_cost - low.uncertainty_penalty
    )


@pytest.mark.parametrize("defect", ["naive_availability", "invalid_outcome", "negative_covariance", "empty_provenance"])
def test_calibration_and_forecast_evidence_cannot_be_silently_repaired(calibration_inputs, forecast, defect):
    scores, returns, args = calibration_inputs
    if defect == "naive_availability":
        args["label_observed_at"] = args["label_observed_at"].dt.tz_localize(None)
        with pytest.raises(TypeError):
            ForecastCalibration.fit(scores, returns, **args)
    elif defect == "invalid_outcome":
        returns.iloc[50] = np.inf
        with pytest.raises(ValueError):
            ForecastCalibration.fit(scores, returns, **args)
    elif defect == "negative_covariance":
        model = ForecastCalibration.fit(scores, returns, **args)
        document = model.document()
        document["coefficient_covariance"] = ((-1, 0), (0, 1))
        with pytest.raises(ValueError):
            ForecastCalibration.from_document(document)
    else:
        combined = combine_forecasts([forecast], as_of=forecast.observed_at)[0]
        with pytest.raises(ValueError):
            validate_forecast(replace(combined, families=()), forecast.observed_at)
