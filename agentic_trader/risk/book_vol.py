"""Book-aware size factor: the largest fraction of a card's notional that keeps the book's daily
dollar volatility within a budget (docs/risk-policy.md, docs/card-evidence.md).

Pure: no config, no I/O. Consumed by one layer (card construction in ``run_scan``) by design --
admission never shrinks a tier the operator was offered -- and never by backtest or replay.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd


__all__ = ["BookVolInputs", "BookVolResult", "book_vol_factor"]


@dataclass(frozen=True)
class BookVolInputs:
    weights: Mapping[str, float]  # signed dollar notional per book symbol (long +, short -)
    candidate: str
    candidate_notional: float  # signed dollar notional at the proposed quantity
    covariance: pd.DataFrame  # daily-return covariance per unit notional; index == columns
    budget_dollars: float  # allowed portfolio daily dollar-vol after the card


@dataclass(frozen=True)
class BookVolResult:
    factor: float
    vol_before: float
    vol_after_full: float
    vol_after_scaled: float
    marginal_vol_full: float


def _validate(inputs: BookVolInputs) -> np.ndarray:
    cov = inputs.covariance
    if list(cov.index) != list(cov.columns):
        raise ValueError("covariance index and columns differ")
    missing = [s for s in (*inputs.weights, inputs.candidate) if s not in cov.index]
    if missing:
        raise ValueError(f"symbols missing from covariance: {sorted(set(missing))}")
    matrix = cov.to_numpy(dtype=float)
    if not np.all(np.isfinite(matrix)):
        raise ValueError("covariance has non-finite entries")
    if np.any(np.diag(matrix) < 0):
        raise ValueError("covariance has negative variances")
    if not (math.isfinite(inputs.budget_dollars) and inputs.budget_dollars > 0):
        raise ValueError("budget_dollars must be positive and finite")
    if not math.isfinite(inputs.candidate_notional):
        raise ValueError("candidate_notional must be finite")
    return matrix


def book_vol_factor(inputs: BookVolInputs) -> BookVolResult:
    """``vol(f)^2 = a + 2 b f + c f^2`` with the book fixed; the largest ``f`` in [0, 1] with ``vol(f) <= budget``."""
    matrix = _validate(inputs)
    symbols = list(inputs.covariance.index)
    w = np.array([inputs.weights.get(s, 0.0) for s in symbols], dtype=float)
    unit = np.array([1.0 if s == inputs.candidate else 0.0 for s in symbols])
    x = inputs.candidate_notional
    a = float(w @ matrix @ w)
    b = float(x * (unit @ matrix @ w))
    c = float(x * x * (unit @ matrix @ unit))
    vol_before = math.sqrt(max(a, 0.0))
    vol_after_full = math.sqrt(max(a + 2 * b + c, 0.0))
    budget_sq = inputs.budget_dollars**2
    if vol_after_full <= inputs.budget_dollars or x == 0.0:
        factor = 1.0
    elif c <= 0.0:
        factor = 0.0
    else:
        # Largest root of c f^2 + 2 b f + (a - budget^2) = 0, clipped to [0, 1].
        disc = b * b - c * (a - budget_sq)
        factor = 0.0 if disc < 0 else min(1.0, max(0.0, (-b + math.sqrt(disc)) / c))
    vol_after_scaled = math.sqrt(max(a + 2 * b * factor + c * factor * factor, 0.0))
    return BookVolResult(
        factor=factor,
        vol_before=vol_before,
        vol_after_full=vol_after_full,
        vol_after_scaled=vol_after_scaled,
        marginal_vol_full=vol_after_full - vol_before,
    )
