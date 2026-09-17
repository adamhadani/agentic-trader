"""Stateless daily payoff screens for forecasts; no simulated broker order lifecycle.

Each eligible day starts and ends in cash. Prices are bar-boundary proxies, not
auction fills. This deliberately cannot qualify, protect or submit a position.
"""

from dataclasses import asdict, dataclass
from numbers import Real

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.simulation import return_statistics
from agentic_trader.research.alpha.validation import frame_digest, validate_sampling


DAILY_POLICY_VERSION = "next_bar_long_flat_proxy_v1"
BASIS_POINTS = 10_000
MAX_COST_SCENARIOS = 10
MAX_SIDE_COST_BPS = 1_000


@dataclass(frozen=True)
class DailyLongFlatPolicy:
    costs_bps: tuple[float, ...]

    def __post_init__(self):
        if not isinstance(self.costs_bps, tuple) or not 1 <= len(self.costs_bps) <= MAX_COST_SCENARIOS:
            raise ValueError("A bounded immutable cost scenario tuple is required")
        if any(
            isinstance(c, bool) or not isinstance(c, Real) or not np.isfinite(c) or not 0 <= c <= MAX_SIDE_COST_BPS
            for c in self.costs_bps
        ):
            raise ValueError("Finite nonnegative bounded per-side costs required")
        if tuple(sorted(set(self.costs_bps))) != self.costs_bps:
            raise ValueError("Cost scenarios must be unique and increasing")
        object.__setattr__(self, "costs_bps", tuple(float(c) for c in self.costs_bps))

    def document(self):
        return {
            "version": DAILY_POLICY_VERSION,
            **asdict(self),
            "decision": "positive_forecast_long_else_cash",
            "capital": "fully_funded_no_leverage",
            "price_scope": "observed_bar_proxy",
        }


@dataclass
class PolicyScenario:
    cost_bps: float
    observations: pd.DataFrame
    metrics: dict
    benchmark_metrics: dict
    folds: list[dict]
    missing_forecasts: int

    def document(self):
        return {
            "cost_bps": self.cost_bps,
            "metrics": self.metrics,
            "benchmark_metrics": self.benchmark_metrics,
            "folds": self.folds,
            "missing_forecasts": self.missing_forecasts,
            "observation_hash": frame_digest(self.observations),
            "authorizes_promotion": False,
        }


def _statistics(returns: pd.Series, entered: pd.Series):
    metrics = return_statistics(returns, [{"net_return": float(v)} for v in returns.loc[entered]])
    return {k: v for k, v in metrics.items() if k not in ("net_returns", "trades")}


def evaluate_daily_policy(
    bars: pd.DataFrame, forecasts: pd.DataFrame, folds: list[dict], policy: DailyLongFlatPolicy
) -> list[PolicyScenario]:
    """Retain complete fold clocks, including first-bar cash and missing forecasts.

    A fixed forecast sign determines all cost scenarios. The comparator enters
    every eligible day; it has the same clocks, cost model and flat fold boundaries.
    """
    validate_sampling(bars, "1d")
    if (
        not isinstance(bars.index, pd.DatetimeIndex)
        or not bars.index.is_unique
        or not bars.index.is_monotonic_increasing
    ):
        raise ValueError("Unique chronological price clock required")
    if (
        not forecasts.index.is_unique
        or not forecasts.index.is_monotonic_increasing
        or not forecasts.index.isin(bars.index).all()
    ):
        raise ValueError("Forecasts require unique aligned decision bars")
    if np.isinf(forecasts.prediction.to_numpy(dtype=float)).any():
        raise ValueError("Infinite forecasts are invalid")
    if not folds:
        raise ValueError("Validation folds required")
    prepared = []
    prior_end = 0
    for number, fold in enumerate(folds):
        start, end = fold["validation_start"], fold["validation_end"]
        if not 0 <= start < end <= len(bars) or start < prior_end:
            raise ValueError("Ordered non-overlapping validation folds required")
        prior_end = end
        prices = bars.iloc[start:end].rename(columns=str.lower)
        observed = prices.iloc[1:][["open", "close"]].to_numpy(dtype=float)
        if not np.isfinite(observed).all() or (observed <= 0).any():
            raise ValueError("Missing or invalid execution proxy prices")
        prediction = pd.Series(np.nan, index=prices.index)
        prediction.iloc[1:] = forecasts.prediction.reindex(prices.index[:-1]).to_numpy()
        eligible = pd.Series(True, index=prices.index)
        eligible.iloc[0] = False
        position = eligible & prediction.gt(0)
        gross = pd.Series(0.0, index=prices.index)
        gross.iloc[1:] = prices.close.iloc[1:] / prices.open.iloc[1:] - 1
        if not np.isfinite(gross.to_numpy()).all():
            raise ValueError("Nonfinite execution proxy price return")
        prepared.append((number, gross, position, eligible, int((eligible & prediction.isna()).sum())))
    scenarios = []
    for cost in policy.costs_bps:
        slip = cost / BASIS_POINTS
        frames, evidence, missing = [], [], 0
        for number, gross, position, eligible, unavailable in prepared:
            net_if_entered = (1 + gross) * (1 - slip) / (1 + slip) - 1
            observations = pd.DataFrame(
                {
                    "fold": number,
                    "position": position.astype(float),
                    "gross_return": gross.where(position, 0.0),
                    "net_return": net_if_entered.where(position, 0.0),
                    "benchmark_return": net_if_entered.where(eligible, 0.0),
                    "benchmark_position": eligible.astype(float),
                    "turnover_legs": position.astype(float) * 2,
                }
            )
            evidence.append(
                {
                    "fold": number,
                    "metrics": _statistics(observations.net_return, position),
                    "benchmark_metrics": _statistics(observations.benchmark_return, eligible),
                    "mean_excess_return": float((observations.net_return - observations.benchmark_return).mean()),
                    "missing_forecasts": unavailable,
                }
            )
            missing += unavailable
            frames.append(observations)
        combined = pd.concat(frames)
        scenarios.append(
            PolicyScenario(
                float(cost),
                combined,
                _statistics(combined.net_return, combined.position.gt(0)),
                _statistics(combined.benchmark_return, combined.benchmark_position.gt(0)),
                evidence,
                missing,
            )
        )
    return scenarios
