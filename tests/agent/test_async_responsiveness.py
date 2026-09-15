import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import load_config
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.db import SignalDatabase


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
        db = AsyncMock(spec=SignalDatabase)
        db.get_state.return_value = None
        db.get_active_notional_exposure.return_value = 0
        db.get_active_position_count.return_value = 0
        db.get_active_positions.return_value = []
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
