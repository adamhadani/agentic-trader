# agentic_trader/research/pooled/runner.py
"""Provider access for the pooled lane: trading calendar and SIP bars, then the cached cube.

Called only from inside an executor's ``build`` callable, after its manifest exists.
Adjusted and raw daily bars are fetched once per cohort and static symbol over the whole
span; hourly bars, for every symbol with at least one eligible session, over one span
that depends only on the spec (first decision minus a week through ``bars_through``), so
a cached hourly frame is never too short for a later build. Everything reuses the setup
study's immutable ``.npz`` bar cache with its range claim (raw bars in the sibling
``bars_raw`` directory). The cube is cached next to the bars under a name that binds it
to the cohort and spec, and is verified by hash on load.

One cube is shared by the power check and every study, so its build is strict and leaves a
record. A fetch that **raised** fails the build, but only after every symbol was attempted:
whatever could be cached is cached and a rerun resumes from there. A fetch that returned
an **empty** frame is not an error: the symbol is recorded in ``bar_failures`` and is
ineligible or unlabelled. The cube's coverage report (outside the cube hash) stores those
failures, the static reference set actually used and one digest over every bar cache file
the build read; a cache hit reports those stored values and recomputes nothing.

Checks B and C and the campaign pass ``require_cached=True``: they must run on the cube
power check A built, so a missing cube fails before any provider access instead of
building a new one.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.insider_filings import (
    InsiderFilingStore,
    files_sha256 as insider_files_sha256,
    parse_quarter,
    quarters_between,
)
from agentic_trader.research.pooled.campaign import EventInputs, InsiderInputs, ShortInputs
from agentic_trader.research.pooled.cohort import LoadedCohort, point_in_time_eligibility
from agentic_trader.research.pooled.cube import CubeSpec, LabelCube, build_cube, load_cube, save_cube
from agentic_trader.research.pooled.events import (
    EventFrames,
    LoadedCalendar,
    build_event_frames,
    load_actions,
    load_calendar_pages,
    verify_event_sources,
)
from agentic_trader.research.pooled.insider_fields import (
    InsiderFrames,
    LoadedInsiderFilings,
    build_insider_frames,
    load_insider_filings,
    verify_insider_sources,
)
from agentic_trader.research.pooled.short_fields import (
    LoadedShortVolume,
    ShortFrames,
    build_short_frames,
    load_short_volume,
    verify_short_sources,
)
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _fetch_cached, claim_cache_range
from agentic_trader.research.short_volume import ShortVolumeStore, files_sha256, read_sessions


__all__ = [
    "CubeBuild",
    "EventSources",
    "InsiderSources",
    "ShortSources",
    "build_cube_inputs",
    "load_event_sources",
    "load_insider_sources",
    "load_short_sources",
    "verify_insider_digests",
    "verify_short_digests",
]

_HOURLY_PAD = timedelta(days=7)
_PROGRESS_SYMBOLS = 25
CIRCUIT_BREAKER = 5  # consecutive raised fetches: the provider is down, stop now
_PROVENANCE = ("bar_failures", "static_used", "static_used_sha256", "bars_sha256", "bar_files")


@dataclass(frozen=True)
class EventSources:
    earnings_pages: Path  # the Nasdaq page directory
    actions_file: Path  # the corporate-actions acquisition's actions.json


def load_event_sources(
    inputs: EventInputs, sources: EventSources
) -> tuple[LoadedCalendar, tuple[CorporateAction, ...]]:
    """Read both event sources from disk and fail closed unless their digests are the protocol's."""
    calendar_pages = load_calendar_pages(
        sources.earnings_pages, inputs.earnings.calendar_from, inputs.earnings.calendar_through
    )
    actions, actions_digest = load_actions(sources.actions_file)
    verify_event_sources(inputs, calendar_pages, actions_digest)
    return calendar_pages, actions


@dataclass(frozen=True)
class ShortSources:
    short_volume_dir: Path  # the FINRA Reg SHO daily-file directory (a ShortVolumeStore)
    actions_file: Path  # the corporate-actions acquisition's actions.json


def load_short_sources(
    inputs: ShortInputs, sources: ShortSources, trading_days: Sequence[date], symbols: Sequence[str]
) -> tuple[LoadedShortVolume, tuple[CorporateAction, ...]]:
    """Read both short-volume sources from disk and fail closed unless their digests are the protocol's.

    ``trading_days`` is the full calendar; only the sessions in ``[files_from, files_through]`` are read,
    and only the rows of ``symbols`` (the cohort) are kept.
    """
    window = inputs.short_volume
    sessions = [day for day in trading_days if window.files_from <= day <= window.files_through]
    loaded = load_short_volume(sources.short_volume_dir, window, sessions, symbols)
    actions, actions_digest = load_actions(sources.actions_file)
    verify_short_sources(inputs, loaded, actions_digest)
    return loaded, actions


def verify_short_digests(inputs: ShortInputs, sources: ShortSources) -> None:
    """Fail closed unless the files on disk and the actions carry the digests the protocol pins.

    A calendar-free pre-ledger check over every session the store holds inside ``[files_from,
    files_through]``: each present body is read and verified against its meta file, then the files digest
    (present and absent sessions, the acquisition's rule) is compared. The full load (with the calendar)
    still runs inside the build.
    """
    window = inputs.short_volume
    store = ShortVolumeStore(sources.short_volume_dir)
    held = (date.fromisoformat(path.name.removesuffix(".meta.json")) for path in store.directory.glob("*.meta.json"))
    sessions = sorted(day for day in held if window.files_from <= day <= window.files_through)
    for _ in read_sessions(store, sessions):
        pass  # each present body is hashed against its meta file; a mismatch raises
    if files_sha256(store, sessions) != window.files_sha256:
        raise ValueError("short files digest does not match the protocol's short_inputs.short_volume.files_sha256")
    _, actions_digest = load_actions(sources.actions_file)
    if actions_digest != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's short_inputs.corporate_actions.content_sha256"
        )


@dataclass(frozen=True)
class InsiderSources:
    insider_filings_dir: Path  # the SEC Form 3/4/5 archive directory (an InsiderFilingStore)
    actions_file: Path  # the corporate-actions acquisition's actions.json


def load_insider_sources(
    inputs: InsiderInputs, sources: InsiderSources, symbols: Sequence[str]
) -> tuple[LoadedInsiderFilings, tuple[CorporateAction, ...]]:
    """Read both insider sources from disk and fail closed unless their digests are the protocol's.

    Only the transactions of ``symbols`` (the cohort) are kept; no calendar is needed.
    """
    loaded = load_insider_filings(sources.insider_filings_dir, inputs.insider_filings, symbols)
    actions, actions_digest = load_actions(sources.actions_file)
    verify_insider_sources(inputs, loaded, actions_digest)
    return loaded, actions


def verify_insider_digests(inputs: InsiderInputs, sources: InsiderSources) -> None:
    """Fail closed unless the archives, the ticker map and the actions carry the digests the protocol pins.

    Reads every archive of ``[quarters_from, quarters_through]`` and the ticker map through the store (each
    body is hashed against its meta file; a missing quarter or map fails), then compares the files digest
    and the actions digest.
    """
    window = inputs.insider_filings
    store = InsiderFilingStore(sources.insider_filings_dir)
    quarters = quarters_between(parse_quarter(window.quarters_from), parse_quarter(window.quarters_through))
    for quarter in quarters:
        if store.load(quarter) is None:
            raise ValueError(f"insider archive for {quarter} is missing")
    if store.load_tickers() is None:
        raise ValueError("insider ticker map (company_tickers.json) is missing")
    if insider_files_sha256(store, quarters) != window.files_sha256:
        raise ValueError(
            "insider files digest does not match the protocol's insider_inputs.insider_filings.files_sha256"
        )
    _, actions_digest = load_actions(sources.actions_file)
    if actions_digest != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's insider_inputs.corporate_actions.content_sha256"
        )


@dataclass(frozen=True)
class CubeBuild:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]  # the cohort symbols' adjusted daily bars
    bar_failures: Mapping[str, str]  # as stored in the cube's coverage by the run that built it
    static_used: tuple[str, ...]  # likewise
    # The cohort symbols' hourly bars the cube was built from; empty unless ``load_hourly`` was asked.
    hourly: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    # The cohort's raw (unadjusted) daily bars; filled only when event, short or insider fields are built.
    raw: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    events: EventFrames | None = None
    short: ShortFrames | None = None
    insider: InsiderFrames | None = None


def _record(target: dict[str, str], symbol: str, reason: str) -> None:
    target[symbol] = f"{target[symbol]}; {reason}" if symbol in target else reason


def _lines_sha256(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def _verify_bar_files(coverage: Mapping, bar_dir: Path, raw_dir: Path) -> str:
    """Re-check, by name, every bar cache file a cached cube was built from; returns a progress note.

    Cache files are named by their content digest and the shared cache loads the first by
    name, so the same first name for every listed (symbol, timeframe, adjustment) means the
    same bars. A cube built before the list was recorded is loaded with a note.
    """
    listed = coverage.get("bar_file_list")
    if listed is None:
        return "no bar file list recorded (a cube built before Part 1b); bar files not re-checked"
    if _lines_sha256(listed) != coverage["bars_sha256"]:
        raise ValueError("the cached cube's bar file list does not match its bars_sha256; delete it and rebuild")
    changed = []
    for entry in listed:
        symbol, timeframe, adjustment, name = entry.split("|")
        folder = (raw_dir if adjustment == "raw" else bar_dir) / f"{symbol}_{timeframe}"
        current = min((path.name for path in folder.glob("*.npz")), default=None)
        if current != name:
            changed.append(f"{symbol} {timeframe}/{adjustment}")
    if changed:
        raise ValueError(
            f"the cached cube was built from bar files that changed since ({len(changed)}: "
            f"{', '.join(changed[:5])}); delete it and rebuild"
        )
    return f"bar files re-checked: {len(listed)} unchanged"


class _Acquisition:
    """Fetches through the shared bar cache, keeping a raised fetch apart from an empty frame."""

    def __init__(self, bars: BarSource, pace: Callable[[], Awaitable[None]], say: Callable[[str], None]):
        self._bars = bars
        self._pace = pace
        self._say = say
        self._consecutive = 0
        self.errors: dict[str, str] = {}  # a fetch raised: fatal once every symbol was attempted
        self.empty: dict[str, str] = {}  # the provider has no bars: recorded, never fatal
        self.files: list[str] = []  # symbol|timeframe|adjustment|<content-digest cache file name>

    async def fetch(
        self, symbol: str, timeframe: str, cache_dir: Path, start: datetime, end: datetime, *, adjustment: str = "all"
    ) -> pd.DataFrame | None:
        label = f"{timeframe}/{adjustment}"
        # Daily bars fit one request; hourly bars use the shared one-year chunks.
        kwargs = {"chunk": end - start} if timeframe == "1d" else {}
        try:
            frame = await _fetch_cached(
                symbol, timeframe, self._bars, cache_dir, start, end, adjustment, self._pace, **kwargs
            )
            # The file the shared cache loads for this symbol; its name is its content digest.
            name = min(path.name for path in (cache_dir / f"{symbol}_{timeframe}").glob("*.npz"))
        except Exception as exc:
            reason = f"{label}: {type(exc).__name__}: {exc}"
            _record(self.errors, symbol, reason)
            self._say(f"fetch failed: {symbol} {reason}")
            self._consecutive += 1
            # Each failing fetch already spent its retries (~25 s); a run of them means the
            # provider is down, so stop instead of trying every remaining symbol.
            if self._consecutive >= CIRCUIT_BREAKER:
                raise ValueError(
                    f"provider unavailable: {self._consecutive} consecutive fetches failed "
                    f"(last: {symbol} {reason}); rerun to resume from the cache"
                ) from exc
            return None
        self._consecutive = 0
        self.files.append(f"{symbol}|{timeframe}|{adjustment}|{name}")
        if frame.empty:
            _record(self.empty, symbol, f"{label}: empty")
            return None
        return frame

    def raise_on_errors(self) -> None:
        if not self.errors:
            return
        listed = "; ".join(f"{symbol} ({reason})" for symbol, reason in sorted(self.errors.items()))
        noun = "symbol" if len(self.errors) == 1 else "symbols"
        raise ValueError(
            f"bar acquisition failed for {len(self.errors)} {noun}: {listed}; rerun to resume from the cache"
        )


async def build_cube_inputs(
    cohort: LoadedCohort,
    spec: CubeSpec,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
    require_cached: bool = False,
    load_hourly: bool = False,
    events: tuple[EventInputs, EventSources] | None = None,
    short: tuple[ShortInputs, ShortSources] | None = None,
    insider: tuple[InsiderInputs, InsiderSources] | None = None,
) -> CubeBuild:
    if sum(source is not None for source in (events, short, insider)) > 1:
        raise ValueError("a build carries one field source, not several: pass one of events=, short= or insider=")

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    cube_path = cache_dir / f"cube-{cohort.sha256[:16]}-{spec.identity[:16]}.npz"
    if require_cached and not cube_path.exists():
        raise ValueError(
            f"no cached cube {cube_path} for this cohort and spec; run alpha pooled power first "
            "with the same --cache (it builds the cube every later check must share)"
        )

    def counted(phase: str, count: int, total: int) -> None:
        if count % _PROGRESS_SYMBOLS == 0 or count == total:
            say(f"{phase} {count}/{total} symbols")

    # Fail closed on the pinned digests before any provider access.
    event_sources = None
    if events is not None:
        inputs, sources = events
        calendar_pages, actions = await asyncio.to_thread(load_event_sources, inputs, sources)
        event_sources = (inputs, calendar_pages, actions)
        say(f"event fields: {calendar_pages.pages} calendar pages, {len(actions)} corporate actions")

    insider_sources = None
    if insider is not None:
        insider_inputs, insider_where = insider
        loaded_insider, insider_actions = await asyncio.to_thread(
            load_insider_sources, insider_inputs, insider_where, tuple(cohort.cohort.symbols)
        )
        insider_sources = (insider_inputs, loaded_insider, insider_actions)
        say(
            f"insider fields: {loaded_insider.rows} transactions over {len(loaded_insider.quarters)} quarters, "
            f"{len(insider_actions)} corporate actions"
        )

    days = await calendar.get_calendar_range(spec.bars_from, spec.bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))
    say(f"calendar: {len(trading_days)} trading days {spec.bars_from.isoformat()}..{spec.bars_through.isoformat()}")
    # The calendar is the only provider access before this check: it fixes the sessions the files must cover.
    short_sources = None
    if short is not None:
        short_inputs, short_where = short
        loaded_short, short_actions = await asyncio.to_thread(
            load_short_sources, short_inputs, short_where, trading_days, tuple(cohort.cohort.symbols)
        )
        short_sources = (short_inputs, loaded_short, short_actions)
        say(
            f"short fields: {loaded_short.rows} rows over {len(loaded_short.sessions)} files "
            f"({len(loaded_short.absent)} absent), {len(short_actions)} corporate actions"
        )
    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    claim_cache_range(bar_dir, start, end)
    claim_cache_range(raw_dir, start, end)

    symbols = tuple(cohort.cohort.symbols)
    acquisition = _Acquisition(bars, pace, say)
    adjusted: dict[str, pd.DataFrame] = {}
    hourly: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    if cube_path.exists():
        cube = await asyncio.to_thread(load_cube, cube_path)
        if cube.spec_identity != spec.identity or cube.cohort_sha256 != cohort.sha256:
            raise ValueError(f"cached cube {cube_path} does not match this cohort/spec")
        missing = [key for key in _PROVENANCE if key not in cube.coverage]
        if missing:
            raise ValueError(f"cached cube {cube_path} records no build provenance ({missing}); delete it and rebuild")
        say(_verify_bar_files(cube.coverage, bar_dir, raw_dir))
        say(f"cube cache hit: {cube_path.name} (sha256 {cube.sha256[:16]}); loading the cohort's adjusted daily bars")
        # Eligibility, raw and hourly bars belong to the build; a study still scores on adjusted bars.
        for count, symbol in enumerate(symbols, start=1):
            frame = await acquisition.fetch(symbol, "1d", bar_dir, start, end)
            if frame is not None:
                adjusted[symbol] = frame
            counted("daily bars", count, len(symbols))
        if load_hourly:
            # The span the build fetched (spec-only), so the bar cache serves it without the provider.
            hourly_start = max(start, datetime.combine(spec.decisions[0], time.min, tzinfo=UTC) - _HOURLY_PAD)
            # Only symbols with an eligible cell were fetched by the build; others have no cache files.
            eligible_any = cube.window(cube.sessions[0], cube.sessions[-1]).eligible.any(axis=0)
            wanted = [symbol for symbol, flag in zip(cube.symbols, eligible_any, strict=True) if flag]
            for count, symbol in enumerate(wanted, start=1):
                frame = await acquisition.fetch(symbol, "1h", bar_dir, hourly_start, end)
                if frame is not None:
                    hourly[symbol] = frame
                counted("hourly bars", count, len(wanted))
        if event_sources is not None or short_sources is not None or insider_sources is not None:
            # Served from the raw bar cache the build filled; no provider access.
            for count, symbol in enumerate(symbols, start=1):
                frame = await acquisition.fetch(symbol, "1d", raw_dir, start, end, adjustment="raw")
                if frame is not None:
                    raw[symbol] = frame
                counted("raw bars", count, len(symbols))
        acquisition.raise_on_errors()
    else:
        everyone = sorted({*symbols, *static_symbols})
        for count, symbol in enumerate(everyone, start=1):
            frame = await acquisition.fetch(symbol, "1d", bar_dir, start, end)
            if frame is not None:
                adjusted[symbol] = frame
            frame = await acquisition.fetch(symbol, "1d", raw_dir, start, end, adjustment="raw")
            if frame is not None:
                raw[symbol] = frame
            counted("daily bars", count, len(everyone))
        static_used = sorted(symbol for symbol in set(static_symbols) if symbol in raw)
        say(f"eligibility: start ({len(symbols)} symbols, {len(static_used)} static reference names)")
        eligibility = await asyncio.to_thread(
            point_in_time_eligibility,
            trading_days,
            spec.decisions,
            symbols,
            adjusted,
            raw,
            {symbol: raw[symbol] for symbol in static_used},
            universe=spec.universe,
            atr_window=spec.bracket.atr_window,
            progress=progress,
        )
        wanted = [symbol for col, symbol in enumerate(symbols) if eligibility.eligible[:, col].any()]
        eligible_cells = int(eligibility.eligible.sum())
        say(
            f"eligibility: done ({eligible_cells} eligible cells over {len(eligibility.sessions)} sessions; "
            f"{len(wanted)} symbols need hourly bars)"
        )
        # One span for every symbol, whatever its own eligible sessions are.
        hourly_start = max(start, datetime.combine(spec.decisions[0], time.min, tzinfo=UTC) - _HOURLY_PAD)
        for count, symbol in enumerate(wanted, start=1):
            frame = await acquisition.fetch(symbol, "1h", bar_dir, hourly_start, end)
            if frame is not None:
                hourly[symbol] = frame
            counted("hourly bars", count, len(wanted))
        # Nothing is labelled or saved from bars known to be incomplete.
        acquisition.raise_on_errors()
        provenance = {
            "bar_failures": dict(sorted(acquisition.empty.items())),
            "static_used": static_used,
            "static_used_sha256": _lines_sha256(static_used),
            "bars_sha256": _lines_sha256(acquisition.files),
            "bar_files": len(acquisition.files),
            "bar_file_list": sorted(acquisition.files),
        }
        say(f"cube build: start ({eligible_cells} eligible cells, {len(hourly)} symbols with hourly bars)")
        cube = await asyncio.to_thread(
            build_cube, eligibility, hourly, spec, cohort_sha256=cohort.sha256, provenance=provenance, progress=progress
        )
        await asyncio.to_thread(save_cube, cube, cube_path)
        labelled_cells = sum(cube.coverage["labelled_by_year"].values())
        say(f"cube saved: {labelled_cells} labelled of {eligible_cells} eligible cells -> {cube_path.name}")
    event_frames = None
    if event_sources is not None:
        inputs, calendar_pages, actions = event_sources
        benchmark = inputs.earnings.benchmark
        # The benchmark's bars must be a file the cube was built from (re-checked above on a cache hit).
        if not any(entry.startswith(f"{benchmark}|1d|all|") for entry in cube.coverage.get("bar_file_list") or ()):
            raise ValueError(
                f"the event benchmark {benchmark} is not among the bar files the cube was built from; "
                "build the cube with it as a static reference name"
            )
        with_benchmark = dict(adjusted)
        if benchmark not in with_benchmark:
            frame = await acquisition.fetch(benchmark, "1d", bar_dir, start, end)
            if frame is not None:
                with_benchmark[benchmark] = frame
            acquisition.raise_on_errors()
        # Only names with an eligible cell can be picked, so only they get frames and count in coverage.
        eligible_any = cube.window(cube.sessions[0], cube.sessions[-1]).eligible.any(axis=0)
        scored = [symbol for symbol, flag in zip(cube.symbols, eligible_any, strict=True) if flag]
        event_frames = await asyncio.to_thread(
            build_event_frames,
            inputs,
            calendar_pages,
            actions,
            with_benchmark,
            {symbol: raw[symbol] for symbol in scored if symbol in raw},
            trading_days,
            scored,
        )
        say(
            f"event fields: {len(event_frames.frames)} symbols with an eligible cell; "
            f"{len(event_frames.coverage.symbols_without_reports)} without reports, "
            f"{len(event_frames.coverage.identity_breaks)} identity breaks"
        )
    short_frames = None
    if short_sources is not None:
        short_inputs, loaded_short, short_actions = short_sources
        # Only names with an eligible cell can be picked, so only they get frames and count in coverage.
        eligible_any = cube.window(cube.sessions[0], cube.sessions[-1]).eligible.any(axis=0)
        scored = [symbol for symbol, flag in zip(cube.symbols, eligible_any, strict=True) if flag]
        short_frames = await asyncio.to_thread(
            build_short_frames,
            short_inputs,
            loaded_short,
            short_actions,
            {symbol: raw[symbol] for symbol in scored if symbol in raw},
            trading_days,
            scored,
        )
        say(
            f"short fields: {len(short_frames.frames)} symbols with an eligible cell; "
            f"{len(short_frames.coverage.symbols_without_rows)} without rows, "
            f"{len(short_frames.coverage.identity_breaks)} identity breaks"
        )
    insider_frames = None
    if insider_sources is not None:
        insider_inputs, loaded_insider, insider_actions = insider_sources
        # Only names with an eligible cell can be picked, so only they get frames and count in coverage.
        eligible_any = cube.window(cube.sessions[0], cube.sessions[-1]).eligible.any(axis=0)
        scored = [symbol for symbol, flag in zip(cube.symbols, eligible_any, strict=True) if flag]
        insider_frames = await asyncio.to_thread(
            build_insider_frames,
            insider_inputs,
            loaded_insider,
            insider_actions,
            {symbol: raw[symbol] for symbol in scored if symbol in raw},
            trading_days,
            scored,
        )
        say(
            f"insider fields: {len(insider_frames.frames)} symbols with an eligible cell; "
            f"{len(insider_frames.coverage.symbols_without_rows)} without rows, "
            f"{len(insider_frames.coverage.identity_breaks)} identity breaks"
        )
    coverage = cube.coverage
    return CubeBuild(
        cube=cube,
        trading_days=trading_days,
        adjusted={symbol: adjusted[symbol] for symbol in symbols if symbol in adjusted},
        bar_failures=dict(coverage["bar_failures"]),
        static_used=tuple(coverage["static_used"]),
        hourly={symbol: hourly[symbol] for symbol in symbols if symbol in hourly} if load_hourly else {},
        raw={symbol: raw[symbol] for symbol in symbols if symbol in raw}
        if event_sources is not None or short_sources is not None or insider_sources is not None
        else {},
        events=event_frames,
        short=short_frames,
        insider=insider_frames,
    )
