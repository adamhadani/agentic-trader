"""Input assembly for the investor-day study: cohorts v3 and xu, their cached cubes, pinned sources and SPY.

Called only from inside ``execute_idday_study``'s ``build`` callable, after the manifest exists. Every pinned
digest is checked on the bytes before use: both pooled protocols, both cohorts, both cached cubes, the Nasdaq
pages and each cohort's corporate actions, the investor-day acquisition, the parsed event table and the parser
check file. The parser check is re-validated against a sample rebuilt from these inputs. Every refusal raises
``ValueError`` before any SPY bar read; the only provider access ahead of that is the calendar read
``build_cube_inputs`` makes. SPY bars are then read as in ``runup_runner`` (daily from the pooled cache when
present, hourly into ``market_cache_dir``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from agentic_trader.research.apriori.catalog import IddayEntry
from agentic_trader.research.apriori.idday_events import (
    IddayInputs,
    cohort_bands,
    idday_candidates,
    sample_parser_check,
    validate_parser_check,
)
from agentic_trader.research.apriori.idday_parser import parse_filings
from agentic_trader.research.apriori.runup_runner import MARKET_SYMBOL, _file_sha256, _market_frame, _npz_files
from agentic_trader.research.investor_days import InvestorDayStore, content_sha256
from agentic_trader.research.pooled.campaign import (
    EventsCampaignProtocol,
    InsiderCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cohort import LoadedCohort, load_cohort
from agentic_trader.research.pooled.cube import merge_cubes
from agentic_trader.research.pooled.events import identity_breaks, load_actions
from agentic_trader.research.pooled.runner import EventSources, build_cube_inputs, load_event_sources
from agentic_trader.research.setups.runner import BarSource, CalendarSource


__all__ = ["build_idday_inputs"]


async def _load_protocol(entry: IddayEntry, name: str, repo_root: Path) -> Any:
    cohort_pin = next(c for c in entry.cohorts if c.name == name)
    path = repo_root / cohort_pin.pooled_protocol
    raw = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != cohort_pin.pooled_protocol_sha256:
        raise ValueError(
            f"{cohort_pin.pooled_protocol} has SHA-256 {digest}, but the entry pins pooled_protocol_sha256 "
            f"{cohort_pin.pooled_protocol_sha256}"
        )
    return (await asyncio.to_thread(load_campaign_protocol, path)).protocol


async def _checked_cohort(protocol: Any, repo_root: Path) -> LoadedCohort:
    cohort = await asyncio.to_thread(load_cohort, repo_root / protocol.cohort)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError(
            f"the cohort file has SHA-256 {cohort.sha256}, but the pooled protocol pins cohort_sha256 "
            f"{protocol.cohort_sha256}"
        )
    return cohort


def _check_cube(name: str, digest: str, entry: IddayEntry) -> None:
    pinned = next(c for c in entry.cohorts if c.name == name).cube_sha256
    if digest != pinned:
        raise ValueError(f"the cached {name} cube has SHA-256 {digest}, but the entry pins cube_sha256 {pinned}")


async def build_idday_inputs(
    entry: IddayEntry,
    *,
    repo_root: Path,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    earnings_pages: Path,
    actions_v3: Path,
    actions_xu: Path,
    investor_days_dir: Path,
    parser_check: Path,
    market_cache_dir: Path,
    static_symbols: Sequence[str] = (),
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
) -> IddayInputs:
    # 1. Protocols, specs and cohorts.
    protocol_v3 = await _load_protocol(entry, "v3", repo_root)
    protocol_xu = await _load_protocol(entry, "xu", repo_root)
    if not isinstance(protocol_v3, EventsCampaignProtocol):
        raise TypeError(f"the v3 protocol is a {protocol_v3.id} protocol, not an events protocol")
    if not isinstance(protocol_xu, InsiderCampaignProtocol):
        raise TypeError(f"the xu protocol is a {protocol_xu.id} protocol, not an insider protocol")
    spec_v3, spec_xu = protocol_v3.cube_spec(), protocol_xu.cube_spec()
    if spec_v3.identity != spec_xu.identity:
        raise ValueError(f"the cohorts' cube specs differ ({spec_v3.identity} vs {spec_xu.identity})")
    entry.check_against(spec_v3)
    entry.check_against(spec_xu)
    cohort_v3 = await _checked_cohort(protocol_v3, repo_root)
    cohort_xu = await _checked_cohort(protocol_xu, repo_root)

    # 2. Event sources: pages and v3 actions through the protocol's loader, xu actions by digest.
    sources = EventSources(earnings_pages=earnings_pages, actions_file=actions_v3)
    pages, v3_actions = await asyncio.to_thread(load_event_sources, protocol_v3.event_inputs, sources)
    xu_actions, xu_digest = await asyncio.to_thread(load_actions, actions_xu)
    pinned_xu = protocol_xu.insider_inputs.corporate_actions.content_sha256
    if xu_digest != pinned_xu:
        raise ValueError(f"the cohort xu corporate actions have digest {xu_digest}, but the protocol pins {pinned_xu}")

    # 3. Cubes.
    build_v3 = await build_cube_inputs(
        cohort_v3,
        spec_v3,
        bars=bars,
        calendar=calendar,
        cache_dir=cache_dir,
        static_symbols=static_symbols,
        pace=pace,
        progress=progress,
        require_cached=True,
        load_hourly=True,
        events=(protocol_v3.event_inputs, sources),
    )
    cube_v3 = await asyncio.to_thread(lambda: build_v3.cube.sha256)
    _check_cube("v3", cube_v3, entry)
    build_xu = await build_cube_inputs(
        cohort_xu,
        spec_xu,
        bars=bars,
        calendar=calendar,
        cache_dir=cache_dir,
        static_symbols=static_symbols,
        pace=pace,
        progress=progress,
        require_cached=True,
        load_hourly=True,
    )
    cube_xu = await asyncio.to_thread(lambda: build_xu.cube.sha256)
    _check_cube("xu", cube_xu, entry)
    merged = await asyncio.to_thread(merge_cubes, build_v3.cube, build_xu.cube)
    merged_sha = await asyncio.to_thread(lambda: merged.sha256)
    days = tuple(build_v3.trading_days)
    position = {day: i for i, day in enumerate(days)}
    if not merged.sessions or any(session not in position for session in merged.sessions):
        raise ValueError("the merged cube's sessions are not all trading days")
    if position[merged.sessions[-1]] - position[merged.sessions[0]] + 1 != len(merged.sessions):
        raise ValueError("the merged cube's sessions are not contiguous trading days")
    symbols = tuple(merged.symbols)
    actions = (*v3_actions, *xu_actions)

    # 4. The investor-day acquisition and its event table.
    store = InvestorDayStore(investor_days_dir)
    acquisition = await asyncio.to_thread(content_sha256, store)
    if acquisition != entry.investor_days.content_sha256:
        raise ValueError(
            f"the investor-day acquisition has content_sha256 {acquisition}, but the entry pins "
            f"{entry.investor_days.content_sha256}"
        )
    table = await asyncio.to_thread(parse_filings, store)
    if table.events_sha256 != entry.investor_days.events_sha256:
        raise ValueError(
            f"the parsed event table has events_sha256 {table.events_sha256}, but the entry pins "
            f"{entry.investor_days.events_sha256}"
        )
    records = await asyncio.to_thread(store.issuers)
    unmapped = await asyncio.to_thread(store.unmapped)
    wanted = set(symbols)
    issuers = {record.cik: record for record in records}
    issuer_of = {symbol: record.cik for record in records for symbol in record.symbols if symbol in wanted}
    reports = {symbol: pages.rows_by_symbol.get(symbol, ()) for symbol in symbols}
    inputs = IddayInputs(
        cube=merged,
        trading_days=days,
        adjusted={**build_v3.adjusted, **build_xu.adjusted},
        hourly={**build_v3.hourly, **build_xu.hourly},
        reports=reports,
        actions=actions,
        identity_breaks=identity_breaks(actions, symbols),
        market_daily=pd.DataFrame(),
        market_hourly=pd.DataFrame(),
        decision_time=spec_v3.bracket.decision_clock(),
        provenance={},
        events=table,
        issuers=issuers,
        issuer_of=issuer_of,
        band_of=cohort_bands(cohort_v3.cohort, cohort_xu.cohort),
    )

    # 5. The parser check: the file's digest, then its labels against a sample rebuilt from these inputs.
    check_bytes = await asyncio.to_thread(parser_check.read_bytes)
    check_digest = hashlib.sha256(check_bytes).hexdigest()
    if check_digest != entry.parser_check.sha256:
        raise ValueError(
            f"the parser check file has SHA-256 {check_digest}, but the entry pins parser_check.sha256 "
            f"{entry.parser_check.sha256}"
        )
    candidates, _ = await asyncio.to_thread(idday_candidates, inputs, entry)
    sample = await asyncio.to_thread(sample_parser_check, candidates, table, entry)
    check_result = validate_parser_check(json.loads(check_bytes), sample, entry)

    # 6. SPY bars: the first provider bar read, only after every check above.
    start = datetime.combine(spec_v3.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(entry.window.bars_through, time.max, tzinfo=UTC)
    if progress is not None:
        progress(f"market: {MARKET_SYMBOL} daily and hourly bars into {market_cache_dir}")
    await asyncio.to_thread(market_cache_dir.mkdir, parents=True, exist_ok=True)
    pooled_daily = cache_dir / "bars"
    if _npz_files(pooled_daily, "1d"):
        market_daily = await _market_frame("1d", bars, pooled_daily, start, end, pace)
        daily_source = "pooled_cache"
        daily_files = _npz_files(pooled_daily, "1d")
    else:
        market_daily = await _market_frame("1d", bars, market_cache_dir, start, end, pace)
        daily_source = "fetched"
        daily_files = _npz_files(market_cache_dir, "1d")
    market_hourly = await _market_frame("1h", bars, market_cache_dir, start, end, pace)
    market_files = await asyncio.to_thread(_file_sha256, [*daily_files, *_npz_files(market_cache_dir, "1h")])

    provenance: dict[str, Any] = {
        "merged_cube_sha256": merged_sha,
        "cube_sha256_v3": cube_v3,
        "cube_sha256_xu": cube_xu,
        "spec_identity": spec_v3.identity,
        "pooled_protocol_sha256_v3": next(c for c in entry.cohorts if c.name == "v3").pooled_protocol_sha256,
        "pooled_protocol_sha256_xu": next(c for c in entry.cohorts if c.name == "xu").pooled_protocol_sha256,
        "cohort_sha256_v3": cohort_v3.sha256,
        "cohort_sha256_xu": cohort_xu.sha256,
        "pages_sha256": pages.pages_sha256,
        "calendar_pages": pages.pages,
        "calendar_page_duplicates": pages.duplicates,
        "corporate_actions_sha256_v3": protocol_v3.event_inputs.corporate_actions.content_sha256,
        "corporate_actions_sha256_xu": xu_digest,
        "investor_days_content_sha256": acquisition,
        "events_sha256": table.events_sha256,
        "parser_check_sha256": check_digest,
        "parser_check": check_result,
        "parser_check_precision": check_result["precision"],
        "issuers": {
            "total": len(records),
            "visible": sum(record.visible for record in records),
            "capped": sum(record.capped for record in records),
            "with_doc_failures": sum(bool(record.doc_failures) for record in records),
        },
        "unmapped": list(unmapped),
        "market_symbol": MARKET_SYMBOL,
        "market_daily_source": daily_source,
        "market_files_sha256": market_files,
        "cohort_symbols": len(symbols),
        "calendar_sessions": len(days),
        "calendar_sha256": hashlib.sha256("\n".join(day.isoformat() for day in days).encode()).hexdigest(),
    }
    return dataclasses.replace(inputs, market_daily=market_daily, market_hourly=market_hourly, provenance=provenance)
