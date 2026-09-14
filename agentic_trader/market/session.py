from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from agentic_trader.constants import AssetClass


if TYPE_CHECKING:
    from alpaca.trading.client import TradingClient

    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)

ET_TZ = ZoneInfo("America/New_York")

# Standard US Equity Market RTH times (ET)
EQUITY_RTH_OPEN = time(9, 30)
EQUITY_RTH_CLOSE = time(16, 0)

# CME Daily Maintenance Halt (ET)
CME_HALT_START = time(17, 0)
CME_HALT_END = time(18, 0)


class MarketSessionType(StrEnum):
    """Classification of market trading session state."""

    RTH = "RTH"  # Regular Trading Hours (Primary Cash Market)
    ETH = "ETH"  # Extended Trading Hours (Pre/Post Market, Overnight Globex)
    CLOSED = "CLOSED"  # Market completely closed
    DAILY_HALT = "DAILY_HALT"  # Daily settlement/maintenance halt (e.g. CME 17:00-18:00 ET)
    WEEKEND_HALT = "WEEKEND_HALT"  # Weekend market closure
    HOLIDAY_HALT = "HOLIDAY_HALT"  # Market closed or halted for statutory/exchange holiday


def ensure_et(dt: datetime | None = None, target_tz: ZoneInfo = ET_TZ) -> datetime:
    """Normalize any datetime (naive or tz-aware) to the authoritative Eastern Timezone.

    - If dt is None: returns the current time in target_tz.
    - If dt is naive: assumes UTC as standard protocol baseline and converts to target_tz.
    - If dt is tz-aware: converts cleanly to target_tz.
    """
    if dt is None:
        return datetime.now(target_tz)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).astimezone(target_tz)
    return dt.astimezone(target_tz)


class MarketHolidayCalendar:
    """Deterministic, zero-dependency calendar for US exchange (NYSE/NASDAQ) and CME Globex holidays.

    Calculates:
    1. Statutory US Exchange Holidays (observed on Friday/Monday if falling on weekend):
       - New Year's Day (Jan 1)
       - Martin Luther King Jr. Day (3rd Mon in Jan)
       - Washington's Birthday / Presidents' Day (3rd Mon in Feb)
       - Good Friday (Friday before Easter Sunday, computed via Butcher's Algorithm)
       - Memorial Day (Last Mon in May)
       - Juneteenth National Independence Day (June 19)
       - Independence Day (July 4)
       - Labor Day (1st Mon in Sep)
       - Thanksgiving Day (4th Thu in Nov)
       - Christmas Day (Dec 25)
    2. Early Close Days (Cash Equities close at 13:00 ET; CME Equity Indices close at 13:15 ET):
       - Day after Thanksgiving (Black Friday)
       - Christmas Eve (Dec 24, if weekday)
       - July 3rd (if July 4 falls on Thursday or Friday)
    3. CME Globex Holiday Schedules:
       - Christmas Day & New Year's Day: Full holiday halt until 18:00 ET.
       - Good Friday: CME equity index futures halt early at 09:15 ET.
       - Holiday Session Halts (MLK, Presidents', Memorial, Juneteenth, July 4, Labor Day, Thanksgiving):
         Trading halts at 13:00 ET and re-opens at 18:00 ET for the next trading date.
    """

    _cache: ClassVar[dict[int, dict[str, Any]]] = {}

    @classmethod
    def _compute_easter(cls, year: int) -> date:
        """Compute Easter Sunday using the Anonymous Gregorian (Butcher's) algorithm."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l_term = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l_term) // 451
        month = (h + l_term - 7 * m + 114) // 31
        day = ((h + l_term - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    @classmethod
    def _nth_weekday(cls, year: int, month: int, weekday: int, n: int) -> date:
        """Find the n-th occurrence of a weekday in a month (0=Mon, ..., 6=Sun)."""
        d = date(year, month, 1)
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d + timedelta(weeks=n - 1)

    @classmethod
    def _last_weekday(cls, year: int, month: int, weekday: int) -> date:
        """Find the last occurrence of a weekday in a month (0=Mon, ..., 6=Sun)."""
        d = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    @classmethod
    def _observe_holiday(cls, d: date) -> date:
        """Apply standard US federal/exchange observation rules (Sat -> Fri, Sun -> Mon)."""
        if d.weekday() == 5:
            return d - timedelta(days=1)
        elif d.weekday() == 6:
            return d + timedelta(days=1)
        return d

    @classmethod
    def get_year_calendar(cls, year: int) -> dict[str, Any]:
        """Compute and cache all market holidays and early closes for a given year."""
        if year in cls._cache:
            return cls._cache[year]

        easter = cls._compute_easter(year)
        good_friday = easter - timedelta(days=2)

        # Full Statutory Holidays (NYSE / NASDAQ closed all day)
        statutory_holidays: dict[date, str] = {
            cls._observe_holiday(date(year, 1, 1)): "New Year's Day",
            cls._nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
            cls._nth_weekday(year, 2, 0, 3): "Presidents' Day",
            good_friday: "Good Friday",
            cls._last_weekday(year, 5, 0): "Memorial Day",
            cls._observe_holiday(date(year, 6, 19)): "Juneteenth National Independence Day",
            cls._observe_holiday(date(year, 7, 4)): "Independence Day",
            cls._nth_weekday(year, 9, 0, 1): "Labor Day",
            cls._nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
            cls._observe_holiday(date(year, 12, 25)): "Christmas Day",
        }

        # Early Closes (Equity closes at 13:00 ET, CME Equity Indices close at 13:15 ET)
        thanksgiving = cls._nth_weekday(year, 11, 3, 4)
        black_friday = thanksgiving + timedelta(days=1)
        early_closes: dict[date, str] = {black_friday: "Day After Thanksgiving (Black Friday)"}

        christmas_eve = date(year, 12, 24)
        if christmas_eve.weekday() < 5:  # Monday through Friday
            early_closes[christmas_eve] = "Christmas Eve"

        # July 3rd early close (when July 4th falls on Thursday or Friday)
        # SIFMA / NYSE rule: If July 4th is a Thursday or Friday, the preceding business day (July 3rd) closes early (13:00 ET)
        july_4 = date(year, 7, 4)
        if july_4.weekday() in (3, 4):  # Thursday=3, Friday=4
            early_closes[date(year, 7, 3)] = "Day Before Independence Day"

        # CME-specific holiday halt classifications
        # Holidays where CME halts at 13:00 ET and re-opens at 18:00 ET
        cme_1300_halt_days: set[date] = {
            cls._nth_weekday(year, 1, 0, 3),  # MLK Day
            cls._nth_weekday(year, 2, 0, 3),  # Presidents' Day
            cls._last_weekday(year, 5, 0),  # Memorial Day
            cls._observe_holiday(date(year, 6, 19)),  # Juneteenth
            cls._observe_holiday(date(year, 7, 4)),  # Independence Day
            cls._nth_weekday(year, 9, 0, 1),  # Labor Day
            thanksgiving,  # Thanksgiving Day
        }

        # Full day CME closure days (closed until 18:00 ET)
        cme_full_halt_days: set[date] = {
            date(year, 1, 1),
            cls._observe_holiday(date(year, 1, 1)),
            date(year, 12, 25),
            cls._observe_holiday(date(year, 12, 25)),
        }

        cal_data = {
            "statutory_holidays": statutory_holidays,
            "early_closes": early_closes,
            "cme_1300_halt_days": cme_1300_halt_days,
            "cme_full_halt_days": cme_full_halt_days,
            "good_friday": good_friday,
        }
        cls._cache[year] = cal_data
        return cal_data

    @classmethod
    def is_equity_holiday(cls, d: date) -> tuple[bool, str | None]:
        """Return True and holiday name if US equity market is closed all day."""
        cal = cls.get_year_calendar(d.year)
        name = cal["statutory_holidays"].get(d)
        return (name is not None, name)

    @classmethod
    def is_equity_early_close(cls, d: date) -> tuple[bool, str | None]:
        """Return True and session name if US equity market closes early (13:00 ET)."""
        cal = cls.get_year_calendar(d.year)
        name = cal["early_closes"].get(d)
        return (name is not None, name)


@dataclass
class MarketSessionInfo:
    """Detailed snapshot of the trading session state for an instrument."""

    symbol: str
    asset_class: AssetClass
    is_open: bool
    is_rth: bool
    session_type: MarketSessionType
    current_time: datetime
    next_open: datetime | None = None
    next_close: datetime | None = None
    source: str = "default"
    details: str = ""


@runtime_checkable
class MarketSessionProtocol(Protocol):
    """Protocol contract for market session and trading hours providers."""

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        """Return True if market is currently trading (RTH or ETH)."""
        ...

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        """Return True only if market is in Regular Trading Hours."""
        ...

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        """Return complete session snapshot with session type and boundary timestamps."""
        ...


class CryptoSessionProvider:
    """Crypto markets trade continuously 24/7/365 without scheduled halts."""

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        return True

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        return True

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        now = timestamp or datetime.now(UTC)
        return MarketSessionInfo(
            symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            is_open=True,
            is_rth=True,
            session_type=MarketSessionType.RTH,
            current_time=now,
            next_open=None,
            next_close=None,
            source="crypto_24_7",
            details="Continuous 24/7/365 crypto market",
        )


class CMEFuturesSessionProvider:
    """Deterministic session provider for CME Globex futures (/MES, /MNQ, /MGC, /MCL).

    Schedule:
    - Opens Sunday at 18:00 ET, closes Friday at 17:00 ET.
    - Daily maintenance halt: Monday through Thursday 17:00 - 18:00 ET.
    - Regular Trading Hours (RTH): Monday through Friday 09:30 - 16:00 ET.
    - Extended Trading Hours (ETH): Sunday 18:00 ET through morning, and evening post-18:00 ET.
    - Weekend halt: Friday 17:00 ET to Sunday 18:00 ET.
    """

    def __init__(self, tz_name: str = "America/New_York"):
        self.tz = ZoneInfo(tz_name)

    def _to_et(self, timestamp: datetime | None) -> datetime:
        return ensure_et(timestamp, target_tz=self.tz)

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_open

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_rth

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        et_time = self._to_et(timestamp)
        d = et_time.date()
        t = et_time.time()
        weekday = et_time.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        cal = MarketHolidayCalendar.get_year_calendar(d.year)

        # 1. Full Holiday Closure Check (e.g. Christmas Day or New Year's Day until 18:00 ET)
        if d in cal["cme_full_halt_days"] and t < CME_HALT_END:
            next_open = datetime.combine(d, CME_HALT_END, tzinfo=self.tz)
            holiday_name = cal["statutory_holidays"].get(d, "Market Holiday")
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.FUTURES,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.HOLIDAY_HALT,
                current_time=et_time,
                next_open=next_open,
                source="cme_holiday_schedule",
                details=f"CME {holiday_name} full day closure (re-opens at 18:00 ET)",
            )

        # 2. Good Friday Early Halt (09:15 ET for equity index futures)
        if d == cal["good_friday"] and t >= time(9, 15):
            days_to_sun = (6 - weekday) % 7
            next_sunday = (et_time + timedelta(days=days_to_sun)).date()
            next_open = datetime.combine(next_sunday, CME_HALT_END, tzinfo=self.tz)
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.FUTURES,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.HOLIDAY_HALT,
                current_time=et_time,
                next_open=next_open,
                source="cme_holiday_schedule",
                details="CME Good Friday early halt (halts at 09:15 ET until Sunday 18:00 ET)",
            )

        # 3. CME 13:00 ET Holiday Halts (MLK, Presidents', Memorial, Juneteenth, July 4, Labor Day, Thanksgiving)
        if d in cal["cme_1300_halt_days"]:
            if time(13, 0) <= t < CME_HALT_END:
                next_open = datetime.combine(d, CME_HALT_END, tzinfo=self.tz)
                holiday_name = cal["statutory_holidays"].get(d, "Market Holiday")
                return MarketSessionInfo(
                    symbol=symbol,
                    asset_class=AssetClass.FUTURES,
                    is_open=False,
                    is_rth=False,
                    session_type=MarketSessionType.HOLIDAY_HALT,
                    current_time=et_time,
                    next_open=next_open,
                    source="cme_holiday_schedule",
                    details=f"CME {holiday_name} holiday halt (13:00 - 18:00 ET)",
                )
            elif t < time(13, 0):
                # Before 13:00 ET on a statutory holiday: Globex is open, but cash equity market is closed!
                # Therefore this is strictly ETH (Extended Hours), NOT RTH.
                next_halt = datetime.combine(d, time(13, 0), tzinfo=self.tz)
                holiday_name = cal["statutory_holidays"].get(d, "Market Holiday")
                return MarketSessionInfo(
                    symbol=symbol,
                    asset_class=AssetClass.FUTURES,
                    is_open=True,
                    is_rth=False,
                    session_type=MarketSessionType.ETH,
                    current_time=et_time,
                    next_close=next_halt,
                    source="cme_holiday_schedule",
                    details=f"CME {holiday_name} morning session (ETH, halts at 13:00 ET)",
                )

        # 4. Early Closes (e.g. Black Friday or Christmas Eve: close at 13:15 ET)
        if d in cal["early_closes"]:
            if t >= time(13, 15):
                days_ahead = (6 - weekday) % 7 if weekday >= 4 else 1
                next_open_d = (et_time + timedelta(days=days_ahead)).date()
                next_open = datetime.combine(next_open_d, CME_HALT_END, tzinfo=self.tz)
                close_name = cal["early_closes"].get(d, "Early Close")
                return MarketSessionInfo(
                    symbol=symbol,
                    asset_class=AssetClass.FUTURES,
                    is_open=False,
                    is_rth=False,
                    session_type=MarketSessionType.HOLIDAY_HALT,
                    current_time=et_time,
                    next_open=next_open,
                    source="cme_holiday_schedule",
                    details=f"CME {close_name} early close (closed after 13:15 ET)",
                )
            elif EQUITY_RTH_OPEN <= t < time(13, 15):
                next_close = datetime.combine(d, time(13, 15), tzinfo=self.tz)
                close_name = cal["early_closes"].get(d, "Early Close")
                return MarketSessionInfo(
                    symbol=symbol,
                    asset_class=AssetClass.FUTURES,
                    is_open=True,
                    is_rth=True,
                    session_type=MarketSessionType.RTH,
                    current_time=et_time,
                    next_close=next_close,
                    source="cme_holiday_schedule",
                    details=f"CME {close_name} Regular Trading Hours (early close at 13:15 ET)",
                )

        # 5. Weekend Check (Friday 17:00 ET to Sunday 18:00 ET)
        if (weekday == 4 and t >= CME_HALT_START) or (weekday == 5) or (weekday == 6 and t < CME_HALT_END):
            # Calculate next Sunday 18:00 ET open
            days_ahead = (6 - weekday) % 7
            next_sunday = (et_time + timedelta(days=days_ahead)).date()
            next_open = datetime.combine(next_sunday, CME_HALT_END, tzinfo=self.tz)
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.FUTURES,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.WEEKEND_HALT,
                current_time=et_time,
                next_open=next_open,
                source="cme_schedule",
                details="CME weekend closure (Fri 17:00 - Sun 18:00 ET)",
            )

        # 6. Daily Maintenance Halt (Monday-Thursday 17:00 - 18:00 ET)
        if weekday in (0, 1, 2, 3) and CME_HALT_START <= t < CME_HALT_END:
            next_open = datetime.combine(et_time.date(), CME_HALT_END, tzinfo=self.tz)
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.FUTURES,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.DAILY_HALT,
                current_time=et_time,
                next_open=next_open,
                source="cme_schedule",
                details="CME daily settlement maintenance halt (17:00 - 18:00 ET)",
            )

        # 7. Regular Trading Hours (RTH: Mon-Fri 09:30 - 16:00 ET)
        if weekday in (0, 1, 2, 3, 4) and EQUITY_RTH_OPEN <= t < EQUITY_RTH_CLOSE:
            next_close = datetime.combine(et_time.date(), EQUITY_RTH_CLOSE, tzinfo=self.tz)
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.FUTURES,
                is_open=True,
                is_rth=True,
                session_type=MarketSessionType.RTH,
                current_time=et_time,
                next_close=next_close,
                source="cme_schedule",
                details="CME Regular Trading Hours (Cash Equity Session)",
            )

        # 8. Extended Trading Hours (ETH / Overnight Globex)
        # Next boundary is either RTH open (09:30 ET) or Daily Halt (17:00 ET)
        if t < EQUITY_RTH_OPEN:
            next_boundary = datetime.combine(et_time.date(), EQUITY_RTH_OPEN, tzinfo=self.tz)
        elif t >= EQUITY_RTH_CLOSE:
            next_boundary = datetime.combine(et_time.date(), CME_HALT_START, tzinfo=self.tz)
        else:
            next_boundary = None

        return MarketSessionInfo(
            symbol=symbol,
            asset_class=AssetClass.FUTURES,
            is_open=True,
            is_rth=False,
            session_type=MarketSessionType.ETH,
            current_time=et_time,
            next_open=next_boundary if t < EQUITY_RTH_OPEN else None,
            next_close=next_boundary if t >= EQUITY_RTH_CLOSE else None,
            source="cme_schedule",
            details="CME Extended Trading Hours (Overnight Globex Session)",
        )


class AlpacaMarketSessionProvider:
    """Dynamic session provider using the Alpaca Trading API get_clock() and get_calendar().

    Provides authoritative real-time exchange clock status for US Equities and ETFs.
    Includes memory caching to avoid excessive REST calls.
    """

    def __init__(
        self,
        trading_client: TradingClient | None = None,
        cache_ttl_seconds: int = 15,
        tz_name: str = "America/New_York",
    ):
        self.client = trading_client
        self.cache_ttl = cache_ttl_seconds
        self.tz = ZoneInfo(tz_name)
        self._cached_clock: Any = None
        self._cache_timestamp: datetime | None = None
        self._calendar_cache: dict[date, tuple[time, time]] = {}

    def _is_cache_valid(self) -> bool:
        if not self._cached_clock or not self._cache_timestamp:
            return False
        return (datetime.now(UTC) - self._cache_timestamp).total_seconds() < self.cache_ttl

    async def _fetch_clock(self) -> Any:
        if self._is_cache_valid():
            return self._cached_clock

        if self.client is None:
            return None

        try:
            clock = await asyncio.to_thread(self.client.get_clock)
            self._cached_clock = clock
            self._cache_timestamp = datetime.now(UTC)
            return clock
        except Exception as e:
            logger.debug("Alpaca get_clock() query failed: %s", e)
            return None

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_open

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_rth

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        # If a historical timestamp is requested, evaluate statically against NYSE schedule
        if timestamp is not None:
            return self._evaluate_static_equities(symbol, timestamp)

        clock = await self._fetch_clock()
        now_et = datetime.now(self.tz)

        if clock is not None:
            is_open = bool(getattr(clock, "is_open", False))
            next_open = getattr(clock, "next_open", None)
            next_close = getattr(clock, "next_close", None)

            # In Alpaca's clock, is_open is True specifically during RTH (09:30-16:00 ET)
            # Pre-market is 04:00-09:30 ET, Post-market is 16:00-20:00 ET
            t = now_et.time()
            weekday = now_et.weekday()

            if is_open:
                session_type = MarketSessionType.RTH
            elif weekday in (5, 6):
                session_type = MarketSessionType.WEEKEND_HALT
            elif time(4, 0) <= t < EQUITY_RTH_OPEN or EQUITY_RTH_CLOSE <= t < time(20, 0):
                session_type = MarketSessionType.ETH
            else:
                session_type = MarketSessionType.CLOSED

            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=is_open,
                is_rth=is_open,
                session_type=session_type,
                current_time=now_et,
                next_open=next_open,
                next_close=next_close,
                source="alpaca_api",
                details=f"Alpaca official exchange clock: {'OPEN' if is_open else 'CLOSED'}",
            )

        # Fallback to static NYSE schedule when Alpaca API client is unconfigured or offline
        return self._evaluate_static_equities(symbol, now_et)

    def _evaluate_static_equities(self, symbol: str, timestamp: datetime) -> MarketSessionInfo:
        et_time = ensure_et(timestamp, target_tz=self.tz)
        d = et_time.date()
        weekday = et_time.weekday()
        t = et_time.time()
        cal = MarketHolidayCalendar.get_year_calendar(d.year)

        # 1. Statutory Market Holiday (NYSE / NASDAQ closed all day)
        if d in cal["statutory_holidays"]:
            holiday_name = cal["statutory_holidays"][d]
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.HOLIDAY_HALT,
                current_time=et_time,
                source="static_nyse_schedule",
                details=f"NYSE full day holiday closure ({holiday_name})",
            )

        # 2. Weekend Check
        if weekday in (5, 6):
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=False,
                is_rth=False,
                session_type=MarketSessionType.WEEKEND_HALT,
                current_time=et_time,
                source="static_nyse_schedule",
                details="NYSE weekend closure",
            )

        # 3. Regular Trading Hours (accounting for early close days e.g. Black Friday or Christmas Eve)
        is_early_close = d in cal["early_closes"]
        rth_close = time(13, 0) if is_early_close else EQUITY_RTH_CLOSE

        if EQUITY_RTH_OPEN <= t < rth_close:
            next_close = datetime.combine(et_time.date(), rth_close, tzinfo=self.tz)
            close_detail = f" ({cal['early_closes'][d]} early close)" if is_early_close else ""
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=True,
                is_rth=True,
                session_type=MarketSessionType.RTH,
                current_time=et_time,
                next_close=next_close,
                source="static_nyse_schedule",
                details=f"NYSE Regular Trading Hours{close_detail}",
            )

        # 4. Extended Hours (Pre-market 04:00 - 09:30 ET, Post-market rth_close - 20:00 ET)
        if time(4, 0) <= t < EQUITY_RTH_OPEN or rth_close <= t < time(20, 0):
            next_open = (
                datetime.combine(et_time.date(), EQUITY_RTH_OPEN, tzinfo=self.tz) if t < EQUITY_RTH_OPEN else None
            )
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=False,  # default strict equity trading considers outside RTH closed unless extended hours enabled
                is_rth=False,
                session_type=MarketSessionType.ETH,
                current_time=et_time,
                next_open=next_open,
                source="static_nyse_schedule",
                details="NYSE Extended Hours (Pre/Post Market)",
            )

        # 5. Overnight Closed
        return MarketSessionInfo(
            symbol=symbol,
            asset_class=AssetClass.EQUITY,
            is_open=False,
            is_rth=False,
            session_type=MarketSessionType.CLOSED,
            current_time=et_time,
            source="static_nyse_schedule",
            details="NYSE closed (Overnight)",
        )


class CompositeMarketSessionProvider:
    """Master session manager that routes queries to the appropriate asset class provider.

    - Futures (/MES, /MNQ, etc.) -> CMEFuturesSessionProvider
    - Crypto (BTC/USD, ETH/USD) -> CryptoSessionProvider
    - Equities & ETFs (SPY, QQQ, etc.) -> AlpacaMarketSessionProvider (with static NYSE fallback)
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        alpaca_client: TradingClient | None = None,
    ):
        self.config = config
        self.crypto_provider = CryptoSessionProvider()
        self.futures_provider = CMEFuturesSessionProvider()
        self.equity_provider = AlpacaMarketSessionProvider(trading_client=alpaca_client)

    def _resolve_provider(self, symbol: str) -> tuple[MarketSessionProtocol, AssetClass]:
        clean_sym = symbol.strip().upper()
        if clean_sym.startswith("/") or clean_sym.endswith("=F"):
            return self.futures_provider, AssetClass.FUTURES
        if "/" in clean_sym or clean_sym.startswith(("BTC", "ETH", "SOL", "DOGE")):
            return self.crypto_provider, AssetClass.CRYPTO
        return self.equity_provider, AssetClass.EQUITY

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        provider, _ = self._resolve_provider(symbol)
        return await provider.is_market_open(symbol, timestamp)

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        provider, _ = self._resolve_provider(symbol)
        return await provider.is_rth(symbol, timestamp)

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        provider, _ = self._resolve_provider(symbol)
        return await provider.get_session_info(symbol, timestamp)

    async def is_session_active(
        self,
        instrument_type: str = "all",
        timestamp: datetime | None = None,
    ) -> tuple[bool, str]:
        """Check if trading session is currently active for the given instrument group."""
        itype = (instrument_type or "all").lower().strip()
        if itype in ("crypto", "cryptocurrency"):
            return True, "Crypto trading is active 24/7"
        if itype in ("futures", "cme"):
            info = await self.futures_provider.get_session_info("/MES", timestamp=timestamp)
            if not info.is_open:
                return False, f"Futures market {info.session_type.value} ({info.details})"
            return True, f"Futures {info.session_type.value} active"
        if itype in ("equity", "equities", "stocks"):
            info = await self.equity_provider.get_session_info("SPY", timestamp=timestamp)
            enforce_rth = getattr(getattr(self.config, "session", None), "enforce_rth", True) if self.config else True
            if enforce_rth and not info.is_rth:
                return False, f"Equities outside RTH ({info.details})"
            if not info.is_open:
                return False, f"Equities market {info.session_type.value} ({info.details})"
            return True, f"Equities {info.session_type.value} active"

        # "all" or general: verify at least one primary trading market is active
        fut_info = await self.futures_provider.get_session_info("/MES", timestamp=timestamp)
        eq_info = await self.equity_provider.get_session_info("SPY", timestamp=timestamp)
        if fut_info.is_open or eq_info.is_open:
            active_markets: list[str] = []
            if fut_info.is_open:
                active_markets.append(f"Futures ({fut_info.session_type.value})")
            if eq_info.is_open:
                active_markets.append(f"Equities ({eq_info.session_type.value})")
            return True, f"Active sessions: {', '.join(active_markets)}"
        return (
            False,
            f"Primary markets closed (Futures: {fut_info.session_type.value}, Equities: {eq_info.session_type.value})",
        )
