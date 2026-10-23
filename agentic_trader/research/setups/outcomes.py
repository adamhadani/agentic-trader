"""Read-only bracket-outcome report over journaled ``scan_candidates_ranked`` events.

Labels each candidate a full-universe scan already ranked (sent or runner-up) with the
same pure ``label_bracket`` the setup-outcome study uses, so ``copilot cards
outcomes`` can show how ``setup_quality`` and the shadow ranker's ``score`` would have
selected versus a random pick and versus each other -- entirely from durable evidence
already recorded by the live scan (``agentic_trader/agent/copilot.py``:
``_journal_scan_ranking``). The summary also counts the candidates the configured card
policy withheld (``card_policy_withheld``) or would have withheld (``would_withhold``), with
their mean cost-adjusted R: a withheld candidate stays journaled and keeps being labelled.
``scan_supply`` counts the scans themselves from the events, so a scan that ranked nothing or
was gated by the lockout or an unavailable regime is counted too.
This module never sends orders or notifications and never writes to the database; it only
reads journaled events and fetches bars.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from agentic_trader.data.pacing import RequestPacer
from agentic_trader.execution.durable import EventKind, RankedOutcome, ScanStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.beta import trailing_beta
from agentic_trader.research.setups.labels import (
    BracketArm,
    BracketHit,
    SetupLevels,
    TrailSpec,
    label_bracket,
    regular_session_bars,
)
from agentic_trader.research.setups.tape import TAPE_BUCKETS, TAPE_HISTORY_DAYS, TapeState, tape_buckets, tape_state


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    # runner imports market_r from here, so the bar-source protocol is type-only.
    from agentic_trader.research.setups.runner import BarSource


__all__ = [
    "CF_COLUMN_PREFIX",
    "DEFAULT_COST_BPS_PER_SIDE",
    "DEFAULT_MAX_HOLD_SESSIONS",
    "FETCH_FAILED_HIT",
    "LIVE_COUNTERFACTUAL_ARMS",
    "MARKET_PROXY_SYMBOL",
    "PRIOR_OUTCOMES",
    "RESOLVED_HITS",
    "label_journaled",
    "market_r",
    "scan_supply",
    "summarize",
    "with_recurrence",
]

_BAR_TIMEFRAME = "1h"

# Calendar days of adjusted daily history fetched before a symbol's earliest decision: the
# 120-session beta window plus holidays and a missing-day margin.
BETA_HISTORY_DAYS = 200

MARKET_PROXY_SYMBOL = "SPY"

# Mirror config/research/setup-outcomes-v1.json's frozen protocol values, so the live
# `copilot cards outcomes` report and the study it compares against never drift apart
# by accident. See test_defaults_match_the_setup_outcomes_protocol.
DEFAULT_MAX_HOLD_SESSIONS = 20
DEFAULT_COST_BPS_PER_SIDE = 5.0

# A bar-fetch failure's own outcome, distinct from every ``BracketHit`` value: it means
# "the labeler was never able to try", not "not enough elapsed bars yet" (IMMATURE).
FETCH_FAILED_HIT = "fetch_failed"
UNCLASSIFIED = "unclassified"

# The four counterfactual exit arms of config/research/bracket-counterfactual-v1.json (the live
# arm A0 is the journaled bracket itself). Hard-coded so the live report needs no file at import;
# test_live_counterfactual_arms_match_the_frozen_protocol pins them to the frozen protocol.
_CF_TRAIL = TrailSpec(trigger_r=1.5, distance_r=1.5)
LIVE_COUNTERFACTUAL_ARMS: tuple[BracketArm, ...] = (
    BracketArm(name="A1_3r", target_r=3.0, trail=None),
    BracketArm(name="A2_2r_trail", target_r=2.0, trail=_CF_TRAIL),
    BracketArm(name="A3_3r_trail", target_r=3.0, trail=_CF_TRAIL),
    BracketArm(name="A4_trail_only", target_r=None, trail=_CF_TRAIL),
)
CF_COLUMN_PREFIX = "cf_"
_CF_KINDS = ("hit", "r_cost", "market_r")
_CF_COLUMNS = tuple(f"{CF_COLUMN_PREFIX}{arm.name}_{kind}" for arm in LIVE_COUNTERFACTUAL_ARMS for kind in _CF_KINDS)

_COLUMNS = (
    "scan_id",
    "session",
    "trigger",
    "contract",
    "strategy",
    "direction",
    "rank",
    "outcome",
    "sent",
    "signal_id",
    "entry",
    "stop",
    "target",
    "atr_14",
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
    "book_sizing_status",
    "book_factor",
    "book_would_scale",
    "news_status",
    "news_n_items",
    "news_tone",
    "news_catalyst",
    "news_relevance",
    "news_alignment",
    "regime_vix",
    "regime_vix_regime",
    "regime_risk_multiplier",
    "regime_macro_stress",
    "spy_ret_20",
    "spy_above_200",
    "tape_reason",
    "prior_sessions",
    "prior_same_session",
    "prior_outcome",
    "market_r",
    "excess_r",
    "beta",
    "beta_reason",
    "beta_market_r",
    "beta_excess_r",
    "hold_r",
    "timing_r",
    *_CF_COLUMNS,
    "market_reason",
    "decided_at",
    "reason",
)


def _parse_decided_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _universe_scan_payloads(events: Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    """The payload of every journaled full-universe scan among ``events``."""
    for event in events:
        if event.get("kind") != EventKind.SCAN_CANDIDATES_RANKED:
            continue
        payload = event.get("payload", event)
        # Only a full-universe scan (any trigger) matches the setup study's population;
        # a symbol-restricted /scan or the intraday non-universe job never journals
        # this event at all, but an explicit check keeps this reader correct even if
        # that write-side guard ever changes.
        if payload.get("scope") == "universe":
            yield payload


def _candidate_entries(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every journaled candidate, grouped by symbol, tagged with its scan's decision time."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for payload in _universe_scan_payloads(events):
        candidates = payload.get("candidates")
        if not candidates:
            continue
        try:
            decided_at = _parse_decided_at(payload["decided_at"])
        except KeyError, TypeError, ValueError:
            continue
        scan_id = payload.get("scan_id")
        raw_regime = payload.get("regime")
        regime = raw_regime if isinstance(raw_regime, dict) else None
        for candidate in candidates:
            contract = candidate.get("contract")
            if not contract:
                continue
            by_symbol.setdefault(contract, []).append(
                {
                    **candidate,
                    "scan_id": scan_id,
                    "decided_at": decided_at,
                    "trigger": payload.get("trigger"),
                    "regime": regime,
                }
            )
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


def buy_and_hold_r(
    direction: str,
    entry: float,
    stop: float,
    entry_time: datetime | pd.Timestamp,
    hourly: pd.DataFrame,
    max_hold_sessions: int,
) -> float | None:
    """The candidate's own buy-and-hold return over the bracket's full horizon, in its R units.

    From the open of the first regular bar at or after ``entry_time`` (the labeler's entry) to the
    close of the last regular bar of the ``max_hold_sessions``-th New York session counted from the
    entry session -- the bracket's own timeout exit -- signed by direction, uncosted. None until
    a bar of a later session exists (when the labeller's timeout would resolve), when the entry bar
    is missing or the geometry has no risk unit.
    """
    risk_unit = abs(float(entry) - float(stop))
    if not risk_unit or hourly is None or hourly.empty or max_hold_sessions < 1:
        return None
    regular = regular_session_bars(hourly)
    walk = regular.loc[regular.index >= pd.Timestamp(entry_time)]
    if walk.empty:
        return None
    local = pd.DatetimeIndex(walk.index).tz_convert(ET_TZ)
    dates = list(dict.fromkeys(local.normalize()))
    if len(dates) < max_hold_sessions:
        return None
    # Matured exactly when the labeller's timeout would be: a bar of a later session exists, so the
    # horizon session's last bar is final (never a still-forming final-hour bar).
    if len(dates) <= max_hold_sessions:
        return None
    horizon_bars = walk.loc[local.normalize() == dates[max_hold_sessions - 1]]
    open_price = float(walk["Open"].iloc[0])
    if not open_price:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (float(horizon_bars["Close"].iloc[-1]) / open_price - 1.0) * float(entry) / risk_unit


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


NEWS_ALIGNMENTS = ("aligned", "opposed", "neutral", "none")
RESOLVED_HITS = (BracketHit.TARGET.value, BracketHit.STOP.value, BracketHit.TIMEOUT.value)
PRIOR_OUTCOMES = (*RESOLVED_HITS, "none")


def news_alignment(direction: str | None, tone: str | None) -> str:
    """Deterministic direction x tone bucket; the scorer never sees the direction."""
    if tone not in {"positive", "negative", "neutral"} or direction not in {"LONG", "SHORT"}:
        return "none"
    if tone == "neutral":
        return "neutral"
    bullish = tone == "positive"
    return "aligned" if bullish == (direction == "LONG") else "opposed"


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
    counterfactual: dict[str, Any] | None = None,
    beta: float | None = None,
    beta_reason: str | None = None,
    hold_value: float | None = None,
) -> dict[str, Any]:
    shadow = entry.get("shadow") or {}
    raw_book = entry.get("book_sizing")
    book: dict[str, Any] = raw_book if isinstance(raw_book, dict) else {}
    llm = entry.get("llm") if isinstance(entry.get("llm"), dict) else None
    raw_news = entry.get("news")
    news: dict[str, Any] = raw_news if isinstance(raw_news, dict) else {}
    raw_score = news.get("score")
    score: dict[str, Any] = raw_score if isinstance(raw_score, dict) else {}
    raw_regime = entry.get("regime")
    regime: dict[str, Any] = raw_regime if isinstance(raw_regime, dict) else {}
    decided_at = entry["decided_at"]
    outcome = entry.get("outcome")
    beta_market_value = beta * market_value if (beta is not None and market_value is not None) else None
    return {
        "scan_id": entry.get("scan_id"),
        "session": decided_at.astimezone(ET_TZ).date().isoformat(),
        "trigger": entry.get("trigger"),
        "contract": symbol,
        "strategy": entry.get("strategy"),
        "direction": entry.get("direction"),
        "rank": entry.get("rank"),
        "outcome": outcome,
        "sent": outcome == "sent",
        "signal_id": entry.get("signal_id"),
        "entry": entry.get("entry"),
        "stop": entry.get("stop"),
        "target": entry.get("target"),
        "atr_14": entry.get("atr_14"),
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
        "book_sizing_status": book.get("status"),
        "book_factor": book.get("factor"),
        "book_would_scale": book.get("would_scale"),
        "news_status": news.get("status") if news else "absent",
        "news_n_items": news.get("n_items") if news else None,
        "news_tone": score.get("tone"),
        "news_catalyst": score.get("catalyst"),
        "news_relevance": score.get("relevance"),
        "news_alignment": news_alignment(entry.get("direction"), score.get("tone")),
        "regime_vix": regime.get("vix"),
        "regime_vix_regime": regime.get("vix_regime"),
        "regime_risk_multiplier": regime.get("risk_multiplier"),
        "regime_macro_stress": regime.get("macro_stress"),
        # The tape is filled per session by label_journaled once the SPY daily fetch is known.
        "spy_ret_20": None,
        "spy_above_200": None,
        "tape_reason": None,
        # Recurrence is filled over the whole frame by with_recurrence (Task 4); these are the
        # "first occurrence" defaults.
        "prior_sessions": 0,
        "prior_same_session": 0,
        "prior_outcome": "none",
        "market_r": market_value,
        "excess_r": (r_cost - market_value) if (r_cost is not None and market_value is not None) else None,
        "beta": beta,
        "beta_reason": beta_reason,
        "beta_market_r": beta_market_value,
        "beta_excess_r": (r_cost - beta_market_value)
        if (r_cost is not None and beta_market_value is not None)
        else None,
        "hold_r": hold_value,
        "timing_r": (r_cost - hold_value) if (r_cost is not None and hold_value is not None) else None,
        **dict.fromkeys(_CF_COLUMNS),
        **(counterfactual or {}),
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
    beta: float | None = None,
    beta_reason: str | None = None,
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
    hold_value = None
    if outcome.r_cost is not None and outcome.entry_time is not None:
        hold_value = buy_and_hold_r(direction, levels.entry, levels.stop, outcome.entry_time, hourly, max_hold_sessions)
    counterfactual: dict[str, Any] = {}
    for arm in LIVE_COUNTERFACTUAL_ARMS:
        arm_outcome = label_bracket(
            levels,
            entry["decided_at"],
            hourly,
            max_hold_sessions=max_hold_sessions,
            cost_bps_per_side=cost_bps,
            arm=arm,
        )
        arm_market = None
        if (
            arm_outcome.r_cost is not None
            and market is not None
            and arm_outcome.entry_time is not None
            and arm_outcome.exit_time is not None
        ):
            arm_market = market_r(
                direction, levels.entry, levels.stop, arm_outcome.entry_time, arm_outcome.exit_time, market
            )
        prefix = f"{CF_COLUMN_PREFIX}{arm.name}_"
        counterfactual[f"{prefix}hit"] = arm_outcome.hit.value
        counterfactual[f"{prefix}r_cost"] = arm_outcome.r_cost
        counterfactual[f"{prefix}market_r"] = arm_market
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
        counterfactual=counterfactual,
        beta=beta,
        beta_reason=beta_reason,
        hold_value=hold_value,
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


def _is_timestamp(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def with_recurrence(frame: pd.DataFrame) -> pd.DataFrame:
    """Adds prior_sessions, prior_same_session and prior_outcome per ``(contract, strategy, direction)``.

    Over the other rows of the same setup in this frame: distinct earlier sessions; same-session rows
    decided strictly earlier (a tie on ``decided_at`` is not earlier); and the hit of the most recent
    earlier row (by ``decided_at``) whose bracket had resolved -- ``hit`` in ``RESOLVED_HITS`` whose
    exit bar had closed (``exit_time`` plus one bar) at or before this row's ``decided_at`` -- else
    ``"none"``. A prior card still open at the decision was not information the desk had, so it never
    counts; immature and fetch-failed rows never count. Rows without a strategy or direction keep the
    first-occurrence defaults. Relative to the frame's window: a setup first journaled before it
    reads as first. Requires a unique index (``label_journaled`` always provides a RangeIndex).
    """
    if not frame.index.is_unique:
        raise ValueError("with_recurrence requires a unique index")
    out = frame.copy()
    prior_sessions = pd.Series(0, index=out.index, dtype=int)
    prior_same = pd.Series(0, index=out.index, dtype=int)
    prior_outcome = pd.Series("none", index=out.index, dtype=object)
    bar = pd.Timedelta(_BAR_TIMEFRAME)
    keyed = out.dropna(subset=["contract", "strategy", "direction"])
    for _, group in keyed.groupby(["contract", "strategy", "direction"], sort=False):
        records = list(group[["session", "decided_at", "hit", "exit_time"]].itertuples())
        for row in records:
            decided = pd.Timestamp(row.decided_at)
            earlier = [other for other in records if pd.Timestamp(other.decided_at) < decided]
            prior_sessions.loc[row.Index] = len({other.session for other in records if other.session < row.session})
            prior_same.loc[row.Index] = sum(1 for other in earlier if other.session == row.session)
            resolved = [
                other
                for other in earlier
                if other.hit in RESOLVED_HITS
                and _is_timestamp(other.exit_time)
                and pd.Timestamp(other.exit_time) + bar <= decided
            ]
            if resolved:
                prior_outcome.loc[row.Index] = max(resolved, key=lambda other: pd.Timestamp(other.decided_at)).hit
    out["prior_sessions"] = prior_sessions
    out["prior_same_session"] = prior_same
    out["prior_outcome"] = prior_outcome
    return out


def label_journaled(
    events: list[dict[str, Any]],
    bars: BarSource,
    *,
    max_hold_sessions: int = DEFAULT_MAX_HOLD_SESSIONS,
    cost_bps: float = DEFAULT_COST_BPS_PER_SIDE,
    now: datetime | None = None,
    max_requests_per_minute: int = 150,
    market_symbol: str | None = MARKET_PROXY_SYMBOL,
    tape_symbol: str | None = MARKET_PROXY_SYMBOL,
    with_beta: bool = True,
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
    The tape (SPY daily, ``TAPE_HISTORY_DAYS`` calendar days before the earliest decision, raw,
    one request after the hourly ones) is computed per decision session with ``tape_state``; a
    failed fetch records ``tape_reason`` on every row.
    The trailing beta (``with_beta``) adds one adjusted daily request per candidate symbol and one
    for the market proxy after the tape; a failed one records ``beta_reason`` on the rows it
    covers. Without it the beta columns are all None.
    """
    now = now or datetime.now(UTC)
    by_symbol = _candidate_entries(events)
    try:
        pacer: RequestPacer | None = RequestPacer(max(1, max_requests_per_minute))
    except Exception:
        pacer = None

    def fetch(
        symbol: str, start: datetime, timeframe: str = _BAR_TIMEFRAME, adjustment: str = "raw"
    ) -> pd.DataFrame | str:
        if pacer is not None:
            pacer.acquire()
        try:
            return bars.fetch_bars(symbol, timeframe, start, now, adjustment=adjustment)
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

    daily: pd.DataFrame | None = None
    tape_reason: str | None = None
    if tape_symbol is not None and market_start is not None:
        tape = fetch(tape_symbol, market_start - timedelta(days=TAPE_HISTORY_DAYS), "1d")
        if isinstance(tape, str):
            tape_reason = tape
        else:
            daily = tape

    # Adjusted daily bars for the trailing beta: one request per candidate symbol (from 200
    # calendar days before its earliest decision) and one for the market proxy.
    beta_daily: dict[str, pd.DataFrame | str] = {}
    beta_market: pd.DataFrame | str | None = None
    if with_beta and market_symbol is not None and market_start is not None:
        # Rows of a symbol whose hourly fetch failed never use its beta, so skip those requests.
        # `starts` already widens the proxy's own window to the earliest decision of all.
        beta_daily = {
            symbol: fetch(symbol, start - timedelta(days=BETA_HISTORY_DAYS), "1d", "all")
            for symbol, start in starts.items()
            if not isinstance(fetched[symbol], str)
        }
        beta_market = (
            beta_daily[market_symbol]
            if market_symbol in beta_daily
            else fetch(market_symbol, market_start - timedelta(days=BETA_HISTORY_DAYS), "1d", "all")
        )

    def beta_for(symbol: str, session: str) -> tuple[float | None, str | None]:
        if beta_market is None:
            return None, None
        symbol_daily = beta_daily[symbol]
        if isinstance(beta_market, str):
            return None, f"fetch_failed: {beta_market.split(':', 1)[0]}"
        if isinstance(symbol_daily, str):
            return None, f"fetch_failed: {symbol_daily.split(':', 1)[0]}"
        return trailing_beta(symbol_daily, beta_market, date.fromisoformat(session))

    rows: list[dict[str, Any]] = []
    for symbol, entries in by_symbol.items():
        hourly = fetched[symbol]
        if isinstance(hourly, str):
            rows.extend(_fetch_failed_row(entry, symbol, hourly, market_reason) for entry in entries)
            continue
        for entry in entries:
            beta, beta_reason = beta_for(symbol, entry["decided_at"].astimezone(ET_TZ).date().isoformat())
            rows.append(
                _label_one(entry, symbol, hourly, max_hold_sessions, cost_bps, market, market_reason, beta, beta_reason)
            )

    states: dict[str, TapeState] = {}
    for row in rows:
        session = row["session"]
        state = states.get(session)
        if state is None:
            state = states[session] = tape_state(daily, date.fromisoformat(session))
        reason = tape_reason
        if reason is None and daily is not None and state.sessions == 0:
            reason = "no daily bars before the session"
        row["spy_ret_20"], row["spy_above_200"], row["tape_reason"] = state.ret_20, state.above_200, reason

    frame = pd.DataFrame(rows, columns=list(_COLUMNS))
    # Signal ids stay integers beside missing ones (nullable Int64, never 5.0), and an absent
    # LLM relabel stays a real None rather than NaN.
    frame["signal_id"] = pd.array([row["signal_id"] for row in rows], dtype="Int64")
    frame["llm_r_cost"] = frame["llm_r_cost"].astype(object).where(frame["llm_r_cost"].notna(), None)
    frame["would_withhold"] = frame["would_withhold"].astype(object).where(frame["would_withhold"].notna(), None)
    for column in (
        "book_sizing_status",
        "book_factor",
        "book_would_scale",
        "regime_vix",
        "regime_vix_regime",
        "regime_risk_multiplier",
        "regime_macro_stress",
        "spy_ret_20",
        "spy_above_200",
        "tape_reason",
    ):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return with_recurrence(frame)


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


def _finite_mean(frame: pd.DataFrame, column: str) -> float | None:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if len(values) else None


def _exposure(controlled: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any] | None:
    """SPY-controlled means over ``controlled`` rows; hold/timing and beta coverage over every
    ``mature`` row of the class (a row without a SPY ``market_r`` still has its own hold)."""
    if mature.empty:
        return None
    with_beta_excess = controlled.dropna(subset=["beta_excess_r"])
    beta_reasons = mature["beta_reason"].dropna()
    return {
        "n": len(controlled),
        "mean_r_cost": _finite_mean(controlled, "r_cost"),
        "mean_market_r": _finite_mean(controlled, "market_r"),
        "mean_excess_r": _finite_mean(controlled, "excess_r"),
        "n_beta": int(pd.to_numeric(controlled["beta"], errors="coerce").notna().sum()),
        "mean_beta": _finite_mean(controlled, "beta"),
        "mean_beta_market_r": _finite_mean(controlled, "beta_market_r"),
        "mean_beta_excess_r": _finite_mean(controlled, "beta_excess_r"),
        "mean_excess_r_same_rows": _finite_mean(with_beta_excess, "excess_r"),
        "beta_reasons": {str(reason): int(count) for reason, count in beta_reasons.value_counts().items()},
        "mean_hold_r": _finite_mean(mature, "hold_r"),
        "mean_timing_r": _finite_mean(mature, "timing_r"),
    }


def _asset_class_summary(mature: pd.DataFrame, asset_classes: Mapping[str, str] | None) -> dict[str, Any]:
    """Mean R, SPY excess and beta-matched excess of the mature rows per asset class (descriptive)."""
    if mature.empty:
        return {}
    labelled = mature.assign(
        asset_class=mature["contract"].map(lambda symbol: (asset_classes or {}).get(symbol, UNCLASSIFIED))
    )
    return {
        str(name): {
            "n": len(group),
            "mean_r_cost": _finite_mean(group, "r_cost"),
            "mean_excess_r": _finite_mean(group, "excess_r"),
            "mean_beta_excess_r": _finite_mean(group, "beta_excess_r"),
        }
        for name, group in sorted(labelled.groupby("asset_class"))
    }


def _mean_delta(edits: pd.DataFrame) -> float | None:
    """Mean ``llm_r_cost - r_cost`` over edited, resolved rows; None when there are none."""
    return float((edits["llm_r_cost"] - edits["r_cost"]).mean()) if len(edits) else None


def _book_sizing_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """Counts by journaled book-sizing status and the descriptive R of rows that would be scaled."""
    has_status = frame["book_sizing_status"].notna()
    by_status = {str(k): int(v) for k, v in frame.loc[has_status, "book_sizing_status"].value_counts().items()}

    def scaled_mask(rows: pd.DataFrame) -> pd.Series:
        flagged = rows["book_would_scale"].map(lambda value: value is True)
        return (flagged | (rows["book_sizing_status"] == "applied")).astype(bool)

    scaled = frame.loc[scaled_mask(frame)]
    factors = pd.to_numeric(scaled["book_factor"], errors="coerce")
    mean_factor = float(factors.mean()) if factors.notna().any() else None
    rated = mature.loc[mature["book_sizing_status"].notna()]
    would_scale = scaled_mask(rated)
    return {
        "by_status": by_status,
        "would_scale": len(scaled),
        "mean_factor_scaled": mean_factor,
        "mean_r_cost": {
            "would_scale": _mean_r_cost(rated.loc[would_scale]),
            "not_scaled": _mean_r_cost(rated.loc[~would_scale]),
        },
    }


def _news_bucket(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"n": 0, "mean_r_cost": None, "hit_rate": None}
    hits = rows["hit"] == BracketHit.TARGET.value
    return {"n": len(rows), "mean_r_cost": _mean_r_cost(rows), "hit_rate": float(hits.mean())}


def _with_news_block(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows whose candidate carried a news block (any status); pre-deploy history and futures are ``absent``."""
    return rows.loc[rows["news_status"].notna() & (rows["news_status"] != "absent")]


def _news_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """The descriptive ``news_context`` block.

    ``blocks``, ``by_status``, ``scored`` and ``by_catalyst`` count every row with a block, mature or not.
    ``by_alignment`` and ``by_tone`` use mature rows with a block only, so ``none`` counts ``no_news``,
    ``unscored``, ``score_failed`` and ``unavailable`` rows, never rows without a block.
    """
    present = _with_news_block(frame)
    by_status = {str(k): int(v) for k, v in present["news_status"].value_counts().items()} if len(present) else {}
    catalysts = present["news_catalyst"].dropna() if len(present) else pd.Series(dtype=object)
    by_catalyst = {str(k): int(v) for k, v in catalysts.value_counts().items()} if len(catalysts) else {}
    measured = _with_news_block(mature)
    tones = measured["news_tone"].dropna()
    return {
        "blocks": len(present),
        "by_status": by_status,
        "scored": int((present["news_status"] == "ok").sum()) if len(present) else 0,
        "by_alignment": {key: _news_bucket(measured.loc[measured["news_alignment"] == key]) for key in NEWS_ALIGNMENTS},
        "by_tone": {
            str(tone): _news_bucket(measured.loc[measured["news_tone"] == tone]) for tone in sorted(tones.unique())
        },
        "by_catalyst": by_catalyst,
    }


def _bucket(rows: pd.DataFrame) -> dict[str, Any]:
    """n, mean R after cost, mean excess over the market control (rows with one) and target rate."""
    if rows.empty:
        return {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None}
    excess = pd.to_numeric(rows["excess_r"], errors="coerce").dropna()
    return {
        "n": len(rows),
        "mean_r_cost": _mean_r_cost(rows),
        "mean_excess_r": float(excess.mean()) if len(excess) else None,
        "hit_rate": float((rows["hit"] == BracketHit.TARGET.value).mean()),
    }


def _tape_bucket_names(rows: pd.DataFrame) -> pd.Series:
    """Each row's tape bucket names -- the one membership rule, ``tape_buckets`` -- computed once per row."""

    def names(row: pd.Series) -> tuple[str, ...]:
        ret = row["spy_ret_20"]
        above = row["spy_above_200"]
        return tape_buckets(
            TapeState(
                ret_20=float(ret) if ret is not None and not pd.isna(ret) else None,
                above_200=bool(above) if above is not None and not pd.isna(above) else None,
                sessions=0,
            )
        )

    return pd.Series([names(row) for _, row in rows.iterrows()], index=rows.index, dtype=object)


def _regime_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """The descriptive ``regime_context`` block: the journaled regime and the derived tape, over mature rows."""
    present = frame.loc[frame["regime_vix_regime"].notna()]
    measured = mature.loc[mature["regime_vix_regime"].notna()]
    regimes = sorted(str(value) for value in measured["regime_vix_regime"].dropna().unique())
    stresses = sorted(str(value) for value in mature["regime_macro_stress"].dropna().unique())
    reasons = frame["tape_reason"].dropna()
    tape_names = _tape_bucket_names(mature)
    return {
        "blocks": len(present),
        "by_vix_regime": {name: _bucket(measured.loc[measured["regime_vix_regime"] == name]) for name in regimes},
        "by_macro_stress": {name: _bucket(mature.loc[mature["regime_macro_stress"] == name]) for name in stresses},
        "by_tape": {
            name: _bucket(mature.loc[tape_names.map(lambda names, key=name: key in names).astype(bool)])
            for name in TAPE_BUCKETS
        },
        "tape_reason": str(reasons.iloc[0]) if len(reasons) else None,
    }


def _recurrence_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """The descriptive ``recurrence`` block: setup counts over every row, buckets over mature rows."""
    key = ["contract", "strategy", "direction"]
    keyed = frame.dropna(subset=key)
    sessions_per_setup = keyed.groupby(key, sort=False)["session"].nunique() if len(keyed) else pd.Series(dtype=int)
    first = mature["prior_sessions"].fillna(0).astype(int) == 0
    return {
        "setups": len(sessions_per_setup),
        "repeated_setups": int((sessions_per_setup > 1).sum()),
        "same_session_repeats": int((frame["prior_same_session"].fillna(0).astype(int) > 0).sum()),
        "by_prior": {"first": _bucket(mature.loc[first]), "repeat": _bucket(mature.loc[~first])},
        "by_prior_outcome": {name: _bucket(mature.loc[mature["prior_outcome"] == name]) for name in PRIOR_OUTCOMES},
    }


def _counterfactual_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    """Each counterfactual arm against the live bracket on the same mature candidates (descriptive)."""
    live_excess = pd.to_numeric(mature["r_cost"], errors="coerce") - pd.to_numeric(mature["market_r"], errors="coerce")
    arms: dict[str, dict[str, Any]] = {}
    for arm in LIVE_COUNTERFACTUAL_ARMS:
        prefix = f"{CF_COLUMN_PREFIX}{arm.name}_"
        r_cost = pd.to_numeric(mature[f"{prefix}r_cost"], errors="coerce")
        paired = r_cost.notna()
        hits = mature.loc[paired, f"{prefix}hit"]
        n_pairs = int(paired.sum())
        arm_market = pd.to_numeric(mature[f"{prefix}market_r"], errors="coerce")
        excess = r_cost - arm_market
        beta_excess = (r_cost - pd.to_numeric(mature["beta"], errors="coerce") * arm_market).dropna()
        advantage = (excess - live_excess).dropna()
        arms[arm.name] = {
            "n_pairs": n_pairs,
            "target_rate": float((hits == BracketHit.TARGET.value).mean()) if n_pairs else None,
            "stop_rate": float((hits == BracketHit.STOP.value).mean()) if n_pairs else None,
            "timeout_rate": float((hits == BracketHit.TIMEOUT.value).mean()) if n_pairs else None,
            "mean_r_cost": float(r_cost[paired].mean()) if n_pairs else None,
            "mean_excess_r": float(excess.dropna().mean()) if excess.notna().any() else None,
            "mean_beta_excess_r": float(beta_excess.mean()) if len(beta_excess) else None,
            "advantage_vs_live_excess": float(advantage.mean()) if len(advantage) else None,
        }
    return {"caveat": "descriptive; not a gate", "arms": arms}


def _add_counts(total: dict[str, Any], key: str, value: Any) -> None:
    """Add one funnel field into ``total``: an integer sums, a ``{name: n}`` dict sums per name."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        total[key] = total.get(key, 0) + value
    elif isinstance(value, dict):
        named = total.setdefault(key, {})
        for name, count in value.items():
            if isinstance(count, int) and not isinstance(count, bool):
                named[name] = named.get(name, 0) + count


def scan_supply(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """What each calling path's journaled scans supplied, keyed by trigger (``unknown`` before the field).

    Per trigger: ``scans``; ``by_status`` (an event without ``status`` is ``completed``);
    ``zero_candidate``, the completed scans that ranked nothing; ``sent``, candidates with the
    outcome ``sent`` whatever their scan's status; ``funnel``, every integer field summed and every
    ``{name: n}`` field summed per name over the events that carry one; and ``funnel_unknown``, the
    completed events whose funnel is missing or null. A gated scan has no funnel by design and is
    counted in ``by_status``, not in ``funnel_unknown``.

    It reads the events, not the labelled frame, so a scan with no candidates is counted. Descriptive
    only: nothing in ``risk/``, ``card_policy``, ``card_stats`` or admission reads it.
    """
    supply: dict[str, dict[str, Any]] = {}
    for payload in _universe_scan_payloads(events):
        entry = supply.setdefault(
            str(payload.get("trigger") or "unknown"),
            {"scans": 0, "by_status": {}, "zero_candidate": 0, "sent": 0, "funnel": {}, "funnel_unknown": 0},
        )
        status = str(payload.get("status") or ScanStatus.COMPLETED)
        completed = status == ScanStatus.COMPLETED
        candidates = payload.get("candidates") or []
        funnel = payload.get("funnel")
        entry["scans"] += 1
        entry["by_status"][status] = entry["by_status"].get(status, 0) + 1
        entry["zero_candidate"] += int(completed and not candidates)
        entry["sent"] += sum(1 for c in candidates if c.get("outcome") == RankedOutcome.SENT)
        entry["funnel_unknown"] += int(completed and not isinstance(funnel, dict))
        if isinstance(funnel, dict):
            for key, value in funnel.items():
                _add_counts(entry["funnel"], key, value)
    for entry in supply.values():
        entry["funnel"] = {
            key: dict(sorted(value.items())) if isinstance(value, dict) else value
            for key, value in entry["funnel"].items()
        }
    return dict(sorted(supply.items()))


def summarize(frame: pd.DataFrame, *, asset_classes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Descriptive summary of a labelled frame.

    Counts by outcome class (sent, runner-up, journal-only) and maturity, base rates, per-scan
    selection quality by scorer, card-policy counts, book sizing, news, regime/tape and recurrence
    blocks. ``asset_classes`` maps a symbol to its class for ``by_asset_class``; unmapped symbols
    are ``unclassified``. A runner-up competed for a card and lost; a journal-only candidate (a swing scan's) never
    competed for one.
    """
    if frame.empty:
        return {
            "total": 0,
            "triggers": {},
            "immature_total": 0,
            "fetch_failed_total": 0,
            "fetch_failed_reasons": {},
            "counts": {
                "sent": {"mature": 0, "immature": 0, "fetch_failed": 0},
                "runner_up": {"mature": 0, "immature": 0, "fetch_failed": 0},
                "journal_only": {"mature": 0, "immature": 0, "fetch_failed": 0},
            },
            "base_rates": {"sent": None, "runner_up": None, "journal_only": None},
            "selection": {"setup_quality": None, "shadow_score": None, "random": None, "scans_basis": "all"},
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
            "market_exposure": {"sent": None, "runner_up": None, "journal_only": None, "reason": None},
            "card_policy": {
                "withheld": 0,
                "would_withhold": 0,
                "withheld_mean_r_cost": None,
                "would_withhold_mean_r_cost": None,
            },
            "book_sizing": _book_sizing_summary(frame, frame),
            "news_context": _news_summary(frame, frame),
            "regime_context": _regime_summary(frame, frame),
            "recurrence": _recurrence_summary(frame, frame),
            "counterfactual": _counterfactual_summary(frame, frame),
            "by_asset_class": {},
        }

    is_immature = frame["hit"] == BracketHit.IMMATURE.value
    is_fetch_failed = frame["hit"] == FETCH_FAILED_HIT
    is_unresolved = is_immature | is_fetch_failed
    is_sent = frame["sent"]
    is_journal_only = frame["outcome"] == RankedOutcome.JOURNAL_ONLY
    classes = {"sent": is_sent, "runner_up": ~is_sent & ~is_journal_only, "journal_only": is_journal_only}
    mature = frame.loc[~is_unresolved]

    counts = {
        name: {
            "mature": int((mask & ~is_unresolved).sum()),
            "immature": int((mask & is_immature).sum()),
            "fetch_failed": int((mask & is_fetch_failed).sum()),
        }
        for name, mask in classes.items()
    }
    base_rates = {name: _base_rate(mature.loc[mask.loc[mature.index]]) for name, mask in classes.items()}
    has_shadow_scores = mature["shadow_score"].notna().any()
    # Only scans that carry a shadow score can compare all three pickers on one population.
    if has_shadow_scores:
        scored_scans = mature.loc[mature["shadow_score"].notna(), "scan_id"].dropna().unique()
        comparable = mature.loc[mature["scan_id"].isin(scored_scans)]
    else:
        comparable = mature
    selection = {
        "scans_basis": "shadow_scored" if has_shadow_scores else "all",
        "setup_quality": _selection_by_score(comparable, "setup_quality"),
        "shadow_score": _selection_by_score(comparable, "shadow_score") if has_shadow_scores else None,
        "random": _selection_random(comparable),
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
        **{
            name: _exposure(controlled.loc[mask.loc[controlled.index]], mature.loc[mask.loc[mature.index]])
            for name, mask in classes.items()
        },
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

    # Scans and sent cards per calling path; pre-trigger events have none and report "unknown".
    by_trigger = frame.assign(trigger=frame["trigger"].fillna("unknown")).groupby("trigger")
    triggers = {
        str(name): {"scans": int(group["scan_id"].nunique()), "sent": int(group["sent"].sum())}
        for name, group in by_trigger
    }

    return {
        "total": len(frame),
        "triggers": triggers,
        "immature_total": int(is_immature.sum()),
        "fetch_failed_total": int(is_fetch_failed.sum()),
        "fetch_failed_reasons": fetch_failed_reasons,
        "counts": counts,
        "base_rates": base_rates,
        "selection": selection,
        "llm_gate": llm_gate,
        "market_exposure": market_exposure,
        "card_policy": card_policy,
        "book_sizing": _book_sizing_summary(frame, mature),
        "news_context": _news_summary(frame, mature),
        "regime_context": _regime_summary(frame, mature),
        "recurrence": _recurrence_summary(frame, mature),
        "counterfactual": _counterfactual_summary(frame, mature),
        "by_asset_class": _asset_class_summary(mature, asset_classes),
    }
