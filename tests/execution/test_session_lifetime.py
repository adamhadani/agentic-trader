"""session_count_v1: the fill session is session 1; exit at 15:45 NY (or close - 15 min) on session N."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    SessionEvidenceError,
    SessionLifetimePolicy,
    TradeLifetimePolicy,
    lifetime_from_dict,
)
from agentic_trader.market.session import MarketCalendarDay


NY = ZoneInfo("America/New_York")
THANKSGIVING = date(2026, 11, 26)
EARLY = {date(2026, 11, 27): time(13, 0)}


def nyse_days(start: date, end: date) -> list[MarketCalendarDay]:
    days, day = [], start
    while day <= end:
        trading = day.weekday() < 5 and day != THANKSGIVING
        close = EARLY.get(day, time(16, 0))
        days.append(
            MarketCalendarDay(
                date=day,
                is_trading_day=trading,
                is_early_close=day in EARLY,
                open_time=time(9, 30) if trading else None,
                close_time=close if trading else None,
            )
        )
        day += timedelta(days=1)
    return days


POLICY = SessionLifetimePolicy(resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS, holding_sessions=20)


def et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=NY)


def test_session_twenty_across_thanksgiving_exits_at_1545():
    # Nov 2 (1) .. Nov 25 (18), Nov 27 early close (19), Nov 30 (20).
    deadline = POLICY.holding_deadline(et(date(2026, 11, 2), 10, 40), nyse_days(date(2026, 11, 2), date(2026, 12, 15)))
    assert deadline == et(date(2026, 11, 30), 15, 45).astimezone(UTC)


def test_early_close_session_twenty_exits_fifteen_minutes_before_close():
    # Oct 30 (1), Nov 2-6 (6), 9-13 (11), 16-20 (16), 23-25 (19), Nov 27 (20, closes 13:00).
    deadline = POLICY.holding_deadline(et(date(2026, 10, 30), 11, 0), nyse_days(date(2026, 10, 30), date(2026, 12, 15)))
    assert deadline == et(date(2026, 11, 27), 12, 45).astimezone(UTC)


def test_a_short_calendar_is_evidence_error_never_a_guess():
    with pytest.raises(SessionEvidenceError, match="session_calendar_short"):
        POLICY.holding_deadline(et(date(2026, 11, 2), 10, 40), nyse_days(date(2026, 11, 2), date(2026, 11, 20)))


@pytest.mark.parametrize("when", [et(date(2026, 11, 2), 8, 0), et(date(2026, 11, 2), 16, 30), et(THANKSGIVING, 11, 0)])
def test_a_fill_outside_a_regular_session_is_evidence_error(when):
    with pytest.raises(SessionEvidenceError, match="fill_outside_regular_session"):
        POLICY.holding_deadline(when, nyse_days(date(2026, 10, 26), date(2026, 12, 31)))


def test_entry_deadline_is_one_session_of_elapsed_seconds():
    submitted = et(date(2026, 11, 2), 10, 40)
    assert POLICY.entry_deadline(submitted) == submitted.astimezone(UTC) + timedelta(seconds=57_600)


@pytest.mark.parametrize(
    "fields",
    [
        {"resting_seconds": 0, "holding_sessions": 20},
        {"resting_seconds": 57_600, "holding_sessions": 0},
        {"resting_seconds": 57_600, "holding_sessions": 61},
        {"resting_seconds": 57_600, "holding_sessions": True},
        {"resting_seconds": 57_600, "holding_sessions": 20, "close_time_et": "25:00"},
        {"resting_seconds": 57_600, "holding_sessions": 20, "version": "elapsed_utc_v2"},
    ],
)
def test_invalid_session_policies_are_refused(fields):
    with pytest.raises(ValueError):
        SessionLifetimePolicy(**fields)


def test_lifetime_from_dict_dispatches_on_version_and_keeps_old_documents():
    old = {"resting_seconds": 120, "holding_seconds": 180, "version": "elapsed_utc_v1"}
    assert lifetime_from_dict(old) == TradeLifetimePolicy(**old)
    new = {"resting_seconds": 57_600, "holding_sessions": 20, "close_time_et": "15:45", "version": "session_count_v1"}
    assert lifetime_from_dict(new) == SessionLifetimePolicy(**new)


def test_a_closing_print_fill_just_after_the_close_counts_as_session_one():
    closing_print = datetime.combine(date(2026, 11, 2), time(16, 0, 0, 250_000), tzinfo=NY)
    deadline = POLICY.holding_deadline(closing_print, nyse_days(date(2026, 11, 2), date(2026, 12, 15)))
    assert deadline == et(date(2026, 11, 30), 15, 45).astimezone(UTC)


def test_an_early_close_fill_within_the_grace_counts_as_session_one():
    # Nov 27 closes 13:00: session 1; Nov 30 .. Dec 24 are sessions 2..20.
    after_early_close = datetime.combine(date(2026, 11, 27), time(13, 0, 30), tzinfo=NY)
    deadline = POLICY.holding_deadline(after_early_close, nyse_days(date(2026, 11, 20), date(2027, 1, 15)))
    assert deadline == et(date(2026, 12, 24), 15, 45).astimezone(UTC)


def test_a_fill_beyond_the_grace_is_still_outside_the_session():
    late = datetime.combine(date(2026, 11, 2), time(16, 1, 30), tzinfo=NY)
    with pytest.raises(SessionEvidenceError, match="fill_outside_regular_session"):
        POLICY.holding_deadline(late, nyse_days(date(2026, 10, 26), date(2026, 12, 31)))
