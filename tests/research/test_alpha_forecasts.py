from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.forecasts import AlphaForecast, ForecastCalibration, combine_forecasts
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.registry import ConflictResolver


def test_combination_is_permutation_invariant_and_horizon_aware():

    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    first = AlphaForecast("a", "SPY", "1h", now, 0.01, 0.02, 1)
    second = replace(first, version_id="b", expected_return=-0.004)
    result = combine_forecasts([first, second], as_of=now)
    assert result == combine_forecasts([second, first], as_of=now)
    assert result[0].expected_return == pytest.approx(0.003)
    assert result[0].contributors == ("a", "b")
    with pytest.raises(ValueError, match="horizon"):
        combine_forecasts([first, replace(second, timeframe="4h")], as_of=now)


@pytest.mark.parametrize("defect", ["stale", "future", "duplicate", "nonfinite"])
def test_forecast_rejection_is_explicit(defect):

    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    forecast = AlphaForecast("a", "SPY", "1h", now, 0.01, 0.02, 1)
    forecasts = [forecast]
    if defect == "stale":
        forecasts = [replace(forecast, observed_at=now - timedelta(days=1))]
    if defect == "future":
        forecasts = [replace(forecast, observed_at=now + timedelta(seconds=1))]
    if defect == "duplicate":
        forecasts.append(forecast)
    if defect == "nonfinite":
        forecasts = [replace(forecast, expected_return=float("nan"))]
    with pytest.raises(ValueError):
        combine_forecasts(forecasts, as_of=now)


def test_calibration_fit_uses_past_targets_only():

    scores = pd.Series(np.arange(100) / 100)
    returns = pd.Series(np.arange(100) / 1000)
    model = ForecastCalibration.fit(scores.iloc[:60], returns.iloc[:60], trained_until="2020-01-01")
    changed = returns.copy()
    changed.iloc[60:] *= -1000
    other = ForecastCalibration.fit(scores.iloc[:60], changed.iloc[:60], trained_until="2020-01-01")
    assert model == other
    assert model.predict(0.5) > 0


def test_same_direction_candidates_have_one_deterministic_owner():

    first = ScreenerCandidate(
        contract="SPY",
        timeframe="1h",
        strategy="alpha_a",
        direction="LONG",
        current_price=100,
        ema_20=100,
        ema_50=100,
        ema_200=100,
        rsi_14=50,
        atr_14=2,
        candle_timestamp="2020-01-01",
        recent_swing_low=98,
        recent_swing_high=102,
        trigger_detail="test",
    )
    second = first.model_copy(update={"strategy": "alpha_b"})
    resolved = ConflictResolver.resolve([first, second])
    assert len(resolved) == 1
    assert resolved == ConflictResolver.resolve([second, first])
    assert len(resolved[0].contributors) == 2
