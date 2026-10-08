"""The daemon's once-daily card-statistics snapshot: due time, idempotence, scan windows, failure, shutdown."""

import asyncio
import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.cli.commands import service
from agentic_trader.config import ScanBudget
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.execution.card_evidence import CardStatsRepository, lookup
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import CardStatsUnavailable, CardStatsWorker, in_scan_window
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import make_snapshot
from tests.research.setups.test_outcomes import AAPL_BARS, DECIDED_AT, SPY_BARS, FakeBarSource, _candidate, _event


DUE = datetime(2026, 3, 3, 13, 31, tzinfo=UTC)  # 08:31 New York (EST)
NOT_DUE = datetime(2026, 3, 3, 13, 29, tzinfo=UTC)  # 08:29


async def plant_scan(db, *, decided_at=DECIDED_AT, scan_id="scan-1", candidates=None):
    event = _event(scan_id, decided_at, candidates or [_candidate(contract="AAPL", rank=1, outcome="sent")])
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(
            session,
            stream=f"scan/{decided_at.astimezone(ET_TZ).date().isoformat()}",
            kind=EventKind.SCAN_CANDIDATES_RANKED,
            payload=event["payload"],
            key=f"scan_candidates_ranked/{scan_id}",
        )


def worker_for(db, config, bars=None, **kwargs):
    bars = bars or FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS})
    return CardStatsWorker(
        db, CardStatsRepository(db.workflows), config, revision="test-rev", bar_source=lambda _config: bars, **kwargs
    ), bars


@pytest.mark.parametrize(
    ("hhmm", "inside"),
    [("10:29", False), ("10:30", True), ("10:54", True), ("10:55", False), ("14:31", True), ("08:30", False)],
)
def test_scan_windows_span_five_minutes_before_to_twenty_after(hhmm, inside):
    hour, minute = (int(part) for part in hhmm.split(":"))
    now_et = datetime(2026, 3, 3, hour, minute, tzinfo=ET_TZ)
    assert in_scan_window(now_et, ["10:35", "14:35"]) is inside


async def test_not_due_before_the_configured_time(temp_db, app_config):
    await plant_scan(temp_db)
    worker, bars = worker_for(temp_db, app_config)
    assert await worker.run_once(NOT_DUE) == "not_due"
    assert bars.calls == [] and await worker.repository.latest() is None


async def test_due_records_one_snapshot_per_new_york_date(temp_db, app_config):
    await plant_scan(temp_db)
    worker, bars = worker_for(temp_db, app_config)

    assert await worker.run_once(DUE) == "recorded"
    calls = len(bars.calls)
    assert await worker.run_once(DUE + timedelta(minutes=5)) == "present"  # idempotent: no second labelling
    assert len(bars.calls) == calls

    events = await temp_db.workflows.events(stream="card_stats")
    assert len(events) == 1 and events[0]["kind"] == EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"
    snapshot = await worker.repository.latest()
    assert snapshot.snapshot_key == "card_stats/2026-03-03"
    assert snapshot.window_start.isoformat() == "2025-12-04"  # 90 ET dates ending 2026-03-03
    assert (snapshot.feed, snapshot.code_revision) == (app_config.market_data.alpaca_feed, "test-rev")
    assert (snapshot.events_considered, snapshot.rows_labelled) == (1, 1)
    assert snapshot.stats("BREAKOUT", "LONG").target_rate == 1.0
    assert await worker.run_once(DUE + timedelta(days=1)) == "recorded"
    assert (await worker.repository.latest()).snapshot_key == "card_stats/2026-03-04"


async def test_a_due_time_inside_a_scan_window_waits_for_it_to_pass(temp_db, app_config):
    app_config.card_policy.stats_time_et = "10:31"
    app_config.scheduler.suggestion_scan_times_et = ["10:35"]
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(datetime(2026, 3, 3, 15, 40, tzinfo=UTC)) == "scan_window"  # 10:40 NY
    assert await worker.run_once(datetime(2026, 3, 3, 15, 56, tzinfo=UTC)) == "recorded"  # 10:56 NY


async def test_a_provider_outage_writes_nothing_and_keeps_the_previous_snapshot(temp_db, app_config):
    repository = CardStatsRepository(temp_db.workflows)
    previous = make_snapshot(computed_at=DUE - timedelta(days=1))
    await repository.record(previous.model_dump(mode="json"))
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=FakeBarSource({}, raise_for={"AAPL", "SPY"}))

    with pytest.raises(CardStatsUnavailable) as raised:
        await worker.run_once(DUE)

    # Exception type names only: a provider's own text (a URL, an account) never reaches readiness.
    assert str(raised.value) == "every bar fetch failed for 1 candidates: RuntimeError"
    assert await repository.latest() == previous
    assert not await repository.exists(DUE.astimezone(ET_TZ).date())


async def test_labelling_runs_off_the_event_loop(temp_db, app_config):
    entered, release = threading.Event(), threading.Event()

    class BlockingBars:
        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            entered.set()
            assert release.wait(2), "labelling blocked the event loop"
            return AAPL_BARS if symbol == "AAPL" else SPY_BARS

    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=BlockingBars())
    task = asyncio.create_task(worker.run_once(DUE))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)  # the loop still runs while the labeller waits
        assert not task.done()
    finally:
        release.set()
    assert await asyncio.wait_for(task, 5) == "recorded"


async def test_cancelling_a_labelling_run_writes_nothing(temp_db, app_config):
    """Shutdown cancels the coroutine; the labelling thread runs to completion but records nothing."""
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class BlockingBars:
        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            entered.set()
            assert release.wait(2)
            return AAPL_BARS if symbol == "AAPL" else SPY_BARS

    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=BlockingBars())
    label = worker._snapshot

    def snapshot(*args):
        try:
            return label(*args)
        finally:
            finished.set()

    worker._snapshot = snapshot
    task = asyncio.create_task(worker.run_once(DUE))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 5)  # the thread finished its labelling...
    await asyncio.sleep(0)
    assert await worker.repository.latest() is None  # ...and nothing recorded it
    assert not await worker.repository.exists(DUE.astimezone(ET_TZ).date())


async def test_a_running_scan_defers_labelling(temp_db, app_config):
    busy = [True]
    await plant_scan(temp_db)
    worker, bars = worker_for(temp_db, app_config, scan_busy=lambda: busy[0])
    assert await worker.run_once(DUE) == "scan_busy"
    assert bars.calls == [] and await worker.repository.latest() is None
    busy[0] = False
    assert await worker.run_once(DUE) == "recorded"
    busy[0] = True
    assert await worker.run_once(DUE) == "present"  # today's snapshot exists: no scan check needed


async def test_progress_is_observed_before_a_long_labelling_run(temp_db, app_config):
    bars = FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS})
    reads_at_progress: list[int] = []
    progress = AsyncMock(side_effect=lambda detail: reads_at_progress.append(len(bars.calls)))
    await plant_scan(temp_db)
    worker, _ = worker_for(temp_db, app_config, bars=bars, on_progress=progress)
    await worker.run_once(DUE)
    progress.assert_awaited_once_with("labelling 1 scan events")
    assert reads_at_progress == [0] and bars.calls  # observed before the first provider read


@pytest.mark.parametrize(
    ("result", "observed"),
    [
        ("not_due", (True, "not_due")),
        # Any other error's message may name a host, DSN or chat: the detail (and so a Telegram
        # incident notice) carries only its type; the message goes to the log.
        (ConnectionError("postgresql://trader:secret@db.internal:5432/trader refused"), (False, "ConnectionError")),
        (
            CardStatsUnavailable("every bar fetch failed " + "y" * 300),
            (False, ("CardStatsUnavailable: every bar fetch failed " + "y" * 300)[:200]),  # /readyz explains it
        ),
    ],
    ids=["idle", "other-error-type-only", "card-stats-unavailable-message"],
)
async def test_every_poll_observes_readiness(monkeypatch, app_config, temp_db, result, observed):
    stop = asyncio.Event()

    class OnePoll:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, now=None):
            stop.set()
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(service, "CardStatsWorker", OnePoll)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=stop)
    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)
    readiness.observe.assert_awaited_once_with(HealthComponent.CARD_STATS, *observed)


OUTAGE = "CardStatsUnavailable: every bar fetch failed for 1 candidates: RuntimeError"


def scripted_worker(monkeypatch, app_config, script, stop):
    """Replace the worker with one whose polls follow ``script``: a status, an exception, or a callable."""

    class Scripted:
        def __init__(self, *args, on_progress=None, **kwargs):
            self.on_progress = on_progress

        async def run_once(self, now=None):
            step = script.pop(0)
            if not script:
                stop.set()
            if callable(step) and not isinstance(step, type):
                step = await step(self)
            if isinstance(step, Exception):
                raise step
            return step

    monkeypatch.setattr(service, "CardStatsWorker", Scripted)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    monkeypatch.setattr(app_config.card_policy, "stats_poll_seconds", 0)


async def test_a_same_day_failure_stays_visible_until_a_snapshot_exists(monkeypatch, app_config, temp_db):
    """Idle polls around the scan slots and a retry's start re-observe the failure instead of flapping ready."""
    stop = asyncio.Event()

    async def retry_then_record(worker):
        await worker.on_progress("labelling 1 scan events")
        return "recorded"

    script = [
        CardStatsUnavailable("every bar fetch failed for 1 candidates: RuntimeError"),
        "scan_window",
        "scan_busy",
        "not_due",
        retry_then_record,
        "scan_window",
        ConnectionError("postgresql://trader:secret@db.internal/trader refused"),
        "scan_window",
        "present",
        "scan_busy",
    ]
    scripted_worker(monkeypatch, app_config, script, stop)
    monkeypatch.setattr(service, "_et_today", lambda: date(2026, 10, 8))
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=stop)

    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)

    observed = [c.args[1:] for c in readiness.observe.await_args_list]
    assert all(c.args[0] == HealthComponent.CARD_STATS for c in readiness.observe.await_args_list)
    assert observed == [
        (False, OUTAGE),
        (False, OUTAGE),  # scan_window
        (False, OUTAGE),  # scan_busy
        (False, OUTAGE),  # not_due
        (False, OUTAGE),  # the retry's progress: still the last outcome
        (True, "recorded"),
        (True, "scan_window"),
        (False, "ConnectionError"),
        (False, "ConnectionError"),  # scan_window
        (True, "present"),
        (True, "scan_busy"),
    ]


async def test_a_failure_is_forgotten_on_the_next_new_york_date(monkeypatch, app_config, temp_db):
    stop = asyncio.Event()
    script = [CardStatsUnavailable("every bar fetch failed for 1 candidates: RuntimeError"), "not_due"]
    scripted_worker(monkeypatch, app_config, script, stop)
    days = iter([date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 9)])
    monkeypatch.setattr(service, "_et_today", lambda: next(days))
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=stop)

    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)

    assert [c.args[1:] for c in readiness.observe.await_args_list] == [(False, OUTAGE), (True, "not_due")]


async def test_a_database_outage_never_ends_the_worker_loop(monkeypatch, app_config, temp_db):
    """run_once fails and so does every readiness write (the database is down): the loop polls again."""
    stop, polls = asyncio.Event(), []

    class Outage:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, now=None):
            polls.append(now)
            if len(polls) == 2:
                stop.set()
            raise ConnectionRefusedError("database is down")

    monkeypatch.setattr(service, "CardStatsWorker", Outage)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    monkeypatch.setattr(app_config.card_policy, "stats_poll_seconds", 0)
    readiness = SimpleNamespace(observe=AsyncMock(side_effect=ConnectionRefusedError("database is down")))
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=stop)
    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)
    assert len(polls) == 2 and readiness.observe.await_count == 2  # one failed observation per poll


async def test_the_daemon_worker_waits_while_a_scan_holds_the_lock(monkeypatch, app_config, temp_db):
    app_config.card_policy.stats_time_et = "00:00"
    app_config.scheduler.suggestion_scan_times_et = []
    stop = asyncio.Event()
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    readiness = SimpleNamespace(observe=AsyncMock(side_effect=lambda *args: stop.set()))
    copilot = SimpleNamespace(
        db=temp_db, card_stats=CardStatsRepository(temp_db.workflows), _shutdown_event=stop, scan_running=True
    )
    await asyncio.wait_for(service.run_card_stats_worker(copilot, app_config, readiness), 2)
    readiness.observe.assert_awaited_once_with(HealthComponent.CARD_STATS, True, "scan_busy")


async def test_shutdown_cancels_an_in_flight_labelling_run(monkeypatch, app_config, temp_db):
    entered, release = threading.Event(), threading.Event()

    class Blocking:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, now=None):
            await asyncio.to_thread(lambda: (entered.set(), release.wait(5)))
            return "recorded"

    monkeypatch.setattr(service, "CardStatsWorker", Blocking)
    monkeypatch.setattr(service, "runtime_identity", lambda: {"revision": "test-rev"})
    readiness = SimpleNamespace(observe=AsyncMock())
    copilot = SimpleNamespace(db=temp_db, card_stats=None, _shutdown_event=asyncio.Event())
    task = asyncio.create_task(service.run_card_stats_worker(copilot, app_config, readiness))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        copilot._shutdown_event.set()
        task.cancel()  # what the daemon's shutdown does
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    finally:
        release.set()
    readiness.observe.assert_not_awaited()


def test_the_copilots_readiness_tracks_card_stats(app_config, temp_db, mock_notifier):
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
    )
    assert copilot.readiness.card_stats_enabled is True


async def test_scan_running_reflects_the_scan_lock(app_config, temp_db, mock_notifier):
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
    )
    assert copilot.scan_running is False
    async with copilot._hold_scan_lock(None):
        assert copilot.scan_running is True
    assert copilot.scan_running is False


async def test_a_recorded_snapshot_reaches_the_next_cards_evidence(budget_desk, temp_db, app_config):  # noqa: F811
    """Integration: journal -> worker -> card_stats stream -> TradingCopilot.run_scan -> card provenance."""
    app_config.card_policy.stats_time_et = "00:00"
    app_config.scheduler.suggestion_scan_times_et = []
    now = datetime.now(UTC)
    await plant_scan(
        temp_db,
        decided_at=now - timedelta(hours=1),
        candidates=[_candidate(contract="AAPL", strategy="TREND_PULLBACK", rank=1, outcome="sent")],
    )
    worker, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(now) == "recorded"

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    snapshot = await CardStatsRepository(temp_db.workflows).latest()
    assert evidence["snapshot_key"] == snapshot.snapshot_key
    assert evidence["status"] == "insufficient" and evidence["n_mature"] == 0  # the planted label is immature
    assert (
        lookup(snapshot, "TREND_PULLBACK", "LONG", now=now, max_age=timedelta(days=4), min_mature=20).status
        == "insufficient"
    )
