"""Predeclared bracket counterfactual (B1-B5) over the setup-outcome replay frame.

See ``docs/superpowers/specs/2026-10-18-bracket-counterfactual-design.md``. Five exit arms
(the live bracket plus four counterfactuals) are compared on the same replayed native setups,
paired by setup. This module holds the frozen protocol model; statistics and the executor
follow in later tasks. Research only: no promotion, shadow or registry credit.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, model_validator

from agentic_trader.research.setups.labels import LIVE_ARM, BracketArm, TrailSpec
from agentic_trader.research.setups.recurrence import _ci90, _p_positive, _weighted, _write_private_gzip
from agentic_trader.research.setups.study import _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "HYPOTHESES",
    "LIVE_ARM_NAME",
    "SETUP_KEY",
    "ArmSpec",
    "BracketCounterfactualProtocol",
    "arm_mean",
    "confirmation",
    "discovery",
    "execute_bracket_counterfactual",
    "holm",
    "pair_arms",
    "paired_contrast",
    "sample_floors",
    "winsorized",
]

HYPOTHESES: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5")
LIVE_ARM_NAME = "A0_live"

_MIN_MATURATION_DAYS = 30
_MIN_EMBARGO_DAYS = 28
_TRAIL_KEYS = {"trigger_r", "distance_r"}
SETUP_KEY = ("decision_at", "symbol", "strategy", "timeframe", "direction")


class ArmSpec(BaseModel, frozen=True, extra="forbid"):
    """One exit arm as declared in the protocol file."""

    name: str
    target_r: float | Literal["planned"] | None
    trail: dict | None

    def to_arm(self) -> BracketArm:
        if self.target_r == "planned":
            return LIVE_ARM
        trail = TrailSpec(**self.trail) if self.trail is not None else None
        return BracketArm(self.name, self.target_r, trail)


class BracketCounterfactualProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified bracket-counterfactual protocol (spec section 7).

    ``hypotheses`` and ``decision_rule`` are recorded verbatim for the human record."""

    version: Literal["bracket_counterfactual_v1"]
    window: tuple[date, date]
    data_cutoff: date
    robustness_window: tuple[date, date]
    robustness_data_cutoff: date
    confirmation_window: tuple[date, date]
    confirmation_data_cutoff: date
    confirmation_prior_use: str
    embargo_sessions: int
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    arms: tuple[ArmSpec, ...]
    r_cap: float
    bootstrap: dict
    holm_alpha: float
    min_setups: int
    min_sessions: int
    min_mature_fraction: float
    min_divergent: int
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    strategy_config: dict
    sector_etf: dict[str, str]
    hypotheses: str
    decision_rule: str

    @model_validator(mode="after")
    def _check(self) -> BracketCounterfactualProtocol:
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if not self.cost_bps_per_side:
            raise ValueError("cost_bps_per_side must be non-empty")
        if self.r_cap <= 0:
            raise ValueError("r_cap must be positive")
        if not 0 < self.holm_alpha < 0.5:
            raise ValueError("holm_alpha must be in (0, 0.5)")
        for name, (start, end), cutoff in (
            ("window", self.window, self.data_cutoff),
            ("robustness_window", self.robustness_window, self.robustness_data_cutoff),
            ("confirmation_window", self.confirmation_window, self.confirmation_data_cutoff),
        ):
            if start >= end:
                raise ValueError(f"{name} must be non-empty and ordered (start < end)")
            required = end + timedelta(days=_MIN_MATURATION_DAYS)
            if cutoff < required:
                raise ValueError(
                    f"data_cutoff for {name} must be at least {_MIN_MATURATION_DAYS} days after its end "
                    f"(need >= {required.isoformat()})"
                )
        if self.robustness_window[1] + timedelta(days=_MIN_MATURATION_DAYS) > self.window[0]:
            raise ValueError("robustness_window must end more than one label horizon (30 days) before window starts")
        if self.confirmation_window[0] < self.window[1] + timedelta(days=_MIN_EMBARGO_DAYS):
            raise ValueError(
                f"confirmation_window must start at least {_MIN_EMBARGO_DAYS} calendar days (the embargo) after the window ends"
            )
        names = [arm.name for arm in self.arms]
        if len(set(names)) != len(names):
            raise ValueError("arm names must be unique")
        first = self.arms[0] if self.arms else None
        if (
            first is None
            or first.name != LIVE_ARM_NAME
            or first.target_r != "planned"
            or first.trail is not None
            or names.count(LIVE_ARM_NAME) != 1
        ):
            raise ValueError("arms must start with exactly one A0_live arm with target_r 'planned' and no trail")
        for arm in self.arms[1:]:
            if arm.target_r == "planned":
                raise ValueError("'planned' target_r is valid only on A0_live")
        for arm in self.arms:
            if arm.trail is None:
                continue
            if set(arm.trail) != _TRAIL_KEYS or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in arm.trail.values()
            ):
                raise ValueError(f"arm {arm.name}: trail needs exactly positive trigger_r and distance_r")
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def bracket_arms(self) -> tuple[BracketArm, ...]:
        return tuple(arm.to_arm() for arm in self.arms)

    def counterfactual_arm_names(self) -> tuple[str, ...]:
        return tuple(arm.name for arm in self.arms[1:])


# ---------------------------------------------------------------- statistics


def winsorized(long: pd.DataFrame, r_cap: float) -> pd.DataFrame:
    """Copy with ``r`` and ``r_cost`` clipped to +-``r_cap``; ``excess_r`` recomputed and clipped too."""
    out = long.copy()
    for column in ("r", "r_cost"):
        out[column] = out[column].astype(float).clip(-r_cap, r_cap)
    out["excess_r"] = (out["r_cost"] - out["market_r"].astype(float)).clip(-r_cap, r_cap)
    return out


def pair_arms(long: pd.DataFrame, live: str, other: str) -> pd.DataFrame:
    """Resolved rows of two arms joined by setup; ``divergent`` marks a differing hit or exit time."""
    keep = [*SETUP_KEY, "session", "hit", "exit_time", "excess_r", "r_cost"]
    resolved = long[long["hit"] != "immature"]
    left = resolved.loc[resolved["arm"] == live, keep]
    right = resolved.loc[resolved["arm"] == other, [*SETUP_KEY, "hit", "exit_time", "excess_r", "r_cost"]]
    joined = left.merge(right, on=list(SETUP_KEY), how="inner", suffixes=("_live", "_other"))
    exit_differs = (joined["exit_time_live"] != joined["exit_time_other"]) & ~(
        joined["exit_time_live"].isna() & joined["exit_time_other"].isna()
    )
    joined["divergent"] = (joined["hit_live"] != joined["hit_other"]) | exit_differs
    joined = joined.rename(columns={"excess_r_live": "excess_live", "excess_r_other": "excess_other"})
    return joined[["session", "excess_live", "excess_other", "r_cost_live", "r_cost_other", "divergent"]].reset_index(
        drop=True
    )


def _session_sums(sessions: pd.Series, values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Per sorted distinct session: sum and count of the (already finite) values."""
    grouped = values.groupby(sessions.to_numpy()).agg(["sum", "count"]).sort_index()
    return grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)


def _bootstrap_mean(
    sums: np.ndarray, counts: np.ndarray, protocol: BracketCounterfactualProtocol
) -> tuple[float, list, int, float]:
    spec = protocol.bootstrap
    n = len(sums)
    everything = np.arange(n)
    point = _weighted(sums, counts, everything) if n else float("nan")
    draws = _stationary_index_draws(n, spec["block_mean"], spec["draws"], spec["seed"])
    boot = np.array([_weighted(sums, counts, rows) for rows in draws], dtype=float) if n else np.zeros(0)
    return point, _ci90(boot), int(np.isfinite(boot).sum()), _p_positive(boot)


def paired_contrast(pairs: pd.DataFrame, protocol: BracketCounterfactualProtocol) -> dict:
    """Setup-weighted mean(excess_other - excess_live); whole sessions resampled once for both arms."""
    diff = pairs["excess_other"].astype(float) - pairs["excess_live"].astype(float)
    finite = np.isfinite(diff.to_numpy(float))
    sums, counts = _session_sums(pairs.loc[finite, "session"], diff[finite])
    point, ci90, finite_draws, p_value = _bootstrap_mean(sums, counts, protocol)
    return {
        "n_pairs": int(counts.sum()),
        "n_sessions": len(sums),
        "n_divergent": int(pairs.loc[finite, "divergent"].sum()),
        "mean_diff": float(point),
        "ci90": [float(v) for v in ci90],
        "p_one_sided": float(p_value),
        "finite_draws": finite_draws,
    }


def arm_mean(long: pd.DataFrame, arm: str, protocol: BracketCounterfactualProtocol) -> dict:
    """Setup-weighted mean ``r_cost`` of the arm's resolved rows with a session-block interval."""
    rows = long[(long["arm"] == arm) & (long["hit"] != "immature")]
    rows = rows[np.isfinite(rows["r_cost"].to_numpy(float))]
    sums, counts = _session_sums(rows["session"], rows["r_cost"].astype(float))
    point, ci90, _, _ = _bootstrap_mean(sums, counts, protocol)
    lower = float(ci90[0])
    return {
        "n": int(counts.sum()),
        "n_sessions": len(sums),
        "mean_r_cost": float(point),
        "ci90": [float(v) for v in ci90],
        "holds": bool(math.isfinite(lower) and lower >= 0.0),
    }


def _b1_holds(rejected: bool, mean_diff: float) -> bool:
    """B1: Holm-rejected and a finite, positive paired mean difference."""
    return bool(rejected and math.isfinite(mean_diff) and mean_diff > 0.0)


def holm(p_values: Mapping[str, float], alpha: float) -> dict[str, dict]:
    """Holm step-down; a NaN p counts as p = 1.0 (included in m, never rejected)."""
    finite = sorted(((p if math.isfinite(p) else 1.0, name) for name, p in p_values.items()), key=lambda item: item[0])
    m = len(finite)
    out: dict[str, dict] = {}
    running = 0.0
    stopped = False
    for rank, (p, name) in enumerate(finite):
        running = max(running, min(1.0, (m - rank) * p))
        rejected = not stopped and running < alpha
        stopped = stopped or not rejected
        out[name] = {"p": float(p), "holm_p": float(running), "rejected": bool(rejected)}
    return {name: out[name] for name in p_values}


def sample_floors(
    long: pd.DataFrame, pairs_by_arm: Mapping[str, pd.DataFrame], protocol: BracketCounterfactualProtocol
) -> dict:
    """B4 sample floors from the live arm's counts and each arm's divergent pairs."""
    live = long[long["arm"] == LIVE_ARM_NAME]
    n_setups = len(live)
    n_sessions = int(live["session"].nunique())
    resolved = int((live["hit"] != "immature").sum())
    mature_fraction = resolved / n_setups if n_setups else 0.0
    base = (
        n_setups >= protocol.min_setups
        and n_sessions >= protocol.min_sessions
        and mature_fraction >= protocol.min_mature_fraction
    )
    arms = {}
    for arm, pairs in pairs_by_arm.items():
        usable = np.isfinite(pairs["excess_other"].astype(float).to_numpy()) & np.isfinite(
            pairs["excess_live"].astype(float).to_numpy()
        )
        n_divergent = int(pairs.loc[usable, "divergent"].sum())
        arms[arm] = {"n_divergent": n_divergent, "holds": bool(base and n_divergent >= protocol.min_divergent)}
    return {
        "n_setups": n_setups,
        "n_sessions": n_sessions,
        "mature_fraction": float(mature_fraction),
        "arms": arms,
    }


def discovery(
    primary_long: pd.DataFrame, robustness_long: pd.DataFrame, protocol: BracketCounterfactualProtocol
) -> dict:
    """Evaluate B1-B4 for every counterfactual arm and select the passing arm with the largest B1 effect."""
    primary = winsorized(primary_long, protocol.r_cap)
    robust = winsorized(robustness_long, protocol.r_cap)
    names = protocol.counterfactual_arm_names()
    primary_pairs = {arm: pair_arms(primary, LIVE_ARM_NAME, arm) for arm in names}
    contrasts = {arm: paired_contrast(primary_pairs[arm], protocol) for arm in names}
    holm_result = holm({arm: contrasts[arm]["p_one_sided"] for arm in names}, protocol.holm_alpha)
    floors = sample_floors(primary, primary_pairs, protocol)
    arms: dict[str, dict] = {}
    for arm in names:
        robust_contrast = paired_contrast(pair_arms(robust, LIVE_ARM_NAME, arm), protocol)
        robust_diff = robust_contrast["mean_diff"]
        arms[arm] = {
            "b1": {
                **contrasts[arm],
                "holm_p": holm_result[arm]["holm_p"],
                "holds": _b1_holds(holm_result[arm]["rejected"], contrasts[arm]["mean_diff"]),
            },
            "b2": arm_mean(primary, arm, protocol),
            "b3": {**robust_contrast, "holds": bool(math.isfinite(robust_diff) and robust_diff > 0.0)},
            "b4": {
                "n_setups": floors["n_setups"],
                "n_sessions": floors["n_sessions"],
                "mature_fraction": floors["mature_fraction"],
                **floors["arms"][arm],
            },
        }
    passing = [arm for arm in names if all(arms[arm][b]["holds"] for b in ("b1", "b2", "b3", "b4"))]
    selected = None
    for arm in passing:
        if selected is None or arms[arm]["b1"]["mean_diff"] > arms[selected]["b1"]["mean_diff"]:
            selected = arm
    return {"arms": arms, "holm": holm_result, "floors": floors, "passing": passing, "selected_arm": selected}


def confirmation(confirmation_long: pd.DataFrame, arm: str, protocol: BracketCounterfactualProtocol) -> dict:
    """The one-shot confirmation rule for the selected arm: positive paired excess and a non-negative mean."""
    long = winsorized(confirmation_long, protocol.r_cap)
    contrast = paired_contrast(pair_arms(long, LIVE_ARM_NAME, arm), protocol)
    mean = arm_mean(long, arm, protocol)
    holds = (
        math.isfinite(contrast["mean_diff"])
        and math.isfinite(mean["mean_r_cost"])
        and contrast["mean_diff"] > 0.0
        and mean["mean_r_cost"] >= 0.0
    )
    return {"contrast": contrast, "arm_mean": mean, "holds": bool(holds)}


# ---------------------------------------------------------------- executor

_WINDOWS = ("primary", "robustness", "confirmation")
_RULES = ("b1", "b2", "b3", "b4")


def _mean_or_nan(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns or frame.empty:
        return float("nan")
    return float(pd.to_numeric(frame[column], errors="coerce").mean())


def _arm_block(rows: pd.DataFrame) -> dict:
    """Descriptive block for one arm's resolved rows (never decisive)."""
    n = len(rows)

    def rate(hit: str) -> float:
        return float((rows["hit"] == hit).mean()) if n else float("nan")

    return {
        "n": n,
        "target_rate": rate("target"),
        "stop_rate": rate("stop"),
        "timeout_rate": rate("timeout"),
        "mean_r_cost": _mean_or_nan(rows, "r_cost"),
        "mean_r": _mean_or_nan(rows, "r"),
        "mean_holding_sessions": _mean_or_nan(rows, "holding_sessions"),
        "mean_market_r": _mean_or_nan(rows, "market_r"),
        "mean_excess_r": _mean_or_nan(rows, "excess_r"),
        "mean_ratchet_moves": _mean_or_nan(rows, "ratchet_moves"),
    }


def _per_arm(resolved: pd.DataFrame, arms: list[str]) -> dict:
    return {arm: _arm_block(resolved[resolved["arm"] == arm]) for arm in arms}


def _yearly_contrast(pairs: pd.DataFrame) -> dict:
    """Plain setup-weighted mean paired excess difference per calendar year of ``session``."""
    if pairs.empty:
        return {}
    diff = pairs["excess_other"].astype(float) - pairs["excess_live"].astype(float)
    years = pd.to_datetime(pairs["session"]).dt.year
    return {str(year): {"mean_diff": float(diff[years == year].mean())} for year in sorted(years.unique())}


def _window_descriptives(long: pd.DataFrame, protocol: BracketCounterfactualProtocol) -> dict:
    wins = winsorized(long, protocol.r_cap)
    resolved = wins[wins["hit"] != "immature"]
    present = set(wins["arm"].unique())
    arms = [arm.name for arm in protocol.arms if arm.name in present]
    stratum = resolved["strategy"].astype(str) + "/" + resolved["timeframe"].astype(str)
    return {
        "arms": _per_arm(resolved, arms),
        "by_setup": {name: _per_arm(resolved[stratum == name], arms) for name in sorted(stratum.unique())},
        "by_year": {
            arm: _yearly_contrast(pair_arms(wins, LIVE_ARM_NAME, arm))
            for arm in protocol.counterfactual_arm_names()
            if arm in present
        },
        "zero_cost": {arm: _mean_or_nan(resolved[resolved["arm"] == arm], "r") for arm in arms},
    }


def execute_bracket_counterfactual(
    protocol: BracketCounterfactualProtocol,
    directory: Path,
    *,
    frames: Mapping[str, Callable[..., pd.DataFrame]],
    environment: dict,
) -> dict:
    """Run B1-B5 and write the write-once artifacts.

    The confirmation declaration is its own file, written before the confirmation frame is
    called, because ``save_json_report`` never overwrites the manifest. Like ``execute_recurrence``
    a failure is recorded in ``result.json`` and returned, not raised.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    started_at = datetime.now(UTC).isoformat()
    save_json_report({**protocol.document(), "identity": protocol.identity}, directory / "protocol.json")
    save_json_report(
        {
            "protocol_id": protocol.identity,
            "environment": environment,
            "started_at": started_at,
            "hypotheses": list(HYPOTHESES),
            "arms": [arm.name for arm in protocol.arms],
            "windows": {
                "primary": [
                    protocol.window[0].isoformat(),
                    protocol.window[1].isoformat(),
                    protocol.data_cutoff.isoformat(),
                ],
                "robustness": [
                    protocol.robustness_window[0].isoformat(),
                    protocol.robustness_window[1].isoformat(),
                    protocol.robustness_data_cutoff.isoformat(),
                ],
                "confirmation": [
                    protocol.confirmation_window[0].isoformat(),
                    protocol.confirmation_window[1].isoformat(),
                    protocol.confirmation_data_cutoff.isoformat(),
                ],
            },
            "authorizes_promotion": False,
            "confirmation_declaration": "confirmation.json",
        },
        directory / "manifest.json",
    )
    try:
        read: dict[str, pd.DataFrame] = {}
        for name in ("primary", "robustness"):
            read[name] = frames[name]()
            _write_private_gzip(read[name], directory / "frames" / f"{name}.csv.gz")
        found = discovery(read["primary"], read["robustness"], protocol)
        selected = found["selected_arm"]
        b5 = None
        if selected is not None:
            save_json_report(
                {
                    "protocol_id": protocol.identity,
                    "confirmation_read": True,
                    "confirmation_arm": selected,
                    "confirmation_arms": [LIVE_ARM_NAME, selected],
                    "declared_at": datetime.now(UTC).isoformat(),
                },
                directory / "confirmation.json",
            )
            chosen = next(arm for arm in protocol.bracket_arms() if arm.name == selected)
            read["confirmation"] = frames["confirmation"]((LIVE_ARM, chosen))
            _write_private_gzip(read["confirmation"], directory / "frames" / "confirmation.csv.gz")
            b5 = confirmation(read["confirmation"], selected, protocol)
        result = {
            "status": "completed",
            "decision": "eligible_bracket_change" if b5 is not None and b5["holds"] else "no_change",
            "selected_arm": selected,
            **{rule: {arm: found["arms"][arm][rule] for arm in found["arms"]} for rule in _RULES},
            "b5": b5,
            "passing": found["passing"],
            "holm": found["holm"],
            "floors": found["floors"],
            "descriptives": {name: _window_descriptives(read[name], protocol) for name in read},
            "n_rows": {name: len(read[name]) for name in read},
            "authorizes_promotion": False,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "protocol_id": protocol.identity,
            "started_at": started_at,
            "error": f"{type(exc).__name__}: {exc}",
            "authorizes_promotion": False,
        }
    save_json_report(_finite_json(result), directory / "result.json")
    return result
