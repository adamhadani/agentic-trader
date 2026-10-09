"""Daemon job registration: session-aligned suggestion scans and the scoped intraday job.

The suggestion scans are cron jobs on the New York clock, not an interval that drifts
against the session. The 15-minute intraday job only ever scans the explicitly
configured contracts -- the wide universe is reserved for the session-aligned scans.
"""

import asyncio
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.cli.commands import service
from agentic_trader.config import ScanBudget, SchedulerConfig, UniverseConfig, UniverseEntry
from agentic_trader.execution.durable import EventKind, NotificationKind, ScanTrigger, WorkKind
from agentic_trader.notifier.outbox import NotificationDispatcher


ET = ZoneInfo("America/New_York")


def test_suggestion_scans_are_cron_jobs_in_new_york_on_weekdays(config):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_suggestion_scans(scheduler, copilot, config, use_llm=True)
    calls = scheduler.add_job.call_args_list
    assert len(calls) == 2
    for call, (hour, minute) in zip(calls, [(10, 35), (14, 35)], strict=True):
        assert call.args[1] == "cron"
        assert call.kwargs["hour"] == hour and call.kwargs["minute"] == minute
        assert call.kwargs["day_of_week"] == "mon-fri"
        assert call.kwargs["timezone"] == ZoneInfo("America/New_York")
    assert calls[0].kwargs["kwargs"] == {"digest": False, "scheduled_time_et": "10:35"}
    assert calls[1].kwargs["kwargs"] == {"digest": True, "scheduled_time_et": "14:35"}


def test_suggestion_scans_never_overlap_and_tolerate_a_late_start(config):
    config.scheduler.suggestion_scan_times_et = ["10:35"]
    config.scheduler.suggestion_scan_misfire_grace_seconds = 1234
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=False)
    kwargs = scheduler.add_job.call_args.kwargs
    assert kwargs["coalesce"] is True
    assert kwargs["max_instances"] == 1
    assert kwargs["misfire_grace_time"] == 1234


def test_suggestion_scan_misfire_grace_defaults_wider_than_the_other_jobs():
    """A dark maintenance wake ~15 minutes late must still run the scan (September 28)."""
    scheduler = SchedulerConfig()
    assert scheduler.suggestion_scan_misfire_grace_seconds == 1800
    assert scheduler.misfire_grace_seconds == 60  # every other job keeps its grace


@pytest.mark.parametrize("seconds", [0, -1])
def test_suggestion_scan_misfire_grace_must_be_positive(seconds):
    with pytest.raises(ValueError):
        SchedulerConfig(suggestion_scan_misfire_grace_seconds=seconds)


async def test_suggestion_scan_runs_full_budget_only_when_the_equity_session_is_open(config):
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.publish_scan_digest = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(False, "holiday"))
    job = service.make_suggestion_scan(copilot, use_llm=True)
    await job(digest=True)
    copilot.run_scan.assert_not_awaited()
    copilot.publish_scan_digest.assert_awaited_once()  # a quiet day still reports
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    await job(digest=False)
    copilot.run_scan.assert_awaited_once_with(
        use_llm=True,
        dry_run=False,
        asset_class="equity",
        budget=ScanBudget.FULL,
        shadow_evidence=True,
        trigger=ScanTrigger.SUGGESTION_SCAN,
        scheduled_time_et=None,
        scheduled_at=None,
    )


def test_each_suggestion_scan_job_names_its_scheduled_time(config):
    config.scheduler.suggestion_scan_times_et = ["09:45", "10:35", "14:35"]
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=True)
    registered = [call.kwargs["kwargs"]["scheduled_time_et"] for call in scheduler.add_job.call_args_list]
    assert registered == ["09:45", "10:35", "14:35"]


async def test_the_scheduled_time_reaches_the_scan(config):
    """Only the scan whose scheduled time is the PEAD decision time may produce a drift card."""
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    job = service.make_suggestion_scan(copilot, use_llm=False)
    await job(digest=False, scheduled_time_et="10:35")
    assert copilot.run_scan.await_args.kwargs["scheduled_time_et"] == "10:35"


async def test_the_scan_learns_when_its_slot_was_due(config):
    """The due instant (today's New York date at the slot) lets the scan measure its lateness."""
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    job = service.make_suggestion_scan(copilot, use_llm=False)
    before = datetime.now(ET).date()
    await job(digest=False, scheduled_time_et="10:35")
    after = datetime.now(ET).date()  # the two differ only across New York midnight
    scheduled_at = copilot.run_scan.await_args.kwargs["scheduled_at"]
    assert scheduled_at in {datetime.combine(day, time(10, 35), tzinfo=ET) for day in (before, after)}
    assert scheduled_at.tzinfo == ET


# --- A dropped suggestion scan is never silent (September 28 misfire) -------------------


def _missed(job_id: str, scheduled: datetime) -> JobExecutionEvent:
    return JobExecutionEvent(EVENT_JOB_MISSED, job_id, "default", scheduled)


def _missed_listener(config, outbox) -> Any:
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, SimpleNamespace(outbox=outbox), config, use_llm=False)
    [call] = scheduler.add_listener.call_args_list
    listener, mask = call.args
    assert mask == EVENT_JOB_MISSED
    return listener


async def _settle(listener) -> None:
    """Let the listener's scheduled outbox writes finish."""
    for _ in range(200):
        if not listener.pending:
            return
        await asyncio.gather(*listener.pending, return_exceptions=True)


async def _notices(db) -> dict[str, dict]:
    """Queued durable notices, by their outbox dedup key."""
    items = {item.id: item for item in await db.workflows.list_work(WorkKind.NOTIFICATION)}
    queued = [e for e in await db.workflows.events() if e["kind"] == EventKind.NOTIFICATION_QUEUED]
    assert len(queued) == len(items)
    return {e["payload"]["dedup_key"]: items[e["stream"].removeprefix("notification/")].payload for e in queued}


def _missed_records(caplog) -> list:
    return [r for r in caplog.records if r.__dict__.get("event") == "suggestion_scan_missed"]


async def test_a_missed_suggestion_scan_queues_one_durable_notice_per_slot(config, temp_db, caplog):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    listener = _missed_listener(config, NotificationDispatcher(temp_db.workflows, MagicMock(), config.execution))
    scheduled = datetime(2026, 9, 28, 10, 35, tzinfo=ET)

    with caplog.at_level("WARNING", logger="copilot"):
        listener(_missed("suggestion_scan_0", scheduled))
        await _settle(listener)
        listener(_missed("suggestion_scan_0", scheduled))  # a second report of the same slot
        await _settle(listener)

    [(key, payload)] = (await _notices(temp_db)).items()
    assert key == "suggestion-scan-missed/2026-09-28/10:35"
    assert payload["kind"] == NotificationKind.MESSAGE
    text = payload["arguments"]["text"]
    assert text.startswith("⚠️ 10:35 NY suggestion scan was missed (")
    assert "min late" in text and "host asleep or event loop blocked" in text
    # Never claims a loss outright: the listener does not read the holiday calendar.
    assert "If the market was open, this slot produced no cards (and no PEAD decision)." in text
    assert "digest" not in text
    record = _missed_records(caplog)[0]
    assert record.__dict__["job_id"] == "suggestion_scan_0"
    assert record.__dict__["slot_et"] == "10:35"
    assert record.__dict__["dedup_key"] == "suggestion-scan-missed/2026-09-28/10:35"
    assert record.__dict__["late_seconds"] > 0


async def test_a_missed_slot_reports_how_late_the_scheduler_woke(config):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    outbox = MagicMock(publish_message=AsyncMock())
    listener = _missed_listener(config, outbox)

    listener(_missed("suggestion_scan_1", datetime.now(UTC) - timedelta(minutes=47, seconds=20)))
    await _settle(listener)

    text = outbox.publish_message.await_args.args[0]
    key = outbox.publish_message.await_args.kwargs["key"]
    assert text.startswith("⚠️ 14:35 NY suggestion scan was missed (the scheduler woke 47 min late")
    # 14:35 is not the PEAD decision time, and as the last slot it owns the digest.
    assert "PEAD" not in text
    assert "If the market was open, this slot produced no cards." in text
    assert "end-of-session digest" in text
    assert key.startswith("suggestion-scan-missed/") and key.endswith("/14:35")


async def test_the_missed_scan_notices_are_keyed_per_slot_and_date(config, temp_db):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    listener = _missed_listener(config, NotificationDispatcher(temp_db.workflows, MagicMock(), config.execution))

    listener(_missed("suggestion_scan_0", datetime(2026, 9, 28, 10, 35, tzinfo=ET)))
    listener(_missed("suggestion_scan_1", datetime(2026, 9, 28, 14, 35, tzinfo=ET)))
    listener(_missed("suggestion_scan_0", datetime(2026, 9, 29, 10, 35, tzinfo=ET)))
    await _settle(listener)

    assert sorted(await _notices(temp_db)) == [
        "suggestion-scan-missed/2026-09-28/10:35",
        "suggestion-scan-missed/2026-09-28/14:35",
        "suggestion-scan-missed/2026-09-29/10:35",
    ]


@pytest.mark.parametrize("job_id", ["swing_scan", "intraday_scan", "position_monitor", "suggestion_scan_9"])
async def test_other_missed_jobs_queue_no_notice(config, caplog, job_id):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    outbox = MagicMock(publish_message=AsyncMock())
    listener = _missed_listener(config, outbox)

    with caplog.at_level("WARNING", logger="copilot"):
        listener(_missed(job_id, datetime.now(UTC) - timedelta(hours=1)))
        await _settle(listener)

    outbox.publish_message.assert_not_awaited()
    assert _missed_records(caplog) == []


async def test_a_failing_outbox_never_escapes_the_listener(config, caplog):
    config.scheduler.suggestion_scan_times_et = ["10:35"]
    outbox = MagicMock(publish_message=AsyncMock(side_effect=RuntimeError("database is down")))
    listener = _missed_listener(config, outbox)

    with caplog.at_level("WARNING", logger="copilot"):
        listener(_missed("suggestion_scan_0", datetime.now(UTC) - timedelta(minutes=40)))
        await _settle(listener)

    outbox.publish_message.assert_awaited_once()
    assert [r for r in caplog.records if r.__dict__.get("event") == "suggestion_scan_missed_notice_failed"]
    assert len(_missed_records(caplog)) == 1  # the log event still records the miss


def test_a_malformed_event_or_no_running_loop_never_raises(config, caplog):
    config.scheduler.suggestion_scan_times_et = ["10:35"]
    outbox = MagicMock(publish_message=AsyncMock())
    listener = _missed_listener(config, outbox)

    with caplog.at_level("WARNING", logger="copilot"):
        listener(_missed("suggestion_scan_0", None))  # a malformed event: no scheduled run time
        listener(_missed("suggestion_scan_0", datetime.now(UTC)))  # synchronous: no running loop

    outbox.publish_message.assert_not_called()
    failures = [r for r in caplog.records if r.__dict__.get("event") == "suggestion_scan_missed_notice_failed"]
    assert len(failures) == 2
    assert listener.pending == set()


def test_no_listener_without_suggestion_scans(config):
    config.scheduler.suggestion_scan_times_et = []
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=True)
    scheduler.add_listener.assert_not_called()


async def test_a_real_scheduler_reports_a_dropped_scan_through_the_outbox(config, temp_db):
    """End to end through APScheduler: a run past its grace is dropped, never executed, and noticed.

    The slot is six hours from now, so no real cron fire can fall between the backdated
    run time and now: APScheduler would coalesce onto that on-time run and execute it.
    """
    slot = (datetime.now(ET) + timedelta(hours=6)).strftime("%H:%M")
    config.scheduler.suggestion_scan_times_et = [slot]
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.outbox = NotificationDispatcher(temp_db.workflows, MagicMock(), config.execution)
    scheduler = AsyncIOScheduler()
    service.register_suggestion_scans(scheduler, copilot, config, use_llm=False)
    due = datetime.now(UTC) - timedelta(seconds=config.scheduler.suggestion_scan_misfire_grace_seconds + 60)
    scheduler.start(paused=True)
    try:
        scheduler.modify_job("suggestion_scan_0", next_run_time=due)
        scheduler.resume()
        notices: dict[str, dict] = {}
        for _ in range(200):
            notices = await _notices(temp_db)
            if notices:
                break
            await asyncio.sleep(0.02)
    finally:
        scheduler.shutdown(wait=False)

    [(key, payload)] = notices.items()
    assert key == f"suggestion-scan-missed/{due.astimezone(ET).date().isoformat()}/{slot}"
    assert key.endswith(f"/{slot}")
    assert payload["arguments"]["text"].startswith(f"⚠️ {slot} NY suggestion scan was missed (the scheduler woke 31 min")
    copilot.run_scan.assert_not_awaited()
    copilot.session_provider.is_session_active.assert_not_called()


def test_intraday_scan_is_restricted_to_non_universe_contracts(config):
    config.universe = UniverseConfig(groups={"g": [UniverseEntry(symbol="AAPL"), UniverseEntry(symbol="MSFT")]})
    config.contracts = {"SPY": MagicMock(), "AAPL": MagicMock(), "MSFT": MagicMock(), "/MES": MagicMock()}
    config.explicit_contracts = ("SPY", "/MES")
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_intraday_scan(scheduler, copilot, config, use_llm=True)
    job = scheduler.add_job.call_args.args[0]  # a functools.partial; its keywords are inspectable
    assert job.keywords["symbols"] == ["/MES", "SPY"]


def test_no_configured_times_disables_the_suggestion_scans(config):
    """F7: an empty suggestion_scan_times_et is the documented operator off switch."""
    config.scheduler.suggestion_scan_times_et = []
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=True)
    scheduler.add_job.assert_not_called()


def test_intraday_scan_is_not_registered_without_explicit_contracts(config):
    """F3: run_scan(symbols=[]) selects the whole universe, so an empty scope must
    register no job at all rather than a 15-minute universe scan."""
    config.universe = UniverseConfig(groups={"g": [UniverseEntry(symbol="AAPL")]})
    config.contracts = {"AAPL": MagicMock()}
    config.explicit_contracts = ()
    scheduler = MagicMock()
    service.register_intraday_scan(scheduler, MagicMock(), config, use_llm=True)
    scheduler.add_job.assert_not_called()


def test_card_expiry_sweep_is_a_five_minute_interval_job_that_also_runs_at_startup():
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_card_expiry_sweep(scheduler, copilot)
    scheduler.add_job.assert_called_once()
    call = scheduler.add_job.call_args
    assert call.args[0] is copilot.expire_stale_cards
    assert call.args[1] == "interval"
    assert call.kwargs["minutes"] == 5
    assert call.kwargs["id"] == "card_expiry_sweep"
    next_run_time = call.kwargs["next_run_time"]
    assert isinstance(next_run_time, datetime) and next_run_time.tzinfo is UTC


async def test_intraday_scan_runs_only_inside_a_session_and_keeps_its_symbols(config):
    config.contracts = {"SPY": MagicMock()}
    config.explicit_contracts = ("SPY",)
    scheduler = MagicMock()
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    service.register_intraday_scan(scheduler, copilot, config, use_llm=False)
    job = scheduler.add_job.call_args.args[0]
    await job()
    copilot.run_scan.assert_awaited_once_with(
        use_llm=False,
        dry_run=False,
        asset_class="all",
        timeframe="15m",
        symbols=["SPY"],
        budget=ScanBudget.FULL,
    )
    copilot.run_scan.reset_mock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(False, "closed"))
    await job()
    copilot.run_scan.assert_not_awaited()


async def test_suggestion_job_requests_shadow_evidence_and_names_its_trigger(config):
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    job = service.make_suggestion_scan(copilot, use_llm=True)
    await job(digest=False, scheduled_time_et="10:35")
    kwargs = copilot.run_scan.await_args.kwargs
    assert kwargs["shadow_evidence"] is True
    assert kwargs["trigger"] is ScanTrigger.SUGGESTION_SCAN
    assert kwargs["budget"] is ScanBudget.FULL


def test_swing_scan_job_names_its_trigger_and_still_runs_at_startup(config):
    """The interval job passes FULL budget + SWING_SCAN and keeps its immediate first run
    (the ``scan`` readiness component needs an observation in every daemon run)."""
    scheduler = MagicMock()
    copilot = MagicMock()
    before = datetime.now(UTC)
    service.register_swing_scan(scheduler, copilot, config, use_llm=True)
    after = datetime.now(UTC)

    scheduler.add_job.assert_called_once()
    call = scheduler.add_job.call_args
    assert call.args[0] == copilot.run_scan
    assert call.args[1] == "interval"
    assert call.kwargs["id"] == "swing_scan"
    assert call.kwargs["hours"] == config.scheduler.cron_hour_interval
    assert call.kwargs["args"] == [True, False]
    assert call.kwargs["kwargs"] == {"budget": ScanBudget.FULL, "trigger": ScanTrigger.SWING_SCAN}
    assert before <= call.kwargs["next_run_time"] <= after
