"""Pre-ex-dividend labelling, the frozen P1-P4 pass rule and the study executor (entry ``exdiv``).

One direction (LONG), two roles: every accepted dividend has a leg row and, when its mid-quarter
placebo survived its own gates, a control row with the same ``pair_id`` and ``session``. The
bracket is PEAD's trade block walked by ``label_bracket`` with ``max_hold_sessions`` equal to the
lead, so the time exit is the close of the last cum-dividend session; a label whose exit bar falls
on or after the ex-date (a missing session in the hourly bars) is excluded and counted. The
secondary hold crosses the ex-date on adjusted bars and is descriptive only.

``execute_exdiv_study`` writes ``protocol.json`` and ``manifest.json`` before calling ``build``
(the only provider access) and records any failure as ``status: failed``; an acquisition with no
actions fails the run.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import ExdivEntry, LoadedExdivEntry
from agentic_trader.research.apriori.exdiv_events import EXDIV_EVENT_COLUMNS, build_exdiv_events
from agentic_trader.research.apriori.pead_events import MarketData
from agentic_trader.research.apriori.pead_study import (
    decision_price,
    levels_for,
    save_frame,
    universe_summary,
    utc_index,
)
from agentic_trader.research.apriori.statistics import mean_test, paired_test, trimmed_mean
from agentic_trader.research.setups.labels import BracketHit, label_bracket
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["ExdivInputs", "evaluate_exdiv", "execute_exdiv_study", "label_exdiv_events"]

_LABEL_SPAN = timedelta(days=60)
_ROLES = ("leg", "control")


@dataclass(frozen=True)
class ExdivInputs:
    actions: tuple[CorporateAction, ...]
    actions_manifest: Mapping[str, Any]
    cohort: Mapping[str, Any]  # path, sha256, symbols
    market: MarketData
    hourly: Mapping[str, pd.DataFrame]
    bar_failures: Mapping[str, str]
    static_requested: tuple[str, ...]


def _cost_column(cost: float) -> str:
    return f"r_cost_{cost:g}bps"


def _label_columns(entry: ExdivEntry) -> list[str]:
    return [
        *EXDIV_EVENT_COLUMNS,
        "decision_price",
        "hit",
        "r",
        "r_cost",
        *(_cost_column(cost) for cost in entry.costs_bps_per_side),
        "hold_secondary_r_cost",
        "holding_sessions",
        "exit_at",
    ]


def label_exdiv_events(
    events: pd.DataFrame, hourly: Mapping[str, pd.DataFrame], entry: ExdivEntry
) -> tuple[pd.DataFrame, dict]:
    counts: Counter[str] = Counter()
    by_year: dict[str, Counter[int]] = defaultdict(Counter)
    by_role: dict[str, Counter[str]] = defaultdict(Counter)
    rows: list[dict] = []

    def miss(reason: str, event: dict) -> None:
        counts[reason] += 1
        by_year[reason][event["session"].year] += 1
        by_role[reason][event["role"]] += 1

    for event in events.to_dict("records"):
        bars = hourly.get(event["symbol"])
        if bars is None or bars.empty:
            miss("no_hourly_bars", event)
            continue
        when = event["decision_at"]
        index = utc_index(bars)
        window = bars.loc[(index >= when - timedelta(days=1)) & (index <= when + _LABEL_SPAN)]
        price = decision_price(window, when)
        if price is None:
            miss("no_decision_price", event)
            continue
        levels = levels_for("LONG", price, float(event["atr"]), entry)
        if levels is None:
            miss("degenerate_levels", event)
            continue
        costs = {
            _cost_column(cost): label_bracket(
                levels, when, window, max_hold_sessions=entry.trade.max_hold_sessions, cost_bps_per_side=cost
            )
            for cost in entry.costs_bps_per_side
        }
        decisive = costs[_cost_column(entry.decision_cost_bps)]
        if decisive.hit == BracketHit.IMMATURE:
            miss("immature", event)
            continue
        exit_at = decisive.exit_time
        if event["role"] == "leg" and exit_at is not None and exit_at.astimezone(ET_TZ).date() >= event["ex_date"]:
            miss("exit_on_or_after_ex_date", event)
            continue
        secondary = math.nan
        if event["role"] == "leg":
            outcome = label_bracket(
                levels,
                when,
                window,
                max_hold_sessions=entry.trade.secondary_hold_sessions,
                cost_bps_per_side=entry.decision_cost_bps,
            )
            secondary = outcome.r_cost if outcome.r_cost is not None else math.nan
        rows.append(
            {
                **event,
                "decision_price": price,
                "hit": str(decisive.hit),
                "r": decisive.r,
                "r_cost": decisive.r_cost,
                **{column: outcome.r_cost for column, outcome in costs.items()},
                "hold_secondary_r_cost": secondary,
                "holding_sessions": decisive.holding_sessions,
                "exit_at": exit_at,
            }
        )
    summary: dict = dict(counts)
    summary["by_year"] = {reason: dict(years) for reason, years in by_year.items()}
    summary["by_role"] = {reason: dict(roles) for reason, roles in by_role.items()}
    return pd.DataFrame(rows, columns=_label_columns(entry)), summary


def evaluate_exdiv(labels: pd.DataFrame, entry: ExdivEntry) -> dict:
    leg = labels[labels["role"] == "leg"]
    control = labels[labels["role"] == "control"]
    paired = set(leg["pair_id"]) & set(control["pair_id"])
    leg_paired = leg[leg["pair_id"].isin(paired)]
    control_paired = control[control["pair_id"].isin(paired)]
    recent = leg[leg["session"] >= entry.window.recent_from]
    trimmed = trimmed_mean(leg["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else float("nan")
    p1 = mean_test(leg, entry.bootstrap) if len(leg) else {"n": 0, "holds": False}
    p2 = paired_test(leg_paired, control_paired, entry.bootstrap) if len(leg_paired) else {"n_leg": 0, "holds": False}
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
    return {
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,
        "passes": all(p["holds"] for p in (p1, p2, p3, p4)),
        "unpaired_legs": len(leg) - len(leg_paired),
    }


def _mean_n(frame: pd.DataFrame, column: str = "r_cost") -> dict:
    values = frame[column].dropna()
    return {"n": len(values), "mean_r_cost": float(values.mean()) if len(values) else float("nan")}


def _terciles(frame: pd.DataFrame, values: pd.Series) -> dict:
    if len(frame) < 3:
        return {}
    buckets = pd.qcut(values.rank(method="first"), 3, labels=["low", "mid", "high"])
    return {str(name): _mean_n(group) for name, group in frame.groupby(buckets, observed=True)}


def _diagnostics(labels: pd.DataFrame) -> dict:
    leg = labels[labels["role"] == "leg"]
    control = labels[labels["role"] == "control"]
    years = pd.to_datetime(leg["session"]).dt.year if len(leg) else pd.Series(dtype=int)
    control_years = pd.to_datetime(control["session"]).dt.year if len(control) else pd.Series(dtype=int)
    per_session = leg.groupby("session").size() if len(leg) else pd.Series(dtype=int)
    return {
        "costs": {column: _mean_n(leg, column) for column in labels.columns if column.startswith("r_cost_")},
        "secondary_hold": _mean_n(leg, "hold_secondary_r_cost"),
        "control": _mean_n(control),
        "by_year": {int(year): _mean_n(group) for year, group in leg.groupby(years)},
        "control_by_year": {int(year): _mean_n(group) for year, group in control.groupby(control_years)},
        "liquidity_terciles": _terciles(leg, leg["median_dollar_volume"]),
        "yield_terciles": _terciles(leg, leg["rate"].astype(float) / leg["decision_price"].astype(float)),
        "by_foreign": {str(flag): _mean_n(group) for flag, group in leg.groupby(leg["foreign"].astype(bool))},
        "hits": {str(k): int(v) for k, v in leg["hit"].value_counts().items()},
        "mean_holding_sessions": float(leg["holding_sessions"].mean()) if len(leg) else float("nan"),
        "events_per_session": {
            "max": int(per_session.max()) if len(per_session) else 0,
            "median": float(per_session.median()) if len(per_session) else 0.0,
            "p90": float(per_session.quantile(0.9)) if len(per_session) else 0.0,
        },
    }


async def execute_exdiv_study(
    loaded: LoadedExdivEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[ExdivInputs]],
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
        if not inputs.actions:
            raise ValueError("the acquisition returned no corporate actions for the cohort and window")
        events, event_counts = await asyncio.to_thread(build_exdiv_events, inputs.actions, inputs.market, entry)
        reference_names = event_counts.pop("reference_names")
        labels, label_counts = await asyncio.to_thread(label_exdiv_events, events, inputs.hourly, entry)
        label_counts["rows"] = len(labels)
        await asyncio.to_thread(save_frame, events, directory / "events.csv.gz")
        await asyncio.to_thread(save_frame, labels, directory / "labels.csv.gz")
        leg = evaluate_exdiv(labels, entry)
        result = {
            "status": "completed",
            "leg": leg,
            "decision": "eligible_for_probe" if leg["passes"] else "failed",
            "diagnostics": _diagnostics(labels),
            "actions": dict(inputs.actions_manifest),
            "cohort": dict(inputs.cohort),
            "universe": universe_summary(inputs.market, inputs.bar_failures, inputs.static_requested, reference_names),
            "events": event_counts,
            "labels": label_counts,
            "bar_failures": dict(inputs.bar_failures),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
