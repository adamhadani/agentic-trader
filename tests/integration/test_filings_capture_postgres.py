"""Two daemons' filings-capture stores on one PostgreSQL journal: a pass slot is journaled once."""

import asyncio

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.data.filings_capture import FILINGS_STREAM, FilingsCaptureStore
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


def _payload(covered: list[str]) -> dict:
    return {
        "requested": ["AAA", "BBB", "ZZZ"],
        "covered": covered,
        "unmapped": ["ZZZ"],
        "failed": {},
        "ticker_map_sha256": "0" * 64,
        "fetched_at": "2026-11-06T11:01:00+00:00",
        "lookback_days": 60,
        "filings": {symbol: [] for symbol in covered},
    }


async def test_concurrent_records_of_one_slot_journal_exactly_one_event(postgres_test_db):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a, b = FilingsCaptureStore(first.workflows), FilingsCaptureStore(second.workflows)
    key = f"{FILINGS_STREAM}/2026-11-06/open"
    payloads = [_payload(["AAA", "BBB"] if i % 2 else ["AAA"]) for i in range(8)]
    results = await asyncio.gather(*[(a if i % 2 else b).record(key, payload) for i, payload in enumerate(payloads)])
    assert sorted(results) == [False] * 7 + [True]
    events = await first.workflows.events(FILINGS_STREAM)
    assert [e["kind"] for e in events] == [EventKind.FILINGS_CAPTURE]
    assert await b.recorded(key) is True and await a.latest() == events[0]["payload"]
    assert await a.record(f"{FILINGS_STREAM}/2026-11-06/pre-1005", _payload(["BBB"])) is True
    assert await b.latest() == _payload(["BBB"])
    await first.engine.dispose()
    await second.engine.dispose()
