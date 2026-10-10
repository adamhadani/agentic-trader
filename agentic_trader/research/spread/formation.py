"""Engle-Granger pair formation on a formation window's log closes (causal: nothing after it)."""

from __future__ import annotations

import math
import warnings
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from agentic_trader.research.spread.protocol import FormationRule


__all__ = ["CoverageError", "PairFit", "eligible_symbols", "fit_pair", "half_life", "select_pairs"]

_MIN_HALF_LIFE_OBSERVATIONS = 10
_MIN_FIT_OBSERVATIONS = 20


class CoverageError(ValueError):
    """Too few names with complete formation bars; the stage fails closed."""


@dataclass(frozen=True)
class PairFit:
    y: str
    x: str
    sector: str
    alpha: float
    beta: float
    sigma: float
    t_stat: float
    p_value: float
    half_life: float
    eligible: bool
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.y, self.x)


def half_life(residual: np.ndarray) -> float:
    """AR(1) half-life of a residual in sessions: ``Δe_t = c + θ e_{t-1}``; ``inf`` unless ``-1 < θ < 0``."""
    e = np.asarray(residual, dtype=float)
    e = e[np.isfinite(e)]
    if e.size < _MIN_HALF_LIFE_OBSERVATIONS:
        return math.inf
    lag = e[:-1]
    design = np.column_stack([np.ones_like(lag), lag])
    coefficients, *_ = np.linalg.lstsq(design, np.diff(e), rcond=None)
    theta = float(coefficients[1])
    if not math.isfinite(theta) or theta >= 0.0 or theta <= -1.0:
        return math.inf
    return math.log(2.0) / -math.log1p(theta)


def _rejected(y: str, x: str, sector: str, reason: str, **values: float) -> PairFit:
    nan = float("nan")
    return PairFit(
        y=y,
        x=x,
        sector=sector,
        alpha=values.get("alpha", nan),
        beta=values.get("beta", nan),
        sigma=values.get("sigma", nan),
        t_stat=values.get("t_stat", nan),
        p_value=values.get("p_value", nan),
        half_life=values.get("half_life", nan),
        eligible=False,
        reason=reason,
    )


def fit_pair(log_y: np.ndarray, log_x: np.ndarray, rule: FormationRule, *, y: str, x: str, sector: str) -> PairFit:
    """OLS hedge ratio, Engle-Granger ``coint`` (MacKinnon p-values), AR(1) half-life, then the filters.

    The regression direction is fixed by the caller (``y`` is the alphabetically earlier name);
    it is never chosen by fit.
    """
    ly = np.asarray(log_y, dtype=float)
    lx = np.asarray(log_x, dtype=float)
    n = ly.size
    if (
        n != lx.size
        or n < _MIN_FIT_OBSERVATIONS + rule.coint_max_lag
        or not (np.isfinite(ly).all() and np.isfinite(lx).all())
    ):
        return _rejected(y, x, sector, "incomplete")
    if np.ptp(lx) == 0.0 or np.ptp(ly) == 0.0:
        return _rejected(y, x, sector, "constant")
    design = np.column_stack([np.ones(n), lx])
    coefficients, *_ = np.linalg.lstsq(design, ly, rcond=None)
    alpha, beta = float(coefficients[0]), float(coefficients[1])
    residual = ly - alpha - beta * lx
    sigma = float(residual.std(ddof=1))
    if not (math.isfinite(sigma) and sigma > 0.0):
        return _rejected(y, x, sector, "degenerate", alpha=alpha, beta=beta, sigma=sigma)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t_stat, p_value, _ = coint(ly, lx, trend="c", maxlag=rule.coint_max_lag, autolag=None)
    except ValueError, np.linalg.LinAlgError:
        return _rejected(y, x, sector, "coint_failed", alpha=alpha, beta=beta, sigma=sigma)
    t_stat, p_value = float(t_stat), float(p_value)
    if not (math.isfinite(t_stat) and math.isfinite(p_value)):
        return _rejected(y, x, sector, "coint_failed", alpha=alpha, beta=beta, sigma=sigma)
    hl = half_life(residual)
    reason = ""
    if not p_value < rule.p_value:
        reason = "p_value"
    elif not rule.half_life_sessions[0] <= hl <= rule.half_life_sessions[1]:
        reason = "half_life"
    elif not rule.hedge_ratio_abs[0] <= abs(beta) <= rule.hedge_ratio_abs[1]:
        reason = "hedge_ratio"
    return PairFit(
        y=y,
        x=x,
        sector=sector,
        alpha=alpha,
        beta=beta,
        sigma=sigma,
        t_stat=t_stat,
        p_value=p_value,
        half_life=hl,
        eligible=reason == "",
        reason=reason,
    )


def eligible_symbols(closes: pd.DataFrame, *, min_eligible: int) -> tuple[str, ...]:
    """Names with a finite, positive close on every row of ``closes``; too few fail closed."""
    values = closes.to_numpy(dtype=float)
    complete = np.isfinite(values) & (values > 0.0)
    names = tuple(sorted(str(c) for c, ok in zip(closes.columns, complete.all(axis=0), strict=True) if ok))
    if len(names) < min_eligible:
        raise CoverageError(f"{len(names)} eligible names below the protocol minimum of {min_eligible}")
    return names


def select_pairs(
    closes_formation: pd.DataFrame, pairs: Sequence[tuple[str, str, str]], rule: FormationRule
) -> tuple[tuple[PairFit, ...], dict]:
    """Fit every pair with both names eligible; keep the ``top_pairs`` eligible fits ranked by the
    cointegration t-statistic (most negative first, ties by name)."""
    names = eligible_symbols(closes_formation, min_eligible=rule.min_eligible_names)
    eligible = set(names)
    logs = {name: np.log(closes_formation[name].to_numpy(dtype=float)) for name in names}
    fits: list[PairFit] = []
    reasons: Counter[str] = Counter()
    for y, x, sector in pairs:
        if y not in eligible or x not in eligible:
            continue
        fit = fit_pair(logs[y], logs[x], rule, y=y, x=x, sector=sector)
        fits.append(fit)
        if not fit.eligible:
            reasons[fit.reason] += 1
    passing = sorted((f for f in fits if f.eligible), key=lambda f: (f.t_stat, f.y, f.x))
    selected = tuple(passing[: rule.top_pairs])
    counts = {
        "eligible_names": len(names),
        "pairs_tested": len(fits),
        "pairs_passing": len(passing),
        "selected": len(selected),
        "reasons": dict(sorted(reasons.items())),
    }
    return selected, counts
