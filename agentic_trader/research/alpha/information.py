"""Versioned cross-sectional Rank IC evidence, separate from legacy miner metrics.

Inference is per uninterrupted fold on an explicitly supplied observation clock.
HAC handles specified serial lags; it does not correct adaptive search/selection.
"""

from dataclasses import asdict, dataclass
from enum import StrEnum
from numbers import Real
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.regression.linear_model import OLS

from agentic_trader.research.alpha.panel import validate_panel
from agentic_trader.research.alpha.targets import ForecastTarget


IC_REPORT_VERSION = "cross_sectional_rank_ic_v1"
MAX_HAC_LAGS = 252


class ICAxis(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"


class ICUnavailable(StrEnum):
    INSUFFICIENT_BREADTH = "insufficient_breadth"
    CONSTANT_SCORE = "constant_score"
    CONSTANT_TARGET = "constant_target"


@dataclass(frozen=True)
class ICPolicy:
    min_assets: int = 3
    min_observations: int = 20
    hac_lags: int = 20
    observations_per_year: float | None = None
    confidence: float = 0.95

    def __post_init__(self):
        if (
            type(self.min_assets) is not int
            or self.min_assets < 3
            or type(self.min_observations) is not int
            or self.min_observations < 2
            or type(self.hac_lags) is not int
            or not 0 <= self.hac_lags <= MAX_HAC_LAGS
            or isinstance(self.confidence, bool)
            or not isinstance(self.confidence, Real)
            or not np.isfinite(self.confidence)
            or not 0.5 < self.confidence < 1
        ):
            raise ValueError("Explicit valid IC breadth, sample, HAC and confidence policy required")
        if self.observations_per_year is not None and (
            isinstance(self.observations_per_year, bool)
            or not isinstance(self.observations_per_year, Real)
            or not np.isfinite(self.observations_per_year)
            or self.observations_per_year <= 0
        ):
            raise ValueError("Explicit finite positive observation frequency required")


@dataclass(frozen=True)
class ICObservation:
    timestamp: str
    fold: str
    pairs: int
    assets: int
    ic: float | None
    reason: ICUnavailable | None


@dataclass(frozen=True)
class ICReport:
    policy: ICPolicy
    target: ForecastTarget
    observations: tuple[ICObservation, ...]
    folds: dict[str, dict]

    def document(self):
        return {
            "version": IC_REPORT_VERSION,
            "axis": ICAxis.CROSS_SECTIONAL,
            "target": self.target.document(),
            "policy": asdict(self.policy),
            "observations": [asdict(o) for o in self.observations],
            "folds": self.folds,
            "authorizes_promotion": False,
            "inference_scope": "Per-fold; HAC is not a multiple-testing or selection correction",
        }


def summarize_expected_values(observations: list[float | None], policy: ICPolicy):
    """Describe an expected scalar clock; missing dates withhold inference, never compress it."""
    if any(
        value is not None and (isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value))
        for value in observations
    ):
        raise ValueError("Finite scalar observations or explicit unavailable dates required")
    values = np.array([value for value in observations if value is not None], dtype=float)
    n = len(values)
    mean = float(values.mean()) if n else None
    std = float(values.std(ddof=1)) if n > 1 else None
    constant = n > 1 and np.unique(values).size == 1
    if constant:
        std = 0.0
    result: dict[str, Any] = {
        "expected": len(observations),
        "observed": n,
        "coverage": n / len(observations) if observations else 0.0,
        "mean": mean,
        "sample_std": std,
        "ratio_per_observation": mean / std if mean is not None and std is not None and std > 0 else None,
        "ratio_annualized_iid": None,
        "iid_t": None,
        "iid_p_two_sided": None,
        "hac_standard_error": None,
        "hac_t": None,
        "hac_p_two_sided": None,
        "hac_ci_low": None,
        "hac_ci_high": None,
        "inference_unavailable": None,
    }
    if n != len(observations):
        result["inference_unavailable"] = "missing_observations"
    elif n < max(policy.min_observations, policy.hac_lags + 2):
        result["inference_unavailable"] = "insufficient_observations"
    elif constant:
        result["inference_unavailable"] = "zero_variance"
    else:
        ratio = result["ratio_per_observation"]
        result["iid_t"] = ratio * np.sqrt(n)
        result["iid_p_two_sided"] = float(2 * stats.t.sf(abs(result["iid_t"]), n - 1))
        if policy.observations_per_year is not None:
            result["ratio_annualized_iid"] = ratio * np.sqrt(policy.observations_per_year)
        fitted = OLS(values, np.ones((n, 1))).fit(
            cov_type="HAC", cov_kwds={"maxlags": policy.hac_lags, "use_correction": True}, use_t=False
        )
        se = float(fitted.bse[0])
        if not np.isfinite(se) or se <= 0:
            result["inference_unavailable"] = "degenerate_hac_variance"
        else:
            ci = fitted.conf_int(alpha=1 - policy.confidence)[0]
            result.update(
                hac_standard_error=se,
                hac_t=float(fitted.tvalues[0]),
                hac_p_two_sided=float(fitted.pvalues[0]),
                hac_ci_low=float(ci[0]),
                hac_ci_high=float(ci[1]),
            )
    return result


def _fold_statistics(observations: list[ICObservation], policy: ICPolicy):
    result = summarize_expected_values([observation.ic for observation in observations], policy)
    names = {
        "mean": "mean_ic",
        "sample_std": "sample_std_ic",
        "ratio_per_observation": "icir_per_observation",
        "ratio_annualized_iid": "icir_annualized_iid",
    }
    return {names.get(name, name): value for name, value in result.items()}


def cross_sectional_ic(
    scores: pd.DataFrame,
    targets: pd.DataFrame,
    folds: pd.Series,
    policy: ICPolicy,
    *,
    expected_index: pd.DatetimeIndex,
    target: ForecastTarget,
) -> ICReport:
    """Rank across available pairs, retain each expected date and never stitch folds.

    The caller supplies the full ordered decision calendar after explicit maturity
    boundaries. Inference is withheld on any unavailable date within a fold.
    """
    validate_panel(scores)
    validate_panel(targets)
    if (
        not isinstance(expected_index, pd.DatetimeIndex)
        or expected_index.tz is None
        or expected_index.empty
        or expected_index.hasnans
        or not expected_index.is_unique
        or not expected_index.is_monotonic_increasing
        or not scores.index.equals(expected_index)
        or not targets.index.equals(expected_index)
        or not folds.index.equals(expected_index)
        or not scores.columns.equals(targets.columns)
        or folds.isna().any()
        or not all(isinstance(f, str) and f for f in folds)
    ):
        raise ValueError("Exact aware decision clock, ordered symbol axes and explicit folds required")
    blocks = folds.loc[folds.ne(folds.shift())].tolist()
    if len(blocks) != len(set(blocks)):
        raise ValueError("A fold must be one contiguous decision block")
    observations = []
    for t, a, b, fold in zip(expected_index, scores.to_numpy(), targets.to_numpy(), folds, strict=True):
        paired = np.isfinite(a) & np.isfinite(b)
        count = int(paired.sum())
        reason = None
        if count < policy.min_assets:
            reason = ICUnavailable.INSUFFICIENT_BREADTH
        elif np.unique(a[paired]).size < 2:
            reason = ICUnavailable.CONSTANT_SCORE
        elif np.unique(b[paired]).size < 2:
            reason = ICUnavailable.CONSTANT_TARGET
        ic = None if reason else float(stats.spearmanr(a[paired], b[paired]).statistic)
        observations.append(ICObservation(t.isoformat(), fold, count, len(a), ic, reason))
    return ICReport(
        policy,
        target,
        tuple(observations),
        {fold: _fold_statistics([o for o in observations if o.fold == fold], policy) for fold in blocks},
    )
