"""Two daemons' insider-capture stores on one PostgreSQL journal: a filing date is journaled once."""

import asyncio

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.data.insider_capture import CAPTURE_STREAM, InsiderCaptureStore
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


async def test_concurrent_records_of_one_filing_date_journal_exactly_one_event(postgres_test_db, tmp_path):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a = InsiderCaptureStore(first.workflows, tmp_path / "a")
    b = InsiderCaptureStore(second.workflows, tmp_path / "b")
    payload = {"filing_date": "2026-10-01", "status": "captured", "transactions": []}
    results = await asyncio.gather(*[(a if i % 2 else b).record(payload, {}) for i in range(6)])
    assert sorted(results) == [False] * 5 + [True]
    events = await first.workflows.events(CAPTURE_STREAM)
    assert [e["kind"] for e in events] == [EventKind.INSIDER_FORM4_CAPTURE]
    assert await b.latest_captured_date() == await a.latest_recorded_date()
    await first.engine.dispose()
    await second.engine.dispose()
