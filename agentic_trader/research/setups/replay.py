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
* The daily window is the last ``LIVE_DAILY_SESSIONS`` *completed* sessions
  (session date strictly before the instant's New York date) plus, when any
  hourly bars for that New York date have closed by the instant, one
  synthesized in-progress daily bar aggregated from them (Open=first,
  High=max, Low=min, Close=last, Volume=sum). This mirrors a documented Alpaca
  quirk: its daily-bar endpoint returns the current day's bar intraday, built
  from whatever trades have happened so far, not the next calendar day's
  history. Indicators are computed from scratch on each window every time --
  EMA seeding depends on the window's start, so nothing here is ever
  precomputed on full history and then sliced.
* Duplicate suppression mirrors ``TradingCopilot``'s ``dedup_hours`` rule
  (``agent/copilot.py``): a (symbol, strategy, timeframe) that already produced
  a record within the effective window is skipped, where effective hours is
  ``min(dedup_hours, 4)`` for 1h candidates and ``min(dedup_hours, 2)`` for 15m
  candidates, else the configured ``dedup_hours``.

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
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.alpha.strategy import entry_limit, execution_policy_from_dict
from agentic_trader.screeners.strategies import StrategyEngine


__all__ = [
    "LIVE_DAILY_SESSIONS",
    "LIVE_HOURLY_DAYS",
    "SetupRecord",
    "decision_instants",
    "frames_at",
    "replay_symbol",
]

LIVE_DAILY_SESSIONS = 252  # live fetch_data daily_period="1y"
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


def _completed_daily(daily_all: pd.DataFrame, ny_date: date) -> pd.DataFrame:
    """Sessions strictly before ``ny_date``, in original order.

    Daily bars are stamped by session date (see providers.py normalization),
    so the plain UTC ``.date()`` of the index already is the session date --
    no timezone conversion, matching ``research/setups/features.py``'s
    ``_closed_frame`` convention.
    """
    if daily_all.empty:
        return daily_all
    session_dates = pd.DatetimeIndex(daily_all.index).date
    return daily_all.loc[session_dates < ny_date]


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

    completed = _completed_daily(daily_all, ny_date).tail(LIVE_DAILY_SESSIONS)

    if hourly_slice.empty:
        today_bars = hourly_slice
    else:
        today_mask = pd.DatetimeIndex(hourly_slice.index).tz_convert(ET_TZ).date == ny_date
        today_bars = hourly_slice.loc[today_mask]

    if today_bars.empty:
        daily_slice = completed
    else:
        in_progress = pd.DataFrame(
            {
                "Open": [float(today_bars["Open"].iloc[0])],
                "High": [float(today_bars["High"].max())],
                "Low": [float(today_bars["Low"].min())],
                "Close": [float(today_bars["Close"].iloc[-1])],
                "Volume": [float(today_bars["Volume"].sum())],
            },
            index=pd.DatetimeIndex([pd.Timestamp(ny_date, tz="UTC")], name="timestamp"),
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

    for t in instants:
        ny_date = t.astimezone(ET_TZ).date()
        if len(_completed_daily(daily_all, ny_date)) < LIVE_DAILY_SESSIONS:
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
