"""Provider access for the PEAD study: trading calendar, Nasdaq pages and SIP bars.

Called only from inside ``execute_pead_study``'s ``build`` callable, after the manifest
exists. Daily bars are fetched once per symbol over the whole window in a single request
(well under one page); hourly bars only for symbols with at least one liquid event, over
that symbol's event span, in the setup study's one-year chunks. Both reuse the setup
study's immutable ``.npz`` cache with its range claim, so a rerun with ``--cache`` never
re-fetches. A symbol whose bars fail is recorded in ``bar_failures`` and its events are
counted as missing, never silently dropped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx
import pandas as pd

from agentic_trader.research.apriori.catalog import PeadEntry
from agentic_trader.research.apriori.earnings_history import CalendarPageStore, acquire_calendar, dedupe_rows, weekdays
from agentic_trader.research.apriori.pead_events import SUPPORTED_SYMBOL, MarketData, build_events
from agentic_trader.research.apriori.pead_study import PeadInputs
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached


__all__ = ["build_pead_inputs"]

_CALENDAR_PAD = timedelta(days=60)
_REPORT_PAD = timedelta(days=7)
_HOURLY_TAIL = timedelta(days=130)


async def _bars_or_failure(
    symbol: str,
    timeframe: str,
    bars: BarSource,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    pace: Callable[[], Awaitable[None]],
    **kwargs,
) -> tuple[pd.DataFrame | None, str | None]:
    try:
        frame = await _fetch_cached(symbol, timeframe, bars, cache_dir, start, end, "all", pace, **kwargs)
    except Exception as exc:
        return None, f"{'daily' if timeframe == '1d' else 'hourly'}: {type(exc).__name__}: {exc}"
    if frame.empty:
        return None, f"{'daily' if timeframe == '1d' else 'hourly'}: empty"
    return frame, None


async def build_pead_inputs(
    entry: PeadEntry,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
    calendar_transport: httpx.AsyncBaseTransport | None = None,
    calendar_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PeadInputs:
    first, last = entry.window.decisions
    bars_through = entry.window.bars_through
    days = await calendar.get_calendar_range(first - _CALENDAR_PAD, bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))

    acquisition = await acquire_calendar(
        weekdays(first - _REPORT_PAD, last),
        CalendarPageStore(cache_dir / "nasdaq"),
        interval_seconds=entry.calendar_request_interval_seconds,
        transport=calendar_transport,
        sleep=calendar_sleep,
    )
    rows, duplicates = dedupe_rows(acquisition.rows)

    bar_dir = cache_dir / "bars"
    start = datetime.combine(first - _CALENDAR_PAD, time.min, tzinfo=UTC)
    end = datetime.combine(bars_through, time.max, tzinfo=UTC)
    _claim_cache_range(bar_dir, start, end)

    benchmark = entry.event.benchmark
    symbols = sorted({benchmark, *static_symbols, *(r.symbol for r in rows if SUPPORTED_SYMBOL.fullmatch(r.symbol))})
    daily: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        frame, failure = await _bars_or_failure(symbol, "1d", bars, bar_dir, start, end, pace, chunk=end - start)
        if frame is None:
            failures[symbol] = failure or "daily: unavailable"
        else:
            daily[symbol] = frame
    if benchmark not in daily:
        raise ValueError(f"benchmark {benchmark} has no daily bars: {failures.get(benchmark)}")

    market = MarketData(trading_days, daily, tuple(s for s in static_symbols if s in daily))
    events, _ = await asyncio.to_thread(build_events, rows, market, entry)
    hourly: dict[str, pd.DataFrame] = {}
    for symbol, group in events.groupby("symbol"):
        span_start = datetime.combine(min(group["session"]) - _REPORT_PAD, time.min, tzinfo=UTC)
        span_end = min(datetime.combine(max(group["session"]), time.max, tzinfo=UTC) + _HOURLY_TAIL, end)
        frame, failure = await _bars_or_failure(str(symbol), "1h", bars, bar_dir, span_start, span_end, pace)
        if frame is None:
            failures[str(symbol)] = failure or "hourly: unavailable"
        else:
            hourly[str(symbol)] = frame
    return PeadInputs(
        acquisition=acquisition,
        rows=tuple(rows),
        duplicates=duplicates,
        market=market,
        hourly=hourly,
        bar_failures=failures,
    )
