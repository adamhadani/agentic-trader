"""Target-aware calibration and conservative, dependency-fenced shadow forecasts."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import statsmodels.api as sm

from agentic_trader.market.bars import BAR_DURATIONS, FIXED_BAR_LAYOUT, SESSION_BAR_LAYOUT
from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


CALIBRATION_VERSION = "ols_hac_forecast_v1"
MIN_CALIBRATION_OBSERVATIONS = 30
DEFAULT_CALIBRATION_HAC_LAGS = 5
CALIBRATION_SCORE_BOUND = 5.0


@dataclass(frozen=True)
class ForecastContract:
    target: ForecastTarget
    feed: str
    adjustment: str
    bar_layout: str
    currency: str

    def __post_init__(self):
        if not isinstance(self.target, ForecastTarget) or not all((self.feed, self.adjustment, self.currency)):
            raise ValueError("Explicit forecast target/feed/price/currency contract required")
        if self.bar_layout not in (FIXED_BAR_LAYOUT, SESSION_BAR_LAYOUT):
            raise ValueError("Unsupported forecast clock contract")

    @classmethod
    def from_document(cls, document):
        target = document["target"]
        return cls(**{**document, "target": ForecastTarget(**{**target, "label": ForecastLabel(target["label"])})})


def forecast_family(expression: str, normalization_window: int) -> str:
    """Execution thresholds, version IDs and display names do not add information."""
    payload = (ast.dump(compile_expression(expression).tree, include_attributes=False), normalization_window)
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


@dataclass(frozen=True)
class ForecastCalibration:
    contract: ForecastContract
    slope: float
    intercept: float
    return_volatility: float
    coefficient_covariance: tuple[tuple[float, float], tuple[float, float]]
    trained_until: str
    observations: int
    hac_lags: int
    training_data_hash: str
    shrinkage: float = 0.5

    def __post_init__(self):
        covariance = np.asarray(self.coefficient_covariance)
        if (
            not isinstance(self.contract, ForecastContract)
            or not np.isfinite([self.slope, self.intercept, self.return_volatility, self.shrinkage]).all()
            or self.return_volatility < 0
            or not 0 <= self.shrinkage <= 1
            or covariance.shape != (2, 2)
            or not np.isfinite(covariance).all()
            or not np.allclose(covariance, covariance.T, atol=1e-12)
            or np.linalg.eigvalsh(covariance).min() < -1e-12
            or pd.Timestamp(self.trained_until).tzinfo is None
            or type(self.observations) is not int
            or self.observations < MIN_CALIBRATION_OBSERVATIONS
            or type(self.hac_lags) is not int
            or not 0 <= self.hac_lags < self.observations - 2
            or not self.training_data_hash
        ):
            raise ValueError("Invalid calibration evidence")

    @classmethod
    def fit(
        cls,
        scores: pd.Series,
        forward_returns: pd.Series,
        *,
        contract: ForecastContract,
        label_observed_at: pd.Series,
        trained_until: str,
        shrinkage: float = 0.5,
        hac_lags: int = DEFAULT_CALIBRATION_HAC_LAGS,
    ):
        if not scores.index.equals(forward_returns.index) or not scores.index.equals(label_observed_at.index):
            raise ValueError("Calibration labels and availability must align")
        cutoff = pd.Timestamp(trained_until)
        if not isinstance(scores.index, pd.DatetimeIndex) or scores.index.tz is None or cutoff.tzinfo is None:
            raise ValueError("Explicit UTC-aware calibration observations required")
        if not scores.index.is_unique or not scores.index.is_monotonic_increasing:
            raise ValueError("Calibration observations must be ordered and unique")
        if not isinstance(label_observed_at.dtype, pd.DatetimeTZDtype):
            raise TypeError("Explicit timezone-aware outcome availability required")
        if not np.isfinite(forward_returns.to_numpy()).all():
            raise ValueError("Complete finite calibration outcomes required")
        ends = pd.to_datetime(label_observed_at, utc=True)
        if ends.isna().any() or (ends > cutoff).any() or (ends <= scores.index).any():
            raise ValueError("All calibration outcomes must be available by the training cutoff")
        joined = pd.DataFrame({"x": scores, "y": forward_returns}).replace([np.inf, -np.inf], np.nan).dropna()
        joined["x"] = joined.x.clip(-CALIBRATION_SCORE_BOUND, CALIBRATION_SCORE_BOUND)
        if type(hac_lags) is not int or hac_lags < 0:
            raise ValueError("Invalid HAC lag policy")
        lags = max(hac_lags, contract.target.horizon_bars - 1)
        if (
            len(joined) < MIN_CALIBRATION_OBSERVATIONS
            or joined.x.std() <= 1e-10
            or not 0 <= shrinkage <= 1
            or lags >= len(joined) - 2
        ):
            raise ValueError("Insufficient calibration evidence or invalid policy")
        design = np.column_stack([np.ones(len(joined)), joined.x.to_numpy()])
        fitted = sm.OLS(joined.y.to_numpy(), design).fit(
            cov_type="HAC", cov_kwds={"maxlags": lags, "use_correction": True}
        )
        covariance = np.asarray(fitted.cov_params())
        if not np.isfinite(covariance).all() or np.linalg.eigvalsh(covariance).min() < -1e-12:
            raise ValueError("Invalid forecast-mean covariance")
        digest = hashlib.sha256(pd.util.hash_pandas_object(joined, index=True).to_numpy().tobytes())
        digest.update(pd.util.hash_pandas_object(ends.loc[joined.index], index=True).to_numpy().tobytes())
        return cls(
            contract,
            float(fitted.params[1]),
            float(fitted.params[0]),
            float(joined.y.std()),
            ((float(covariance[0, 0]), float(covariance[0, 1])), (float(covariance[1, 0]), float(covariance[1, 1]))),
            cutoff.isoformat(),
            len(joined),
            lags,
            digest.hexdigest(),
            shrinkage,
        )

    def _design(self, score: float):
        if not np.isfinite(score):
            raise ValueError("Missing forecast score")
        return np.array([1.0, float(np.clip(score, -CALIBRATION_SCORE_BOUND, CALIBRATION_SCORE_BOUND))])

    def predict(self, score: float):
        return self.shrinkage * float(self._design(score) @ np.array([self.intercept, self.slope]))

    def standard_error(self, score: float) -> float:
        x = self._design(score)
        return self.shrinkage * float(np.sqrt(max(0.0, x @ np.asarray(self.coefficient_covariance) @ x)))

    def document(self):
        return {"version": CALIBRATION_VERSION, **asdict(self)}

    @classmethod
    def from_document(cls, document):
        if document.get("version") != CALIBRATION_VERSION:
            raise ValueError("Calibration contract requires fresh target-aware evidence")
        values = {k: v for k, v in document.items() if k != "version"}
        values["contract"] = ForecastContract.from_document(values["contract"])
        values["coefficient_covariance"] = tuple(tuple(row) for row in values["coefficient_covariance"])
        return cls(**values)

    @property
    def calibration_id(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AlphaForecast:
    version_id: str
    symbol: str
    contract: ForecastContract
    observed_at: datetime
    expected_return: float
    standard_error: float
    weight: float
    family_id: str
    calibration_id: str


@dataclass(frozen=True)
class CombinedForecast:
    symbol: str
    contract: ForecastContract
    observed_at: datetime
    expected_return: float
    standard_error: float
    contributors: tuple[str, ...]
    families: tuple[str, ...]
    calibration_ids: tuple[str, ...]


def validate_forecast(forecast: AlphaForecast | CombinedForecast, as_of: datetime):
    if not isinstance(forecast.contract, ForecastContract):
        raise TypeError("Explicit forecast contract required")
    if forecast.observed_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("Forecast availability must be timezone-aware")
    if isinstance(forecast, CombinedForecast):
        provenance = (forecast.contributors, forecast.families, forecast.calibration_ids)
        if (
            not forecast.symbol
            or len({len(values) for values in provenance}) != 1
            or any(not values or not all(values) or len(set(values)) != len(values) for values in provenance)
        ):
            raise ValueError("Complete unique combined forecast provenance required")
    age = as_of - forecast.observed_at
    if age.total_seconds() < 0 or age > BAR_DURATIONS[forecast.contract.target.timeframe]:
        raise ValueError("Stale or future forecast")
    if not np.isfinite([forecast.expected_return, forecast.standard_error]).all() or forecast.standard_error < 0:
        raise ValueError("Invalid forecast mean or standard error")


def combine_forecasts(forecasts: list[AlphaForecast], *, as_of: datetime) -> tuple[CombinedForecast, ...]:
    """Explicit weighted blend with a worst-correlation standard-error bound.

    Correlated model fitting is a separate charged experiment. Duplicate families
    or calibration evidence cannot be passed off as independent contributors here.
    """
    grouped: dict[str, list[AlphaForecast]] = {}
    seen_versions: set[tuple[str, str]] = set()
    seen_families: set[tuple[str, str]] = set()
    seen_calibrations: set[tuple[str, str]] = set()
    for forecast in forecasts:
        validate_forecast(forecast, as_of)
        if not all((forecast.version_id, forecast.family_id, forecast.calibration_id, forecast.symbol)):
            raise ValueError("Forecast provenance required")
        for seen, identity, kind in (
            (seen_versions, forecast.version_id, "version"),
            (seen_families, forecast.family_id, "family"),
            (seen_calibrations, forecast.calibration_id, "calibration"),
        ):
            key = (forecast.symbol, identity)
            if key in seen:
                raise ValueError(f"Duplicate forecast {kind}")
            seen.add(key)
        if not np.isfinite(forecast.weight) or forecast.weight <= 0:
            raise ValueError("Invalid blend weight")
        grouped.setdefault(forecast.symbol, []).append(forecast)
    results = []
    for symbol, items in sorted(grouped.items()):
        items = sorted(items, key=lambda item: item.version_id)
        if len({item.contract for item in items}) != 1:
            raise ValueError("Cannot pool different forecast contracts/horizons")
        if len({item.observed_at for item in items}) != 1:
            raise ValueError("Forecast observations must align")
        weights = np.array([item.weight for item in items], dtype=float)
        weights /= weights.sum()
        results.append(
            CombinedForecast(
                symbol,
                items[0].contract,
                items[0].observed_at,
                float(weights @ np.array([i.expected_return for i in items])),
                float(weights @ np.array([i.standard_error for i in items])),
                tuple(i.version_id for i in items),
                tuple(i.family_id for i in items),
                tuple(i.calibration_id for i in items),
            )
        )
    return tuple(results)
