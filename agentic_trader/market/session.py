from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
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
        if timestamp is None:
            return datetime.now(self.tz)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC).astimezone(self.tz)
        return timestamp.astimezone(self.tz)

    async def is_market_open(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_open

    async def is_rth(self, symbol: str, timestamp: datetime | None = None) -> bool:
        info = await self.get_session_info(symbol, timestamp)
        return info.is_rth

    async def get_session_info(self, symbol: str, timestamp: datetime | None = None) -> MarketSessionInfo:
        et_time = self._to_et(timestamp)
        weekday = et_time.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        t = et_time.time()

        # 1. Weekend Check (Friday 17:00 ET to Sunday 18:00 ET)
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

        # 2. Daily Maintenance Halt (Monday-Thursday 17:00 - 18:00 ET)
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

        # 3. Regular Trading Hours (RTH: Mon-Fri 09:30 - 16:00 ET)
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

        # 4. Extended Trading Hours (ETH / Overnight Globex)
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
        et_time = (
            timestamp.astimezone(self.tz) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC).astimezone(self.tz)
        )
        weekday = et_time.weekday()
        t = et_time.time()

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

        if EQUITY_RTH_OPEN <= t < EQUITY_RTH_CLOSE:
            next_close = datetime.combine(et_time.date(), EQUITY_RTH_CLOSE, tzinfo=self.tz)
            return MarketSessionInfo(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                is_open=True,
                is_rth=True,
                session_type=MarketSessionType.RTH,
                current_time=et_time,
                next_close=next_close,
                source="static_nyse_schedule",
                details="NYSE Regular Trading Hours",
            )

        # Pre/Post market
        if time(4, 0) <= t < EQUITY_RTH_OPEN or EQUITY_RTH_CLOSE <= t < time(20, 0):
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
