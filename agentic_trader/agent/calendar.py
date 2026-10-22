import hashlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from agentic_trader.config import AppConfig
from agentic_trader.constants import FOREX_FACTORY_JSON_FEED_URL, CalendarStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.risk import Rejection, RiskLimits, in_lockout_window, macro_lockout


logger = logging.getLogger(__name__)


def _format_delta(delta: timedelta) -> str:
    """Render a positive time delta as `<h>h <m>m`, clamped at zero."""
    total_minutes = max(0, int(delta.total_seconds() // 60))
    return f"{total_minutes // 60}h {total_minutes % 60}m"


def _utc_minute(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


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
# A live title's kind is the first keyword it contains, so "press conference" is matched before any statement
# keyword. The scheduled rate decision and its same-day releases count; individual Fed speakers
# ("FOMC Member ... Speaks") and the minutes do not.
TIER1_KEYWORDS: tuple[tuple[str, Tier1Kind], ...] = (
    ("fomc press conference", Tier1Kind.FOMC_PRESS_CONFERENCE),
    ("fomc statement", Tier1Kind.FOMC_STATEMENT),
    ("fomc economic projections", Tier1Kind.FOMC_STATEMENT),
    ("fed interest rate decision", Tier1Kind.FOMC_STATEMENT),
    ("federal funds rate", Tier1Kind.FOMC_STATEMENT),
    ("cpi", Tier1Kind.CPI),
    ("consumer price index", Tier1Kind.CPI),
    ("ppi", Tier1Kind.PPI),
    ("producer price index", Tier1Kind.PPI),
    ("non-farm payrolls", Tier1Kind.EMPLOYMENT_SITUATION),
    ("nonfarm payrolls", Tier1Kind.EMPLOYMENT_SITUATION),
    ("non-farm employment", Tier1Kind.EMPLOYMENT_SITUATION),
    ("nonfarm employment", Tier1Kind.EMPLOYMENT_SITUATION),
    ("nfp", Tier1Kind.EMPLOYMENT_SITUATION),
    ("unemployment rate", Tier1Kind.EMPLOYMENT_SITUATION),
)
# The only hosts a static calendar date may be sourced from.
OFFICIAL_HOSTS = frozenset({"www.federalreserve.gov", "www.bls.gov"})
FOREXFACTORY_SOURCE = "forexfactory"


def tier1_kind(title: str, country: str) -> Tier1Kind | None:
    """The tier-1 kind a live feed title names, or None (always None outside USD/US)."""
    if country.upper() not in ("USD", "US"):
        return None
    title_lower = title.lower()
    return next((kind for keyword, kind in TIER1_KEYWORDS if keyword in title_lower), None)


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


@dataclass(frozen=True)
class CalendarFetch:
    """One live-feed read. A `failed` read carries no events, and `error` is an exception type name.

    `fetched_at` is the instant of the request this status came from. `coverage` is the first and last New York
    date of the payload's parseable items, any country; `unparsed_items` counts the items that could not be read.
    """

    status: CalendarStatus
    events: tuple[MacroEvent, ...] = ()
    fetched_at: datetime | None = None
    error: str | None = None
    http_status: int | None = None
    coverage: tuple[date, date] | None = None
    unparsed_items: int = 0


class BaseEconomicCalendar(ABC):
    """A live economic-calendar feed. Its one read never raises: a failure is a `failed` `CalendarFetch`."""

    source: ClassVar[str]  # journaled as the live source
    label: ClassVar[str]  # named in the prompt's live cross-check

    @abstractmethod
    async def fetch_events(self, force_refresh: bool = False) -> CalendarFetch:
        """Fetch the provider's current events."""
        ...


class LiveFeedError(Exception):
    """A response the live feed cannot use; the subclass name is the reported `error`."""


class UnexpectedStatus(LiveFeedError):
    """The feed answered with a status other than 200."""


class UnexpectedPayload(LiveFeedError):
    """The feed's JSON body is not a non-empty list of items, or none of its items has a readable instant.

    The weekly feed always lists dozens of items across countries, so an empty week is a broken feed.
    """


class ForexFactoryCalendar(BaseEconomicCalendar):
    """The public ForexFactory JSON feed: a success is cached for 30 minutes, a failure remembered for 5.

    A failure is never served as a cached success. The feed is reportedly capped at two downloads per five
    minutes per IP, so a failed read is not retried until its backoff has passed.
    """

    source: ClassVar[str] = FOREXFACTORY_SOURCE
    label: ClassVar[str] = "ForexFactory"

    def __init__(
        self,
        cache_ttl_minutes: int = 30,
        failure_backoff_minutes: int = 5,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.failure_backoff = timedelta(minutes=failure_backoff_minutes)
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last: CalendarFetch | None = None

    async def fetch_events(self, force_refresh: bool = False) -> CalendarFetch:
        now = self._clock()
        last = self._last
        if not force_refresh and last is not None and last.fetched_at is not None:
            hold = self.cache_ttl if last.status is CalendarStatus.OK else self.failure_backoff
            if now - last.fetched_at < hold:
                return last
        self._last = await self._download(now)
        return self._last

    async def _download(self, now: datetime) -> CalendarFetch:
        http_status: int | None = None
        try:
            async with httpx.AsyncClient(timeout=6.0, transport=self._transport) as client:
                resp = await client.get(FOREX_FACTORY_JSON_FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    http_status = resp.status_code
                    raise UnexpectedStatus
                payload = resp.json()
            if not isinstance(payload, list) or not payload:
                raise UnexpectedPayload
            fetch = _parse_items(payload, now)
            if fetch.coverage is None:  # no item parsed
                raise UnexpectedPayload
            return fetch
        except Exception as exc:
            logger.warning(
                "Failed to fetch economic calendar from ForexFactory",
                extra={"event": "macro_calendar_fetch_failed", "error": type(exc).__name__, "http_status": http_status},
            )
            return CalendarFetch(
                CalendarStatus.FAILED, fetched_at=now, error=type(exc).__name__, http_status=http_status
            )


def _feed_instant(item: Any) -> datetime | None:
    """An item's aware instant; None for a non-dict item or a missing, unreadable or offset-less date."""
    if not isinstance(item, dict) or not isinstance(item.get("date"), str):
        return None
    try:
        at = datetime.fromisoformat(item["date"])
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _parse_items(payload: list[Any], fetched_at: datetime) -> CalendarFetch:
    """USD items with high impact or a tier-1 kind become events; an item without a readable instant is counted."""
    events: list[MacroEvent] = []
    dates: list[date] = []
    unparsed = 0
    for item in payload:
        at = _feed_instant(item)
        if at is None:
            unparsed += 1
            continue
        dates.append(at.astimezone(ET_TZ).date())
        country, title, impact = (str(item.get(key) or "") for key in ("country", "title", "impact"))
        kind = tier1_kind(title, country)
        if country == "USD" and (impact == "High" or kind is not None):
            events.append(
                MacroEvent(
                    title=title,
                    country=country,
                    impact=impact,
                    timestamp=at.astimezone(UTC),
                    forecast=item.get("forecast"),
                    previous=item.get("previous"),
                    kind=kind,
                )
            )
    return CalendarFetch(
        CalendarStatus.OK,
        tuple(events),
        fetched_at,
        coverage=(min(dates), max(dates)) if dates else None,
        unparsed_items=unparsed,
    )


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


# A live and a static event are one release when they share a kind within this tolerance.
MATCH_TOLERANCE = timedelta(minutes=5)
UPCOMING_WINDOW = timedelta(hours=24)
MAX_DISAGREEMENTS = 10


@dataclass(frozen=True)
class CalendarLockout:
    """The tier-1 event whose lockout window contains the check's `now`, and the sources that list it."""

    title: str
    at: datetime
    kind: Tier1Kind | None
    sources: tuple[str, ...]  # ("static",), ("static", "live") or ("live",)


@dataclass(frozen=True)
class CalendarDisagreement:
    """Evidence only: a static event the live feed lacks within its coverage, or a live tier-1 event the static
    calendar lacks. The union locks out on both either way."""

    side: Literal["static_only", "live_only"]
    kind: Tier1Kind
    at: datetime
    title: str


@dataclass(frozen=True)
class CalendarCheck:
    """The union calendar's one read at `now`: the lockout gate, the prompt block, the card note and the journal
    block all render from it, so they cannot disagree."""

    now: datetime
    pre_minutes: int
    post_minutes: int
    unknown_policy: Literal["open", "closed"]
    static: StaticCalendarState
    live: CalendarFetch
    live_source: str
    live_label: str
    lockout: CalendarLockout | None
    upcoming: tuple[MacroEvent, ...]
    disagreements: tuple[CalendarDisagreement, ...]

    @property
    def verified(self) -> bool:
        """The static calendar is the authority: verified iff its status is `ok`."""
        return self.static.status is CalendarStatus.OK

    @property
    def live_state(self) -> str:
        return "ok" if self.live.status is CalendarStatus.OK else f"failed ({self.live.error})"

    def lockout_rejection(self, limits: RiskLimits) -> Rejection | None:
        """``agentic_trader.risk.macro_lockout`` applied to this check at its own `now`."""
        lockout = self.lockout
        return macro_lockout(
            lockout.title if lockout is not None else None,
            lockout.at if lockout is not None else None,
            self.now,
            limits,
            calendar_verified=self.verified,
            static_status=self.static.status,
        )

    def prompt_text(self) -> str:
        """The Economic Calendar Status block: the evaluation time, the verdict and its source, then the next 24
        hours. It says "verified" only as "UNVERIFIED", and only when the static calendar is not ok."""
        parts = [f"Evaluation time: {_utc_minute(self.now)}."]
        lockout = self.lockout
        if lockout is not None:
            parts.append(
                f"LOCKOUT ACTIVE: Event '{lockout.title}' scheduled at {_utc_minute(lockout.at)} "
                f"(lockout window: {self.pre_minutes} minutes before and {self.post_minutes} minutes after the "
                "event). Trading lockout in effect."
            )
        elif self.verified:
            parts.append(
                f"Lockout CLEAR per static tier-1 calendar {self.static.calendar_id} "
                f"(sha256 {(self.static.sha256 or '')[:8]}), valid through {self.static.valid_through}; "
                f"live cross-check ({self.live_label}): {self.live_state}."
            )
        if not self.verified:
            parts.append(
                f"Lockout UNVERIFIED: static tier-1 calendar {self.static.status}; live feed {self.live_state}; "
                "treat scheduled-release timing as unknown."
            )
        if lockout is None and self.upcoming:
            lines = "\n".join(
                f"- {e.title} at {_utc_minute(e.timestamp)} (in {_format_delta(e.timestamp - self.now)})"
                for e in self.upcoming
            )
            parts.append(
                f"Next tier-1 release in {_format_delta(self.upcoming[0].timestamp - self.now)}. "
                f"Upcoming tier-1 releases in the next 24 hours:\n{lines}"
            )
        elif lockout is None and self.verified:
            # Only the authoritative static calendar can say there is nothing scheduled.
            parts.append("No tier-1 US releases (FOMC, CPI, PPI, Employment Situation) in the next 24 hours.")
        return " ".join(parts)

    def macro_note(self) -> str | None:
        """The card's label for an unverified calendar; None when verified."""
        if self.verified:
            return None
        return f"static tier-1 calendar {self.static.status}; live feed {self.live_state}"

    def journal_block(self) -> dict[str, Any]:
        """The scan journal's `calendar` block: every key always present, JSON-ready."""
        static, live, lockout = self.static, self.live, self.lockout
        return {
            "verified": self.verified,
            "unknown_policy": self.unknown_policy,
            "match_tolerance_minutes": int(MATCH_TOLERANCE.total_seconds() // 60),
            "static": {
                "calendar_id": static.calendar_id,
                "sha256": static.sha256,
                "pinned": static.pinned,
                "status": static.status.value,
                "valid_through": static.valid_through.isoformat() if static.valid_through is not None else None,
                "days_remaining": static.days_remaining,
                "error": static.error,
            },
            "live": {
                "source": self.live_source,
                "status": live.status.value,
                "fetched_at": live.fetched_at.isoformat() if live.fetched_at is not None else None,
                "error": live.error,
                "http_status": live.http_status,
                "tier1_events": sum(1 for e in live.events if e.kind is not None),
                "coverage": [d.isoformat() for d in live.coverage] if live.coverage is not None else None,
            },
            "lockout": None
            if lockout is None
            else {
                "title": lockout.title,
                "at": lockout.at.isoformat(),
                "kind": lockout.kind.value if lockout.kind is not None else None,
                "sources": list(lockout.sources),
            },
            "disagreements": [
                {"side": d.side, "kind": d.kind.value, "at": d.at.isoformat(), "title": d.title}
                for d in self.disagreements
            ],
        }


def _same_release(static_event: MacroEvent, live_event: MacroEvent) -> bool:
    return (
        static_event.kind == live_event.kind and abs(static_event.timestamp - live_event.timestamp) <= MATCH_TOLERANCE
    )


class _OfflineFeed(BaseEconomicCalendar):
    """No live feed: every read is `failed` (`Offline`) and nothing is requested."""

    source: ClassVar[str] = "offline"
    label: ClassVar[str] = "Offline"

    async def fetch_events(self, force_refresh: bool = False) -> CalendarFetch:
        return CalendarFetch(CalendarStatus.FAILED, error="Offline")


class UnionTier1Calendar:
    """The one calendar the desk reads: the digest-pinned static schedule is authoritative, and live tier-1 events
    with no static match are added, so an unscheduled release still locks out.

    Its one read is `check`. Under `unknown_policy` `open` an unverified static calendar is labelled and never
    refuses; under `closed` ``risk.macro_lockout`` refuses every entry until it is verified again.
    """

    def __init__(
        self,
        static: StaticTier1Calendar,
        live: BaseEconomicCalendar,
        *,
        unknown_policy: Literal["open", "closed"],
    ):
        self.static = static
        self.live = live
        self.unknown_policy: Literal["open", "closed"] = unknown_policy

    @classmethod
    def from_config(cls, config: AppConfig, *, live: BaseEconomicCalendar | None = None) -> UnionTier1Calendar:
        """The configured static file (loaded now; a bad file becomes `invalid`) beside the ForexFactory feed."""
        risk = config.risk
        return cls(
            StaticTier1Calendar.load(risk.macro_calendar_path, risk.macro_calendar_sha256),
            live if live is not None else ForexFactoryCalendar(),
            unknown_policy=risk.macro_calendar_unknown,
        )

    @classmethod
    def offline(cls) -> UnionTier1Calendar:
        """A union that reads no file and makes no request: research replay, which never `check`s it."""
        static = StaticTier1Calendar("offline", None, False, "Offline", None, None, ())
        return cls(static, _OfflineFeed(), unknown_policy="open")

    async def check(self, now: datetime, pre_minutes: int, post_minutes: int) -> CalendarCheck:
        """Judge `now` against the static events (when verified) and the unmatched live tier-1 events (when the
        feed is ok), static first; record the next 24 hours and, when both sources are ok, their disagreements."""
        state = self.static.state(now)
        live = await self.live.fetch_events()
        static_ok = state.status is CalendarStatus.OK
        live_ok = live.status is CalendarStatus.OK
        static_events = self.static.events if static_ok else ()
        live_tier1 = (
            sorted((e for e in live.events if e.kind is not None), key=lambda e: e.timestamp) if live_ok else []
        )
        matched = [any(_same_release(s, lv) for lv in live_tier1) for s in static_events]
        live_only = [lv for lv in live_tier1 if not any(_same_release(s, lv) for s in static_events)]
        candidates: list[tuple[MacroEvent, tuple[str, ...]]] = [
            *((s, ("static", "live") if m else ("static",)) for s, m in zip(static_events, matched, strict=True)),
            *((lv, ("live",)) for lv in live_only),
        ]
        lockout = next(
            (
                CalendarLockout(e.title, e.timestamp, e.kind, sources)
                for e, sources in candidates
                if in_lockout_window(now, e.timestamp, pre_minutes, post_minutes)
            ),
            None,
        )
        horizon = now + UPCOMING_WINDOW
        upcoming = tuple(sorted((e for e, _ in candidates if now <= e.timestamp <= horizon), key=lambda e: e.timestamp))
        disagreements: tuple[CalendarDisagreement, ...] = ()
        if static_ok and live_ok and live.coverage is not None:
            first, last = live.coverage
            found = [
                *(
                    CalendarDisagreement("static_only", s.kind, s.timestamp, s.title)
                    for s, m in zip(static_events, matched, strict=True)
                    if s.kind is not None and not m and first <= s.timestamp.astimezone(ET_TZ).date() <= last
                ),
                *(
                    CalendarDisagreement("live_only", lv.kind, lv.timestamp, lv.title)
                    for lv in live_only
                    if lv.kind is not None
                ),
            ]
            disagreements = tuple(sorted(found, key=lambda d: d.at)[:MAX_DISAGREEMENTS])
        return CalendarCheck(
            now=now,
            pre_minutes=pre_minutes,
            post_minutes=post_minutes,
            unknown_policy=self.unknown_policy,
            static=state,
            live=live,
            live_source=self.live.source,
            live_label=self.live.label,
            lockout=lockout,
            upcoming=upcoming,
            disagreements=disagreements,
        )


__all__ = [
    "FOREXFACTORY_SOURCE",
    "MATCH_TOLERANCE",
    "OFFICIAL_HOSTS",
    "TIER1_KEYWORDS",
    "TIER1_TITLES",
    "BaseEconomicCalendar",
    "CalendarCheck",
    "CalendarDisagreement",
    "CalendarFetch",
    "CalendarFileError",
    "CalendarIdMismatch",
    "CalendarLockout",
    "DigestMismatch",
    "DuplicateEvent",
    "EventOutsideValidity",
    "ForexFactoryCalendar",
    "InvalidValidityRange",
    "LiveFeedError",
    "MacroEvent",
    "StaticCalendarState",
    "StaticTier1Calendar",
    "Tier1CalendarFile",
    "Tier1EventRecord",
    "Tier1Kind",
    "UnexpectedPayload",
    "UnexpectedStatus",
    "UnionTier1Calendar",
    "UnofficialSource",
    "UnsortedEvents",
    "ValidityBeyondSeries",
    "WrongNewYorkOffset",
    "macro_calendar_readiness",
    "tier1_kind",
]
