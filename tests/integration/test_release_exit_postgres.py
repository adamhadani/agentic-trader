"""Two daemons' release-exit passes on one PostgreSQL journal: a date's pass and its notices are journaled once."""

import asyncio

import pytest

from agentic_trader.agent.earnings import EarningsEvent, EarningsTiming
from agentic_trader.config import AppConfig, ReleaseExitConfig
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.execution.release_exit import RELEASE_EXIT_STREAM, ReleaseExitService
from agentic_trader.storage.db import SignalDatabase
from tests.execution.test_release_exit import FRIDAY, FakeBroker, FakeCalendar, FakeCaptures, FakeEarnings, at


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


def _service(db: SignalDatabase) -> ReleaseExitService:
    return ReleaseExitService(
        ReleaseExitConfig(mode="preview"),
        db,
        close_service=PositionCloseService(FakeBroker([]), db),
        earnings=FakeEarnings({"AAPL": EarningsEvent("AAPL", FRIDAY, EarningsTiming.AFTER_HOURS)}),
        captures=FakeCaptures(None),
        calendar=FakeCalendar(),
        scope_of=lambda position: "native",
    )


async def test_concurrent_passes_of_one_date_journal_exactly_one_event_and_notice(postgres_test_db):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    signal_id = await first.record_signal(
        contract="AAPL",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=25.0,
        status=SignalStatus.EXECUTED,
        asset_class=AssetClass.EQUITY,
        quantity=5.0,
    )
    services = [_service(first if i % 2 else second) for i in range(6)]
    statuses = await asyncio.gather(*[release.run(at(FRIDAY, 15, 30)) for release in services])
    assert sorted(statuses) == ["done"] + ["present"] * 5

    events = await first.workflows.events(RELEASE_EXIT_STREAM)
    assert [event["kind"] for event in events] == [EventKind.RELEASE_EXIT_PASS]
    notices = await second.workflows.list_work(WorkKind.NOTIFICATION)
    assert [item.payload["arguments"]["text"].split(":")[0] for item in notices] == ["Release exit (preview)"]
    assert events[0]["payload"]["positions"][0]["signal_id"] == signal_id
    await first.engine.dispose()
    await second.engine.dispose()
