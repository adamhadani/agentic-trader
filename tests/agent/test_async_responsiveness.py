import asyncio
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.cli.commands import service
from agentic_trader.config import load_config
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.notifier.telegram_bot import TelegramNotifier


@pytest.mark.parametrize("operation", ["scan", "schema"])
@pytest.mark.asyncio
async def test_blocking_dependencies_leave_event_loop_responsive(operation, monkeypatch, temp_db):
    entered, release = Event(), Event()

    def blocking_work(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=2):
            raise AssertionError("Event loop could not release worker")
        return SimpleNamespace(daily=SimpleNamespace(empty=True))

    if operation == "schema":
        monkeypatch.setattr("agentic_trader.storage.db.run_migrations_head", blocking_work)
        pending = temp_db.init_db()
    else:
        config = load_config()
        fetcher = MagicMock()
        fetcher.fetch_data.side_effect = blocking_work
        db = temp_db
        copilot = TradingCopilot(config, db=db, data_fetcher=fetcher, notifier=TelegramNotifier(None, None))
        copilot.regime_detector = AsyncMock()
        copilot.session_provider = AsyncMock()
        copilot.session_provider.is_session_active.return_value = (True, "open")
        copilot.calendar = AsyncMock()
        copilot.calendar.is_in_lockout_window.return_value = (False, None)
        pending = copilot.run_scan(use_llm=False, symbols=[next(iter(config.contracts))])
    task = asyncio.create_task(pending)
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not task.done(), "Blocking work ran on the event loop"
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_daemon_runs_initial_jobs_after_slow_telegram_startup(monkeypatch, config):
    config.scheduler.intraday_scan_enabled = False
    config.scheduler.macro_briefing_enabled = False
    scan_ran, monitor_ran, initialized = asyncio.Event(), asyncio.Event(), asyncio.Event()
    copilot = MagicMock()
    copilot.broker.connect = AsyncMock(return_value=True)
    copilot.broker.supports_trade_stream = False
    copilot.broker.stop_trade_stream = AsyncMock()
    copilot.db.record_audit = AsyncMock()
    copilot.metrics_server = None
    copilot._shutdown_event = asyncio.Event()
    copilot.notifier.is_configured.return_value = True
    copilot.notifier.stop_polling = AsyncMock()

    async def slow_start():
        # Longer than APScheduler's default one-second misfire window.
        await asyncio.sleep(1.1)
        initialized.set()

    async def scan(*args, **kwargs):
        assert initialized.is_set()
        scan_ran.set()

    async def monitor():
        assert initialized.is_set()
        monitor_ran.set()

    copilot.notifier.start_polling = slow_start
    copilot.workflow_worker = AsyncMock()
    copilot.run_scan = scan
    copilot.monitor_positions = monitor
    monkeypatch.setattr(service, "get_copilot_and_config", lambda: (copilot, config))
    monkeypatch.setattr(service, "monitor_event_loop", AsyncMock())
    task = asyncio.create_task(service.daemon.callback.__wrapped__(no_llm=True))
    try:
        await asyncio.wait_for(asyncio.gather(scan_ran.wait(), monitor_ran.wait()), timeout=5)
    finally:
        task.cancel()
        await task
    copilot.notifier.stop_polling.assert_awaited_once()


@pytest.mark.parametrize(
    ("component", "factory"),
    [
        (HealthComponent.ALPHA_OBSERVER, "SessionObservationService"),
        (HealthComponent.ALPHA_DECISIONS, "SessionDecisionService"),
    ],
)
async def test_session_worker_finishes_inflight_capture_before_closing_readers(monkeypatch, config, component, factory):

    config.alpha_pipeline.observations.enabled = True
    entered, release, closed, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()

    @contextmanager
    def readers(*args):
        try:
            yield object()
        finally:
            closed.set()

    class Observer:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self):
            entered.set()
            await release.wait()
            assert not closed.is_set()
            return []

    monkeypatch.setattr(service, "session_source", readers)
    monkeypatch.setattr(service, factory, Observer)
    readiness = SimpleNamespace(observe=AsyncMock())
    task = asyncio.create_task(
        service.run_session_worker(config, object(), readiness, MagicMock(), stopped, component=component)
    )
    await asyncio.wait_for(entered.wait(), 2)
    stopped.set()
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 2)
    assert closed.is_set()
    assert readiness.observe.await_args.args[0] == component
