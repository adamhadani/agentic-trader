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
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from agentic_trader.research.pooled.cohort import LoadedCohort, point_in_time_eligibility
from agentic_trader.research.pooled.cube import CubeSpec, LabelCube, build_cube, load_cube, save_cube
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached


__all__ = ["CubeBuild", "build_cube_inputs"]

_HOURLY_PAD = timedelta(days=7)
_PROGRESS_SYMBOLS = 25
CIRCUIT_BREAKER = 5  # consecutive raised fetches: the provider is down, stop now
_PROVENANCE = ("bar_failures", "static_used", "static_used_sha256", "bars_sha256", "bar_files")


@dataclass(frozen=True)
class CubeBuild:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]  # the cohort symbols' adjusted daily bars
    bar_failures: Mapping[str, str]  # as stored in the cube's coverage by the run that built it
    static_used: tuple[str, ...]  # likewise


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
) -> CubeBuild:
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

    days = await calendar.get_calendar_range(spec.bars_from, spec.bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))
    say(f"calendar: {len(trading_days)} trading days {spec.bars_from.isoformat()}..{spec.bars_through.isoformat()}")
    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    _claim_cache_range(bar_dir, start, end)
    _claim_cache_range(raw_dir, start, end)

    symbols = tuple(cohort.cohort.symbols)
    acquisition = _Acquisition(bars, pace, say)
    adjusted: dict[str, pd.DataFrame] = {}
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
        acquisition.raise_on_errors()
    else:
        raw: dict[str, pd.DataFrame] = {}
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
        hourly: dict[str, pd.DataFrame] = {}
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
    coverage = cube.coverage
    return CubeBuild(
        cube=cube,
        trading_days=trading_days,
        adjusted={symbol: adjusted[symbol] for symbol in symbols if symbol in adjusted},
        bar_failures=dict(coverage["bar_failures"]),
        static_used=tuple(coverage["static_used"]),
    )
