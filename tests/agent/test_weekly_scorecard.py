"""The weekly scorecard job: due logic, once per ISO week, retry on failure, nothing but type names in the text."""

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from agentic_trader.config import ScorecardConfig
from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.presentation.scorecard import format_scorecard
from agentic_trader.research.setups.scorecard import LaneScorecard
from agentic_trader.storage.models import DomainEventRecord
from tests.agent.test_auto_entry_sweep import auto_desk, notifications, temp_db  # noqa: F401


SECRET = "secret-detail-must-not-leak"
KEY = "weekly-scorecard/2026-W46"  # ISO week of Saturday 2026-11-14
FRIDAY_2300 = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)  # Friday 23:00 New York (EST)
SATURDAY_0900 = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)
SATURDAY_0915 = datetime(2026, 11, 14, 14, 15, tzinfo=UTC)
MONDAY_1000 = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
TUESDAY_2300 = datetime(2026, 11, 18, 4, 0, tzinfo=UTC)  # Tuesday 23:00 New York
WEDNESDAY_1000 = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
NEXT_SATURDAY_0900 = datetime(2026, 11, 21, 14, 0, tzinfo=UTC)


def card_at(now: datetime) -> LaneScorecard:
    return LaneScorecard(window_start=now, window_end=now, lanes=(), account=None)


@pytest.fixture
def desk(auto_desk):  # noqa: F811
    """The paper-scope desk of the auto-entry tests (this module's scorecard job never reads its broker)."""
    return auto_desk


@pytest.fixture
def builds(desk, monkeypatch):
    """Replace the scorecard build with an offline fake and record every call."""
    calls: list[dict] = []

    async def fake_build(db, config, *, now, activity_days, record_days, **kwargs):
        calls.append({"now": now, "activity_days": activity_days, "record_days": record_days, **kwargs})
        return card_at(now)

    monkeypatch.setattr("agentic_trader.agent.copilot.build_scorecard", fake_build)
    return calls


async def scorecard_events(db) -> list[DomainEventRecord]:
    async with db.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(DomainEventRecord).where(DomainEventRecord.kind == EventKind.WEEKLY_SCORECARD)
                )
            ).all()
        )


async def test_friday_night_is_not_due(desk, builds):
    assert await desk.publish_weekly_scorecard(FRIDAY_2300) is False
    assert builds == []
    assert not [key for key in await notifications(desk.db) if key.startswith("weekly-scorecard/")]


async def test_saturday_nine_publishes_one_message_and_one_event(desk, builds):
    assert await desk.publish_weekly_scorecard(SATURDAY_0900) is True

    [call] = builds
    assert (call["now"], call["activity_days"], call["record_days"]) == (SATURDAY_0900, 7, 90)
    queued = await notifications(desk.db)
    assert queued[KEY] == {
        "kind": NotificationKind.MESSAGE,
        "arguments": {"text": format_scorecard(card_at(SATURDAY_0900)), "formatted": True},
    }
    [event] = await scorecard_events(desk.db)
    assert (event.stream, event.event_key) == ("scorecard/weekly", KEY)


async def test_a_second_tick_that_week_is_a_no_op(desk, builds):
    assert await desk.publish_weekly_scorecard(SATURDAY_0900) is True
    assert await desk.publish_weekly_scorecard(SATURDAY_0915) is False
    assert len(builds) == 1  # the key is read before any bar read
    assert len(await scorecard_events(desk.db)) == 1


async def test_a_daemon_down_saturday_and_up_monday_publishes_once(desk, builds):
    assert await desk.publish_weekly_scorecard(MONDAY_1000) is True
    assert await desk.publish_weekly_scorecard(MONDAY_1000.replace(minute=15)) is False
    assert KEY in await notifications(desk.db)  # the missed week, not Monday's own ISO week (W47)
    assert "weekly-scorecard/2026-W47" not in await notifications(desk.db)
    assert len(builds) == 1


async def test_a_restart_mid_week_after_the_publish_sends_nothing(desk, builds):
    assert await desk.publish_weekly_scorecard(SATURDAY_0900) is True
    assert await desk.publish_weekly_scorecard(WEDNESDAY_1000) is False
    assert len(builds) == 1


async def test_a_restart_late_in_a_week_that_never_published_sends_nothing(desk, builds):
    assert await desk.publish_weekly_scorecard(WEDNESDAY_1000) is False  # Saturday + 4 days 1 hour
    assert builds == []
    assert await desk.publish_weekly_scorecard(TUESDAY_2300) is True  # still inside the catch-up window


async def test_the_next_weeks_due_time_publishes_that_weeks_key(desk, builds):
    assert await desk.publish_weekly_scorecard(SATURDAY_0900) is True
    assert await desk.publish_weekly_scorecard(NEXT_SATURDAY_0900) is True
    queued = await notifications(desk.db)
    assert {key for key in queued if key.startswith("weekly-scorecard/")} == {KEY, "weekly-scorecard/2026-W47"}


async def test_a_new_year_week_uses_the_iso_year(desk, builds):
    # Saturday 2027-01-02 belongs to ISO week 2026-W53.
    assert await desk.publish_weekly_scorecard(datetime(2027, 1, 2, 15, 0, tzinfo=UTC)) is True
    assert "weekly-scorecard/2026-W53" in await notifications(desk.db)


async def test_a_build_failure_retries_next_tick_and_logs_the_type_only(desk, builds, monkeypatch, caplog):
    async def failing(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("agentic_trader.agent.copilot.build_scorecard", failing)
    with caplog.at_level(logging.WARNING):
        assert await desk.publish_weekly_scorecard(SATURDAY_0900) is False

    [record] = [r for r in caplog.records if r.__dict__.get("event") == "weekly_scorecard_failed"]
    assert record.__dict__["error_type"] == "RuntimeError"
    assert SECRET not in caplog.text and record.exc_info is None
    assert KEY not in await notifications(desk.db)
    assert await scorecard_events(desk.db) == []

    monkeypatch.setattr("agentic_trader.agent.copilot.build_scorecard", _recovered)
    assert await desk.publish_weekly_scorecard(SATURDAY_0915) is True
    assert KEY in await notifications(desk.db)


async def _recovered(db, config, *, now, activity_days, record_days, **kwargs):
    return card_at(now)


async def test_a_queue_failure_never_raises_into_the_scheduler(desk, builds, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise OSError(SECRET)

    monkeypatch.setattr(desk.db.workflows, "queue_weekly_scorecard", broken)
    with caplog.at_level(logging.WARNING):
        assert await desk.publish_weekly_scorecard(SATURDAY_0900) is False
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "weekly_scorecard_failed"]
    assert record.__dict__["error_type"] == "OSError" and SECRET not in caplog.text


async def test_nothing_is_built_while_a_scan_runs(desk, builds):
    async with desk._scan_lock:
        assert await desk.publish_weekly_scorecard(SATURDAY_0900) is False
    assert builds == []
    assert await desk.publish_weekly_scorecard(SATURDAY_0915) is True  # the next tick retries


async def test_disabled_publishes_nothing(desk, builds):
    desk.config.scorecard = ScorecardConfig(enabled=False)
    assert await desk.publish_weekly_scorecard(SATURDAY_0900) is False
    assert builds == []


async def test_a_configured_due_day_and_time_are_honoured(desk, builds):
    desk.config.scorecard = ScorecardConfig(due_weekday=6, due_time_et="18:30")  # Sunday 18:30 ET
    assert await desk.publish_weekly_scorecard(datetime(2026, 11, 15, 23, 0, tzinfo=UTC)) is False  # 18:00 ET
    assert await desk.publish_weekly_scorecard(datetime(2026, 11, 15, 23, 30, tzinfo=UTC)) is True
