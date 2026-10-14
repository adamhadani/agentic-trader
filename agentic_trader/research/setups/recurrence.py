"""Predeclared setup-recurrence test (R1–R4) over the setup-outcome replay frame.

See ``docs/superpowers/specs/2026-10-14-setup-recurrence-design.md``. The live
``cards outcomes`` ``recurrence`` block showed repeat setups resolving far better than
first occurrences on 46 correlated rows; this freezes that hypothesis and tests it on
replayed history the live record never touched. Like ``baserates.py`` it reuses
``runner.build_window_frames`` and the study's stationary session-block bootstrap, fits
no model and grants no promotion, shadow or registry credit. Every frame callable is
called exactly once inside the try block; any failure is recorded as ``status: failed``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, model_validator

from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.study import _base_rates, _drop_immature, _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "BAR",
    "HYPOTHESES",
    "PRIOR_OUTCOMES",
    "STATES",
    "SetupRecurrenceProtocol",
    "execute_recurrence",
    "with_setup_recurrence",
]

# The labeller's bar: a prior counts as resolved only once its exit bar has closed.
BAR = pd.Timedelta("1h")
STATES = ("first", "repeat", "repeat_open")
RESOLVED = (BracketHit.TARGET.value, BracketHit.STOP.value, BracketHit.TIMEOUT.value)
PRIOR_OUTCOMES = (*RESOLVED, "none")
HYPOTHESES: tuple[str, ...] = ("R1", "R2", "R3", "R4")
DESCRIPTIVE_LOOKBACKS = (5, 10, 20)

_MIN_MATURATION_DAYS = 30
_SETUP_KEY = ["symbol", "strategy", "timeframe", "direction"]
_SORT_KEY = ["decision_at", *_SETUP_KEY]


def _is_timestamp(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def with_setup_recurrence(frame: pd.DataFrame, *, lookback_sessions: int) -> pd.DataFrame:
    """Tag each row's recurrence state over the same (symbol, strategy, timeframe, direction).

    A prior is a row of the same setup in a strictly earlier session within
    ``lookback_sessions`` trading sessions (distinct ``session`` values of the frame, so a
    holiday gap is not a session). ``state`` is ``first`` without a prior, ``repeat`` when the
    most recent prior had resolved by this decision (``hit`` in RESOLVED and
    ``exit_time + BAR <= decision_at``), else ``repeat_open``. ``prior_outcome`` is the resolved
    prior's hit or ``"none"``; ``prior_sessions_k`` counts the distinct earlier sessions holding
    the setup inside the lookback; ``prior_r_cost`` is the resolved prior's ``r_cost`` (NaN
    otherwise). Rows without a strategy, timeframe or direction are ``first``. Requires a
    unique index. Pure.

    Window-start truncation: priors are looked up only inside ``frame``, so the first
    ``lookback_sessions`` sessions of a window can only be ``first`` (their true priors
    precede the window). A caller that drops immature rows first (``execute_recurrence`` does)
    cannot produce ``repeat_open`` from an immature prior; ``repeat_open`` then only arises
    from a resolved-but-not-yet-closed exit bar.
    """
    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be at least 1")
    if not frame.index.is_unique:
        raise ValueError("with_setup_recurrence requires a unique index")
    out = frame.copy()
    state = pd.Series("first", index=out.index, dtype=object)
    prior_outcome = pd.Series("none", index=out.index, dtype=object)
    prior_sessions = pd.Series(0, index=out.index, dtype=int)
    prior_r_cost = pd.Series(np.nan, index=out.index, dtype=float)
    if len(out):
        ordinal = {session: i for i, session in enumerate(sorted(out["session"].unique()))}
        keyed = out.dropna(subset=_SETUP_KEY)
        for _, group in keyed.groupby(_SETUP_KEY, sort=False):
            records = list(group[["session", "decision_at", "hit", "exit_time", "r_cost"]].itertuples())
            for row in records:
                here = ordinal[row.session]
                priors = [
                    other
                    for other in records
                    if ordinal[other.session] < here and here - ordinal[other.session] <= lookback_sessions
                ]
                if not priors:
                    continue
                prior_sessions.loc[row.Index] = len({other.session for other in priors})
                latest = max(priors, key=lambda other: pd.Timestamp(other.decision_at))
                decided = pd.Timestamp(row.decision_at)
                resolved = (
                    latest.hit in RESOLVED
                    and _is_timestamp(latest.exit_time)
                    and pd.Timestamp(latest.exit_time) + BAR <= decided
                )
                if resolved:
                    state.loc[row.Index] = "repeat"
                    prior_outcome.loc[row.Index] = str(latest.hit)
                    prior_r_cost.loc[row.Index] = float(latest.r_cost)
                else:
                    state.loc[row.Index] = "repeat_open"
    out["state"] = state
    out["prior_outcome"] = prior_outcome
    out["prior_sessions_k"] = prior_sessions
    out["prior_r_cost"] = prior_r_cost
    return out


class SetupRecurrenceProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified recurrence protocol (spec §3.3). ``hypotheses`` and
    ``decision_rule`` are recorded verbatim for the human record; the computation lives in
    ``execute_recurrence``."""

    version: Literal["setup_recurrence_v1"]
    window: tuple[date, date]
    data_cutoff: date
    robustness_window: tuple[date, date]
    robustness_data_cutoff: date
    lookback_sessions: int
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    bootstrap: dict
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    strategy_config: dict
    sector_etf: dict[str, str]
    min_repeats: int
    min_repeat_sessions: int
    min_after_stop: int
    hypotheses: str
    decision_rule: str

    @model_validator(mode="after")
    def _check(self) -> SetupRecurrenceProtocol:
        if self.lookback_sessions < 1:
            raise ValueError("lookback_sessions must be at least 1")
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if not self.cost_bps_per_side:
            raise ValueError("cost_bps_per_side must be non-empty")
        for name, (start, end), cutoff in (
            ("window", self.window, self.data_cutoff),
            ("robustness_window", self.robustness_window, self.robustness_data_cutoff),
        ):
            if start >= end:
                raise ValueError(f"{name} must be non-empty and ordered (start < end)")
            required = end + timedelta(days=_MIN_MATURATION_DAYS)
            if cutoff < required:
                label = "data_cutoff" if name == "window" else "robustness_data_cutoff"
                raise ValueError(
                    f"{label} must be at least {_MIN_MATURATION_DAYS} days after {name} end (need >= {required.isoformat()})"
                )
        if self.robustness_window[1] + timedelta(days=_MIN_MATURATION_DAYS) > self.window[0]:
            raise ValueError("robustness_window must end more than one label horizon (30 days) before window starts")
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


def _draws(n_sessions: int, protocol: SetupRecurrenceProtocol) -> np.ndarray:
    spec = protocol.bootstrap
    return _stationary_index_draws(n_sessions, spec["block_mean"], spec["draws"], spec["seed"])


def _ci90(boot: np.ndarray) -> list[float]:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return [float("nan"), float("nan")]
    return [float(np.percentile(finite, 5)), float(np.percentile(finite, 95))]


def _p_positive(boot: np.ndarray) -> float:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return float("nan")
    return (int(np.sum(finite <= 0.0)) + 1) / (finite.size + 1)


def _session_table(frame: pd.DataFrame, masks: Mapping[str, pd.Series]) -> pd.DataFrame:
    """Per session: r_cost sum and count for each named mask (sessions with any setup at all)."""
    sessions = pd.Index(sorted(frame["session"].unique()))
    table = pd.DataFrame(index=sessions)
    for name, mask in masks.items():
        grouped = frame.loc[mask].groupby("session")["r_cost"].agg(["sum", "count"])
        table[f"sum_{name}"] = grouped["sum"].reindex(sessions).fillna(0.0)
        table[f"n_{name}"] = grouped["count"].reindex(sessions).fillna(0).astype(int)
    return table


def _weighted(sums: np.ndarray, counts: np.ndarray, rows: np.ndarray) -> float:
    n = counts[rows].sum()
    return float(sums[rows].sum() / n) if n > 0 else float("nan")


def _paired_difference(
    frame: pd.DataFrame,
    a: pd.Series,
    b: pd.Series,
    protocol: SetupRecurrenceProtocol,
    *,
    names: tuple[str, str] = ("repeat", "first"),
) -> dict:
    """Setup-weighted mean(a) − mean(b); each draw resamples whole sessions once for both.

    ``names`` labels the two groups in the output keys (``n_<name>``, ``mean_<name>``).
    """
    table = _session_table(frame, {"a": a, "b": b})
    sa, na = table["sum_a"].to_numpy(float), table["n_a"].to_numpy(float)
    sb, nb = table["sum_b"].to_numpy(float), table["n_b"].to_numpy(float)

    def diff(rows: np.ndarray) -> float:
        return _weighted(sa, na, rows) - _weighted(sb, nb, rows)

    n_sessions = len(table)
    boot = np.array([diff(rows) for rows in _draws(n_sessions, protocol)], dtype=float) if n_sessions else np.zeros(0)
    ci90 = _ci90(boot)
    point = diff(np.arange(n_sessions)) if n_sessions else float("nan")
    return {
        f"n_{names[0]}": int(na.sum()),
        f"n_{names[1]}": int(nb.sum()),
        "n_sessions": n_sessions,
        f"mean_{names[0]}": _weighted(sa, na, np.arange(n_sessions)) if n_sessions else float("nan"),
        f"mean_{names[1]}": _weighted(sb, nb, np.arange(n_sessions)) if n_sessions else float("nan"),
        "mean_diff": point,
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _group_mean(frame: pd.DataFrame, mask: pd.Series, protocol: SetupRecurrenceProtocol) -> dict:
    """Setup-weighted mean r_cost of the masked rows.

    Resamples the full session calendar (every session with any setup), the same unit as R1;
    a draw that selects no masked row is NaN and is dropped, as ``finite_draws`` reports.
    """
    table = _session_table(frame, {"g": mask})
    sums, counts = table["sum_g"].to_numpy(float), table["n_g"].to_numpy(float)
    n_sessions = len(table)
    boot = (
        np.array([_weighted(sums, counts, rows) for rows in _draws(n_sessions, protocol)], dtype=float)
        if n_sessions
        else np.zeros(0)
    )
    ci90 = _ci90(boot)
    return {
        "n": int(counts.sum()),
        "n_sessions": n_sessions,
        "n_sessions_with_rows": int((counts > 0).sum()),
        "mean_r_cost": _weighted(sums, counts, np.arange(n_sessions)) if n_sessions else float("nan"),
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _bucket(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {
            "n": 0,
            "mean_r_cost": None,
            "mean_r": None,
            "target_rate": None,
            "stop_rate": None,
            "mean_holding_sessions": None,
        }
    hits = rows["hit"].astype(str)
    return {
        "n": len(rows),
        "mean_r_cost": float(rows["r_cost"].mean()),
        "mean_r": float(rows["r"].mean()),
        "target_rate": float((hits == BracketHit.TARGET.value).mean()),
        "stop_rate": float((hits == BracketHit.STOP.value).mean()),
        "mean_holding_sessions": float(pd.to_numeric(rows["holding_sessions"], errors="coerce").mean()),
    }


def _state_buckets(group: pd.DataFrame) -> dict:
    """All three states, plus the repeat rows split by the resolved prior's outcome."""
    out = {name: _bucket(group.loc[group["state"] == name]) for name in STATES}
    repeats = group.loc[group["state"] == "repeat"]
    out["repeat_by_prior_outcome"] = {
        name: _bucket(repeats.loc[repeats["prior_outcome"] == name]) for name in PRIOR_OUTCOMES
    }
    return out


def _within_session(frame: pd.DataFrame, protocol: SetupRecurrenceProtocol) -> dict:
    """Mean per-session (repeat mean − first mean) over sessions holding both, session-block bootstrapped."""
    repeat = frame.loc[frame["state"] == "repeat"].groupby("session")["r_cost"].mean()
    first = frame.loc[frame["state"] == "first"].groupby("session")["r_cost"].mean()
    both = repeat.index.intersection(first.index)
    diffs = (repeat.loc[both] - first.loc[both]).to_numpy(float)
    n = len(diffs)
    if n == 0:
        return {"n_sessions": 0, "mean_diff": float("nan"), "ci90": [float("nan"), float("nan")]}
    boot = np.array([diffs[rows].mean() for rows in _draws(n, protocol)], dtype=float)
    return {"n_sessions": n, "mean_diff": float(diffs.mean()), "ci90": _ci90(boot)}


def _by_tape(frame: pd.DataFrame, protocol: SetupRecurrenceProtocol) -> dict:
    """R1 recomputed by the SPY tape feature (above/below its 200-day average); NaN is counted only."""
    tape = frame["spy_above_200"] if "spy_above_200" in frame.columns else pd.Series(np.nan, index=frame.index)
    repeat = frame["state"] == "repeat"
    first = frame["state"] == "first"
    unknown = tape.isna()
    return {
        "above_200": _paired_difference(frame, repeat & (tape == 1.0), first & (tape == 1.0), protocol),
        "below_200": _paired_difference(frame, repeat & (tape == 0.0), first & (tape == 0.0), protocol),
        "unknown": {"n_repeat": int((repeat & unknown).sum()), "n_first": int((first & unknown).sum())},
    }


def _descriptives(tagged: pd.DataFrame, clean: pd.DataFrame, protocol: SetupRecurrenceProtocol) -> dict:
    by_state = {name: _bucket(tagged.loc[tagged["state"] == name]) for name in STATES}
    by_prior = {
        name: _bucket(tagged.loc[(tagged["state"] == "repeat") & (tagged["prior_outcome"] == name)])
        for name in PRIOR_OUTCOMES
    }
    by_setup = {
        f"{strategy}/{timeframe}": _state_buckets(group)
        for (strategy, timeframe), group in tagged.groupby(["strategy", "timeframe"])
    }
    years = tagged["session"].map(lambda value: str(value.year))
    by_year = {year: _state_buckets(group) for year, group in tagged.groupby(years)}
    state = tagged["state"]
    live_definition = _paired_difference(
        tagged,
        state.isin(("repeat", "repeat_open")),
        state == "first",
        protocol,
        names=("repeat_or_open", "first"),
    )
    by_lookback = {}
    for k in DESCRIPTIVE_LOOKBACKS:
        retagged = tagged if k == protocol.lookback_sessions else with_setup_recurrence(clean, lookback_sessions=k)
        by_lookback[str(k)] = _paired_difference(
            retagged, retagged["state"] == "repeat", retagged["state"] == "first", protocol
        )
    return {
        "by_state": by_state,
        "by_prior_outcome": by_prior,
        "by_setup": by_setup,
        "by_year": by_year,
        "by_lookback": by_lookback,
        "within_session": _within_session(tagged, protocol),
        "by_tape": _by_tape(tagged, protocol),
        "live_definition": live_definition,
        "repeat_open": int((tagged["state"] == "repeat_open").sum()),
        "base_rates": _base_rates(tagged),
    }


def _write_private_gzip(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
        frame.to_csv(compressed, index=False)


def execute_recurrence(
    protocol: SetupRecurrenceProtocol,
    directory: Path,
    *,
    frames: Mapping[str, Callable[[], pd.DataFrame]],
    environment: dict,
) -> dict:
    """Run R1–R4 on the primary window and R3 on the robustness window; write the artifacts.

    ``protocol.data_cutoff`` does not clip the cached bars here: labels end at the cutoff
    because the caches end there (the command claims the cache range from the cutoff) and the
    ``max_hold_sessions`` cap bounds every label.
    """
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
        tagged: dict[str, pd.DataFrame] = {}
        clean: dict[str, pd.DataFrame] = {}
        immature: dict[str, int] = {}
        for name in ("primary", "robustness"):
            raw = frames[name]()
            dropped, immature[name] = _drop_immature(raw)
            clean[name] = dropped.sort_values(_SORT_KEY, kind="stable").reset_index(drop=True)
            tagged[name] = with_setup_recurrence(clean[name], lookback_sessions=protocol.lookback_sessions)
            _write_private_gzip(tagged[name], directory / "frames" / f"{name}.csv.gz")
        primary = tagged["primary"]
        repeat = primary["state"] == "repeat"
        first = primary["state"] == "first"
        after_stop = repeat & (primary["prior_outcome"] == BracketHit.STOP.value)
        r1 = _paired_difference(primary, repeat, first, protocol)
        r2 = _group_mean(primary, after_stop, protocol)
        robustness = tagged["robustness"]
        r3_diff = _paired_difference(
            robustness, robustness["state"] == "repeat", robustness["state"] == "first", protocol
        )
        r3 = {**r3_diff, "holds": bool(np.isfinite(r3_diff["mean_diff"]) and r3_diff["mean_diff"] > 0.0)}
        n_repeat = int(repeat.sum())
        n_repeat_sessions = int(primary.loc[repeat, "session"].nunique())
        n_after_stop = int(after_stop.sum())
        r4 = {
            "n_repeat": n_repeat,
            "n_repeat_sessions": n_repeat_sessions,
            "n_after_stop": n_after_stop,
            "min_repeats": protocol.min_repeats,
            "min_repeat_sessions": protocol.min_repeat_sessions,
            "min_after_stop": protocol.min_after_stop,
            "holds": bool(
                n_repeat >= protocol.min_repeats
                and n_repeat_sessions >= protocol.min_repeat_sessions
                and n_after_stop >= protocol.min_after_stop
            ),
        }
        decision = "eligible_repeat_preference" if all(rule["holds"] for rule in (r1, r2, r3, r4)) else "no_change"
        result = {
            "status": "completed",
            "immature_dropped": immature,
            "n_setups": {name: len(tagged[name]) for name in tagged},
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "r4": r4,
            "decision": decision,
            "descriptives": {name: _descriptives(tagged[name], clean[name], protocol) for name in tagged},
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
