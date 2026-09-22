import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import httpx

from agentic_trader.constants import (
    DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
    DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
    FOREX_FACTORY_JSON_FEED_URL,
)


logger = logging.getLogger(__name__)

TIER_1_KEYWORDS = [
    "cpi",
    "consumer price index",
    "ppi",
    "producer price index",
    # The scheduled rate decision and its same-day releases; not individual Fed
    # speakers ("FOMC Member ... Speaks") or the minutes.
    "fomc statement",
    "fomc economic projections",
    "fomc press conference",
    "fed interest rate decision",
    "federal funds rate",
    "non-farm payrolls",
    "nonfarm payrolls",
    "non-farm employment",
    "nonfarm employment",
    "nfp",
    "unemployment rate",
]


def _format_delta(delta: timedelta) -> str:
    """Render a positive time delta as `<h>h <m>m`, clamped at zero."""
    total_minutes = max(0, int(delta.total_seconds() // 60))
    return f"{total_minutes // 60}h {total_minutes % 60}m"


@dataclass
class MacroEvent:
    title: str
    country: str
    impact: str
    timestamp: datetime  # UTC
    forecast: str | None = None
    previous: str | None = None


@runtime_checkable
class EconomicCalendarProtocol(Protocol):
    """Protocol interface defining the economic calendar contract."""

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]: ...

    def is_tier_1(self, title: str, country: str = "USD") -> bool: ...

    async def get_upcoming_tier1_events(
        self, window_hours: int = 24, now: datetime | None = None
    ) -> list[MacroEvent]: ...

    async def is_in_lockout_window(
        self,
        pre_minutes: int = DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
        post_minutes: int = DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
        now: datetime | None = None,
    ) -> tuple[bool, MacroEvent | None]: ...

    async def get_macro_summary_for_prompt(
        self,
        now: datetime | None = None,
        pre_minutes: int = DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
        post_minutes: int = DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
    ) -> str: ...


class BaseEconomicCalendar(ABC):
    """Abstract base class for economic calendar providers.

    Subclasses must implement `fetch_events()`. Default implementations are
    provided for tier-1 classification, lockout detection, and prompt formatting.
    """

    def is_tier_1(self, title: str, country: str = "USD") -> bool:
        if country.upper() not in ("USD", "US"):
            return False
        title_lower = title.lower()
        return any(kw in title_lower for kw in TIER_1_KEYWORDS)

    @abstractmethod
    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        """Fetch economic calendar events from provider."""
        ...

    async def get_upcoming_tier1_events(self, window_hours: int = 24, now: datetime | None = None) -> list[MacroEvent]:
        now = now or datetime.now(UTC)
        events = await self.fetch_events()
        cutoff = now + timedelta(hours=window_hours)
        return [e for e in events if now <= e.timestamp <= cutoff and self.is_tier_1(e.title, e.country)]

    async def is_in_lockout_window(
        self,
        pre_minutes: int = DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
        post_minutes: int = DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
        now: datetime | None = None,
    ) -> tuple[bool, MacroEvent | None]:
        """Returns (True, event) if current time is within [event - pre_minutes, event + post_minutes]."""
        now = now or datetime.now(UTC)
        events = await self.fetch_events()
        for e in events:
            if not self.is_tier_1(e.title, e.country):
                continue
            lockout_start = e.timestamp - timedelta(minutes=pre_minutes)
            lockout_end = e.timestamp + timedelta(minutes=post_minutes)
            if lockout_start <= now <= lockout_end:
                return True, e
        return False, None

    async def get_macro_summary_for_prompt(
        self,
        now: datetime | None = None,
        pre_minutes: int = DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
        post_minutes: int = DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
    ) -> str:
        """Render the macro context block, stating the evaluation time and the computed clearance.

        The deterministic lockout gate runs before the LLM is consulted, so the
        block reports that verdict explicitly; an LLM cannot reconstruct macro
        timing from an event time alone.
        """
        now = now or datetime.now(UTC)
        header = f"Evaluation time: {now.strftime('%Y-%m-%d %H:%M UTC')}."
        in_lockout, lock_event = await self.is_in_lockout_window(
            pre_minutes=pre_minutes, post_minutes=post_minutes, now=now
        )
        if in_lockout and lock_event:
            return (
                f"{header} LOCKOUT ACTIVE: Event '{lock_event.title}' scheduled at "
                f"{lock_event.timestamp.strftime('%Y-%m-%d %H:%M UTC')} "
                f"(lockout window: {pre_minutes} minutes before and {post_minutes} minutes after the event). "
                "Trading lockout in effect."
            )

        upcoming = sorted(
            await self.get_upcoming_tier1_events(window_hours=24, now=now),
            key=lambda e: e.timestamp,
        )
        if not upcoming:
            return (
                f"{header} Lockout verified: CLEAR. No Tier-1 US economic releases "
                "(CPI, PPI, FOMC, NFP) scheduled in the next 24 hours."
            )

        event_descriptions = [
            f"- {e.title} at {e.timestamp.strftime('%Y-%m-%d %H:%M UTC')} (in {_format_delta(e.timestamp - now)})"
            for e in upcoming
        ]
        return (
            f"{header} Lockout verified: CLEAR "
            f"(next Tier-1 event in {_format_delta(upcoming[0].timestamp - now)}). "
            "Upcoming Tier-1 releases in the next 24 hours:\n" + "\n".join(event_descriptions)
        )


class ForexFactoryCalendar(BaseEconomicCalendar):
    """Default economic calendar implementation parsing the public ForexFactory JSON feed."""

    def __init__(self, cache_ttl_minutes: int = 30):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cached_events: list[MacroEvent] = []
        self._last_fetch_time: datetime | None = None

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        """Fetch economic calendar events from public feed with caching."""
        now = datetime.now(UTC)
        if not force_refresh and self._last_fetch_time and (now - self._last_fetch_time) < self.cache_ttl:
            return self._cached_events

        events: list[MacroEvent] = []
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    FOREX_FACTORY_JSON_FEED_URL,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data:
                        country = item.get("country", "")
                        title = item.get("title", "")
                        date_str = item.get("date", "")
                        impact = item.get("impact", "")
                        if country == "USD" and (impact == "High" or self.is_tier_1(title, country)):
                            try:
                                dt = datetime.fromisoformat(date_str).astimezone(UTC)
                                events.append(
                                    MacroEvent(
                                        title=title,
                                        country=country,
                                        impact=impact,
                                        timestamp=dt,
                                        forecast=item.get("forecast"),
                                        previous=item.get("previous"),
                                    )
                                )
                            except Exception:
                                pass
        except Exception as e:
            logger.warning("Failed to fetch economic calendar from ForexFactory", extra={"error": str(e)})

        self._cached_events = events
        self._last_fetch_time = now
        return events


__all__ = [
    "TIER_1_KEYWORDS",
    "BaseEconomicCalendar",
    "EconomicCalendarProtocol",
    "ForexFactoryCalendar",
    "MacroEvent",
]
