"""Data acquisition runner: fetch/cache bars, replay setups, and lazily build the
development/holdout feature frames for the setup-outcome study.

Bar fetching and replay (setups without labels) happen eagerly in
``build_setup_frames``. Labelling -- the only step that touches ``label_bracket`` --
happens exclusively inside the two callables it returns: ``study.execute_setup_study``
must never be able to observe a holdout label before ``ranker.json``/``selection.json``
are saved, so nothing here computes a label before the caller actually invokes
``development()``/``holdout()``.
"""

from __future__ import annotations

import asyncio
import bisect
import sys
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from agentic_trader.config import AppConfig
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.alpha.data import load_dataset, save_dataset
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.research.setups.features import cross_section, setup_vector
from agentic_trader.research.setups.labels import SetupLevels, label_bracket
from agentic_trader.research.setups.replay import SetupRecord, decision_instants, replay_symbol
from agentic_trader.research.setups.study import SetupStudyProtocol


__all__ = ["BarSource", "CalendarSource", "build_setup_frames"]

# Padding before development[0] so a prior trading session is always resolvable
# for the very first decision date's cross-section ``as_of``.
_CALENDAR_PAD_DAYS = 20
# Client-side pacing fallback when the shared market-data pacer cannot be constructed.
_FETCH_SLEEP_SECONDS = 0.35
# Daily window mirrors the live suggestion scan's period="1y" fetch (see replay.py);
# fetch well before that so the earliest development decision has a mature window.
_BARS_LOOKBACK_DAYS = 400

_FRAME_METADATA_COLUMNS: tuple[str, ...] = (
    "decision_at",
    "session",
    "symbol",
    "strategy",
    "timeframe",
    "direction",
    "hit",
    "r",
    "r_cost",
)


class BarSource(Protocol):
    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, adjustment: str
    ) -> pd.DataFrame: ...


class CalendarSource(Protocol):
    async def get_calendar_range(self, start_date: date, end_date: date) -> list[MarketCalendarDay]: ...


def _progress(message: str) -> None:
    print(f"[setup-study] {message}", file=sys.stderr, flush=True)


def _build_pacer(config: AppConfig) -> Callable[[], Awaitable[None]]:
    """The shared market-data request pacer if constructible, else a fixed sleep."""
    try:
        pacer: RequestPacer | None = RequestPacer(max(1, config.market_data.max_requests_per_minute))
    except Exception:
        pacer = None

    async def pace() -> None:
        if pacer is not None:
            await asyncio.to_thread(pacer.acquire)
        else:
            await asyncio.sleep(_FETCH_SLEEP_SECONDS)

    return pace


async def _fetch_cached(
    symbol: str,
    timeframe: str,
    bars: BarSource,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    adjustment: str,
    pace: Callable[[], Awaitable[None]],
) -> pd.DataFrame:
    # One immutable .npz artifact per (symbol, timeframe) via the research dataset
    # adapter (no pickles). Provider attrs are not cached: the study reads only OHLCV.
    cache_path = cache_dir / f"{symbol}_{timeframe}"
    cached = sorted(cache_path.glob("*.npz")) if cache_path.exists() else []
    if cached:
        return await asyncio.to_thread(load_dataset, cached[0])
    await pace()
    frame = await asyncio.to_thread(bars.fetch_bars, symbol, timeframe, start, end, adjustment=adjustment)
    plain = frame.copy()
    plain.attrs = {}
    await asyncio.to_thread(save_dataset, plain, cache_path, frame_digest(plain))
    return frame


async def _fetch_symbol(
    symbol: str,
    bars: BarSource,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    adjustment: str,
    pace: Callable[[], Awaitable[None]],
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, str | None]:
    """Fetch both timeframes; a symbol missing either one is reported, never raised."""
    daily: pd.DataFrame | None = None
    hourly: pd.DataFrame | None = None
    reasons: list[str] = []

    try:
        daily = await _fetch_cached(symbol, "1d", bars, cache_dir, start, end, adjustment, pace)
        if daily.empty:
            reasons.append("empty daily bars")
            daily = None
    except Exception as exc:
        reasons.append(f"daily fetch failed: {type(exc).__name__}: {exc}")

    try:
        hourly = await _fetch_cached(symbol, "1h", bars, cache_dir, start, end, adjustment, pace)
        if hourly.empty:
            reasons.append("empty hourly bars")
            hourly = None
    except Exception as exc:
        reasons.append(f"hourly fetch failed: {type(exc).__name__}: {exc}")

    return daily, hourly, ("; ".join(reasons) if reasons else None)


def _replay_all(
    symbols: Sequence[str],
    daily_by_symbol: dict[str, pd.DataFrame],
    hourly_by_symbol: dict[str, pd.DataFrame],
    instants: list[datetime],
    config: AppConfig,
    dedup_hours: int,
    max_workers: int,
) -> list[SetupRecord]:
    records: list[SetupRecord] = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                replay_symbol,
                symbol,
                daily_by_symbol[symbol],
                hourly_by_symbol[symbol],
                instants,
                config,
                dedup_hours,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            records.extend(future.result())
    return records


def _previous_trading_day(trading_days: list[date], ny_date: date) -> date | None:
    """The latest date in ``trading_days`` strictly before ``ny_date``, or None."""
    idx = bisect.bisect_left(trading_days, ny_date)
    if idx == 0:
        return None
    return trading_days[idx - 1]


def _window_records(records: Sequence[SetupRecord], start: date, end: date) -> list[SetupRecord]:
    return [record for record in records if start <= record.decision_at.astimezone(ET_TZ).date() <= end]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_FRAME_METADATA_COLUMNS))


def _build_frame(
    records: Sequence[SetupRecord],
    daily_by_symbol: dict[str, pd.DataFrame],
    hourly_by_symbol: dict[str, pd.DataFrame],
    sectors: dict[str, str],
    protocol: SetupStudyProtocol,
    trading_days: list[date],
) -> pd.DataFrame:
    """Features + both cost levels' bracket outcomes, for one window's records.

    One ``CrossSection`` is computed per decision date and reused for every setup
    on that date; label_bracket is only ever called from here, i.e. only when the
    caller (``development()``/``holdout()``) actually runs this.
    """
    if not records:
        return _empty_frame()

    zero_cost = protocol.cost_bps_per_side[0]
    primary_cost = protocol.cost_bps_per_side[-1]

    by_date: dict[date, list[SetupRecord]] = {}
    for record in records:
        by_date.setdefault(record.decision_at.astimezone(ET_TZ).date(), []).append(record)

    rows: list[dict] = []
    for ny_date in sorted(by_date):
        as_of = _previous_trading_day(trading_days, ny_date)
        if as_of is None:
            continue
        cs = cross_section(daily_by_symbol, sectors, as_of)

        for record in by_date[ny_date]:
            hourly = hourly_by_symbol.get(record.symbol)
            if hourly is None or hourly.empty:
                continue
            try:
                levels = SetupLevels(
                    direction=record.direction, entry=record.entry, stop=record.stop, target=record.target
                )
            except ValueError:
                continue

            outcome_zero = label_bracket(
                levels,
                record.decision_at,
                hourly,
                max_hold_sessions=protocol.max_hold_sessions,
                cost_bps_per_side=zero_cost,
            )
            outcome_primary = (
                outcome_zero
                if primary_cost == zero_cost
                else label_bracket(
                    levels,
                    record.decision_at,
                    hourly,
                    max_hold_sessions=protocol.max_hold_sessions,
                    cost_bps_per_side=primary_cost,
                )
            )

            risk_unit = abs(record.entry - record.stop)
            stop_atr = risk_unit / record.atr if record.atr else float("nan")
            reward_risk = abs(record.target - record.entry) / risk_unit if risk_unit else float("nan")

            row: dict[str, object] = dict(
                setup_vector(
                    cs,
                    symbol=record.symbol,
                    direction=record.direction,
                    strategy=record.strategy,
                    timeframe=record.timeframe,
                    setup_quality=record.setup_quality,
                    stop_atr=stop_atr,
                    reward_risk=reward_risk,
                )
            )
            row.update(
                {
                    "decision_at": record.decision_at,
                    "session": ny_date,
                    "symbol": record.symbol,
                    "strategy": record.strategy,
                    "timeframe": record.timeframe,
                    "direction": record.direction,
                    "hit": outcome_zero.hit.value,
                    "r": outcome_zero.r,
                    "r_cost": outcome_primary.r_cost,
                }
            )
            rows.append(row)

    if not rows:
        return _empty_frame()

    frame = pd.DataFrame(rows)
    # setup_vector only emits the *true* strategy/timeframe dummy per row; a row's
    # other dummies are absent (not zero) once every row is concatenated into one
    # frame, so backfill the rest of the one-hot columns with 0.0 explicitly rather
    # than leaving them NaN for the imputer to guess at.
    onehot_columns = [c for c in frame.columns if c.startswith(("strategy=", "timeframe="))]
    if onehot_columns:
        frame[onehot_columns] = frame[onehot_columns].fillna(0.0)
    return frame


async def build_setup_frames(
    protocol: SetupStudyProtocol,
    universe: Sequence[tuple[str, str]],
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    config: AppConfig,
    *,
    max_workers: int,
) -> tuple[Callable[[], pd.DataFrame], Callable[[], pd.DataFrame], dict]:
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    pace = _build_pacer(config)

    bars_start = datetime.combine(protocol.development[0] - timedelta(days=_BARS_LOOKBACK_DAYS), time.min, tzinfo=UTC)
    bars_end = datetime.combine(protocol.data_cutoff, time.max, tzinfo=UTC)

    sectors: dict[str, str] = {}
    coverage: dict[str, dict] = {}
    daily_by_symbol: dict[str, pd.DataFrame] = {}
    hourly_by_symbol: dict[str, pd.DataFrame] = {}
    included_symbols: list[str] = []

    total = len(universe)
    for i, (symbol, sector) in enumerate(universe, start=1):
        sectors[symbol] = sector
        daily, hourly, reason = await _fetch_symbol(
            symbol, bars, cache_dir, bars_start, bars_end, protocol.adjustment, pace
        )
        included = daily is not None and hourly is not None
        coverage[symbol] = {
            "daily_rows": None if daily is None else len(daily),
            "hourly_rows": None if hourly is None else len(hourly),
            "included": included,
            "reason": reason,
        }
        if included:
            daily_by_symbol[symbol] = daily
            hourly_by_symbol[symbol] = hourly
            included_symbols.append(symbol)
        _progress(f"fetch {i}/{total}")

    calendar_start = protocol.development[0] - timedelta(days=_CALENDAR_PAD_DAYS)
    calendar_end = protocol.holdout[1]
    days = await calendar.get_calendar_range(calendar_start, calendar_end)
    trading_days = sorted(day.date for day in days if day.is_trading_day)
    instant_days = [day for day in days if day.date >= protocol.development[0]]
    instants = decision_instants(instant_days, protocol.scan_times_et)

    dedup_hours = config.risk.deduplication_hours
    records = await asyncio.to_thread(
        _replay_all, included_symbols, daily_by_symbol, hourly_by_symbol, instants, config, dedup_hours, max_workers
    )
    _progress("replay done")

    development_records = _window_records(records, protocol.development[0], protocol.development[1])
    holdout_records = _window_records(records, protocol.holdout[0], protocol.holdout[1])

    def development() -> pd.DataFrame:
        frame = _build_frame(development_records, daily_by_symbol, hourly_by_symbol, sectors, protocol, trading_days)
        _progress("development labelled")
        return frame

    def holdout() -> pd.DataFrame:
        frame = _build_frame(holdout_records, daily_by_symbol, hourly_by_symbol, sectors, protocol, trading_days)
        _progress("holdout labelled")
        return frame

    return development, holdout, coverage
