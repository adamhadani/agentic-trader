"""Read-only measurement of the insider watch cards over journaled ``insider_watch_decision`` events.

Descriptive only (spec section 4): ``copilot cards outcomes`` labels every sent and every
eligible-but-unsent journaled event with the frozen cube bracket on hourly bars (``label_bracket``, as the
PEAD forward record does), reports the mean cost-adjusted R and the beta-matched excess (the same past-only
yardstick native rows use), and counts taps and fills. It sends nothing, writes nothing and gates nothing;
a watch card is unvalidated, so these numbers are a record for the operator, not evidence of an edge.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import select

from agentic_trader.constants import SignalStatus
from agentic_trader.research.apriori.pead_study import decision_price, utc_index
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID
from agentic_trader.research.pooled.cube import BracketSpec
from agentic_trader.research.setups.beta import trailing_beta
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket
from agentic_trader.research.setups.outcomes import MARKET_PROXY_SYMBOL, market_r
from agentic_trader.storage.models import SignalRecord


if TYPE_CHECKING:
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["WatchBars", "decision_frame", "label_watch_events", "summarize_watch", "watch_bars", "watch_signals"]

WATCH_STRATEGY = INSIDER_WATCH_STRATEGY_ID
_HOURLY_LOOKBACK_DAYS = 7
_DAILY_LOOKBACK_DAYS = 200  # the beta window's 120 sessions, with holiday margin
_LABEL_SPAN = timedelta(days=45)  # 20 sessions of holding, with holiday margin
_COLUMNS = (
    "scan_id",
    "event_id",
    "symbol",
    "session",
    "decision_at",
    "atr",
    "buyers",
    "opp_dollars",
    "outcome",
    "sent",
)
# A signal that never got past the card (still pending, dismissed, expired) is not a tap.
_NOT_TAPPED = (SignalStatus.PENDING, SignalStatus.DISMISSED, SignalStatus.EXPIRED)


def decision_frame(payloads: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per sent or eligible-but-unsent journaled event, deduped by its ``event_id``.

    The same event can be journaled by more than one decision scan of its session (a rerun); it is one
    event, kept as the scan that sent it when one did, else the first. Eligible means the event carries the
    ATR the eligibility rule measured; an ineligible or incomplete event is journaled without one and is not
    part of this record. ``sent`` is the journaling scan's own outcome.
    """
    by_event: dict[Any, dict[str, Any]] = {}
    for payload in payloads:
        scan_id = payload.get("scan_id")
        for event in payload.get("events") or []:
            if event.get("atr") is None:
                continue
            key = event.get("event_id") or (scan_id, event["symbol"])
            row = {
                "scan_id": scan_id,
                "event_id": event.get("event_id"),
                "symbol": event["symbol"],
                "session": date.fromisoformat(event["session"]),
                "decision_at": datetime.fromisoformat(event["decision_at"]),
                "atr": float(event["atr"]),
                "buyers": event.get("buyers"),
                "opp_dollars": event.get("opp_dollars"),
                "outcome": event.get("outcome"),
                "sent": event.get("outcome") == "sent",
            }
            kept = by_event.get(key)
            if kept is None or (row["sent"] and not kept["sent"]):
                by_event[key] = row
    return pd.DataFrame(list(by_event.values()), columns=list(_COLUMNS))


@dataclass
class WatchBars:
    hourly: dict[str, pd.DataFrame] = field(default_factory=dict)
    daily: dict[str, pd.DataFrame] = field(default_factory=dict)
    market_hourly: pd.DataFrame | None = None
    market_daily: pd.DataFrame | None = None
    failures: dict[str, str] = field(default_factory=dict)


def watch_bars(
    fetch: Callable[[str, str, datetime, datetime], pd.DataFrame], frame: pd.DataFrame, now: datetime
) -> WatchBars:
    """Hourly bars per event symbol, adjusted daily bars for its beta and the proxy's own pair.

    ``fetch(symbol, timeframe, start, end)`` is the caller's paced, adjusted source. A symbol whose hourly
    read fails is recorded in ``failures`` (type name and message) and its daily bars are not requested.
    """
    bars = WatchBars()
    if frame.empty:
        return bars
    first = {symbol: min(group["session"]) for symbol, group in frame.groupby("symbol")}
    earliest = min(first.values())

    def start_of(day: date, lookback: int) -> datetime:
        return datetime.combine(day - timedelta(days=lookback), time.min, tzinfo=UTC)

    def read(symbol: str, timeframe: str, day: date, lookback: int) -> pd.DataFrame | None:
        try:
            return fetch(symbol, timeframe, start_of(day, lookback), now)
        except Exception as exc:
            bars.failures[symbol] = f"{type(exc).__name__}: {exc}"
            return None

    for symbol, day in sorted(first.items()):
        hourly = read(symbol, "1h", day, _HOURLY_LOOKBACK_DAYS)
        if hourly is None:
            continue
        bars.hourly[symbol] = hourly
        daily = read(symbol, "1d", day, _DAILY_LOOKBACK_DAYS)
        if daily is not None:
            bars.daily[symbol] = daily
    bars.market_hourly = read(MARKET_PROXY_SYMBOL, "1h", earliest, _HOURLY_LOOKBACK_DAYS)
    bars.market_daily = read(MARKET_PROXY_SYMBOL, "1d", earliest, _DAILY_LOOKBACK_DAYS)
    return bars


def _row(event: dict[str, Any], **values: Any) -> dict[str, Any]:
    blank: dict[str, Any] = {
        "hit": None,
        "r": None,
        "r_cost": None,
        "holding_sessions": None,
        "beta": None,
        "beta_reason": None,
        "market_r": None,
        "beta_excess_r": None,
        "reason": None,
    }
    return {**event, **blank, **values}


def label_watch_events(
    frame: pd.DataFrame,
    hourly: Mapping[str, pd.DataFrame],
    daily: Mapping[str, pd.DataFrame],
    market_hourly: pd.DataFrame | None,
    market_daily: pd.DataFrame | None,
    bracket: BracketSpec,
    cost_bps_per_side: float,
) -> pd.DataFrame:
    """One row per journaled event: the frozen bracket's outcome and its beta-matched excess.

    The levels are the study's (decision-bar close, ``stop_atr_multiple`` x ATR, ``target_r`` x risk) and the
    walk is ``label_bracket`` over the event's hourly bars at the decision time. ``reason`` names why a row
    has no ``r_cost`` (``no_hourly_bars``, ``no_decision_price``, ``degenerate_levels``, ``immature``).
    ``beta_excess_r`` is None when the beta or the proxy return is unavailable (``beta_reason`` says why).
    """
    rows: list[dict[str, Any]] = []
    for event in frame.to_dict("records"):
        bars = hourly.get(event["symbol"])
        if bars is None or bars.empty:
            rows.append(_row(event, reason="no_hourly_bars"))
            continue
        when = event["decision_at"]
        index = utc_index(bars)
        window = bars.loc[(index >= when - timedelta(days=1)) & (index <= when + _LABEL_SPAN)]
        price = decision_price(window, when)
        if price is None:
            rows.append(_row(event, reason="no_decision_price"))
            continue
        risk = bracket.stop_atr_multiple * float(event["atr"])
        if price - risk <= 0:
            rows.append(_row(event, reason="degenerate_levels"))
            continue
        levels = SetupLevels("LONG", price, price - risk, price + bracket.target_r * risk)
        outcome = label_bracket(
            levels,
            when,
            window,
            max_hold_sessions=bracket.max_hold_sessions,
            cost_bps_per_side=cost_bps_per_side,
        )
        if outcome.hit == BracketHit.IMMATURE or outcome.r_cost is None:
            rows.append(_row(event, reason="immature"))
            continue
        beta, beta_reason = trailing_beta(daily.get(event["symbol"]), market_daily, event["session"])
        proxy = (
            market_r("LONG", price, levels.stop, outcome.entry_time, outcome.exit_time, market_hourly)
            if market_hourly is not None and outcome.entry_time is not None and outcome.exit_time is not None
            else None
        )
        matched = beta * proxy if beta is not None and proxy is not None else None
        rows.append(
            _row(
                event,
                hit=str(outcome.hit),
                r=outcome.r,
                r_cost=outcome.r_cost,
                holding_sessions=outcome.holding_sessions,
                beta=beta,
                beta_reason=beta_reason,
                market_r=proxy,
                beta_excess_r=(outcome.r_cost - matched) if matched is not None else None,
            )
        )
    return pd.DataFrame(rows)


def _finite_mean(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    mean = float(numeric.mean())
    return mean if math.isfinite(mean) else None


def _block(rows: pd.DataFrame) -> dict[str, Any]:
    mature = rows[rows["r_cost"].notna()] if not rows.empty else rows
    return {
        "n": len(mature),
        "mean_r_cost": _finite_mean(mature["r_cost"]) if len(mature) else None,
        "mean_beta_excess_r": _finite_mean(mature["beta_excess_r"]) if len(mature) else None,
    }


def summarize_watch(frame: pd.DataFrame, labels: pd.DataFrame, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, the labelled R blocks for all, sent and unsent events, taps and fills.

    ``signals`` is one dict per ``insider_watch_long`` signal in the window (``SignalRecord.to_dict()``).
    A tap is a signal that left the card (any status but pending, dismissed or expired); a fill is one with
    an ``executed_at``. Counts of events the labeller could not score are kept by reason, never dropped.
    """
    labelled = labels if not labels.empty else pd.DataFrame(columns=[*_COLUMNS, "r_cost", "beta_excess_r", "reason"])
    sent = labelled[labelled["sent"].astype(bool)] if not labelled.empty else labelled
    unsent = labelled[~labelled["sent"].astype(bool)] if not labelled.empty else labelled
    reasons = Counter(str(r) for r in labelled["reason"].dropna()) if not labelled.empty else Counter()
    watch = [s for s in signals if s.get("strategy") == WATCH_STRATEGY]
    return {
        "events": len(frame),
        "sent": int(frame["sent"].sum()) if not frame.empty else 0,
        "all": _block(labelled),
        "sent_events": _block(sent),
        "unsent_events": _block(unsent),
        "unlabelled": dict(sorted(reasons.items())),
        "taps": sum(1 for s in watch if s.get("status") not in _NOT_TAPPED),
        "fills": sum(1 for s in watch if s.get("executed_at") is not None),
        "note": "Descriptive record of an unvalidated watch rule; never a gate, never promotion evidence.",
    }


async def watch_signals(db: SignalDatabase, since: datetime) -> list[dict[str, Any]]:
    """``insider_watch_long`` signals in ``db``'s own scope, recorded since ``since``."""
    async with db.session_factory() as session:
        stmt = select(SignalRecord).where(
            *db._scope(), SignalRecord.strategy == WATCH_STRATEGY, SignalRecord.timestamp >= since
        )
        return [record.to_dict() for record in (await session.execute(stmt)).scalars().all()]
