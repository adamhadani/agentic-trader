"""Read-only bracket-outcome report over journaled ``scan_candidates_ranked`` events.

Labels each candidate a suggestion scan already ranked (sent or runner-up) with the
same pure ``label_bracket`` the setup-outcome study uses, so ``copilot cards
outcomes`` can show how ``setup_quality`` and the shadow ranker's ``score`` would have
selected versus a random pick and versus each other -- entirely from durable evidence
already recorded by the live scan (``agentic_trader/agent/copilot.py``:
``_journal_scan_ranking``). This module never sends orders or notifications and never
writes to the database; it only reads journaled events and fetches bars.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from agentic_trader.data.pacing import RequestPacer
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket
from agentic_trader.research.setups.runner import BarSource


__all__ = [
    "DEFAULT_COST_BPS_PER_SIDE",
    "DEFAULT_MAX_HOLD_SESSIONS",
    "FETCH_FAILED_HIT",
    "label_journaled",
    "summarize",
]

_BAR_TIMEFRAME = "1h"

# Mirror config/research/setup-outcomes-v1.json's frozen protocol values, so the live
# `copilot cards outcomes` report and the study it compares against never drift apart
# by accident. See test_defaults_match_the_setup_outcomes_protocol.
DEFAULT_MAX_HOLD_SESSIONS = 20
DEFAULT_COST_BPS_PER_SIDE = 5.0

# A bar-fetch failure's own outcome, distinct from every ``BracketHit`` value: it means
# "the labeler was never able to try", not "not enough elapsed bars yet" (IMMATURE).
FETCH_FAILED_HIT = "fetch_failed"

_COLUMNS = (
    "scan_id",
    "session",
    "contract",
    "strategy",
    "direction",
    "rank",
    "outcome",
    "sent",
    "setup_quality",
    "shadow_score",
    "hit",
    "r",
    "r_cost",
    "holding_sessions",
    "decided_at",
    "reason",
)


def _parse_decided_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _candidate_entries(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every journaled candidate, grouped by symbol, tagged with its scan's decision time."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("kind") != EventKind.SCAN_CANDIDATES_RANKED:
            continue
        payload = event.get("payload", event)
        # Only a full-universe scheduled scan matches the setup study's population;
        # a symbol-restricted /scan or the intraday non-universe job never journals
        # this event at all, but an explicit check keeps this reader correct even if
        # that write-side guard ever changes.
        if payload.get("scope") != "universe":
            continue
        candidates = payload.get("candidates")
        if not candidates:
            continue
        try:
            decided_at = _parse_decided_at(payload["decided_at"])
        except KeyError, TypeError, ValueError:
            continue
        scan_id = payload.get("scan_id")
        for candidate in candidates:
            contract = candidate.get("contract")
            if not contract:
                continue
            by_symbol.setdefault(contract, []).append({**candidate, "scan_id": scan_id, "decided_at": decided_at})
    return by_symbol


def _immature_row(entry: dict[str, Any], symbol: str) -> dict[str, Any]:
    return _row(entry, symbol, hit=BracketHit.IMMATURE.value, r=None, r_cost=None, holding_sessions=0)


def _fetch_failed_row(entry: dict[str, Any], symbol: str, reason: str) -> dict[str, Any]:
    """A bar fetch that raised, not a candidate that merely has not matured yet."""
    return _row(entry, symbol, hit=FETCH_FAILED_HIT, r=None, r_cost=None, holding_sessions=0, reason=reason)


def _row(
    entry: dict[str, Any],
    symbol: str,
    *,
    hit: str,
    r: float | None,
    r_cost: float | None,
    holding_sessions: int,
    reason: str | None = None,
) -> dict[str, Any]:
    shadow = entry.get("shadow") or {}
    decided_at = entry["decided_at"]
    outcome = entry.get("outcome")
    return {
        "scan_id": entry.get("scan_id"),
        "session": decided_at.astimezone(ET_TZ).date().isoformat(),
        "contract": symbol,
        "strategy": entry.get("strategy"),
        "direction": entry.get("direction"),
        "rank": entry.get("rank"),
        "outcome": outcome,
        "sent": outcome == "sent",
        "setup_quality": entry.get("setup_quality"),
        "shadow_score": shadow.get("score"),
        "hit": hit,
        "r": r,
        "r_cost": r_cost,
        "holding_sessions": holding_sessions,
        "decided_at": decided_at,
        "reason": reason,
    }


def _label_one(
    entry: dict[str, Any], symbol: str, hourly: pd.DataFrame, max_hold_sessions: int, cost_bps: float
) -> dict[str, Any]:
    direction = entry.get("direction")
    entry_price, stop_price, target_price = entry.get("entry"), entry.get("stop"), entry.get("target")
    if direction not in ("LONG", "SHORT") or entry_price is None or stop_price is None or target_price is None:
        return _immature_row(entry, symbol)
    try:
        levels = SetupLevels(
            direction=direction, entry=float(entry_price), stop=float(stop_price), target=float(target_price)
        )
    except ValueError:
        # A malformed geometry (e.g. zero risk) cannot be judged; not the labeler's own IMMATURE.
        return _immature_row(entry, symbol)
    outcome = label_bracket(
        levels, entry["decided_at"], hourly, max_hold_sessions=max_hold_sessions, cost_bps_per_side=cost_bps
    )
    return _row(
        entry,
        symbol,
        hit=outcome.hit.value,
        r=outcome.r,
        r_cost=outcome.r_cost,
        holding_sessions=outcome.holding_sessions,
    )


def label_journaled(
    events: list[dict[str, Any]],
    bars: BarSource,
    *,
    max_hold_sessions: int = DEFAULT_MAX_HOLD_SESSIONS,
    cost_bps: float = DEFAULT_COST_BPS_PER_SIDE,
    now: datetime | None = None,
    max_requests_per_minute: int = 150,
) -> pd.DataFrame:
    """One row per journaled ranked candidate, labelled with its realized bracket outcome.

    ``events`` is the journal's own shape, exactly as
    ``SignalDatabase.workflows.events()`` returns it (each item is
    ``{"payload": {...}, ...}``, and ``payload["candidates"]`` is the list
    ``_journal_scan_ranking`` records, one event per scan). Bars are fetched once per
    symbol -- from that symbol's earliest journaled decision through ``now`` -- and
    reused for every one of that symbol's candidates, since ``label_bracket`` walks
    forward from its own ``decision_at`` regardless of any earlier bars already in the
    frame. Fetches are paced with the same sliding-window ``RequestPacer`` the setup
    runner uses, so a wide symbol population cannot exceed the configured provider rate.
    A fetch failure for a symbol marks every one of its candidates ``FETCH_FAILED_HIT``
    -- distinct from IMMATURE, which means "not enough elapsed bars yet" -- rather than
    raising: this is a best-effort report, not an admission or execution path.
    """
    now = now or datetime.now(UTC)
    by_symbol = _candidate_entries(events)
    try:
        pacer: RequestPacer | None = RequestPacer(max(1, max_requests_per_minute))
    except Exception:
        pacer = None

    rows: list[dict[str, Any]] = []
    for symbol, entries in by_symbol.items():
        if pacer is not None:
            pacer.acquire()
        start = min(e["decided_at"] for e in entries)
        try:
            hourly = bars.fetch_bars(symbol, _BAR_TIMEFRAME, start, now, adjustment="raw")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            rows.extend(_fetch_failed_row(entry, symbol, reason) for entry in entries)
            continue
        rows.extend(_label_one(entry, symbol, hourly, max_hold_sessions, cost_bps) for entry in entries)

    return pd.DataFrame(rows, columns=list(_COLUMNS))


def _selection_by_score(frame: pd.DataFrame, score_column: str) -> dict[str, Any] | None:
    """Mean cost-adjusted R of the top-1/top-2 pick per scan, ranked by ``score_column``.

    Grouped by ``scan_id`` -- one journaled scan, not a trading session (a session may
    hold more than one scheduled scan) -- hence the ``scans`` key below.
    """
    scored = frame.dropna(subset=["scan_id", score_column])
    if scored.empty:
        return None
    top1: list[float] = []
    top2: list[float] = []
    for _, group in scored.groupby("scan_id"):
        ranked = group.sort_values(score_column, ascending=False)
        top1.append(float(ranked["r_cost"].iloc[:1].mean()))
        top2.append(float(ranked["r_cost"].iloc[:2].mean()))
    return {
        "scans": len(top1),
        "top1_mean_r_cost": sum(top1) / len(top1),
        "top2_mean_r_cost": sum(top2) / len(top2),
    }


def _selection_random(frame: pd.DataFrame) -> dict[str, Any] | None:
    """A random pick's expected cost-adjusted R equals the scan's own mean -- for any pick count."""
    scoped = frame.dropna(subset=["scan_id"])
    if scoped.empty:
        return None
    per_scan = scoped.groupby("scan_id")["r_cost"].mean()
    mean_r_cost = float(per_scan.mean())
    return {"scans": len(per_scan), "top1_mean_r_cost": mean_r_cost, "top2_mean_r_cost": mean_r_cost}


def _base_rate(frame: pd.DataFrame) -> dict[str, Any] | None:
    n = len(frame)
    if n == 0:
        return None
    return {
        "n": n,
        "target_rate": float((frame["hit"] == BracketHit.TARGET.value).mean()),
        "stop_rate": float((frame["hit"] == BracketHit.STOP.value).mean()),
        "timeout_rate": float((frame["hit"] == BracketHit.TIMEOUT.value).mean()),
        "mean_r_cost": float(frame["r_cost"].mean()),
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    """Counts by outcome/maturity, base rates, and per-scan selection quality by scorer."""
    if frame.empty:
        return {
            "total": 0,
            "immature_total": 0,
            "fetch_failed_total": 0,
            "fetch_failed_reasons": {},
            "counts": {
                "sent": {"mature": 0, "immature": 0, "fetch_failed": 0},
                "runner_up": {"mature": 0, "immature": 0, "fetch_failed": 0},
            },
            "base_rates": {"sent": None, "runner_up": None},
            "selection": {"setup_quality": None, "shadow_score": None, "random": None},
        }

    is_immature = frame["hit"] == BracketHit.IMMATURE.value
    is_fetch_failed = frame["hit"] == FETCH_FAILED_HIT
    is_unresolved = is_immature | is_fetch_failed
    is_sent = frame["sent"].astype(bool)
    mature = frame.loc[~is_unresolved]

    counts = {
        "sent": {
            "mature": int((is_sent & ~is_unresolved).sum()),
            "immature": int((is_sent & is_immature).sum()),
            "fetch_failed": int((is_sent & is_fetch_failed).sum()),
        },
        "runner_up": {
            "mature": int((~is_sent & ~is_unresolved).sum()),
            "immature": int((~is_sent & is_immature).sum()),
            "fetch_failed": int((~is_sent & is_fetch_failed).sum()),
        },
    }
    base_rates = {
        "sent": _base_rate(mature.loc[mature["sent"].astype(bool)]),
        "runner_up": _base_rate(mature.loc[~mature["sent"].astype(bool)]),
    }
    has_shadow_scores = mature["shadow_score"].notna().any()
    selection = {
        "setup_quality": _selection_by_score(mature, "setup_quality"),
        "shadow_score": _selection_by_score(mature, "shadow_score") if has_shadow_scores else None,
        "random": _selection_random(mature),
    }
    fetch_failed_reasons: dict[str, int] = {}
    if is_fetch_failed.any():
        fetch_failed_reasons = frame.loc[is_fetch_failed, "reason"].value_counts().to_dict()

    return {
        "total": len(frame),
        "immature_total": int(is_immature.sum()),
        "fetch_failed_total": int(is_fetch_failed.sum()),
        "fetch_failed_reasons": fetch_failed_reasons,
        "counts": counts,
        "base_rates": base_rates,
        "selection": selection,
    }
