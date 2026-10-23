"""Insider-cluster events: opportunistic officer/director purchases on cohort symbols (spec section 5, steps 1-3)."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.research.apriori.catalog import InsiderEntry
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.insider_fields import _routine


INSIDER_EVENT_COLUMNS = ("event_id", "symbol", "filing_date", "session", "decision_at", "buyers", "opp_dollars", "year")


def opportunistic_flags(purchases: Sequence[InsiderTransaction]) -> list[bool]:
    """Per purchase, whether it is opportunistic (not routine)."""
    return [not routine for routine in _routine(purchases)]


def purchase_dates(transactions: Mapping[str, Sequence[InsiderTransaction]]) -> dict[str, np.ndarray]:
    """Per symbol, the sorted filing-date ordinals of every purchase, routine or not."""
    out: dict[str, np.ndarray] = {}
    for symbol, rows in transactions.items():
        ordinals = sorted(t.filing_date.toordinal() for t in rows if t.code == "P")
        if ordinals:
            out[symbol] = np.array(ordinals, dtype=np.int64)
    return out


def _triggers(
    rows: Sequence[InsiderTransaction], day_ordinals: np.ndarray, entry: InsiderEntry
) -> tuple[list[tuple[date, int, float]], int]:
    """The symbol's triggers as ``(F, buyers, opp_dollars)`` and the count suppressed by the cooldown."""
    purchases = sorted((t for t in rows if t.code == "P"), key=lambda t: t.filing_date)
    opportunistic = [t for t, flag in zip(purchases, opportunistic_flags(purchases), strict=True) if flag]
    if not opportunistic:
        return [], 0
    positions = np.searchsorted(day_ordinals, [t.filing_date.toordinal() for t in opportunistic], side="left")
    event = entry.event
    triggers: list[tuple[date, int, float]] = []
    suppressed = 0
    last_position: int | None = None
    for filing in sorted({t.filing_date for t in opportunistic}):
        position = int(np.searchsorted(day_ordinals, filing.toordinal(), side="left"))
        in_window = [
            t
            for t, p in zip(opportunistic, positions, strict=True)
            if position - (event.cluster_window_sessions - 1) <= p <= position
        ]
        buyers = len({t.owner_cik for t in in_window})
        if buyers < event.min_buyers:
            continue
        if last_position is not None and position - last_position < event.cooldown_sessions:
            suppressed += 1
            continue
        last_position = position
        triggers.append((filing, buyers, float(sum(t.dollars for t in in_window))))
    return triggers, suppressed


def build_insider_events(
    transactions: Mapping[str, Sequence[InsiderTransaction]],
    trading_days: Sequence[date],
    identity_breaks: Mapping[str, date],
    entry: InsiderEntry,
    *,
    decision_time: time,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Triggers that pass placement, identity and the cooldown, with their counts by reason and year."""
    days = list(trading_days)
    day_ordinals = np.array([d.toordinal() for d in days], dtype=np.int64)
    start, end = entry.window.decisions
    rows: list[dict[str, Any]] = []
    excluded: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    accepted: dict[int, int] = defaultdict(int)
    n_triggers = 0
    n_suppressed = 0
    for symbol in sorted(transactions):
        triggers, suppressed = _triggers(transactions[symbol], day_ordinals, entry)
        n_triggers += len(triggers)
        n_suppressed += suppressed
        for filing, buyers, dollars in triggers:
            index = bisect_right(days, filing)
            if index >= len(days):
                excluded["after_calendar"][filing.year] += 1
                continue
            session = days[index]
            if not start <= session <= end:
                excluded["outside_window"][session.year] += 1
                continue
            broke = identity_breaks.get(symbol)
            if broke is not None and filing < broke:
                excluded["identity_break"][session.year] += 1
                continue
            accepted[session.year] += 1
            rows.append(
                {
                    "event_id": f"{symbol}:{filing.isoformat()}",
                    "symbol": symbol,
                    "filing_date": filing,
                    "session": session,
                    "decision_at": decision_at(session, decision_time),
                    "buyers": buyers,
                    "opp_dollars": dollars,
                    "year": session.year,
                }
            )
    frame = pd.DataFrame(rows, columns=list(INSIDER_EVENT_COLUMNS))
    frame = frame.sort_values(["session", "symbol"], kind="stable").reset_index(drop=True)
    counts = {
        "triggers": n_triggers,
        "suppressed_by_cooldown": n_suppressed,
        "excluded": {reason: dict(sorted(years.items())) for reason, years in sorted(excluded.items())},
        "accepted_by_year": dict(sorted(accepted.items())),
    }
    return frame, counts
