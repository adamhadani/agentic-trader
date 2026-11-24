"""Two daemons' earnings-capture stores on one PostgreSQL journal: a capture date is journaled once."""

import asyncio
import gzip
import hashlib
import stat
from datetime import date

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.data.earnings_capture import CAPTURE_STREAM, EarningsCaptureStore
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


async def test_concurrent_records_of_one_capture_date_journal_exactly_one_event(postgres_test_db, tmp_path):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a = EarningsCaptureStore(first.workflows, tmp_path / "a")
    b = EarningsCaptureStore(second.workflows, tmp_path / "b")
    bodies = {date(2026, 11, 6): b'{"one": 1}', date(2026, 11, 9): b'{"two": 2}'}
    payload = {
        "capture_date": "2026-11-06",
        "pages": [
            {"date": day.isoformat(), "body_sha256": hashlib.sha256(body).hexdigest()} for day, body in bodies.items()
        ],
        "rows": {"2026-11-06": [["A"], ["B"]], "2026-11-09": [["C"]]},
    }
    assert await a.record(payload, bodies) is True
    results = await asyncio.gather(*[(a if i % 2 else b).record(payload, bodies) for i in range(6)])
    assert results == [False] * 6
    events = await first.workflows.events(CAPTURE_STREAM)
    assert [e["kind"] for e in events] == [EventKind.EARNINGS_CALENDAR_CAPTURE]
    assert await b.recorded(date(2026, 11, 6)) is True
    assert await b.latest() == {"capture_date": date(2026, 11, 6), "pages": 2, "rows": 3}
    for page in payload["pages"]:
        path = tmp_path / "a" / "2026-11-06" / f"{page['date']}.json.gz"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == page["body_sha256"]
    await first.engine.dispose()
    await second.engine.dispose()
