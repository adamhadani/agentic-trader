"""Calibrated forecasts and deterministic combination for shadow portfolio research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from agentic_trader.market.bars import BAR_DURATIONS


@dataclass(frozen=True)
class ForecastCalibration:
    slope: float
    intercept: float
    return_volatility: float
    trained_until: str
    observations: int
    shrinkage: float = 0.5

    @classmethod
    def fit(cls, scores: pd.Series, forward_returns: pd.Series, *, trained_until: str, shrinkage: float = 0.5):
        if not scores.index.equals(forward_returns.index):
            raise ValueError("Calibration labels must align")
        joined = pd.DataFrame({"x": scores, "y": forward_returns}).replace([np.inf, -np.inf], np.nan).dropna()
        if len(joined) < 30 or joined.x.std() <= 1e-10 or not 0 <= shrinkage <= 1:
            raise ValueError("Insufficient calibration evidence")
        centered = joined.x - joined.x.mean()
        slope = float(centered @ (joined.y - joined.y.mean()) / (centered @ centered))
        return cls(
            slope,
            float(joined.y.mean() - slope * joined.x.mean()),
            float(joined.y.std()),
            trained_until,
            len(joined),
            shrinkage,
        )

    def predict(self, score: float):
        if not np.isfinite(score):
            raise ValueError("Missing forecast score")
        return self.shrinkage * (self.intercept + self.slope * float(np.clip(score, -5, 5)))


@dataclass(frozen=True)
class AlphaForecast:
    version_id: str
    symbol: str
    timeframe: str
    observed_at: datetime
    expected_return: float
    uncertainty: float
    weight: float


@dataclass(frozen=True)
class CombinedForecast:
    symbol: str
    timeframe: str
    observed_at: datetime
    expected_return: float
    uncertainty: float
    contributors: tuple[str, ...]


def combine_forecasts(forecasts: list[AlphaForecast], *, as_of: datetime) -> tuple[CombinedForecast, ...]:
    """Shrunk inverse-uncertainty pooling. Incompatible horizons must stay separate."""
    grouped: dict[str, list[AlphaForecast]] = {}
    seen = set()
    for forecast in forecasts:
        key = (forecast.symbol, forecast.version_id)
        if key in seen:
            raise ValueError("Duplicate forecast version")
        seen.add(key)
        if forecast.timeframe not in BAR_DURATIONS:
            raise ValueError("Unknown horizon")
        age = as_of - forecast.observed_at
        if age.total_seconds() < 0 or age > BAR_DURATIONS[forecast.timeframe]:
            raise ValueError("Stale or future forecast")
        if (
            not all(np.isfinite(x) for x in (forecast.expected_return, forecast.uncertainty, forecast.weight))
            or min(forecast.uncertainty, forecast.weight) <= 0
        ):
            raise ValueError("Nonfinite or invalid forecast")
        grouped.setdefault(forecast.symbol, []).append(forecast)
    results = []
    for symbol, items in sorted(grouped.items()):
        items = sorted(items, key=lambda item: item.version_id)
        if len({item.timeframe for item in items}) != 1:
            raise ValueError("Cannot pool different forecast horizons")
        if len({item.observed_at for item in items}) != 1:
            raise ValueError("Forecast observations must align")
        weights = np.array([item.weight / item.uncertainty for item in items])
        weights /= weights.sum()
        # Conservative linear uncertainty avoids claiming independence between alphas.
        results.append(
            CombinedForecast(
                symbol,
                items[0].timeframe,
                items[0].observed_at,
                float(weights @ np.array([i.expected_return for i in items])),
                float(weights @ np.array([i.uncertainty for i in items])),
                tuple(i.version_id for i in items),
            )
        )
    return tuple(results)
