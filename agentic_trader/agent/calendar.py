import hashlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from agentic_trader.constants import (
    DEFAULT_LOCKOUT_POST_EVENT_MINUTES,
    DEFAULT_LOCKOUT_PRE_EVENT_MINUTES,
    FOREX_FACTORY_JSON_FEED_URL,
    CalendarStatus,
)
from agentic_trader.market.session import ET_TZ
from agentic_trader.risk import in_lockout_window


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


class Tier1Kind(StrEnum):
    """The scheduled releases the desk locks out around. An SEP release shares the statement's instant."""

    FOMC_STATEMENT = "fomc_statement"
    FOMC_PRESS_CONFERENCE = "fomc_press_conference"
    CPI = "cpi"
    PPI = "ppi"
    EMPLOYMENT_SITUATION = "employment_situation"


TIER1_TITLES: Mapping[Tier1Kind, str] = {
    Tier1Kind.FOMC_STATEMENT: "FOMC Statement",
    Tier1Kind.FOMC_PRESS_CONFERENCE: "FOMC Press Conference",
    Tier1Kind.CPI: "CPI",
    Tier1Kind.PPI: "PPI",
    Tier1Kind.EMPLOYMENT_SITUATION: "Employment Situation",
}
# The only hosts a static calendar date may be sourced from.
OFFICIAL_HOSTS = frozenset({"www.federalreserve.gov", "www.bls.gov"})
FOREXFACTORY_SOURCE = "forexfactory"


@dataclass
class MacroEvent:
    title: str
    country: str
    impact: str
    timestamp: datetime  # UTC
    forecast: str | None = None
    previous: str | None = None
    source: str = FOREXFACTORY_SOURCE  # "forexfactory" or f"static:{calendar_id}"
    kind: Tier1Kind | None = None


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
        """Returns (True, event) for the first tier-1 event whose lockout window contains the current time.

        The window is ``agentic_trader.risk.in_lockout_window``: ``[event - pre_minutes, event + post_minutes]``,
        inclusive at both ends.
        """
        now = now or datetime.now(UTC)
        events = await self.fetch_events()
        for e in events:
            if not self.is_tier_1(e.title, e.country):
                continue
            if in_lockout_window(now, e.timestamp, pre_minutes, post_minutes):
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


class Tier1EventRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Tier1Kind
    at: AwareDatetime
    source_url: str
    # Evidence for the renewal runbook only: a tentative event locks out like any other.
    tentative: bool = False


class Tier1CalendarFile(BaseModel):
    """`config/calendar/us-tier1-vN.json`: immutable once merged; a moved release or a new year is vN+1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    calendar_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*-v\d+$")
    timezone: Literal["America/New_York"]
    valid_from: date
    valid_through: date
    retrieved_on: date
    # Each series' last listed release date in the source; `valid_through` may not pass the shortest.
    series_last_listed: dict[Tier1Kind, date]
    events: tuple[Tier1EventRecord, ...]

    @field_validator("series_last_listed")
    @classmethod
    def _every_series_is_listed(cls, value: dict[Tier1Kind, date]) -> dict[Tier1Kind, date]:
        if set(value) != set(Tier1Kind):
            raise ValueError("series_last_listed must name every tier-1 kind")
        return value


class CalendarFileError(ValueError):
    """A calendar file that parses but breaks a rule; the subclass name is the reported `error`."""


class DigestMismatch(CalendarFileError):
    """The file's SHA-256 is not the one `risk.macro_calendar_sha256` pins."""


class CalendarIdMismatch(CalendarFileError):
    """`calendar_id` is not the file stem."""


class InvalidValidityRange(CalendarFileError):
    """`valid_from` is after `valid_through`."""


class ValidityBeyondSeries(CalendarFileError):
    """`valid_through` is later than the shortest series' last listed date."""


class WrongNewYorkOffset(CalendarFileError):
    """An event's UTC offset is not the one New York had at that instant."""


class EventOutsideValidity(CalendarFileError):
    """An event's New York date is outside `[valid_from, valid_through]`."""


class UnsortedEvents(CalendarFileError):
    """Events are not ordered by `(at, kind)`."""


class DuplicateEvent(CalendarFileError):
    """Two events share a `(kind, at)`."""


class UnofficialSource(CalendarFileError):
    """A `source_url` is not https on an official schedule host."""


def _check_header(doc: Tier1CalendarFile, stem: str) -> None:
    if doc.calendar_id != stem:
        raise CalendarIdMismatch
    if doc.valid_from > doc.valid_through:
        raise InvalidValidityRange
    if doc.valid_through > min(doc.series_last_listed.values()):
        raise ValidityBeyondSeries


def _is_official(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in OFFICIAL_HOSTS


def _check_events(doc: Tier1CalendarFile) -> None:
    if any(r.at.utcoffset() != r.at.astimezone(ET_TZ).utcoffset() for r in doc.events):
        raise WrongNewYorkOffset
    if any(not doc.valid_from <= r.at.astimezone(ET_TZ).date() <= doc.valid_through for r in doc.events):
        raise EventOutsideValidity
    keys = [(r.at, r.kind) for r in doc.events]
    if keys != sorted(keys):
        raise UnsortedEvents
    if len(set(keys)) != len(keys):
        raise DuplicateEvent
    if not all(_is_official(r.source_url) for r in doc.events):
        raise UnofficialSource


@dataclass(frozen=True)
class StaticCalendarState:
    calendar_id: str
    sha256: str | None
    pinned: bool
    status: CalendarStatus
    valid_from: date | None
    valid_through: date | None
    days_remaining: int | None
    error: str | None


@dataclass(frozen=True)
class StaticTier1Calendar:
    """The digest-pinned schedule of tier-1 releases. It is verified iff its `state` is `ok`.

    `load` never raises: a file that cannot be trusted becomes an `invalid` calendar with no events.
    """

    calendar_id: str
    sha256: str | None  # of the bytes read; None when the file was unreadable
    pinned: bool
    error: str | None  # exception type name; None when the file is valid
    valid_from: date | None
    valid_through: date | None
    events: tuple[MacroEvent, ...]

    @classmethod
    def load(cls, path: Path, sha256: str | None) -> StaticTier1Calendar:
        calendar_id = path.stem
        digest: str | None = None
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if sha256 is not None and digest != sha256:
                raise DigestMismatch
            doc = Tier1CalendarFile.model_validate_json(raw)
            _check_header(doc, calendar_id)
            _check_events(doc)
        except Exception as exc:
            logger.error(
                "Static tier-1 calendar is invalid", extra={"calendar_id": calendar_id, "error": type(exc).__name__}
            )
            return cls(calendar_id, digest, sha256 is not None, type(exc).__name__, None, None, ())
        events = tuple(
            MacroEvent(
                title=TIER1_TITLES[record.kind],
                country="USD",
                impact="High",
                timestamp=record.at.astimezone(UTC),
                source=f"static:{calendar_id}",
                kind=record.kind,
            )
            for record in doc.events
        )
        return cls(calendar_id, digest, sha256 is not None, None, doc.valid_from, doc.valid_through, events)

    def state(self, now: datetime) -> StaticCalendarState:
        """Status at `now`, judged on New York's date so it flips at New York midnight without a reload."""
        if self.error is not None or self.valid_from is None or self.valid_through is None:
            return StaticCalendarState(
                self.calendar_id, self.sha256, self.pinned, CalendarStatus.INVALID, None, None, None, self.error
            )
        today = now.astimezone(ET_TZ).date()
        status = CalendarStatus.OK if self.valid_from <= today <= self.valid_through else CalendarStatus.EXPIRED
        return StaticCalendarState(
            self.calendar_id,
            self.sha256,
            self.pinned,
            status,
            self.valid_from,
            self.valid_through,
            (self.valid_through - today).days,
            None,
        )


def macro_calendar_readiness(state: StaticCalendarState, *, renewal_days: int, unknown_policy: str) -> dict[str, Any]:
    """The `macro_calendar` readiness check: ready iff the static calendar is ok with `renewal_days` left."""
    days = state.days_remaining
    if state.status is CalendarStatus.INVALID or days is None:
        detail = f"invalid: {state.error}"
    elif state.status is CalendarStatus.EXPIRED:
        unverified = f"lockout unverified (policy {unknown_policy})"
        if days < 0:
            detail = f"{state.calendar_id} expired {state.valid_through}; {unverified}"
        else:
            detail = f"{state.calendar_id} not valid until {state.valid_from}; {unverified}"
    elif days < renewal_days:
        detail = f"{state.calendar_id} expires {state.valid_through} ({days} days); publish v2"
    else:
        detail = f"{state.calendar_id} valid through {state.valid_through} ({days} days)"
    ready = state.status is CalendarStatus.OK and days is not None and days >= renewal_days
    return {"ready": ready, "detail": detail, "status": state.status.value, "days_remaining": days}


__all__ = [
    "FOREXFACTORY_SOURCE",
    "OFFICIAL_HOSTS",
    "TIER1_TITLES",
    "TIER_1_KEYWORDS",
    "BaseEconomicCalendar",
    "CalendarFileError",
    "CalendarIdMismatch",
    "DigestMismatch",
    "DuplicateEvent",
    "EconomicCalendarProtocol",
    "EventOutsideValidity",
    "ForexFactoryCalendar",
    "InvalidValidityRange",
    "MacroEvent",
    "StaticCalendarState",
    "StaticTier1Calendar",
    "Tier1CalendarFile",
    "Tier1EventRecord",
    "Tier1Kind",
    "UnofficialSource",
    "UnsortedEvents",
    "ValidityBeyondSeries",
    "WrongNewYorkOffset",
    "macro_calendar_readiness",
]
