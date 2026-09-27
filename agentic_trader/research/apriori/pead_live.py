"""Live PEAD events for one decision session, computed by the study's own ``build_events``.

The frozen entry is the identity; a copy narrowed to ``decisions=(session, session)`` is
only a window, never validated or persisted. Any missing evidence (Nasdaq page, SPY or
the static liquidity reference) makes the whole session unavailable: no partial sets.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import ReportedPage
from agentic_trader.research.apriori.catalog import PeadEntry
from agentic_trader.research.apriori.earnings_history import dedupe_rows

# `_by_session` is the study's own UTC -> New York session-date mapping (leading
# underscore: a deliberate same-package reuse, not a public contract). Reusing it here
# means the benchmark availability check uses exactly the date mapping `build_events`
# uses, rather than a second, possibly divergent tz-conversion rule.
from agentic_trader.research.apriori.pead_events import SUPPORTED_SYMBOL, MarketData, _by_session, build_events
from agentic_trader.screeners.dynamic_universe import static_reference


__all__ = ["LOOKBACK_CALENDAR_DAYS", "DailyBars", "LiveEvents", "event_document", "live_events"]

# Calendar days looked back from the decision session for the trading-day list; must
# comfortably cover the event's vol_window/atr_window plus the D, D+1, D+2=session triplet.
LOOKBACK_CALENDAR_DAYS = 120
# A journaled unavailable reason keeps the provider's message, bounded.
REASON_MAX_CHARS = 200


class DailyBars(Protocol):
    def fetch_daily_many(
        self, symbols: Sequence[str], start: datetime, end: datetime, *, adjustment: str
    ) -> dict[str, pd.DataFrame]: ...


@dataclass(frozen=True)
class LiveEvents:
    status: Literal["ok", "unavailable"]
    session: date
    report_date: date | None
    events: tuple[dict[str, Any], ...]
    counts: dict[str, Any]
    reason: str | None
    page: ReportedPage | None


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None  # a missing reported surprise (NaN once pandas widens the column)
    return value


def event_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """A JSON-safe copy of one ``EVENT_COLUMNS`` row: dates/datetimes as ISO strings, numpy scalars as Python types."""
    return {key: _plain(value) for key, value in row.items()}


def _unavailable(
    session: date,
    reason: str,
    *,
    report_date: date | None = None,
    page: ReportedPage | None = None,
    counts: dict[str, Any] | None = None,
) -> LiveEvents:
    return LiveEvents("unavailable", session, report_date, (), counts or {}, reason, page)


def _benchmark_bars_available(frame: pd.DataFrame | None, d_minus_1: date, d_plus_1: date) -> bool:
    """Whether the benchmark has finite closes at both reaction endpoints, D-1 and D+1.

    ``pead_events._reaction_z`` reads the benchmark at exactly these two sessions (never
    the report day D itself); reusing ``pead_events._by_session`` applies the same
    UTC -> New York session-date mapping ``build_events`` uses, rather than a second
    bespoke one.
    """
    if frame is None or frame.empty:
        return False
    keyed = _by_session(frame)
    if d_minus_1 not in keyed.index or d_plus_1 not in keyed.index:
        return False
    closes = keyed.loc[[d_minus_1, d_plus_1], "Close"].to_numpy(dtype=float)
    return bool(np.all(np.isfinite(closes)))


async def live_events(
    entry: PeadEntry,
    session: date,
    *,
    earnings,
    calendar,
    bars: DailyBars,
    static_symbols: Sequence[str],
) -> LiveEvents:
    """The narrow-leg PEAD events for ``session``, computed by the study's own ``build_events``.

    Unavailable, never partial: a missing Nasdaq page, an empty page, a missing SPY D-1
    or D+1 bar, a bar-fetch failure or too few static liquidity names all fail the whole
    session closed rather than returning some but not all events.
    """
    days = await calendar.get_calendar_range(session - timedelta(days=LOOKBACK_CALENDAR_DAYS), session)
    trading = tuple(sorted(day.date for day in days if day.is_trading_day))
    if len(trading) < 4 or trading[-1] != session:
        return _unavailable(session, "not_a_trading_session")
    report_date, as_of = trading[-3], trading[-2]
    d_minus_1 = trading[-4]  # the session before D, both of the benchmark's reaction endpoints

    page = await earnings.reported_rows(report_date)
    if page.rows is None:
        return _unavailable(session, f"calendar_unavailable: {page.detail}", report_date=report_date, page=page)
    if not page.rows:
        return _unavailable(session, "calendar_empty", report_date=report_date, page=page)
    rows, _ = dedupe_rows(page.rows)
    reporters = sorted({row.symbol for row in rows if SUPPORTED_SYMBOL.fullmatch(row.symbol)})

    benchmark = entry.event.benchmark
    start = datetime.combine(trading[0], time.min, tzinfo=UTC)
    end = datetime.combine(as_of, time(23, 59), tzinfo=UTC)
    try:
        adjusted = await asyncio.to_thread(bars.fetch_daily_many, [benchmark, *reporters], start, end, adjustment="all")
        raw = await asyncio.to_thread(
            bars.fetch_daily_many, sorted({*reporters, *static_symbols}), start, end, adjustment="raw"
        )
    except Exception as exc:
        reason = f"bars_unavailable: {type(exc).__name__}: {exc}"[:REASON_MAX_CHARS]
        return _unavailable(session, reason, report_date=report_date, page=page)

    if not _benchmark_bars_available(adjusted.get(benchmark), d_minus_1, as_of):
        return _unavailable(session, "benchmark_bars_unavailable", report_date=report_date, page=page)

    # Checked independently of `rows`/`reporters`: a row only reaches build_events'
    # own reference gate after clearing earlier per-row checks (bars present, in
    # window, ...), so an empty or too-small `raw` fetch could otherwise leave every
    # row short-circuited on an earlier reason (e.g. `no_daily_bars`) and fail open
    # with `status="ok"`, `events=()` instead of failing the session closed.
    market_static_symbols = tuple(s for s in static_symbols if s in raw)
    static_daily = {s: raw[s] for s in market_static_symbols}
    reference = static_reference(static_daily, entry.universe.static_percentile, as_of=as_of)
    if reference.value is None:
        return _unavailable(session, "static_reference_unavailable", report_date=report_date, page=page)

    market = MarketData(trading, adjusted, market_static_symbols, liquidity_daily=raw)
    window = entry.window.model_copy(update={"decisions": (session, session)})
    frame, counts = await asyncio.to_thread(build_events, rows, market, entry.model_copy(update={"window": window}))

    long_events = frame[frame["leg"] == "LONG"].sort_values(["z", "symbol"], ascending=[False, True])
    events = tuple(event_document(row) for row in long_events.to_dict("records"))
    return LiveEvents("ok", session, report_date, events, counts, None, page)
