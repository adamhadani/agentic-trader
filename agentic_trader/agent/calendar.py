from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx


TIER_1_KEYWORDS = [
    "cpi",
    "consumer price index",
    "ppi",
    "producer price index",
    "fomc",
    "fed interest rate decision",
    "federal funds rate",
    "non-farm payrolls",
    "nonfarm payrolls",
    "nfp",
    "unemployment rate",
]


@dataclass
class MacroEvent:
    title: str
    country: str
    impact: str
    timestamp: datetime  # UTC
    forecast: str | None = None
    previous: str | None = None


class EconomicCalendar:
    def __init__(self, finnhub_api_key: str | None = None):
        self.finnhub_api_key = finnhub_api_key
        self._cached_events: list[MacroEvent] = []
        self._last_fetch_time: datetime | None = None

    def is_tier_1(self, title: str, country: str = "USD") -> bool:
        if country.upper() not in ("USD", "US"):
            return False
        title_lower = title.lower()
        return any(kw in title_lower for kw in TIER_1_KEYWORDS)

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        """Fetch economic calendar events from public feed with caching."""
        now = datetime.now(UTC)
        if not force_refresh and self._last_fetch_time and (now - self._last_fetch_time) < timedelta(minutes=30):
            return self._cached_events

        events: list[MacroEvent] = []

        # 1. Try public ForexFactory calendar feed
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
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
                                # Example: 2026-09-12T08:30:00-04:00
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
        except Exception:
            # Fallback gracefully if network / feed is unreachable
            pass

        self._cached_events = events
        self._last_fetch_time = now
        return events

    async def get_upcoming_tier1_events(self, window_hours: int = 24, now: datetime | None = None) -> list[MacroEvent]:
        now = now or datetime.now(UTC)
        events = await self.fetch_events()
        cutoff = now + timedelta(hours=window_hours)
        return [e for e in events if now <= e.timestamp <= cutoff and self.is_tier_1(e.title, e.country)]

    async def is_in_lockout_window(
        self,
        pre_minutes: int = 60,
        post_minutes: int = 30,
        now: datetime | None = None,
    ) -> tuple[bool, MacroEvent | None]:
        """
        Returns (True, event) if current time is within [event - pre_minutes, event + post_minutes].
        """
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

    async def get_macro_summary_for_prompt(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        in_lockout, lock_event = await self.is_in_lockout_window(now=now)
        if in_lockout and lock_event:
            return f"LOCKOUT ACTIVE: Event '{lock_event.title}' scheduled at {lock_event.timestamp.strftime('%Y-%m-%d %H:%M UTC')}. Trading lockout in effect."

        upcoming = await self.get_upcoming_tier1_events(window_hours=24, now=now)
        if not upcoming:
            return "Macro Clear: No Tier-1 US economic releases (CPI, PPI, FOMC, NFP) scheduled in the next 24 hours."

        event_descriptions = [f"- {e.title} at {e.timestamp.strftime('%H:%M UTC')}" for e in upcoming]
        return "Upcoming Tier-1 releases in next 24 hours:\n" + "\n".join(event_descriptions)
