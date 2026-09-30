# agentic_trader/research/pooled/stats.py
"""Session-paired edge statistics for pooled picks.

The decisive statistic is the mean over sessions with a pick of
``mean R(picks) - mean R(all labelled eligible cells)`` that session, with a stationary
bootstrap over whole sessions (``_stationary_index_draws``, block mean 20). Sessions
without a pick carry zero weight in the point estimate and in every bootstrap draw.
Two-way clustered errors, a calendar-time Newey-West series and the design effect are
cross-checks, reported and never decisive. The calendar-time series spreads each pick's
residual evenly over its holding sessions (the cube stores final R, not a daily path).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive
from agentic_trader.research.setups.study import _stationary_index_draws


__all__ = [
    "SessionTable",
    "bootstrap_draws",
    "calendar_time_newey_west",
    "design_effect",
    "leg_mean_test",
    "paired_edge_test",
    "session_table",
    "trimmed_mean",
    "two_way_clustered",
]


@dataclass(frozen=True)
class SessionTable:
    sessions: tuple[date, ...]
    pick_sum: np.ndarray  # float [S]
    pick_n: np.ndarray  # float [S]
    control_mean: np.ndarray  # float [S], NaN without a labelled cell
    edge: np.ndarray  # float [S], NaN without a pick
    pick_rows: pd.DataFrame
    dropped_unlabelled: int
    dropped_purged: int


def session_table(picks: Picks, view: CubeView, *, purge: bool, column: str = "r_cost") -> SessionTable:
    values = getattr(view, column)
    usable = view.labelled.copy()
    if purge:
        last = len(view.sessions) - 1
        exits = np.arange(len(view.sessions))[:, None] + view.holding.astype(np.int64) - 1
        usable &= exits <= last
    counts = usable.sum(axis=1)
    sums = np.where(usable, values, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        control_mean = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    pick_sum = np.zeros(len(view.sessions))
    pick_n = np.zeros(len(view.sessions))
    rows = []
    dropped_unlabelled = dropped_purged = 0
    for row, col in zip(picks.session_idx.tolist(), picks.symbol_idx.tolist(), strict=True):
        if not view.labelled[row, col]:
            dropped_unlabelled += 1
            continue
        if not usable[row, col]:
            dropped_purged += 1
            continue
        value = float(values[row, col])
        pick_sum[row] += value
        pick_n[row] += 1
        rows.append(
            {
                "session_idx": row,
                "symbol_idx": col,
                "r_gross": float(view.r_gross[row, col]),
                "r_cost": float(view.r_cost[row, col]),
                "hit": int(view.hit[row, col]),
                "holding": int(view.holding[row, col]),
                "control_mean": float(control_mean[row]),
                "residual": value - float(control_mean[row]),
            }
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        edge = np.where(
            (pick_n > 0) & np.isfinite(control_mean), pick_sum / np.maximum(pick_n, 1) - control_mean, np.nan
        )
    columns = ["session_idx", "symbol_idx", "r_gross", "r_cost", "hit", "holding", "control_mean", "residual"]
    return SessionTable(
        sessions=view.sessions,
        pick_sum=pick_sum,
        pick_n=pick_n,
        control_mean=control_mean,
        edge=edge,
        pick_rows=pd.DataFrame(rows, columns=columns),
        dropped_unlabelled=dropped_unlabelled,
        dropped_purged=dropped_purged,
    )


@lru_cache(maxsize=16)
def bootstrap_draws(n: int, block_mean: float, draws: int, seed: int) -> np.ndarray:
    return _stationary_index_draws(n, block_mean, draws, seed)


def _ratio_boot(numerator: np.ndarray, weight: np.ndarray, draws: np.ndarray) -> np.ndarray:
    if draws.shape[1] == 0:
        return np.full(draws.shape[0], np.nan)
    num = numerator[draws].sum(axis=1)
    den = weight[draws].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.maximum(den, 1e-300), np.nan)


def _summary(point: float, boot: np.ndarray) -> dict:
    finite = boot[np.isfinite(boot)]
    se = float(np.std(finite, ddof=1)) if finite.size > 1 else float("nan")
    ci90 = _ci90(boot)
    return {
        "mean": point,
        "se": se,
        "t": point / se if se and math.isfinite(se) and se > 0 else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_one_sided_positive(boot),
        "finite_draws": int(finite.size),
        "holds": bool(math.isfinite(point) and point > 0 and math.isfinite(ci90[0]) and ci90[0] > 0),
    }


def paired_edge_test(table: SessionTable, draws: np.ndarray) -> dict:
    weight = np.isfinite(table.edge).astype(float)
    numerator = np.where(weight > 0, table.edge, 0.0)
    total = weight.sum()
    point = float(numerator.sum() / total) if total else float("nan")
    return {**_summary(point, _ratio_boot(numerator, weight, draws)), "n_sessions": int(total)}


def leg_mean_test(table: SessionTable, draws: np.ndarray) -> dict:
    total = table.pick_n.sum()
    point = float(table.pick_sum.sum() / total) if total else float("nan")
    return {**_summary(point, _ratio_boot(table.pick_sum, table.pick_n, draws)), "n": int(total)}


def trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = math.floor(fraction * len(ordered))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")


def two_way_clustered(rows: pd.DataFrame) -> dict:
    """Standard error of the mean residual, clustered by session and by symbol (Thompson 2011)."""
    if rows.empty:
        return {"mean": float("nan"), "se": float("nan"), "t": float("nan")}
    residual = rows["residual"].to_numpy(float)
    mean = float(residual.mean())
    e = pd.Series(residual - mean)
    n = len(e)
    by_date = float(sum(g.sum() ** 2 for _, g in e.groupby(rows["session_idx"].to_numpy())))
    by_symbol = float(sum(g.sum() ** 2 for _, g in e.groupby(rows["symbol_idx"].to_numpy())))
    white = float((e**2).sum())
    variance = by_date + by_symbol - white
    if variance <= 0:
        variance = max(by_date, by_symbol)
    se = math.sqrt(variance) / n
    return {"mean": mean, "se": se, "t": mean / se if se > 0 else float("nan")}


def calendar_time_newey_west(rows: pd.DataFrame, n_sessions: int, lag: int) -> dict:
    """Mean per-session residual of open picks (each spread over its hold), Newey-West t."""
    if rows.empty or n_sessions == 0:
        return {"mean": float("nan"), "se": float("nan"), "t": float("nan"), "sessions": 0}
    totals = np.zeros(n_sessions)
    counts = np.zeros(n_sessions)
    for start, hold, residual in rows[["session_idx", "holding", "residual"]].itertuples(index=False):
        end = min(int(start) + int(hold), n_sessions)
        totals[int(start) : end] += float(residual) / max(int(hold), 1)
        counts[int(start) : end] += 1
    series = totals[counts > 0] / counts[counts > 0]
    t_len = len(series)
    mean = float(series.mean())
    x = series - mean
    variance = float(x @ x) / t_len
    for k in range(1, min(lag, t_len - 1) + 1):
        variance += 2 * (1 - k / (lag + 1)) * float(x[k:] @ x[:-k]) / t_len
    se = math.sqrt(max(variance, 0.0) / t_len)
    return {"mean": mean, "se": se, "t": mean / se if se > 0 else float("nan"), "sessions": t_len}


def design_effect(rows: pd.DataFrame) -> dict:
    """Mean picks per session, intra-session correlation (one-way ANOVA) and DEFF = 1 + (m-1)rho."""
    if rows.empty:
        return {"picks_per_session": float("nan"), "icc": float("nan"), "deff": float("nan"), "effective_n": 0.0}
    groups = [g.to_numpy(float) for _, g in rows.groupby("session_idx")["residual"]]
    n = sum(len(g) for g in groups)
    k = len(groups)
    m_bar = n / k
    grand = float(np.concatenate(groups).mean())
    if k < 2 or n <= k:
        return {"picks_per_session": m_bar, "icc": float("nan"), "deff": float("nan"), "effective_n": float(n)}
    ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ssw = sum(((g - g.mean()) ** 2).sum() for g in groups)
    msb, msw = ssb / (k - 1), ssw / (n - k)
    n0 = (n - sum(len(g) ** 2 for g in groups) / n) / (k - 1)
    denominator = msb + (n0 - 1) * msw
    icc = float((msb - msw) / denominator) if denominator > 0 else 1.0
    icc = min(max(icc, 0.0), 1.0)
    deff = 1 + (m_bar - 1) * icc
    return {"picks_per_session": m_bar, "icc": icc, "deff": deff, "effective_n": n / deff}
