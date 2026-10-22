"""Doubles for the union tier-1 calendar's one read, ``check``.

Every desk and evaluator reads macro timing through ``UnionTier1Calendar.check``; a test of
anything else installs ``union_double`` with a prebuilt ``CalendarCheck``. A check judges its
own ``now``: ``lockout_rejection`` applies ``risk.macro_lockout`` at that instant.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

from agentic_trader.agent.calendar import (
    FOREXFACTORY_SOURCE,
    CalendarCheck,
    CalendarFetch,
    CalendarLockout,
    StaticCalendarState,
    Tier1Kind,
    UnionTier1Calendar,
)
from agentic_trader.constants import CalendarStatus
from agentic_trader.market.session import ET_TZ


NOW = datetime(2026, 10, 21, 16, tzinfo=UTC)
CALENDAR_ID = "us-tier1-test-v1"
SHA256 = "0123456789abcdef" * 4
VALID_FROM = date(2020, 1, 1)
VALID_THROUGH = date(2099, 12, 31)


def _verified(now: datetime) -> StaticCalendarState:
    today = now.astimezone(ET_TZ).date()
    return StaticCalendarState(
        CALENDAR_ID, SHA256, False, CalendarStatus.OK, VALID_FROM, VALID_THROUGH, (VALID_THROUGH - today).days, None
    )


def _unverified(status: CalendarStatus, now: datetime) -> StaticCalendarState:
    if status is CalendarStatus.EXPIRED:
        through = now.astimezone(ET_TZ).date() - timedelta(days=1)
        return StaticCalendarState(CALENDAR_ID, SHA256, False, status, VALID_FROM, through, -1, None)
    return StaticCalendarState(CALENDAR_ID, None, False, status, None, None, None, "DigestMismatch")


def _check(now: datetime, static: StaticCalendarState, lockout: CalendarLockout | None = None) -> CalendarCheck:
    return CalendarCheck(
        now=now,
        pre_minutes=60,
        post_minutes=30,
        unknown_policy="open",
        static=static,
        live=CalendarFetch(CalendarStatus.OK, fetched_at=now),
        live_source=FOREXFACTORY_SOURCE,
        live_label="ForexFactory",
        lockout=lockout,
        upcoming=(),
        disagreements=(),
    )


def clear_check(now: datetime = NOW) -> CalendarCheck:
    """A verified static calendar and an ok, empty live feed: no lockout, nothing upcoming."""
    return _check(now, _verified(now))


def lockout_check(
    title: str, at: datetime, *, kind: Tier1Kind = Tier1Kind.CPI, now: datetime | None = None
) -> CalendarCheck:
    """A verified static calendar locked out by one static event; the check judges ``now`` (default: ``at``)."""
    instant = at if now is None else now
    return _check(instant, _verified(instant), CalendarLockout(title, at, kind, ("static",)))


def unverified_check(status: CalendarStatus = CalendarStatus.EXPIRED, *, now: datetime = NOW) -> CalendarCheck:
    """An expired or invalid static calendar beside an ok, empty live feed: no lockout, unverified."""
    return _check(now, _unverified(status, now))


def union_double(check: CalendarCheck) -> AsyncMock:
    """A ``UnionTier1Calendar`` whose every ``check`` returns ``check``."""
    calendar = AsyncMock(spec=UnionTier1Calendar)
    calendar.check.return_value = check
    return calendar
