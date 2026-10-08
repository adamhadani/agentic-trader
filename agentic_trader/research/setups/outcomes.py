"""Read-only bracket-outcome report over journaled ``scan_candidates_ranked`` events.

Labels each candidate a suggestion scan already ranked (sent or runner-up) with the
same pure ``label_bracket`` the setup-outcome study uses, so ``copilot cards
outcomes`` can show how ``setup_quality`` and the shadow ranker's ``score`` would have
selected versus a random pick and versus each other -- entirely from durable evidence
already recorded by the live scan (``agentic_trader/agent/copilot.py``:
``_journal_scan_ranking``). The summary also counts the candidates the configured card
policy withheld (``card_policy_withheld``) or would have withheld (``would_withhold``), with
their mean cost-adjusted R: a withheld candidate stays journaled and keeps being labelled.
This module never sends orders or notifications and never writes to the database; it only
reads journaled events and fetches bars.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from agentic_trader.data.pacing import RequestPacer
from agentic_trader.execution.durable import EventKind, RankedOutcome
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket, regular_session_bars
from agentic_trader.research.setups.runner import BarSource


__all__ = [
    "DEFAULT_COST_BPS_PER_SIDE",
    "DEFAULT_MAX_HOLD_SESSIONS",
    "FETCH_FAILED_HIT",
    "MARKET_PROXY_SYMBOL",
    "label_journaled",
    "market_r",
    "summarize",
]

_BAR_TIMEFRAME = "1h"

MARKET_PROXY_SYMBOL = "SPY"

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
    "signal_id",
    "entry",
    "stop",
    "setup_quality",
    "shadow_score",
    "hit",
    "r",
    "r_cost",
    "holding_sessions",
    "entry_time",
    "exit_time",
    "llm_ran",
    "llm_vetoed",
    "llm_bracket_edited",
    "llm_r_cost",
    "llm_stop_loss",
    "card_policy_withheld",
    "would_withhold",
    "market_r",
    "excess_r",
    "market_reason",
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


def market_r(
    direction: str,
    entry: float,
    stop: float,
    entry_time: datetime | pd.Timestamp,
    exit_time: datetime | pd.Timestamp,
    market: pd.DataFrame,
) -> float | None:
    """The market proxy's open-to-close return over the candidate's own holding window, in the candidate's R units.

    Entry at the proxy's open on the first regular bar at or after ``entry_time`` (the
    labeler's own entry convention) and exit at its close on the last regular bar at or
    before ``exit_time`` -- the nearest regular bars, at the raw prices the report fetches.
    An exit on the entry bar itself (a gap-at-entry exit) held no market time: 0.0, not a
    full bar of proxy return. Uncosted: a beta-one exposure control, not a tradable return.
    None when the proxy has no bars, either bar is missing or the geometry has no risk unit.
    """
    risk_unit = abs(float(entry) - float(stop))
    if not risk_unit or market is None or market.empty:
        return None
    if pd.Timestamp(exit_time) == pd.Timestamp(entry_time):
        return 0.0
    regular = regular_session_bars(market)
    at_entry = regular.loc[regular.index >= pd.Timestamp(entry_time)]
    at_exit = regular.loc[regular.index <= pd.Timestamp(exit_time)]
    if at_entry.empty or at_exit.empty or at_exit.index[-1] < at_entry.index[0]:
        return None
    open_price = float(at_entry["Open"].iloc[0])
    close_price = float(at_exit["Close"].iloc[-1])
    if not open_price:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (close_price / open_price - 1.0) * float(entry) / risk_unit


def _immature_row(entry: dict[str, Any], symbol: str, market_reason: str | None = None) -> dict[str, Any]:
    return _row(
        entry,
        symbol,
        hit=BracketHit.IMMATURE.value,
        r=None,
        r_cost=None,
        holding_sessions=0,
        market_reason=market_reason,
    )


def _fetch_failed_row(
    entry: dict[str, Any], symbol: str, reason: str, market_reason: str | None = None
) -> dict[str, Any]:
    """A bar fetch that raised, not a candidate that merely has not matured yet."""
    return _row(
        entry,
        symbol,
        hit=FETCH_FAILED_HIT,
        r=None,
        r_cost=None,
        holding_sessions=0,
        reason=reason,
        market_reason=market_reason,
    )


def _llm_bracket_edited(entry: dict[str, Any], llm: dict[str, Any] | None) -> bool:
    """The LLM's recorded bracket (applied or not) differs from the deterministic one, mature or not."""
    if llm is None:
        return False
    try:
        edited = (float(llm["stop_loss"]), float(llm["take_profit"]))
        deterministic = (float(entry["stop"]), float(entry["target"]))
    except KeyError, TypeError, ValueError:
        return False
    return edited != deterministic


def _applied_llm_stop(llm: dict[str, Any] | None) -> float | None:
    """The stop the LLM applied to the card it sent (execution evidence's journal fallback)."""
    if llm is None or not llm.get("applied"):
        return None
    try:
        stop = float(llm["stop_loss"])
    except KeyError, TypeError, ValueError:
        return None
    return stop if math.isfinite(stop) else None


def _would_withhold(entry: dict[str, Any]) -> bool | None:
    """The journaled card-policy verdict, or None when the scan predates the policy or it was off."""
    block = entry.get("card_policy")
    value = block.get("would_withhold") if isinstance(block, dict) else None
    return value if isinstance(value, bool) else None


def _mean_r_cost(rows: pd.DataFrame) -> float | None:
    if rows.empty:
        return None
    value = float(pd.to_numeric(rows["r_cost"], errors="coerce").mean())
    return value if math.isfinite(value) else None


def _row(
    entry: dict[str, Any],
    symbol: str,
    *,
    hit: str,
    r: float | None,
    r_cost: float | None,
    holding_sessions: int,
    reason: str | None = None,
    entry_time: pd.Timestamp | None = None,
    exit_time: pd.Timestamp | None = None,
    llm_r_cost: float | None = None,
    market_value: float | None = None,
    market_reason: str | None = None,
) -> dict[str, Any]:
    shadow = entry.get("shadow") or {}
    llm = entry.get("llm") if isinstance(entry.get("llm"), dict) else None
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
        "signal_id": entry.get("signal_id"),
        "entry": entry.get("entry"),
        "stop": entry.get("stop"),
        "setup_quality": entry.get("setup_quality"),
        "shadow_score": shadow.get("score"),
        "hit": hit,
        "r": r,
        "r_cost": r_cost,
        "holding_sessions": holding_sessions,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "llm_ran": llm is not None,
        "llm_vetoed": outcome == RankedOutcome.LLM_VETOED,
        "llm_bracket_edited": _llm_bracket_edited(entry, llm),
        "llm_r_cost": llm_r_cost,
        "llm_stop_loss": _applied_llm_stop(llm),
        "card_policy_withheld": outcome == RankedOutcome.CARD_POLICY_WITHHELD,
        "would_withhold": _would_withhold(entry),
        "market_r": market_value,
        "excess_r": (r_cost - market_value) if (r_cost is not None and market_value is not None) else None,
        "market_reason": market_reason,
        "decided_at": decided_at,
        "reason": reason,
    }


def _label_one(
    entry: dict[str, Any],
    symbol: str,
    hourly: pd.DataFrame,
    max_hold_sessions: int,
    cost_bps: float,
    market: pd.DataFrame | None,
    market_reason: str | None,
) -> dict[str, Any]:
    direction = entry.get("direction")
    entry_price, stop_price, target_price = entry.get("entry"), entry.get("stop"), entry.get("target")
    if direction not in ("LONG", "SHORT") or entry_price is None or stop_price is None or target_price is None:
        return _immature_row(entry, symbol, market_reason)
    try:
        levels = SetupLevels(
            direction=direction, entry=float(entry_price), stop=float(stop_price), target=float(target_price)
        )
    except ValueError:
        # A malformed geometry (e.g. zero risk) cannot be judged; not the labeler's own IMMATURE.
        return _immature_row(entry, symbol, market_reason)
    outcome = label_bracket(
        levels, entry["decided_at"], hourly, max_hold_sessions=max_hold_sessions, cost_bps_per_side=cost_bps
    )
    llm_r_cost = _llm_relabel(entry, levels, hourly, max_hold_sessions, cost_bps)
    market_value = None
    if (
        outcome.r_cost is not None
        and market is not None
        and outcome.entry_time is not None
        and outcome.exit_time is not None
    ):
        market_value = market_r(direction, levels.entry, levels.stop, outcome.entry_time, outcome.exit_time, market)
    return _row(
        entry,
        symbol,
        hit=outcome.hit.value,
        r=outcome.r,
        r_cost=outcome.r_cost,
        holding_sessions=outcome.holding_sessions,
        entry_time=outcome.entry_time,
        exit_time=outcome.exit_time,
        llm_r_cost=llm_r_cost,
        market_value=market_value,
        market_reason=market_reason,
    )


def _llm_relabel(
    entry: dict[str, Any], levels: SetupLevels, hourly: pd.DataFrame, max_hold_sessions: int, cost_bps: float
) -> float | None:
    """Cost-adjusted R under the LLM's bracket, only when it differs from the deterministic one."""
    llm = entry.get("llm")
    if not isinstance(llm, dict):
        return None
    stop, target = llm.get("stop_loss"), llm.get("take_profit")
    if stop is None or target is None or (float(stop), float(target)) == (levels.stop, levels.target):
        return None
    try:
        llm_levels = SetupLevels(direction=levels.direction, entry=levels.entry, stop=float(stop), target=float(target))
    except ValueError:
        return None
    return label_bracket(
        llm_levels, entry["decided_at"], hourly, max_hold_sessions=max_hold_sessions, cost_bps_per_side=cost_bps
    ).r_cost


def label_journaled(
    events: list[dict[str, Any]],
    bars: BarSource,
    *,
    max_hold_sessions: int = DEFAULT_MAX_HOLD_SESSIONS,
    cost_bps: float = DEFAULT_COST_BPS_PER_SIDE,
    now: datetime | None = None,
    max_requests_per_minute: int = 150,
    market_symbol: str | None = MARKET_PROXY_SYMBOL,
) -> pd.DataFrame:
    """One row per journaled ranked candidate, labelled with its realized bracket outcome.

    ``events`` is the journal's own shape, exactly as
    ``SignalDatabase.workflows.events()`` returns it (each item is
    ``{"payload": {...}, ...}``, and ``payload["candidates"]`` is the list
    ``_journal_scan_ranking`` records, one event per scan). Bars are fetched once per
    symbol -- from that symbol's earliest journaled decision through ``now`` (the market
    proxy from the earliest decision of all, once, even when it is also a candidate) -- and
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

    def fetch(symbol: str, start: datetime) -> pd.DataFrame | str:
        if pacer is not None:
            pacer.acquire()
        try:
            return bars.fetch_bars(symbol, _BAR_TIMEFRAME, start, now, adjustment="raw")
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    # Every candidate symbol first (once each), then the market proxy once, then label. The
    # proxy's window starts at the earliest decision of all; when the proxy is itself a
    # candidate its one fetch covers that window (labelling ignores bars before a decision).
    starts: dict[str, datetime] = {
        symbol: min(e["decided_at"] for e in entries) for symbol, entries in by_symbol.items()
    }
    market_start = min(starts.values(), default=None)
    if market_start is not None and market_symbol in starts:
        starts[market_symbol] = market_start
    fetched = {symbol: fetch(symbol, start) for symbol, start in starts.items()}
    market: pd.DataFrame | None = None
    market_reason: str | None = None
    if market_symbol is not None and market_start is not None:
        result = fetched[market_symbol] if market_symbol in fetched else fetch(market_symbol, market_start)
        if isinstance(result, str):
            market_reason = result
        else:
            market = result

    rows: list[dict[str, Any]] = []
    for symbol, entries in by_symbol.items():
        hourly = fetched[symbol]
        if isinstance(hourly, str):
            rows.extend(_fetch_failed_row(entry, symbol, hourly, market_reason) for entry in entries)
            continue
        rows.extend(
            _label_one(entry, symbol, hourly, max_hold_sessions, cost_bps, market, market_reason) for entry in entries
        )

    frame = pd.DataFrame(rows, columns=list(_COLUMNS))
    # Signal ids stay integers beside missing ones (nullable Int64, never 5.0), and an absent
    # LLM relabel stays a real None rather than NaN.
    frame["signal_id"] = pd.array([row["signal_id"] for row in rows], dtype="Int64")
    frame["llm_r_cost"] = frame["llm_r_cost"].astype(object).where(frame["llm_r_cost"].notna(), None)
    frame["would_withhold"] = frame["would_withhold"].astype(object).where(frame["would_withhold"].notna(), None)
    return frame


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


def _exposure(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    return {
        "n": len(frame),
        "mean_r_cost": float(frame["r_cost"].mean()),
        "mean_market_r": float(frame["market_r"].mean()),
        "mean_excess_r": float(frame["excess_r"].mean()),
    }


def _mean_delta(edits: pd.DataFrame) -> float | None:
    """Mean ``llm_r_cost - r_cost`` over edited, resolved rows; None when there are none."""
    return float((edits["llm_r_cost"] - edits["r_cost"]).mean()) if len(edits) else None


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    """Counts by outcome/maturity, base rates, per-scan selection quality by scorer and card-policy counts."""
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
            "llm_gate": {
                "ran": 0,
                "vetoed": 0,
                "approved": 0,
                "vetoed_mean_r_cost": None,
                "approved_mean_r_cost": None,
                "bracket_edited": 0,
                "bracket_edit_mature": 0,
                "bracket_edit_mean_delta_r": None,
                "vetoed_bracket_edit_mean_delta_r": None,
            },
            "market_exposure": {"sent": None, "runner_up": None, "reason": None},
            "card_policy": {
                "withheld": 0,
                "would_withhold": 0,
                "withheld_mean_r_cost": None,
                "would_withhold_mean_r_cost": None,
            },
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
    ran = mature.loc[mature["llm_ran"].astype(bool)]
    vetoed = ran.loc[ran["llm_vetoed"].astype(bool)]
    approved = ran.loc[~ran["llm_vetoed"].astype(bool)]
    # An edited bracket counts whatever its maturity; its delta needs both brackets resolved,
    # and an approved edit (the bracket that traded) is averaged apart from a vetoed one.
    edited_mature = (
        ran.loc[ran["llm_bracket_edited"].astype(bool)].dropna(subset=["llm_r_cost"]).astype({"llm_r_cost": float})
    )
    approved_edits = edited_mature.loc[~edited_mature["llm_vetoed"].astype(bool)]
    vetoed_edits = edited_mature.loc[edited_mature["llm_vetoed"].astype(bool)]
    llm_gate = {
        "ran": len(ran),
        "vetoed": len(vetoed),
        "approved": len(approved),
        "vetoed_mean_r_cost": float(vetoed["r_cost"].mean()) if len(vetoed) else None,
        "approved_mean_r_cost": float(approved["r_cost"].mean()) if len(approved) else None,
        "bracket_edited": int(frame["llm_bracket_edited"].astype(bool).sum()),
        "bracket_edit_mature": len(edited_mature),
        "bracket_edit_mean_delta_r": _mean_delta(approved_edits),
        "vetoed_bracket_edit_mean_delta_r": _mean_delta(vetoed_edits),
    }
    controlled = mature.dropna(subset=["market_r"])
    market_reasons = frame["market_reason"].dropna()
    market_exposure = {
        "sent": _exposure(controlled.loc[controlled["sent"].astype(bool)]),
        "runner_up": _exposure(controlled.loc[~controlled["sent"].astype(bool)]),
        # Why the control is missing when the proxy fetch failed (one fetch, so one reason).
        "reason": str(market_reasons.iloc[0]) if len(market_reasons) else None,
    }
    fetch_failed_reasons: dict[str, int] = {}
    if is_fetch_failed.any():
        fetch_failed_reasons = frame.loc[is_fetch_failed, "reason"].value_counts().to_dict()
    withheld = frame["card_policy_withheld"].astype(bool)
    would = frame["would_withhold"].map(lambda value: value is True).astype(bool)
    card_policy = {
        "withheld": int(withheld.sum()),
        "would_withhold": int(would.sum()),
        "withheld_mean_r_cost": _mean_r_cost(mature.loc[withheld.loc[mature.index]]),
        "would_withhold_mean_r_cost": _mean_r_cost(mature.loc[would.loc[mature.index]]),
    }

    return {
        "total": len(frame),
        "immature_total": int(is_immature.sum()),
        "fetch_failed_total": int(is_fetch_failed.sum()),
        "fetch_failed_reasons": fetch_failed_reasons,
        "counts": counts,
        "base_rates": base_rates,
        "selection": selection,
        "llm_gate": llm_gate,
        "market_exposure": market_exposure,
        "card_policy": card_policy,
    }
