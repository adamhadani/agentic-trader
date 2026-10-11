# tests/agent/test_corporate_actions_worker.py
"""Once per New York date, outside scan windows, bounded symbols, fail-closed captures, readiness."""

from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.data.corporate_actions_worker import CorporateActionsWorker
from agentic_trader.storage.corporate_actions import CAPTURE_STREAM, CHECK_STREAM, CorporateActionsStore
from agentic_trader.storage.workflow import WorkflowStore
from tests.data.test_corporate_actions import RAW


DUE = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)  # Monday 09:00 New York
SCAN = datetime(2026, 10, 12, 14, 40, tzinfo=UTC)  # 10:40 New York, inside the 10:35 window


class FakeSource:
    def __init__(self, data=RAW, *, fail=False):
        self.data, self.fail, self.calls = data, fail, []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        if self.fail:
            raise ConnectionError("provider down")
        now = datetime.now(UTC)
        return CorporateActionsPage(normalize_actions(self.data), now, now, len(symbols), (start, end), 1, ("p",))


def worker_for(db, config, source=None, symbols=("NVDA", "AAPL"), **kwargs):
    source = source or FakeSource()
    store = CorporateActionsStore(WorkflowStore(db), max_actions=config.corporate_actions.max_actions)

    async def provide():
        return list(symbols)

    return CorporateActionsWorker(source, store, config, symbols=provide, **kwargs), source, store


async def test_captures_once_per_date_with_the_process_date_window(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config)
    assert await worker.run_once(DUE) == "captured"
    assert source.calls == [(("AAPL", "NVDA"), date(2026, 8, 28), date(2027, 2, 9))]  # today-45 .. today+120
    assert await worker.run_once(DUE + timedelta(hours=3)) == "present"
    assert len(source.calls) == 1
    assert len(await temp_db.workflows.events(CAPTURE_STREAM)) == 1
    capture = await store.latest_capture()
    assert capture["as_of"] == "2026-10-12" and capture["symbols_count"] == 2


async def test_unchanged_content_next_day_records_only_a_check(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config)
    assert await worker.run_once(DUE) == "captured"
    assert await worker.run_once(DUE + timedelta(days=1)) == "unchanged"
    assert len(source.calls) == 2
    assert len(await temp_db.workflows.events(CAPTURE_STREAM)) == 1
    assert len(await temp_db.workflows.events(CHECK_STREAM)) == 2
    assert (await store.latest_check())["as_of"] == "2026-10-13"


async def test_restart_on_a_checked_date_is_present(temp_db, app_config):
    first, _, _ = worker_for(temp_db, app_config)
    assert await first.run_once(DUE) == "captured"
    restarted, source, _ = worker_for(temp_db, app_config)
    assert await restarted.run_once(DUE + timedelta(hours=1)) == "present" and source.calls == []


async def test_scan_window_skips_without_reading(temp_db, app_config):
    worker, source, _ = worker_for(temp_db, app_config)
    assert await worker.run_once(SCAN) == "scan_window" and source.calls == []


async def test_failure_records_nothing_and_retries(temp_db, app_config):
    worker, source, store = worker_for(temp_db, app_config, source=FakeSource(fail=True))
    with pytest.raises(ConnectionError):
        await worker.run_once(DUE)
    assert await store.latest_check() is None
    source.fail = False
    assert await worker.run_once(DUE + timedelta(minutes=5)) == "captured"


async def test_symbol_cap_fails_closed_and_symbols_are_normalised(temp_db, app_config):
    config = app_config.model_copy(
        update={"corporate_actions": app_config.corporate_actions.model_copy(update={"max_symbols": 2})}
    )
    worker, source, _ = worker_for(temp_db, config, symbols=("nvda", "AAPL", "", "NVDA"))
    assert await worker.run_once(DUE) == "captured" and source.calls[0][0] == ("AAPL", "NVDA")
    worker, source, _ = worker_for(temp_db, config, symbols=("A", "B", "C"))
    with pytest.raises(ValueError, match="exceed max_symbols 2"):
        await worker.run_once(DUE + timedelta(days=1))
    assert source.calls == []


async def test_progress_callback_reports_the_symbol_count(temp_db, app_config):
    seen = []

    async def progress(detail):
        seen.append(detail)

    worker, _, _ = worker_for(temp_db, app_config, on_progress=progress)
    await worker.run_once(DUE)
    assert seen == ["capturing 2 symbols"]
