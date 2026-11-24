"""Input assembly for the run-up study: the pooled events protocol's cached cube, its pinned event sources and SPY.

Called only from inside ``execute_runup_study``'s ``build`` callable, after the manifest exists. The entry
pins the pooled protocol by SHA-256 (checked on the bytes before any other work) and the cube by content
hash; the protocol pins the cohort, the earnings pages and the corporate actions, each checked here before
use. The cube must already be cached by the pooled power check. Provider access: the calendar read
``build_cube_inputs`` makes and SPY's daily (only if the pooled cache has none) and hourly bars, fetched once
into ``market_cache_dir`` (never the pooled cache) and reused when present.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from agentic_trader.research.apriori.catalog import RunupEntry
from agentic_trader.research.apriori.runup_events import RunupInputs
from agentic_trader.research.pooled.campaign import EventsCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.runner import EventSources, build_cube_inputs, load_event_sources
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _fetch_cached


__all__ = ["MARKET_SYMBOL", "build_runup_inputs"]

MARKET_SYMBOL = "SPY"


def _file_sha256(files: Sequence[Path]) -> dict[str, str]:
    return {f"{path.parent.name}/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def _npz_files(cache_dir: Path, timeframe: str) -> list[Path]:
    return sorted((cache_dir / f"{MARKET_SYMBOL}_{timeframe}").glob("*.npz"))


async def _market_frame(
    timeframe: str,
    bars: BarSource,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    pace: Callable[[], Awaitable[None]],
) -> pd.DataFrame:
    kwargs = {"chunk": end - start} if timeframe == "1d" else {}
    frame = await _fetch_cached(MARKET_SYMBOL, timeframe, bars, cache_dir, start, end, "all", pace, **kwargs)
    if frame.empty:
        raise ValueError(f"the provider returned no {MARKET_SYMBOL} {timeframe} bars for {start.date()}..{end.date()}")
    return frame


async def build_runup_inputs(
    entry: RunupEntry,
    *,
    repo_root: Path,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    earnings_pages: Path,
    actions_file: Path,
    market_cache_dir: Path,
    static_symbols: Sequence[str] = (),
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
) -> RunupInputs:
    protocol_path = repo_root / entry.pooled_protocol
    raw = await asyncio.to_thread(protocol_path.read_bytes)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry.pooled_protocol_sha256:
        raise ValueError(
            f"{entry.pooled_protocol} has SHA-256 {digest}, but the entry pins pooled_protocol_sha256 "
            f"{entry.pooled_protocol_sha256}"
        )
    loaded = await asyncio.to_thread(load_campaign_protocol, protocol_path)
    protocol = loaded.protocol
    if not isinstance(protocol, EventsCampaignProtocol):
        raise TypeError(f"{entry.pooled_protocol} is a {protocol.id} protocol, not an events protocol")
    spec = protocol.cube_spec()
    entry.check_against(spec)
    cohort = await asyncio.to_thread(load_cohort, repo_root / protocol.cohort)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError(
            f"the cohort file has SHA-256 {cohort.sha256}, but the pooled protocol pins cohort_sha256 "
            f"{protocol.cohort_sha256}"
        )
    symbols = tuple(cohort.cohort.symbols)
    sources = EventSources(earnings_pages=earnings_pages, actions_file=actions_file)
    # Verifies the pages and actions digests against the protocol's event_inputs (fail closed, names both).
    pages, actions = await asyncio.to_thread(load_event_sources, protocol.event_inputs, sources)
    build = await build_cube_inputs(
        cohort,
        spec,
        bars=bars,
        calendar=calendar,
        cache_dir=cache_dir,
        static_symbols=static_symbols,
        pace=pace,
        progress=progress,
        require_cached=True,
        load_hourly=True,
        events=(protocol.event_inputs, sources),
    )
    cube_sha = await asyncio.to_thread(lambda: build.cube.sha256)
    if cube_sha != entry.cube_sha256:
        raise ValueError(f"the cached cube has SHA-256 {cube_sha}, but the entry pins cube_sha256 {entry.cube_sha256}")

    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(entry.window.bars_through, time.max, tzinfo=UTC)
    if progress is not None:
        progress(f"market: {MARKET_SYMBOL} daily and hourly bars into {market_cache_dir}")
    await asyncio.to_thread(market_cache_dir.mkdir, parents=True, exist_ok=True)
    pooled_daily = cache_dir / "bars"
    if _npz_files(pooled_daily, "1d"):
        # Read-only use of the pooled cache; nothing is written there.
        market_daily = await _market_frame("1d", bars, pooled_daily, start, end, pace)
        daily_source = "pooled_cache"
        daily_files = _npz_files(pooled_daily, "1d")
    else:
        market_daily = await _market_frame("1d", bars, market_cache_dir, start, end, pace)
        daily_source = "fetched"
        daily_files = _npz_files(market_cache_dir, "1d")
    market_hourly = await _market_frame("1h", bars, market_cache_dir, start, end, pace)
    market_files = await asyncio.to_thread(_file_sha256, [*daily_files, *_npz_files(market_cache_dir, "1h")])

    days = tuple(build.trading_days)
    reports = {symbol: pages.rows_by_symbol.get(symbol, ()) for symbol in symbols}
    provenance: dict[str, Any] = {
        "cube_sha256": cube_sha,
        "spec_identity": spec.identity,
        "pooled_protocol_sha256": digest,
        "cohort_sha256": cohort.sha256,
        "pages_sha256": pages.pages_sha256,
        "calendar_pages": pages.pages,
        "calendar_page_duplicates": pages.duplicates,
        "corporate_actions_sha256": protocol.event_inputs.corporate_actions.content_sha256,
        "market_symbol": MARKET_SYMBOL,
        "market_daily_source": daily_source,
        "market_files_sha256": market_files,
        "symbols_without_archive_rows": sorted(symbol for symbol in symbols if not reports[symbol]),
        "cohort_symbols": len(symbols),
        "calendar_sessions": len(days),
        "calendar_sha256": hashlib.sha256("\n".join(day.isoformat() for day in days).encode()).hexdigest(),
    }
    provenance["n_symbols_without_archive_rows"] = len(provenance["symbols_without_archive_rows"])
    return RunupInputs(
        cube=build.cube,
        trading_days=days,
        adjusted=build.adjusted,
        hourly=build.hourly,
        reports=reports,
        actions=actions,
        identity_breaks=identity_breaks(actions, symbols),
        market_daily=market_daily,
        market_hourly=market_hourly,
        decision_time=spec.bracket.decision_clock(),
        provenance=provenance,
    )
