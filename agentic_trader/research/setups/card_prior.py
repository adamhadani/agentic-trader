"""The frozen replay prior for ``card_stats`` v2: per-key net-excess moments and the posterior.

The prior is estimated once, offline, from the frozen bracket-counterfactual replay frame (``A0_live``
arm) in the ``net_excess_r_v1`` unit, committed as a digest-pinned JSON file and loaded at runtime. It
never includes a live row. ``tau`` is the between-quarter standard deviation of the key's mean
(method of moments, floored), ``se_session`` the session-clustered standard error of the key's mean.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from agentic_trader.research.setups.beta import BETA_WINDOW_SESSIONS, trailing_beta
from agentic_trader.research.setups.net_excess import (
    COST_CAP_BPS,
    COST_FLOOR_BPS,
    COST_MIN_SESSIONS,
    COST_WINDOW_SESSIONS,
    IMPACT_COEFFICIENT,
    NET_EXCESS_UNIT,
    R_CLIP,
    REFERENCE_RISK_USD,
    cost_inputs,
    name_cost_bps,
    net_excess_r,
)


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


__all__ = [
    "PRIOR_MIN_QUARTERS",
    "PRIOR_MIN_ROWS_PER_QUARTER",
    "PRIOR_TAU_FLOOR",
    "PRIOR_VERSION",
    "CardStatsPrior",
    "PriorEntry",
    "PriorUnavailable",
    "cost_model_constants",
    "estimate_prior",
    "load_prior",
    "posterior",
    "prior_rows",
    "shrink_across_keys",
]

PRIOR_VERSION = "card-stats-prior-v2"
PRIOR_MIN_ROWS_PER_QUARTER = 20
PRIOR_MIN_QUARTERS = 8
# A controller ruling: a moment estimate of 0 must not make the prior immovable.
PRIOR_TAU_FLOOR = 0.05
# The shrunk mean must reproduce from the stored raw mean, cross-key mean and shrinkage to this tolerance.
SHRINKAGE_TOLERANCE = 1e-12

_KEY_COLUMNS = ["strategy", "timeframe", "direction"]


class PriorUnavailable(RuntimeError):
    """The frozen prior cannot be used; the message is one fixed literal, never a path or file content."""


class PriorEntry(BaseModel, frozen=True, allow_inf_nan=False):
    """One key's replay moments. A positive ``sd`` and ``tau`` keep the posterior's weight ``k`` finite and positive."""

    strategy: str
    timeframe: str
    direction: str
    n_rows: int = Field(ge=0)
    n_sessions: int = Field(ge=0)
    mean: float
    replay_mean: float
    shrinkage: float = Field(ge=0, le=1)
    sd: float = Field(gt=0)
    se_session: float = Field(ge=0)
    tau: float = Field(gt=0)
    tau_source: Literal["key", "pooled"]


class CardStatsPrior(BaseModel, frozen=True, allow_inf_nan=False, extra="forbid"):
    version: str
    unit: str
    cost_model: dict[str, float]
    r_clip: float
    beta_window_sessions: int
    tau_floor: float
    min_rows_per_quarter: int
    min_quarters: int
    source: dict[str, str]
    excluded_symbols: tuple[str, ...]
    pooled_sd: float = Field(gt=0)
    pooled_tau: float = Field(gt=0)
    pooled_n_rows: int
    cross_key_mean: float
    between_key_tau: float = Field(gt=0)
    keys: tuple[PriorEntry, ...]
    cost_bps_quantiles: dict[str, float]
    n_no_beta: int
    code_revision: str

    def is_replay(self, strategy: str, timeframe: str, direction: str) -> bool:
        return any(_matches(k, strategy, timeframe, direction) for k in self.keys)

    def entry(self, strategy: str, timeframe: str, direction: str) -> PriorEntry:
        """The replay key, or a skeptical zero-mean entry at the pooled spread and between-quarter tau."""
        for key in self.keys:
            if _matches(key, strategy, timeframe, direction):
                return key
        return PriorEntry(
            strategy=strategy,
            timeframe=timeframe,
            direction=direction,
            n_rows=0,
            n_sessions=0,
            mean=0.0,
            replay_mean=0.0,
            shrinkage=1.0,
            sd=self.pooled_sd,
            se_session=0.0,
            tau=self.pooled_tau,
            tau_source="pooled",
        )


def _matches(key: PriorEntry, strategy: str, timeframe: str, direction: str) -> bool:
    return (key.strategy, key.timeframe, key.direction) == (strategy, timeframe, direction)


def cost_model_constants() -> dict[str, float]:
    """The Task 1 cost constants by lower-case name, as recorded in the prior file."""
    return {
        "cost_floor_bps": float(COST_FLOOR_BPS),
        "impact_coefficient": float(IMPACT_COEFFICIENT),
        "reference_risk_usd": float(REFERENCE_RISK_USD),
        "cost_cap_bps": float(COST_CAP_BPS),
        "cost_window_sessions": float(COST_WINDOW_SESSIONS),
        "cost_min_sessions": float(COST_MIN_SESSIONS),
    }


def load_prior(path: Path, sha256: str | None) -> CardStatsPrior:
    """Load the digest-pinned prior; any failure raises ``PriorUnavailable`` with one fixed literal message."""
    if sha256 is None:
        raise PriorUnavailable("prior digest is not configured")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PriorUnavailable("prior file not found") from None
    except OSError:
        raise PriorUnavailable("prior file unreadable") from None
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise PriorUnavailable("prior digest mismatch")
    try:
        prior = CardStatsPrior.model_validate_json(raw)
    except ValidationError:
        raise PriorUnavailable("prior file invalid") from None
    if prior.version != PRIOR_VERSION:
        raise PriorUnavailable("prior version mismatch")
    if any(
        abs(k.mean - (prior.cross_key_mean + k.shrinkage * (k.replay_mean - prior.cross_key_mean)))
        > SHRINKAGE_TOLERANCE
        for k in prior.keys
    ):
        raise PriorUnavailable("prior shrinkage inconsistent")
    if prior.unit != NET_EXCESS_UNIT:
        raise PriorUnavailable("prior unit mismatch")
    if (
        prior.cost_model != cost_model_constants()
        or prior.r_clip != R_CLIP
        or prior.beta_window_sessions != BETA_WINDOW_SESSIONS
    ):
        raise PriorUnavailable("prior constants differ from the code")
    return prior


def _quarter_key(sessions: pd.Series) -> pd.Series:
    dates = pd.to_datetime(sessions)
    return dates.dt.year * 10 + (dates.dt.month - 1) // 3


def _tau(values: pd.Series, sessions: pd.Series) -> tuple[float, int]:
    """``(tau, n_quarters)`` by the method of moments over quarters with enough rows."""
    frame = pd.DataFrame({"x": values.to_numpy(dtype=float), "q": _quarter_key(sessions).to_numpy()})
    grouped = frame.groupby("q")["x"].agg(["mean", "var", "count"])
    grouped = grouped[grouped["count"] >= PRIOR_MIN_ROWS_PER_QUARTER]
    n_quarters = len(grouped)
    if n_quarters < 2:
        return PRIOR_TAU_FLOOR, n_quarters
    sampling = float((grouped["var"].fillna(0.0) / grouped["count"]).mean())
    tau_sq = max(float(np.var(grouped["mean"].to_numpy(), ddof=1)) - sampling, 0.0)
    return max(math.sqrt(tau_sq), PRIOR_TAU_FLOOR), n_quarters


def _sd(values: pd.Series) -> float:
    return float(np.std(values.to_numpy(dtype=float), ddof=1)) if len(values) > 1 else 0.0


def estimate_prior(rows: pd.DataFrame) -> tuple[tuple[PriorEntry, ...], float, float, int]:
    """Per-key moments over rows with a finite ``net_excess_r``: ``(keys, pooled_sd, pooled_tau, n)``.

    Raises ``ValueError`` when no row is finite or the pooled set has fewer than two quarters with
    ``PRIOR_MIN_ROWS_PER_QUARTER`` rows: its tau would be the floor by default, not an estimate.
    """
    finite = rows[np.isfinite(pd.to_numeric(rows["net_excess_r"], errors="coerce"))]
    if finite.empty:
        raise ValueError("no finite net_excess_r rows for the pooled prior")
    pooled_tau, pooled_quarters = _tau(finite["net_excess_r"], finite["session"])
    if pooled_quarters < 2:
        raise ValueError(
            f"the pooled prior has {pooled_quarters} quarter(s) with {PRIOR_MIN_ROWS_PER_QUARTER}+ rows; it needs 2"
        )
    entries: list[PriorEntry] = []
    for (strategy, timeframe, direction), group in finite.groupby(_KEY_COLUMNS, sort=True):
        key_tau, n_quarters = _tau(group["net_excess_r"], group["session"])
        by_session = group.groupby("session")["net_excess_r"].mean()
        n_sessions = len(by_session)
        se_session = _sd(by_session) / math.sqrt(n_sessions) if n_sessions > 1 else 0.0
        enough = n_quarters >= PRIOR_MIN_QUARTERS
        entries.append(
            PriorEntry(
                strategy=str(strategy),
                timeframe=str(timeframe),
                direction=str(direction),
                n_rows=len(group),
                n_sessions=n_sessions,
                mean=float(group["net_excess_r"].mean()),
                replay_mean=float(group["net_excess_r"].mean()),
                shrinkage=1.0,
                sd=_sd(group["net_excess_r"]),
                se_session=se_session,
                tau=key_tau if enough else pooled_tau,
                tau_source="key" if enough else "pooled",
            )
        )
    return tuple(entries), _sd(finite["net_excess_r"]), pooled_tau, len(finite)


def shrink_across_keys(entries: Sequence[PriorEntry], between_key_tau: float) -> tuple[tuple[PriorEntry, ...], float]:
    """Empirical-Bayes shrink of each key's ``replay_mean`` toward the cross-key mean: ``(entries, mu)``.

    ``A = between_key_tau**2`` is the between-key variance; ``mu`` weights each key by
    ``1/(A + se_session**2)``; key ``k`` keeps ``B_k = A/(A + se_k**2)`` of its distance from ``mu``.
    Only ``mean`` and ``shrinkage`` change, so the posterior weight ``k`` is untouched.
    """
    a = between_key_tau**2
    weights = [1.0 / (a + e.se_session**2) for e in entries]
    mu = sum(w * e.replay_mean for w, e in zip(weights, entries, strict=True)) / sum(weights)
    shrunk: list[PriorEntry] = []
    for e in entries:
        b = a / (a + e.se_session**2)
        shrunk.append(e.model_copy(update={"mean": mu + b * (e.replay_mean - mu), "shrinkage": b}))
    return tuple(shrunk), mu


def prior_rows(
    frame: pd.DataFrame,
    dailies: Mapping[str, pd.DataFrame | None],
    market_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Add ``beta``, ``beta_reason``, ``name_cost_bps`` and ``net_excess_r`` to the replay rows."""
    out = frame.reset_index(drop=True).copy()
    betas: list[float] = []
    reasons: list[str | None] = []
    costs: list[float] = []
    excess: list[float] = []
    for row in out.itertuples(index=False):
        session = pd.Timestamp(row.session).date()
        daily = dailies.get(row.symbol)
        beta, reason = trailing_beta(daily, market_daily, session)
        fill = float(row.entry_price)
        risk_unit = abs(float(row.entry) - float(row.stop))
        cost = name_cost_bps(fill, risk_unit, cost_inputs(daily, session))
        value = net_excess_r(float(row.r), fill, risk_unit, cost, beta, float(row.market_r))
        betas.append(math.nan if beta is None else beta)
        reasons.append(reason)
        costs.append(cost)
        excess.append(math.nan if value is None else value)
    out["beta"] = betas
    out["beta_reason"] = reasons
    out["name_cost_bps"] = costs
    out["net_excess_r"] = excess
    return out


def posterior(n: int, live_mean: float | None, entry: PriorEntry) -> tuple[float, float, float]:
    """``(posterior_mean, posterior_se, prior_weight_k)``; ``n == 0`` returns the prior.

    ``PriorEntry`` guarantees ``sd > 0`` and ``tau > 0``, so ``v0`` and ``k`` are positive and finite.
    """
    v0 = entry.tau**2 + entry.se_session**2
    k = entry.sd**2 / v0
    if n <= 0 or live_mean is None:
        return entry.mean, math.sqrt(v0), k
    mean = (n * live_mean + k * entry.mean) / (n + k)
    return mean, math.sqrt(1.0 / (n / entry.sd**2 + 1.0 / v0)), k
