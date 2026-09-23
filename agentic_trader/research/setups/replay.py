"""Live-clock replay: reconstruct exactly what the live suggestion scan saw.

At each historical suggestion-scan instant (10:35 and 14:35 New York, see
``AppConfig.suggestion_scan_times_et``), this module rebuilds the
``ContractMarketData`` that ``MarketDataFetcher.fetch_data`` would have fetched
at that instant, then runs the unchanged live ``StrategyEngine`` on it. Research
and live must see identical shapes and identical windows -- that parity is the
whole point, so every helper here mirrors a specific piece of live behaviour:

* Hourly and 4-hour windows only ever include bars that had *closed* (bar start
  + 1h) at or before the instant, over the trailing ``LIVE_HOURLY_DAYS``
  calendar days -- matching ``fetch_data(hourly_period="60d")``. The 4-hour
  frame is ``resample_to_4h`` of that exact same clean hourly slice, then
  ``compute_intraday_indicators`` -- never resampled from a wider or narrower
  window.

  **Known parity gap.** Live ``fetch_data`` never drops an unfinished bar: the
  Alpaca hourly endpoint returns the current, still-forming hour, and the
  native strategies read its ``iloc[-1]``. Replay cannot reproduce that bar
  without lookahead (it doesn't close until after ``t``), so it deliberately
  excludes it instead. Concretely, at a 10:35 or 14:35 scan the freshest bar
  replay can use ended at 10:00/14:00 -- replay's last hourly/4h bar, and the
  synthesized daily ``Close`` derived from it (below), are up to ~35 minutes
  staler than what live saw.

* The daily window is ``live_daily_window``: every bar *stamped* in
  ``[t - LIVE_DAILY_LOOKBACK_DAYS, t]`` -- mirroring ``fetch_data(daily_period="1y")``:
  live's ``AlpacaDataProvider.fetch_bars`` calls
  ``providers.parse_period_to_timedelta("1y")`` (365 days), sets
  ``start = now(UTC) - delta`` (see ``data/providers.py``), and Alpaca returns
  bars stamped at or after ``start``; replay uses the identical timedelta with
  ``t`` standing in for "now". A daily bar is stamped at its session's
  midnight, so the session on the cutoff date itself (stamped before
  ``start``) is outside the window, as it is live. Of that window, only
  completed sessions (session date strictly before the instant's New York
  date) are kept. The setup study's feature cross-section (``runner.py``) uses
  the same helper, so research features see exactly the live window. Plus, when any hourly
  bars for that New York date have closed by the instant, one synthesized
  in-progress daily bar aggregated from them (Open=first, High=max, Low=min,
  Close=last, Volume=sum over *every* closed hour, not just regular session
  hours -- extended-hours trades can update Alpaca's daily volume too; see
  docs/alpha-daily-panel.md). This mirrors a documented Alpaca quirk: its
  daily-bar endpoint returns the current day's bar intraday, built from
  whatever trades have happened so far, not the next calendar day's history.
  The synthesized bar is stamped at New York midnight of ``t``'s date,
  converted to ``daily_all``'s own index timezone -- matching Alpaca's actual
  daily-bar timestamp convention (New York midnight; see
  docs/alpha-trade-lifetimes.md) and keeping ``pd.concat`` tz-safe against a
  tz-aware ``daily_all`` (the shape ``providers.py`` actually returns).
  Indicators are computed from scratch on each window every time -- EMA
  seeding depends on the window's start, so nothing here is ever precomputed
  on full history and then sliced.
* An instant is skipped entirely (no scan) unless ``daily_all``'s own history
  starts at or before ``t - LIVE_DAILY_LOOKBACK_DAYS`` -- i.e. unless a full
  live daily window is actually available; a shorter history would silently
  understate the window rather than reproduce live's completed one-year fetch.
* Duplicate suppression is a *research* definition, not a copy of live's
  literal one: live only ever suppresses against a signal it already
  persisted to the database, so a symbol/strategy/timeframe live had already
  suppressed can never re-suppress a later scan the way this replay's
  in-memory ``last_seen`` does. What is shared exactly is the window
  arithmetic from ``TradingCopilot`` (``agent/copilot.py``, ``dedup_hours``):
  a (symbol, strategy, timeframe) that already produced a record within the
  effective window is skipped, where effective hours is ``min(dedup_hours,
  4)`` for 1h candidates and ``min(dedup_hours, 2)`` for 15m candidates, else
  the configured ``dedup_hours``; the boundary is inclusive
  (``t - previous <= effective_hours`` suppresses, matching
  ``storage/db.py``'s ``timestamp >= cutoff``).

``replay_symbol`` is a module-level function with picklable arguments so a
``ProcessPoolExecutor`` can later call it once per symbol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd

from agentic_trader.agent.calendar import BaseEconomicCalendar, MacroEvent
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData, MarketDataFetcher
from agentic_trader.data.providers import parse_period_to_timedelta
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.alpha.strategy import entry_limit, execution_policy_from_dict
from agentic_trader.screeners.strategies import StrategyEngine


__all__ = [
    "LIVE_DAILY_LOOKBACK_DAYS",
    "LIVE_HOURLY_DAYS",
    "SetupRecord",
    "decision_instants",
    "frames_at",
    "live_daily_window",
    "replay_symbol",
]

# live fetch_data(daily_period="1y"): mirror providers.parse_period_to_timedelta("1y")
# exactly rather than hardcoding 365, so this stays in lockstep with that arithmetic.
LIVE_DAILY_LOOKBACK_DAYS = parse_period_to_timedelta("1y").days
LIVE_HOURLY_DAYS = 60  # live fetch_data hourly_period="60d" (calendar days back from t)

# Per-timeframe dedup ceilings, mirroring agent/copilot.py's dedup_hours narrowing.
_FIFTEEN_MIN_TIMEFRAMES = frozenset({"15m", "15min", "fifteen_minute"})
_HOURLY_TIMEFRAMES = frozenset({"1h", "hourly"})


@dataclass(frozen=True)
class SetupRecord:
    decision_at: datetime
    symbol: str
    strategy: str
    timeframe: str
    direction: str
    setup_quality: float
    entry: float
    stop: float
    target: float
    atr: float


class _NoIoCalendar(BaseEconomicCalendar):
    """Stub calendar: ``calculate_levels_deterministic`` never reads it, but
    construction must still never risk a network call."""

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        return []


class _NoIoProvider:
    """Stub provider: only ``MarketDataFetcher``'s pure indicator/resample
    methods are ever called on this fetcher; nothing here should be reached."""

    name = "no-io"

    def supports_symbol(self, symbol: str) -> bool:
        raise NotImplementedError("replay never fetches; it only computes indicators on given frames")

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError("replay never fetches; it only computes indicators on given frames")

    def fetch_latest_price(self, symbol: str) -> float | None:
        raise NotImplementedError("replay never fetches; it only computes indicators on given frames")


_FETCHER = MarketDataFetcher(provider=_NoIoProvider())


def decision_instants(days: Sequence[MarketCalendarDay], scan_times_et: Sequence[str]) -> list[datetime]:
    """Every day x scan time, converted to UTC and kept only while the market is open.

    Non-trading days, and any scan time outside ``[open_time, close_time)`` --
    including afternoon scans dropped by an early close -- are excluded.
    """
    instants: list[datetime] = []
    for day in days:
        if not day.is_trading_day or day.open_time is None or day.close_time is None:
            continue
        for hhmm in scan_times_et:
            local_time = time.fromisoformat(hhmm)
            if not (day.open_time <= local_time < day.close_time):
                continue
            local_dt = datetime.combine(day.date, local_time, tzinfo=ET_TZ)
            instants.append(local_dt.astimezone(UTC))
    return instants


def _live_daily_start(t: datetime) -> datetime:
    """``start`` of live's ``fetch_data(daily_period="1y")`` request made at ``t``."""
    return t - timedelta(days=LIVE_DAILY_LOOKBACK_DAYS)


def live_daily_window(daily: pd.DataFrame, t: datetime) -> pd.DataFrame:
    """The rows a live ``fetch_data(daily_period="1y")`` made at ``t`` would return.

    Bars stamped in ``[t - LIVE_DAILY_LOOKBACK_DAYS, t]``, in original order (see the
    module docstring). A tz-naive index is read as UTC, like the provider's output.
    """
    if daily.empty:
        return daily
    stamps = pd.DatetimeIndex(daily.index)
    if stamps.tz is None:
        stamps = stamps.tz_localize(UTC)
    return daily.loc[(stamps >= _live_daily_start(t)) & (stamps <= t)]


def _completed_daily(daily_all: pd.DataFrame, t: datetime) -> pd.DataFrame:
    """``live_daily_window`` at ``t``, minus ``t``'s own (in-progress) New York session.

    Daily bars are stamped by session date (see providers.py normalization),
    so the plain UTC ``.date()`` of the index already is the session date --
    no timezone conversion, matching ``research/setups/features.py``'s
    ``_closed_frame`` convention.
    """
    window = live_daily_window(daily_all, t)
    if window.empty:
        return window
    ny_date = t.astimezone(ET_TZ).date()
    return window.loc[pd.DatetimeIndex(window.index).date < ny_date]


def _ny_midnight(ny_date: date, tz) -> pd.Timestamp:
    """New York midnight for session date ``ny_date``, converted to ``tz`` --
    matching Alpaca's actual daily-bar timestamp convention (see the module
    docstring and docs/alpha-trade-lifetimes.md)."""
    local_midnight = datetime.combine(ny_date, time(0, 0), tzinfo=ET_TZ)
    return pd.Timestamp(local_midnight).tz_convert(tz)


def frames_at(symbol: str, daily_all: pd.DataFrame, hourly_all: pd.DataFrame, t: datetime) -> ContractMarketData:
    """Exactly what live ``fetch_data`` would have produced at instant ``t``."""
    ny_date = t.astimezone(ET_TZ).date()

    if hourly_all.empty:
        hourly_slice = hourly_all
    else:
        hourly_index = pd.DatetimeIndex(hourly_all.index)
        bar_ends = hourly_index + pd.Timedelta(hours=1)
        cutoff_start = t - timedelta(days=LIVE_HOURLY_DAYS)
        mask = (bar_ends <= t) & (hourly_index >= cutoff_start)
        hourly_slice = hourly_all.loc[mask]

    hourly = _FETCHER.compute_intraday_indicators(hourly_slice)
    four_hour = _FETCHER.compute_intraday_indicators(_FETCHER.resample_to_4h(hourly_slice))

    completed = _completed_daily(daily_all, t)

    if hourly_slice.empty:
        today_bars = hourly_slice
    else:
        today_mask = pd.DatetimeIndex(hourly_slice.index).tz_convert(ET_TZ).date == ny_date
        today_bars = hourly_slice.loc[today_mask]

    if today_bars.empty:
        daily_slice = completed
    else:
        daily_index = pd.DatetimeIndex(daily_all.index)
        target_tz = daily_index.tz if not daily_all.empty and daily_index.tz is not None else UTC
        in_progress = pd.DataFrame(
            {
                "Open": [float(today_bars["Open"].iloc[0])],
                "High": [float(today_bars["High"].max())],
                "Low": [float(today_bars["Low"].min())],
                "Close": [float(today_bars["Close"].iloc[-1])],
                "Volume": [float(today_bars["Volume"].sum())],
            },
            index=pd.DatetimeIndex([_ny_midnight(ny_date, target_tz)], name="timestamp"),
        )
        daily_slice = pd.concat([completed, in_progress])

    daily = _FETCHER.compute_daily_indicators(daily_slice)

    return ContractMarketData(contract=symbol, ticker=symbol, daily=daily, four_hour=four_hour, hourly=hourly)


def _effective_dedup_hours(timeframe: str, dedup_hours: int) -> int:
    tf = timeframe.lower()
    if tf in _FIFTEEN_MIN_TIMEFRAMES:
        return min(dedup_hours, 2)
    if tf in _HOURLY_TIMEFRAMES:
        return min(dedup_hours, 4)
    return dedup_hours


def replay_symbol(
    symbol: str,
    daily_all: pd.DataFrame,
    hourly_all: pd.DataFrame,
    instants: Sequence[datetime],
    config: AppConfig,
    dedup_hours: int,
) -> list[SetupRecord]:
    """Replay one symbol's history through the unchanged live strategy engine."""
    engine = StrategyEngine(config)
    evaluator = RiskEvaluator(
        config,
        calendar=_NoIoCalendar(),
        regime_detector=RegimeDetector(config=config.regime),
    )

    records: list[SetupRecord] = []
    last_seen: dict[tuple[str, str, str], datetime] = {}
    daily_index = pd.DatetimeIndex(daily_all.index) if not daily_all.empty else None

    for t in instants:
        if daily_index is None or daily_index.min() > _live_daily_start(t):
            continue

        data = frames_at(symbol, daily_all, hourly_all, t)
        candidates = engine.scan_contract(data, asset_class=AssetClass.EQUITY)

        for candidate in candidates:
            key = (symbol, candidate.strategy, candidate.timeframe)
            effective_hours = _effective_dedup_hours(candidate.timeframe, dedup_hours)
            previous = last_seen.get(key)
            if previous is not None and t - previous <= timedelta(hours=effective_hours):
                continue
            last_seen[key] = t

            levels = evaluator.calculate_levels_deterministic(candidate)
            entry = (
                entry_limit(candidate.current_price, execution_policy_from_dict(candidate.alpha_policy))
                if candidate.alpha_policy
                else candidate.current_price
            )

            records.append(
                SetupRecord(
                    decision_at=t,
                    symbol=symbol,
                    strategy=candidate.strategy,
                    timeframe=candidate.timeframe,
                    direction=candidate.direction,
                    setup_quality=candidate.setup_quality,
                    entry=entry,
                    stop=levels.stop_loss,
                    target=levels.take_profit,
                    atr=candidate.atr_14,
                )
            )

    return records
