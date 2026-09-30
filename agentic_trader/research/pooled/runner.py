# agentic_trader/research/pooled/runner.py
"""Provider access for the pooled lane: trading calendar and SIP bars, then the cached cube.

Called only from inside an executor's ``build`` callable, after its manifest exists.
Adjusted and raw daily bars are fetched once per cohort and static symbol over the whole
span; hourly bars only for symbols with at least one eligible session. Everything reuses
the setup study's immutable ``.npz`` bar cache with its range claim (raw bars in the
sibling ``bars_raw`` directory). The cube is cached next to the bars under a name that
binds it to the cohort and spec, and is verified by hash on load.
"""

from __future__ import annotations

import asyncio
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
_HOURLY_TAIL = timedelta(days=45)


@dataclass(frozen=True)
class CubeBuild:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]
    bar_failures: Mapping[str, str]
    static_used: tuple[str, ...]


async def _bars(symbol, timeframe, bars, cache_dir, start, end, pace, *, adjustment="all", **kwargs):
    try:
        frame = await _fetch_cached(symbol, timeframe, bars, cache_dir, start, end, adjustment, pace, **kwargs)
    except Exception as exc:
        return None, f"{timeframe}/{adjustment}: {type(exc).__name__}: {exc}"
    if frame.empty:
        return None, f"{timeframe}/{adjustment}: empty"
    return frame, None


async def build_cube_inputs(
    cohort: LoadedCohort,
    spec: CubeSpec,
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    static_symbols: Sequence[str],
    pace: Callable[[], Awaitable[None]],
) -> CubeBuild:
    days = await calendar.get_calendar_range(spec.bars_from, spec.bars_through)
    trading_days = tuple(sorted(day.date for day in days if day.is_trading_day))
    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    _claim_cache_range(bar_dir, start, end)
    _claim_cache_range(raw_dir, start, end)

    symbols = tuple(cohort.cohort.symbols)
    adjusted: dict[str, pd.DataFrame] = {}
    raw: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in sorted({*symbols, *static_symbols}):
        frame, failure = await _bars(symbol, "1d", bars, bar_dir, start, end, pace, chunk=end - start)
        if frame is None:
            failures[symbol] = failure or "daily unavailable"
        else:
            adjusted[symbol] = frame
        frame, failure = await _bars(symbol, "1d", bars, raw_dir, start, end, pace, adjustment="raw", chunk=end - start)
        if frame is None:
            failures[symbol] = (
                f"{failures[symbol]}; {failure}" if symbol in failures else (failure or "raw unavailable")
            )
        else:
            raw[symbol] = frame
    static_used = tuple(s for s in static_symbols if s in raw)
    eligibility = await asyncio.to_thread(
        point_in_time_eligibility,
        trading_days,
        spec.decisions,
        symbols,
        adjusted,
        raw,
        {s: raw[s] for s in static_used},
        universe=spec.universe,
        atr_window=spec.bracket.atr_window,
    )

    cube_path = cache_dir / f"cube-{cohort.sha256[:16]}-{spec.identity[:16]}.npz"
    if cube_path.exists():
        cube = await asyncio.to_thread(load_cube, cube_path)
        if cube.spec_identity != spec.identity or cube.cohort_sha256 != cohort.sha256:
            raise ValueError(f"cached cube {cube_path} does not match this cohort/spec")
    else:
        hourly: dict[str, pd.DataFrame] = {}
        for col, symbol in enumerate(symbols):
            rows = eligibility.eligible[:, col].nonzero()[0]
            if rows.size == 0:
                continue
            span_start = datetime.combine(eligibility.sessions[rows[0]], time.min, tzinfo=UTC) - _HOURLY_PAD
            span_end = min(datetime.combine(eligibility.sessions[rows[-1]], time.max, tzinfo=UTC) + _HOURLY_TAIL, end)
            frame, failure = await _bars(symbol, "1h", bars, bar_dir, span_start, span_end, pace)
            if frame is None:
                failures[symbol] = (
                    f"{failures[symbol]}; {failure}" if symbol in failures else (failure or "hourly unavailable")
                )
            else:
                hourly[symbol] = frame
        cube = await asyncio.to_thread(build_cube, eligibility, hourly, spec, cohort_sha256=cohort.sha256)
        await asyncio.to_thread(save_cube, cube, cube_path)
    return CubeBuild(
        cube=cube, trading_days=trading_days, adjusted=adjusted, bar_failures=failures, static_used=static_used
    )
