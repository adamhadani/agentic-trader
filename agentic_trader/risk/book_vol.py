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
    if cov.index.has_duplicates:
        raise ValueError("covariance index has duplicate symbols")
    missing = [s for s in (*inputs.weights, inputs.candidate) if s not in cov.index]
    if missing:
        raise ValueError(f"symbols missing from covariance: {sorted(set(missing))}")
    try:
        matrix = cov.to_numpy(dtype=float)
        weights = [float(v) for v in inputs.weights.values()]
        budget = float(inputs.budget_dollars)
        notional = float(inputs.candidate_notional)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric input: {type(exc).__name__}") from None
    if not np.all(np.isfinite(matrix)):
        raise ValueError("covariance has non-finite entries")
    if not np.allclose(matrix, matrix.T, rtol=1e-9, atol=1e-12):
        raise ValueError("covariance is not symmetric")
    if np.any(np.diag(matrix) < 0):
        raise ValueError("covariance has negative variances")
    if matrix.size and np.linalg.eigvalsh(matrix).min() < -1e-10 * max(1.0, float(np.abs(matrix).max())):
        raise ValueError("covariance is not positive semi-definite")
    if not all(math.isfinite(v) for v in weights):
        raise ValueError("weights must be finite")
    if not (math.isfinite(budget) and budget > 0):
        raise ValueError("budget_dollars must be positive and finite")
    if not math.isfinite(notional):
        raise ValueError("candidate_notional must be finite")
    return matrix


def book_vol_factor(inputs: BookVolInputs) -> BookVolResult:
    """``vol(f)^2 = a + 2 b f + c f^2`` with the book fixed; the largest ``f`` in [0, 1] with ``vol(f) <= budget``.

    A book already over budget gives 0 unless the full-size card brings it under (a hedge). Only
    ``ValueError`` is raised, for invalid inputs.
    """
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
    budget = float(inputs.budget_dollars)
    if vol_after_full <= budget:
        # The full-size card fits (including a hedge that brings an over-budget book back under).
        factor = 1.0
    elif vol_before > budget or c <= 0.0:
        # The book is already over budget and the card cannot fix it at full size: the feasible
        # fractions, if any, lie between two roots and would size the card to flip the book past
        # the budget boundary. The rule never does that; the card is not sized.
        factor = 0.0
    else:
        # vol(f)^2 = c f^2 + 2 b f + a; the book is under budget (a < budget^2) and the full card is
        # over, so exactly one root lies in (0, 1). Numerically stable form of the larger root.
        disc = b * b - c * (a - budget * budget)
        root = (budget * budget - a) / (b + math.sqrt(disc)) if b + math.sqrt(disc) > 0 else (-b + math.sqrt(disc)) / c
        factor = min(1.0, max(0.0, root))
    vol_after_scaled = math.sqrt(max(a + 2 * b * factor + c * factor * factor, 0.0))
    return BookVolResult(
        factor=factor,
        vol_before=vol_before,
        vol_after_full=vol_after_full,
        vol_after_scaled=vol_after_scaled,
        marginal_vol_full=vol_after_full - vol_before,
    )
