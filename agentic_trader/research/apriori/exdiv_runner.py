"""Provider access for the exdiv study: trading calendar, cached corporate actions and SIP bars.

Called only from inside ``execute_exdiv_study``'s ``build`` callable, after the manifest exists.
The cohort file is read (and digested) first; part 1's ``fetch_corporate_actions_cached`` supplies
the actions over the entry's process-date window; adjusted daily bars for the cohort (ATR), raw daily
bars for the cohort and static universe (the liquidity gate) and regular-session hourly bars for
symbols with at least one event all reuse the setup study's immutable ``.npz`` cache. A symbol whose
bars fail is recorded in ``bar_failures``, never silently dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from agentic_trader.data.corporate_actions import CorporateActionsSource
from agentic_trader.research.apriori.catalog import ExdivEntry
from agentic_trader.research.apriori.exdiv_events import build_exdiv_events
from agentic_trader.research.apriori.exdiv_study import ExdivInputs
from agentic_trader.research.apriori.pead_events import SUPPORTED_SYMBOL, MarketData
from agentic_trader.research.apriori.pead_runner import bars_or_failure, record_failure
from agentic_trader.research.corporate_actions import cohort_symbols, fetch_corporate_actions_cached
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range


__all__ = ["build_exdiv_inputs"]

_CALENDAR_PAD = timedelta(days=60)
_HOURLY_LEAD = timedelta(days=7)
_HOURLY_TAIL = timedelta(days=60)


async def build_exdiv_inputs(
    entry: ExdivEntry,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    actions_source: CorporateActionsSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
    cohort_path: Path,
) -> ExdivInputs:
    cohort_bytes = cohort_path.read_bytes()  # before any provider call
    symbols = sorted(
        {s.strip().upper() for s in cohort_symbols(cohort_path) if SUPPORTED_SYMBOL.fullmatch(s.strip().upper())}
    )
    cohort = {"path": str(cohort_path), "sha256": hashlib.sha256(cohort_bytes).hexdigest(), "symbols": len(symbols)}

    first, _last = entry.window.decisions
    bars_through = entry.window.bars_through
    days = await calendar.get_calendar_range(first - _CALENDAR_PAD, bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))

    actions, actions_manifest = await fetch_corporate_actions_cached(
        actions_source, symbols, entry.actions_window[0], entry.actions_window[1], cache_dir, pace=pace
    )

    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    start = datetime.combine(first - _CALENDAR_PAD, time.min, tzinfo=UTC)
    end = datetime.combine(bars_through, time.max, tzinfo=UTC)
    _claim_cache_range(bar_dir, start, end)
    _claim_cache_range(raw_dir, start, end)

    daily: dict[str, pd.DataFrame] = {}
    liquidity_daily: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        frame, failure = await bars_or_failure(symbol, "1d", bars, bar_dir, start, end, pace, chunk=end - start)
        if frame is None:
            record_failure(failures, symbol, failure or "daily: unavailable")
        else:
            daily[symbol] = frame
    for symbol in sorted({*symbols, *static_symbols}):
        frame, failure = await bars_or_failure(
            symbol, "1d", bars, raw_dir, start, end, pace, adjustment="raw", chunk=end - start
        )
        if frame is None:
            record_failure(failures, symbol, failure or "daily_raw: unavailable")
        else:
            liquidity_daily[symbol] = frame

    market = MarketData(
        trading_days,
        daily,
        tuple(s for s in static_symbols if s in liquidity_daily),
        liquidity_daily=liquidity_daily,
    )
    events, _ = await asyncio.to_thread(build_exdiv_events, actions, market, entry)
    hourly: dict[str, pd.DataFrame] = {}
    for symbol, group in events.groupby("symbol"):
        span_start = min(group["decision_at"]) - _HOURLY_LEAD
        span_end = min(max(group["decision_at"]) + _HOURLY_TAIL, end)
        frame, failure = await bars_or_failure(str(symbol), "1h", bars, bar_dir, span_start, span_end, pace)
        if frame is None:
            record_failure(failures, str(symbol), failure or "hourly: unavailable")
        else:
            hourly[str(symbol)] = frame
    return ExdivInputs(
        actions=actions,
        actions_manifest=actions_manifest,
        cohort=cohort,
        market=market,
        hourly=hourly,
        bar_failures=failures,
        static_requested=tuple(static_symbols),
    )
