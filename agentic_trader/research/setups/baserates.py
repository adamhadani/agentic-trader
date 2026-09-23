"""Predeclared, descriptive short-suppression base-rate test.

See ``docs/superpowers/specs/2026-09-23-short-suppression-test.md``. This reuses the
setup-outcome study's window-acquisition machinery (``runner.build_window_frames``)
and its stationary session-block bootstrap (``study._stationary_index_draws``) over
one arbitrary, named decision window, but fits no model and grants no promotion or
shadow credit: it only computes descriptive per-(strategy, timeframe, direction) base
rates and the two predeclared S1/S2 hypotheses, then applies the frozen decision rule.

Exactly like ``execute_setup_study``, ``frame`` (the labelled window, built by
``runner.build_window_frames``) is called exactly once, and any failure -- including
inside ``frame()`` itself -- is recorded as ``{"status": "failed"}`` in ``result.json``
rather than raised, so a failed run always leaves durable evidence of the attempt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, model_validator

from agentic_trader.research.setups.study import _base_rates, _drop_immature, _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["HYPOTHESES", "SetupBaseRateProtocol", "execute_baserates"]

# S1 (pooled short mean r_cost) + S2 (paired long - short mean r_cost).
HYPOTHESES: tuple[str, ...] = ("S1", "S2")

_MIN_MATURATION_DAYS = 30


class SetupBaseRateProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified base-rate protocol.

    See ``docs/superpowers/specs/2026-09-23-short-suppression-test.md``. ``hypotheses``
    and ``decision_rule`` are free text recorded verbatim for the human record; they are
    not parsed or evaluated -- the actual S1/S2 computation and decision rule live in
    ``execute_baserates`` below.
    """

    version: Literal["setup_baserates_v1"]
    window: tuple[date, date]
    data_cutoff: date
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    bootstrap: dict
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    strategy_config: dict
    sector_etf: dict[str, str]
    hypotheses: str
    decision_rule: str

    @model_validator(mode="after")
    def _check_window(self) -> SetupBaseRateProtocol:
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if not self.cost_bps_per_side:
            raise ValueError("cost_bps_per_side must be non-empty")

        start, end = self.window
        if start >= end:
            raise ValueError("window must be non-empty and ordered (start < end)")

        required_cutoff = end + timedelta(days=_MIN_MATURATION_DAYS)
        if self.data_cutoff < required_cutoff:
            raise ValueError(
                f"data_cutoff must be at least {_MIN_MATURATION_DAYS} days after window end {end.isoformat()} "
                f"(need >= {required_cutoff.isoformat()}, got data_cutoff {self.data_cutoff.isoformat()})"
            )
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


def _bootstrap_kwargs(protocol: SetupBaseRateProtocol) -> dict:
    return {
        "block_mean": protocol.bootstrap["block_mean"],
        "draws": protocol.bootstrap["draws"],
        "seed": protocol.bootstrap["seed"],
    }


def _session_sums(frame: pd.DataFrame) -> pd.DataFrame:
    """Per session with any setup: r_cost sum and setup count for each direction.

    Sessions are the resampled unit; statistics are setup-weighted (sum / count over the
    drawn sessions), matching the protocol's "mean R_cost of ... setups".
    """
    grouped = frame.groupby(["session", "direction"])["r_cost"].agg(["sum", "count"])
    table = grouped.unstack("direction", fill_value=0).sort_index()
    out = pd.DataFrame(index=table.index)
    for direction in ("LONG", "SHORT"):
        out[f"sum_{direction}"] = table["sum"].get(direction, 0.0)
        out[f"n_{direction}"] = table["count"].get(direction, 0)
    return out


def _weighted(sums: np.ndarray, counts: np.ndarray, rows: np.ndarray) -> float:
    n = counts[rows].sum()
    return float(sums[rows].sum() / n) if n > 0 else float("nan")


def _draws(n_sessions: int, protocol: SetupBaseRateProtocol) -> np.ndarray:
    kwargs = _bootstrap_kwargs(protocol)
    return _stationary_index_draws(n_sessions, kwargs["block_mean"], kwargs["draws"], kwargs["seed"])


def _ci90(boot: np.ndarray) -> list[float]:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return [float("nan"), float("nan")]
    return [float(np.percentile(finite, 5)), float(np.percentile(finite, 95))]


def _p_one_sided_negative(boot: np.ndarray) -> float:
    """One-sided p for "mean < 0": the (add-one corrected) fraction of the bootstrap
    distribution that is *not* negative -- i.e. evidence against the hypothesis."""
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return float("nan")
    return (int(np.sum(finite >= 0.0)) + 1) / (finite.size + 1)


def _p_one_sided_positive(boot: np.ndarray) -> float:
    """One-sided p for "mean > 0" (mirrors ``spearman_by_session_bootstrap``'s convention)."""
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return float("nan")
    return (int(np.sum(finite <= 0.0)) + 1) / (finite.size + 1)


def _s1(frame: pd.DataFrame, protocol: SetupBaseRateProtocol) -> dict:
    """S1: pooled short mean r_cost < 0 (setup-weighted; whole sessions resampled)."""
    table = _session_sums(frame)
    short = table[table["n_SHORT"] > 0]
    sums, counts = short["sum_SHORT"].to_numpy(float), short["n_SHORT"].to_numpy(float)
    boot = (
        np.array([_weighted(sums, counts, rows) for rows in _draws(len(short), protocol)], dtype=float)
        if len(short)
        else np.zeros(0, dtype=float)
    )
    ci90 = _ci90(boot)
    return {
        "n_sessions": len(short),
        "n_setups": int(counts.sum()),
        "mean_r_cost": _weighted(sums, counts, np.arange(len(short))) if len(short) else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_one_sided_negative(boot),
        "holds": bool(np.isfinite(ci90[1]) and ci90[1] < 0.0),
    }


def _s2(frame: pd.DataFrame, protocol: SetupBaseRateProtocol) -> dict:
    """S2: mean r_cost(long) - mean r_cost(short) > 0.

    Setup-weighted means over every session with setups; each bootstrap draw resamples
    whole sessions once and computes both means on the same draw (paired).
    """
    table = _session_sums(frame)
    long_sums, long_n = table["sum_LONG"].to_numpy(float), table["n_LONG"].to_numpy(float)
    short_sums, short_n = table["sum_SHORT"].to_numpy(float), table["n_SHORT"].to_numpy(float)

    def diff(rows: np.ndarray) -> float:
        return _weighted(long_sums, long_n, rows) - _weighted(short_sums, short_n, rows)

    boot = (
        np.array([diff(rows) for rows in _draws(len(table), protocol)], dtype=float)
        if len(table)
        else np.zeros(0, dtype=float)
    )
    ci90 = _ci90(boot)
    return {
        "n_sessions": len(table),
        "n_long": int(long_n.sum()),
        "n_short": int(short_n.sum()),
        "mean_diff": diff(np.arange(len(table))) if len(table) else float("nan"),
        "ci90": ci90,
        "p_one_sided": _p_one_sided_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def execute_baserates(
    protocol: SetupBaseRateProtocol,
    directory: Path,
    *,
    frame: Callable[[], pd.DataFrame],
    environment: dict,
) -> dict:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)

    save_json_report({**protocol.document(), "identity": protocol.identity}, directory / "protocol.json")
    save_json_report(
        {
            "protocol_id": protocol.identity,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "hypotheses": list(HYPOTHESES),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )

    try:
        raw = frame()
        clean, immature_dropped = _drop_immature(raw)

        s1 = _s1(clean, protocol)
        s2 = _s2(clean, protocol)
        decision = "suppress_native_shorts" if s1["holds"] and s2["holds"] else "no_change"

        result = {
            "status": "completed",
            "immature_dropped": immature_dropped,
            "n_setups": len(clean),
            "base_rates": _base_rates(clean),
            "s1": s1,
            "s2": s2,
            "decision": decision,
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    save_json_report(_finite_json(result), directory / "result.json")
    return result
