"""Input assembly for the insider study: the pooled protocol's cached cube and its pinned insider sources.

Called only from inside ``execute_insider_study``'s ``build`` callable, after the manifest exists. The
entry pins the pooled protocol by SHA-256 (checked on the bytes before any other work) and the cube by
content hash (checked here and again by the executor). The cube must already be cached by the pooled
power check; the only provider access is the calendar read ``build_cube_inputs`` makes.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from agentic_trader.research.apriori.catalog import InsiderEntry
from agentic_trader.research.apriori.insider_study import InsiderLegInputs
from agentic_trader.research.insider_filings import InsiderFilingStore, symbol_ciks
from agentic_trader.research.pooled.campaign import InsiderCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.insider_fields import dollar_volume_series, plausible_transactions
from agentic_trader.research.pooled.runner import InsiderSources, build_cube_inputs, load_insider_sources
from agentic_trader.research.setups.runner import BarSource, CalendarSource


__all__ = ["build_insider_leg_inputs"]


async def build_insider_leg_inputs(
    entry: InsiderEntry,
    *,
    repo_root: Path,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    insider_filings_dir: Path,
    actions_file: Path,
    static_symbols: Sequence[str] = (),
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
) -> InsiderLegInputs:
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
    if not isinstance(protocol, InsiderCampaignProtocol):
        raise TypeError(f"{entry.pooled_protocol} is a {protocol.id} protocol, not an insider protocol")
    spec = protocol.cube_spec()
    entry.check_against(spec)
    cohort = await asyncio.to_thread(load_cohort, repo_root / protocol.cohort)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError("the cohort file does not match the pooled protocol's cohort_sha256")
    symbols = tuple(cohort.cohort.symbols)
    sources = InsiderSources(insider_filings_dir=insider_filings_dir, actions_file=actions_file)
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
        insider=(protocol.insider_inputs, sources),
    )
    cube_sha = await asyncio.to_thread(lambda: build.cube.sha256)
    if cube_sha != entry.cube_sha256:
        raise ValueError(f"the cached cube has SHA-256 {cube_sha}, but the entry pins cube_sha256 {entry.cube_sha256}")

    inputs = protocol.insider_inputs
    loaded_filings, actions = await asyncio.to_thread(load_insider_sources, inputs, sources, symbols)
    days = tuple(build.trading_days)
    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    max_trade_rel = inputs.insider_filings.max_trade_rel
    transactions: dict[str, tuple[Any, ...]] = {}
    implausible = 0
    for symbol in symbols:
        kept, dropped = plausible_transactions(
            loaded_filings.by_symbol.get(symbol, ()),
            ordinals,
            dollar_volume_series(build.raw.get(symbol), days),
            max_trade_rel,
        )
        transactions[symbol] = tuple(kept)
        implausible += dropped
    tickers = await asyncio.to_thread(InsiderFilingStore(insider_filings_dir).load_tickers)
    if tickers is None:
        raise ValueError(f"no verified ticker map under {insider_filings_dir}")
    ciks, _without = symbol_ciks(tickers, symbols)
    issuer_of = {symbol: cik for cik, group in ciks.items() for symbol in group}
    return InsiderLegInputs(
        cube=build.cube,
        trading_days=days,
        adjusted=build.adjusted,
        transactions=transactions,
        identity_breaks=identity_breaks(actions, symbols),
        issuer_of=issuer_of,
        decision_time=spec.bracket.decision_clock(),
        provenance={
            "cube_sha256": cube_sha,
            "spec_identity": spec.identity,
            "pooled_protocol_sha256": digest,
            "cohort_sha256": cohort.sha256,
            "insider_files_sha256": loaded_filings.files_sha256,
            "corporate_actions_sha256": inputs.corporate_actions.content_sha256,
            "loader_drops": dict(loaded_filings.drops),
            "transactions": sum(len(v) for v in transactions.values()),
            "implausible_size": implausible,
            "max_trade_rel": max_trade_rel,
        },
    )
