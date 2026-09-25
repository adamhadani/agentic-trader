"""PEAD labelling, the frozen P1-P4 pass rule and the study executor.

Each event is labelled twice (LONG and SHORT brackets) with the setup study's
``label_bracket``: the control set for a direction is every labelled event in that
direction, and leg rows (``is_leg``) are the subset whose surprise and reaction agree.
The decision price is the close of the last regular-session hourly bar that *ends* at or
before the 10:35 decision on the same New York date -- never a later or earlier-day price.

Statistics follow the short-suppression precedent (``research/setups/baserates.py``):
event-weighted means, a stationary bootstrap over whole decision sessions, ``ci90`` =
[5th, 95th] percentiles, a criterion holding when its lower bound is above zero, and
P2's paired draw resampling each session once for both means.

``execute_pead_study`` writes ``protocol.json`` and ``manifest.json`` before calling
``build`` (the only provider access), and records any failure as ``status: failed`` in
``result.json`` instead of raising.
"""

from __future__ import annotations

import asyncio
import gzip
import math
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import LoadedEntry, PeadEntry
from agentic_trader.research.apriori.earnings_history import CalendarAcquisition
from agentic_trader.research.apriori.pead_events import MarketData, build_events
from agentic_trader.research.setups.baserates import _ci90, _p_one_sided_positive, _weighted
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket
from agentic_trader.research.setups.study import _finite_json, _stationary_index_draws
from agentic_trader.screeners.coverage import REGULAR_SESSION_HOURS_NY
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "PeadInputs",
    "decision_price",
    "evaluate_leg",
    "execute_pead_study",
    "label_events",
    "levels_for",
]

_DIRECTIONS = ("LONG", "SHORT")
# Hourly bars are trimmed to this span around each decision before labelling; the
# secondary hold (60 sessions) fits well inside it.
_LABEL_SPAN = timedelta(days=130)


@dataclass(frozen=True)
class PeadInputs:
    acquisition: CalendarAcquisition
    rows: tuple[CalendarRow, ...]
    duplicates: int
    market: MarketData
    hourly: Mapping[str, pd.DataFrame]
    bar_failures: Mapping[str, str]  # symbol -> reason


def _utc_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(frame.index)
    return index.tz_localize("UTC") if index.tz is None else index


def decision_price(hourly: pd.DataFrame, when: datetime) -> float | None:
    if hourly is None or hourly.empty:
        return None
    index = _utc_index(hourly)
    local = index.tz_convert(ET_TZ)
    day = when.astimezone(ET_TZ).date()
    eligible = (
        (local.hour.isin(REGULAR_SESSION_HOURS_NY)) & (local.date == day) & (index + pd.Timedelta(hours=1) <= when)
    )
    if not eligible.any():
        return None
    value = float(hourly["Close"].to_numpy(float)[np.flatnonzero(eligible)[-1]])
    return value if math.isfinite(value) and value > 0 else None


def levels_for(direction: str, price: float, atr: float, entry: PeadEntry) -> SetupLevels | None:
    risk = entry.trade.stop_atr_multiple * atr
    reward = entry.trade.target_r * risk
    if direction == "LONG":
        stop, target = price - risk, price + reward
    else:
        stop, target = price + risk, price - reward
    if min(stop, target) <= 0:
        return None
    return SetupLevels(direction=direction, entry=price, stop=stop, target=target)


def _cost_column(cost: float) -> str:
    return f"r_cost_{cost:g}bps"


def label_events(
    events: pd.DataFrame, hourly: Mapping[str, pd.DataFrame], entry: PeadEntry
) -> tuple[pd.DataFrame, dict]:
    counts: Counter[str] = Counter()
    rows: list[dict] = []
    for event in events.to_dict("records"):
        bars = hourly.get(event["symbol"])
        if bars is None or bars.empty:
            counts["no_hourly_bars"] += 1
            continue
        when = event["decision_at"]
        index = _utc_index(bars)
        window = bars.loc[(index >= when - timedelta(days=1)) & (index <= when + _LABEL_SPAN)]
        price = decision_price(window, when)
        if price is None:
            counts["no_decision_price"] += 1
            continue
        for direction in _DIRECTIONS:
            levels = levels_for(direction, price, float(event["atr"]), entry)
            if levels is None:
                counts["degenerate_levels"] += 1
                continue
            costs = {
                _cost_column(cost): label_bracket(
                    levels, when, window, max_hold_sessions=entry.trade.max_hold_sessions, cost_bps_per_side=cost
                )
                for cost in entry.costs_bps_per_side
            }
            decisive = costs[_cost_column(entry.decision_cost_bps)]
            if decisive.hit == BracketHit.IMMATURE:
                counts["immature"] += 1
                continue
            secondary = label_bracket(
                levels,
                when,
                window,
                max_hold_sessions=entry.trade.secondary_hold_sessions,
                cost_bps_per_side=entry.decision_cost_bps,
            )
            rows.append(
                {
                    **event,
                    "direction": direction,
                    "is_leg": event["leg"] == direction,
                    "hit": str(decisive.hit),
                    "r": decisive.r,
                    "r_cost": decisive.r_cost,
                    **{column: outcome.r_cost for column, outcome in costs.items()},
                    "hold_secondary_r_cost": secondary.r_cost,
                    "holding_sessions": decisive.holding_sessions,
                }
            )
    return pd.DataFrame(rows), dict(counts)


# --- Statistics ----------------------------------------------------------------------------


def _sums(frame: pd.DataFrame, sessions: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    grouped = frame.groupby("session")["r_cost"].agg(["sum", "count"]).reindex(sessions, fill_value=0)
    return grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)


def _draws(n: int, entry: PeadEntry) -> np.ndarray:
    spec = entry.bootstrap
    return _stationary_index_draws(n, spec.block_mean, spec.draws, spec.seed)


def _mean_test(leg: pd.DataFrame, entry: PeadEntry) -> dict:
    sessions = pd.Index(sorted(leg["session"].unique()))
    sums, counts = _sums(leg, sessions)
    boot = np.array([_weighted(sums, counts, rows) for rows in _draws(len(sessions), entry)], dtype=float)
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


def _paired_test(leg: pd.DataFrame, control: pd.DataFrame, entry: PeadEntry) -> dict:
    sessions = pd.Index(sorted(control["session"].unique()))
    leg_sums, leg_n = _sums(leg, sessions)
    ctl_sums, ctl_n = _sums(control, sessions)

    def diff(rows: np.ndarray) -> float:
        return _weighted(leg_sums, leg_n, rows) - _weighted(ctl_sums, ctl_n, rows)

    boot = np.array([diff(rows) for rows in _draws(len(sessions), entry)], dtype=float)
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


def _trimmed_mean(values: np.ndarray, fraction: float) -> float:
    ordered = np.sort(values[np.isfinite(values)])
    cut = math.floor(fraction * len(ordered))
    kept = ordered[cut : len(ordered) - cut]
    return float(kept.mean()) if kept.size else float("nan")


def evaluate_leg(labels: pd.DataFrame, direction: str, entry: PeadEntry) -> dict:
    control = labels[labels["direction"] == direction]
    leg = control[control["is_leg"].astype(bool)]
    recent = leg[leg["session"] >= entry.window.recent_from]
    trimmed = _trimmed_mean(leg["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else float("nan")
    p1 = _mean_test(leg, entry) if len(leg) else {"n": 0, "holds": False}
    p2 = _paired_test(leg, control, entry) if len(leg) else {"n_leg": 0, "holds": False}
    p3 = {
        "trimmed_mean": trimmed,
        "recent_mean": recent_mean,
        "holds": bool(np.isfinite(trimmed) and trimmed > 0 and np.isfinite(recent_mean) and recent_mean > 0),
    }
    p4 = {
        "n": len(leg),
        "n_recent": len(recent),
        "holds": len(leg) >= entry.pass_rule.min_events and len(recent) >= entry.pass_rule.min_recent_events,
    }
    return {"p1": p1, "p2": p2, "p3": p3, "p4": p4, "passes": all(p["holds"] for p in (p1, p2, p3, p4))}


# --- Diagnostics (descriptive; never decisive) ---------------------------------------------


def _mean_n(frame: pd.DataFrame, column: str = "r_cost") -> dict:
    values = frame[column].dropna()
    return {"n": len(values), "mean_r_cost": float(values.mean()) if len(values) else float("nan")}


def _diagnostics(labels: pd.DataFrame, events: pd.DataFrame, entry: PeadEntry) -> dict:
    out: dict = {}
    for direction in _DIRECTIONS:
        side = labels[labels["direction"] == direction]
        leg = side[side["is_leg"].astype(bool)]
        flag = "long" if direction == "LONG" else "short"
        by_year = {int(year): _mean_n(group) for year, group in leg.groupby(pd.to_datetime(leg["session"]).dt.year)}
        terciles = {}
        if len(leg) >= 3:
            # Rank first so repeated values cannot produce duplicate bin edges.
            buckets = pd.qcut(leg["median_dollar_volume"].rank(method="first"), 3, labels=["low", "mid", "high"])
            terciles = {str(name): _mean_n(group) for name, group in leg.groupby(buckets, observed=True)}
        out[direction] = {
            "costs": {column: _mean_n(leg, column) for column in labels.columns if column.startswith("r_cost_")},
            "surprise_only": _mean_n(side[side[f"surprise_{flag}"].astype(bool)]),
            "reaction_only": _mean_n(side[side[f"reaction_{flag}"].astype(bool)]),
            "secondary_hold": _mean_n(leg, "hold_secondary_r_cost"),
            "by_year": by_year,
            "liquidity_terciles": terciles,
            "hits": {str(k): int(v) for k, v in leg["hit"].value_counts().items()},
            "events_per_session": {
                "max": int(leg.groupby("session").size().max()) if len(leg) else 0,
                "median": float(leg.groupby("session").size().median()) if len(leg) else 0.0,
                "p90": float(leg.groupby("session").size().quantile(0.9)) if len(leg) else 0.0,
            },
        }
    both = events.dropna(subset=["surprise_pct", "surprise_pct_reported"])
    both = both[(both["surprise_pct"] != 0) & (both["surprise_pct_reported"] != 0)]
    out["surprise_sign_disagreements"] = int(
        (np.sign(both["surprise_pct"]) != np.sign(both["surprise_pct_reported"])).sum()
    )
    return out


def _save_frame(frame: pd.DataFrame, path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as target:
        target.write(frame.to_csv(index=False).encode())


async def execute_pead_study(
    loaded: LoadedEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[PeadInputs]],
    environment: dict,
) -> dict:
    entry = loaded.entry
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**entry.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "entry_id": entry.id,
            "version": entry.version,
            "sha256": loaded.sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        inputs = await build()
        acquisition = inputs.acquisition
        if acquisition.failed_fraction > entry.max_failed_calendar_fraction:
            raise ValueError(
                f"calendar acquisition failed for {len(acquisition.failed_dates)}/{acquisition.requested_dates} dates, "
                f"above the frozen {entry.max_failed_calendar_fraction:.0%} limit"
            )
        events, event_counts = await asyncio.to_thread(build_events, inputs.rows, inputs.market, entry)
        labels, label_counts = await asyncio.to_thread(label_events, events, inputs.hourly, entry)
        await asyncio.to_thread(_save_frame, events, directory / "events.csv.gz")
        await asyncio.to_thread(_save_frame, labels, directory / "labels.csv.gz")
        legs = {direction: evaluate_leg(labels, direction, entry) for direction in _DIRECTIONS}
        result = {
            "status": "completed",
            "legs": legs,
            "decisions": {d: ("eligible_for_probe" if legs[d]["passes"] else "failed") for d in _DIRECTIONS},
            "diagnostics": _diagnostics(labels, events, entry),
            "calendar": {
                "requested_dates": acquisition.requested_dates,
                "fetched_dates": acquisition.fetched_dates,
                "reused_dates": acquisition.reused_dates,
                "failed_dates": [d.isoformat() for d in acquisition.failed_dates],
                "rows": len(inputs.rows),
                "duplicates": inputs.duplicates,
            },
            "events": event_counts,
            "labels": label_counts,
            "bar_failures": dict(inputs.bar_failures),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
