"""Shared a priori statistics: event-weighted means under a stationary session-block bootstrap.

Moved verbatim from ``pead_study`` (short-suppression precedent, ``research/setups/baserates.py``):
``ci90`` = [5th, 95th] percentiles; a criterion holds when its lower bound is above zero; the
paired test resamples each control session once for both means, and ``boot_sd`` is the sample standard
deviation of its finite draws. ``difference_in_paired_test`` compares two paired differences under one
joint resampling of the union of their sessions.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from agentic_trader.research.apriori.catalog import BootstrapSpec
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive, _weighted
from agentic_trader.research.setups.study import _stationary_index_draws


__all__ = ["difference_in_paired_test", "mean_test", "paired_test", "trimmed_mean"]


def _sums(frame: pd.DataFrame, sessions: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    grouped = frame.groupby("session")["r_cost"].agg(["sum", "count"]).reindex(sessions, fill_value=0)
    return grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)


def _draws(n: int, spec: BootstrapSpec) -> np.ndarray:
    return _stationary_index_draws(n, spec.block_mean, spec.draws, spec.seed)


def _sd(boot: np.ndarray) -> float:
    finite = boot[np.isfinite(boot)]
    return float(np.std(finite, ddof=1)) if finite.size > 1 else float("nan")


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
        "boot_sd": _sd(boot),
        "holds": bool(np.isfinite(mean_diff) and mean_diff > 0.0 and np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _paired_sums(
    leg: pd.DataFrame, control: pd.DataFrame, sessions: pd.Index
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Both sides' per-session sums and counts on ``sessions``, the leg's kept only where a control is.

    ``paired_test`` keys both sides by the control sessions; zeroing the leg's other sessions keeps that
    keying when ``sessions`` is a wider union.
    """
    leg_sums, leg_n = _sums(leg, sessions)
    ctl_sums, ctl_n = _sums(control, sessions)
    paired = ctl_n > 0
    return np.where(paired, leg_sums, 0.0), np.where(paired, leg_n, 0.0), ctl_sums, ctl_n


def difference_in_paired_test(
    leg: pd.DataFrame,
    leg_control: pd.DataFrame,
    other: pd.DataFrame,
    other_control: pd.DataFrame,
    spec: BootstrapSpec,
) -> dict:
    """``paired_test(leg, leg_control)``'s difference minus ``paired_test(other, other_control)``'s.

    One stationary session-block bootstrap (``spec``'s block mean, draws and seed) over the union of the two
    control frames' sessions resamples each session once for all four means, so a session both sides share
    moves both differences together. Each side is keyed by its own control sessions as ``paired_test`` keys
    it, so ``leg_diff`` and ``other_diff`` equal the two ``paired_test`` ``mean_diff`` values. Holds when the
    difference and its ci90 lower bound are above zero; a side without pairs gives ``NaN``.
    """
    sessions = pd.Index(sorted(set(leg_control["session"]) | set(other_control["session"])))
    leg_sums, leg_n, leg_ctl_sums, leg_ctl_n = _paired_sums(leg, leg_control, sessions)
    other_sums, other_n, other_ctl_sums, other_ctl_n = _paired_sums(other, other_control, sessions)

    def leg_diff(rows: np.ndarray) -> float:
        return _weighted(leg_sums, leg_n, rows) - _weighted(leg_ctl_sums, leg_ctl_n, rows)

    def other_diff(rows: np.ndarray) -> float:
        return _weighted(other_sums, other_n, rows) - _weighted(other_ctl_sums, other_ctl_n, rows)

    boot = np.array([leg_diff(rows) - other_diff(rows) for rows in _draws(len(sessions), spec)], dtype=float)
    ci90 = _ci90(boot)
    every = np.arange(len(sessions))
    leg_value = leg_diff(every) if len(sessions) else float("nan")
    other_value = other_diff(every) if len(sessions) else float("nan")
    mean_diff = leg_value - other_value
    return {
        "n_leg": int(leg_n.sum()),
        "n_leg_control": int(leg_ctl_n.sum()),
        "n_other": int(other_n.sum()),
        "n_other_control": int(other_ctl_n.sum()),
        "n_sessions": len(sessions),
        "leg_diff": leg_value,
        "other_diff": other_value,
        "mean_diff": mean_diff,
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_one_sided_positive(boot),
        "boot_sd": _sd(boot),
        "holds": bool(np.isfinite(mean_diff) and mean_diff > 0.0 and np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = math.floor(fraction * len(ordered))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")
