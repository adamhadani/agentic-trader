"""Shared a priori statistics: event-weighted means under a stationary session-block bootstrap.

Moved verbatim from ``pead_study`` (short-suppression precedent, ``research/setups/baserates.py``):
``ci90`` = [5th, 95th] percentiles; a criterion holds when its lower bound is above zero; the
paired test resamples each control session once for both means.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from agentic_trader.research.apriori.catalog import BootstrapSpec
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive, _weighted
from agentic_trader.research.setups.study import _stationary_index_draws


__all__ = ["mean_test", "paired_test", "trimmed_mean"]


def _sums(frame: pd.DataFrame, sessions: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    grouped = frame.groupby("session")["r_cost"].agg(["sum", "count"]).reindex(sessions, fill_value=0)
    return grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)


def _draws(n: int, spec: BootstrapSpec) -> np.ndarray:
    return _stationary_index_draws(n, spec.block_mean, spec.draws, spec.seed)


def mean_test(leg: pd.DataFrame, spec: BootstrapSpec) -> dict:
    sessions = pd.Index(sorted(leg["session"].unique()))
    sums, counts = _sums(leg, sessions)
    boot = np.array([_weighted(sums, counts, rows) for rows in _draws(len(sessions), spec)], dtype=float)
    ci90 = _ci90(boot)
    mean_r_cost = _weighted(sums, counts, np.arange(len(sessions))) if len(sessions) else float("nan")
    return {
        "n": int(counts.sum()),
        "n_sessions": len(sessions),
        "mean_r_cost": mean_r_cost,
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_one_sided_positive(boot),
        "holds": bool(np.isfinite(mean_r_cost) and mean_r_cost > 0.0 and np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def paired_test(leg: pd.DataFrame, control: pd.DataFrame, spec: BootstrapSpec) -> dict:
    sessions = pd.Index(sorted(control["session"].unique()))
    leg_sums, leg_n = _sums(leg, sessions)
    ctl_sums, ctl_n = _sums(control, sessions)

    def diff(rows: np.ndarray) -> float:
        return _weighted(leg_sums, leg_n, rows) - _weighted(ctl_sums, ctl_n, rows)

    boot = np.array([diff(rows) for rows in _draws(len(sessions), spec)], dtype=float)
    ci90 = _ci90(boot)
    mean_diff = diff(np.arange(len(sessions))) if len(sessions) else float("nan")
    return {
        "n_leg": int(leg_n.sum()),
        "n_control": int(ctl_n.sum()),
        "n_sessions": len(sessions),
        "control_mean_r_cost": _weighted(ctl_sums, ctl_n, np.arange(len(sessions))) if len(sessions) else float("nan"),
        "mean_diff": mean_diff,
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_one_sided_positive(boot),
        "holds": bool(np.isfinite(mean_diff) and mean_diff > 0.0 and np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = math.floor(fraction * len(ordered))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")
