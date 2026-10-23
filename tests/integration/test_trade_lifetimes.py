"""Real SDK lifecycle races over durable SQLite/PostgreSQL workflow state.

Fixtures seed already-accepted timed orders; session-alpha admission stays disabled.
"""

import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, time as dt_time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import SignalStatus, SystemStateKey
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.execution.durable import EventKind, WorkItem, WorkKind, WorkStatus
from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    NATIVE_ENTRY_LIFETIME_KEY,
    SessionLifetimePolicy,
    TradeLifetimePolicy,
    daily_entry_lifetime,
)
from agentic_trader.execution.lifetimes import TradeLifetimeService
from agentic_trader.market.session import AlpacaCalendarProvider, MarketCalendarDay
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy, TimedAlphaExecutionPolicy, session_entry_policy
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.lifetimes import LifetimeRepository
from agentic_trader.storage.models import SignalRecord, WorkItemRecord
from agentic_trader.storage.workflow import encode


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def lifecycle_case(request, alpaca_http, temp_db):
    venue, broker = alpaca_http
    if request.param == "postgres":
        url = request.getfixturevalue("postgres_test_db")
        databases = [SignalDatabase(db_url=url) for _ in range(2)]
    else:
        databases = [temp_db, temp_db]
        await temp_db.init_db()
    db = databases[0]
    now = datetime.now(UTC)
    venue.entry.update(
        status="new",
        filled_qty="0",
        filled_avg_price=None,
        filled_at=None,
        submitted_at=(now - timedelta(seconds=121)).isoformat(),
        updated_at=now.isoformat(),
    )
    venue.position = None
    policy = TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(resting_seconds=120, holding_seconds=180))
    signal_id = await db.record_signal(
        contract="SPY",
        strategy="alpha_lifetime_fixture",
        direction="LONG",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        risk_dollars=50,
        quantity=10,
        asset_class="EQUITY",
        alpha_policy=policy.to_dict(),
        alpha_version="fixture-session-version",
        timeframe="15m",
    )
    await db.update_signal_execution(signal_id, venue.entry["id"])
    order_request = OrderRequest(
        signal_id=signal_id,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        client_order_id=venue.entry["client_order_id"],
    )
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        event = await db.workflows.append(
            session, stream="fixture/accepted-entry", kind=EventKind.ENTRY_RESOLVED, payload={"fixture": True}
        )
        session.add(
            WorkItemRecord(
                id=order_request.client_order_id,
                scope=db.workflows.scope,
                kind=WorkKind.ENTRY,
                status=WorkStatus.ACCEPTED,
                dedup_key=str(signal_id),
                sequence=event.id,
                payload=encode(order_request.model_dump(mode="json")),
                result=encode({"order_id": venue.entry["id"], "success": True}),
                attempts=1,
                created_at=now,
                available_at=now,
            )
        )
    services = [
        TradeLifetimeService(
            LifetimeRepository(database.workflows),
            broker,
            PositionCloseService(broker, database),
            config=broker.config.execution,
        )
        for database in databases
    ]
    yield SimpleNamespace(
        venue=venue, broker=broker, db=db, databases=databases, services=services, signal_id=signal_id
    )
    for database in databases:
        await database.engine.dispose()


def complete_cancel(c):
    for order in (c.venue.entry, c.venue.stop, c.venue.take_profit):
        order.update(status="canceled", updated_at=datetime.now(UTC).isoformat())


@pytest.mark.parametrize("outcome", ["confirmed", "lost_reply", "pending", "partial_race", "filled_race"])
async def test_exact_cancel_once_across_clients_and_restarts(lifecycle_case, outcome):
    c = lifecycle_case

    def response(method, path, query, body):
        if method == "DELETE":
            assert path == f"/v2/orders/{c.venue.entry['id']}"
            if outcome in ("confirmed", "lost_reply"):
                complete_cancel(c)
            elif outcome in ("partial_race", "filled_race"):
                complete_cancel(c)
                c.venue.entry.update(
                    filled_qty="2" if outcome == "partial_race" else "10",
                    filled_avg_price="100",
                    filled_at=datetime.now(UTC).isoformat(),
                )
            if outcome == "lost_reply":
                # Outlast the SDK socket deadline. Mutation occurred, acknowledgement was lost.
                time.sleep(c.broker.client.request_timeout + 0.5)
            return 204, None
        return None

    c.venue.override = response
    await asyncio.gather(*(service.reconcile() for service in c.services))
    await c.services[1].reconcile()
    cancels = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert len(cancels) == 1, str(await c.db.workflows.events())
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)
    safe = outcome in ("confirmed", "lost_reply")
    assert cancels[0].status == (WorkStatus.ACCEPTED if safe else WorkStatus.UNKNOWN)
    assert (await c.db.get_state(SystemStateKey.TRADING_HALTED) == "true") is (not safe)
    if outcome == "lost_reply":
        assert cancels[0].result["transport_error"]
    if outcome == "partial_race":
        assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.EXECUTED
    await c.db.workflows.rebuild_order_views()
    await c.services[1].recover()
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1


# A realistic pre-marker native card's provenance: every block a scan writes, no entry lifetime.
HISTORICAL_NATIVE_PROVENANCE = {
    "strategy": "trend_pullback",
    "setup_quality": 0.71,
    "regime": {"macro_stress": "LOW", "vix": 15.2, "risk_multiplier": 1.0},
    "news": {"status": "scored", "headlines": 2, "tone": "neutral"},
    "book_sizing": {"mode": "enforce", "factor": 0.83},
    "llm_verdict": {"approved": True},
}


@pytest.mark.parametrize(
    "state", ["partial", "historical", "historical_provenance", "replacement", "missing_protection"]
)
async def test_unsafe_or_untimed_entries_never_cancel(lifecycle_case, state):
    c = lifecycle_case
    if state == "historical_provenance":
        async with c.db.session_factory() as session, session.begin():
            row = await session.get(SignalRecord, c.signal_id)
            row.alpha_policy = None
            row.decision_provenance = json.dumps(HISTORICAL_NATIVE_PROVENANCE, allow_nan=False)
    elif state == "partial":
        c.venue.entry.update(status="partially_filled", filled_qty="2")
    elif state == "replacement":
        c.venue.entry["replaced_by"] = "00000000-0000-0000-0000-000000000099"
    elif state == "missing_protection":
        c.venue.entry["legs"] = None
    else:
        async with c.db.session_factory() as session, session.begin():
            row = await session.get(SignalRecord, c.signal_id)
            row.alpha_policy = None
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    untimed = state in ("historical", "historical_provenance")
    assert (await c.db.get_state(SystemStateKey.TRADING_HALTED) == "true") is (not untimed)


def filled_position(c):
    c.venue.entry.update(
        status="filled",
        filled_qty="10",
        filled_avg_price="100",
        filled_at=(datetime.now(UTC) - timedelta(seconds=181)).isoformat(),
    )
    c.venue.position = {
        "asset_id": "00000000-0000-0000-0000-000000000001",
        "symbol": "SPY",
        "exchange": "ARCA",
        "asset_class": "us_equity",
        "avg_entry_price": "100",
        "qty": "10",
        "qty_available": "0",
        "side": "long",
        "cost_basis": "1000",
        "current_price": "105",
        "unrealized_pl": "50",
    }


async def test_holding_expiry_uses_one_durable_close_and_waits_for_session(lifecycle_case):
    c = lifecycle_case
    filled_position(c)
    c.venue.market_open = False
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    c.venue.market_open = True
    await asyncio.gather(*(service.reconcile() for service in c.services))
    await c.services[1].reconcile()
    posts = [call for call in c.venue.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][3]["client_order_id"].startswith("hold-")
    assert posts[0][3]["side"] == "sell" and posts[0][3]["qty"] == 10
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
    events = await c.db.workflows.events("close/" + posts[0][3]["client_order_id"])
    assert any(e["kind"] == EventKind.CLOSE_REQUESTED for e in events)


@pytest.mark.parametrize("crash_after_mutation", [False, True])
async def test_crashed_cancel_owner_is_never_replayed(lifecycle_case, monkeypatch, crash_after_mutation):
    c = lifecycle_case

    async def crash(order_id):
        if crash_after_mutation:
            complete_cancel(c)
        raise asyncio.CancelledError

    monkeypatch.setattr(c.broker, "cancel_order", crash)
    with pytest.raises(asyncio.CancelledError):
        await c.services[0].reconcile()
    (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert item.status == WorkStatus.SUBMITTING
    c.services[1].clock = lambda: datetime.now(UTC) + timedelta(minutes=1)
    await c.services[1].recover()
    (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert item.status == (WorkStatus.ACCEPTED if crash_after_mutation else WorkStatus.UNKNOWN)
    assert not any(call[0] != "GET" for call in c.venue.calls)


async def test_outbox_failure_rolls_back_cancel_intent_before_mutation(lifecycle_case, monkeypatch):
    c = lifecycle_case

    async def fail(*args, **kwargs):
        raise OSError("fixture outbox write failed")

    with monkeypatch.context() as patch:
        patch.setattr(c.db.workflows, "add_notification", fail)
        with pytest.raises(OSError):
            await c.services[0].reconcile()
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert not any(call[0] != "GET" for call in c.venue.calls)


async def test_newer_partial_projection_fences_stale_rest_cancel_claim(lifecycle_case):
    c = lifecycle_case
    observations = await c.broker.read_entry_group(c.venue.entry["id"])
    newer = observations[1].model_copy(
        update={
            "status": "partially_filled",
            "filled_quantity": "2",
            "updated_at": datetime.now(UTC) + timedelta(seconds=1),
            "source": "stream",
        }
    )
    await c.db.workflows.observe_orders([newer])
    await c.services[0].reconcile()
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) == "true"


async def test_pending_cancellation_fences_entry_admission_and_risk_release(lifecycle_case, monkeypatch):
    c = lifecycle_case

    async def crash(order_id):
        raise asyncio.CancelledError

    monkeypatch.setattr(c.broker, "cancel_order", crash)
    with pytest.raises(asyncio.CancelledError):
        await c.services[0].reconcile()
    claimed, close = await c.db.claim_close_request(
        {"id": "competing-close", "symbol": "SPY", "direction": "LONG", "quantity": 10, "signal_id": c.signal_id}
    )
    assert not claimed and "cancellation" in close["detail"]
    assert await c.db.get_close_request("competing-close") is None
    c.venue.entry.update(status="canceled", updated_at=datetime.now(UTC).isoformat())
    await c.db.workflows.observe_orders(await c.broker.read_entry_group(c.venue.entry["id"]))
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.EXECUTED
    candidate = await c.db.record_signal(
        contract="QQQ",
        strategy="fixture",
        direction="LONG",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        risk_dollars=50,
        quantity=10,
        asset_class="EQUITY",
    )
    request = OrderRequest(
        signal_id=candidate,
        symbol="QQQ",
        asset_class="EQUITY",
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
    )
    item, reason = await c.db.workflows.enqueue_entry(request, c.broker.config)
    assert item is None and "cancellation" in reason
    complete_cancel(c)
    await c.services[1].recover()
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.FAILED


@pytest.mark.parametrize("filled", ["0", "2"])
async def test_resume_checks_partial_exposure_without_mutations(lifecycle_case, filled):
    c = lifecycle_case
    c.venue.entry.update(filled_qty=filled, status="partially_filled" if filled == "2" else "new")
    assert bool(await c.services[0].resume_blockers()) is (filled == "2")
    assert not any(call[0] != "GET" for call in c.venue.calls)


@pytest.mark.parametrize("status", ["rejected", "accepted"])
async def test_holding_close_never_replays_a_terminal_failure_or_pending_submission(lifecycle_case, status):
    c = lifecycle_case
    filled_position(c)
    c.venue.close_status = status
    await c.services[0].reconcile()
    first = [call for call in c.venue.calls if call[0] == "POST"]
    assert len(first) == 1
    for service in c.services:
        await service.reconcile()
    assert len([call for call in c.venue.calls if call[0] == "POST"]) == 1
    assert bool(await c.services[0].resume_blockers()) is (status == "rejected")


@pytest.mark.parametrize("exit_filled", ["2", "10"])
async def test_existing_protective_exit_is_never_replaced_by_holding_close(lifecycle_case, exit_filled):
    c = lifecycle_case
    filled_position(c)
    c.venue.stop.update(
        status="filled" if exit_filled == "10" else "partially_filled",
        filled_qty=exit_filled,
        filled_avg_price="95",
        filled_at=datetime.now(UTC).isoformat(),
    )
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert (await c.db.get_state(SystemStateKey.TRADING_HALTED) == "true") is (exit_filled == "2")


async def test_inflight_cancel_recovery_does_not_halt_a_healthy_owner(lifecycle_case, monkeypatch):
    c = lifecycle_case
    original = c.broker.cancel_order
    started, finish = asyncio.Event(), asyncio.Event()

    async def pause(order_id):
        started.set()
        await finish.wait()
        await original(order_id)
        complete_cancel(c)

    monkeypatch.setattr(c.broker, "cancel_order", pause)
    owner = asyncio.create_task(c.services[0].reconcile())
    await asyncio.wait_for(started.wait(), 2)
    try:
        await c.services[1].recover()
        (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
        assert item.status == WorkStatus.SUBMITTING
        assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
    finally:
        finish.set()
        await owner
    (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert item.status == WorkStatus.ACCEPTED
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1


async def use_daily_entry_policy(c, *, submitted_seconds_ago):
    policy = session_entry_policy().to_dict()
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy, row.timeframe = json.dumps(policy, allow_nan=False), "1d"
    c.venue.entry.update(submitted_at=(datetime.now(UTC) - timedelta(seconds=submitted_seconds_ago)).isoformat())


async def test_daily_entry_only_policy_cancels_after_one_session_and_not_before(lifecycle_case):
    c = lifecycle_case
    await use_daily_entry_policy(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS - 60)
    await c.services[0].reconcile()
    assert not any(call[0] == "DELETE" for call in c.venue.calls)
    await use_daily_entry_policy(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)

    def response(method, path, query, body):
        if method == "DELETE":
            complete_cancel(c)
            return 204, None
        return None

    c.venue.override = response
    await asyncio.gather(*(service.reconcile() for service in c.services))
    cancels = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert len(cancels) == 1 and cancels[0].status == WorkStatus.ACCEPTED
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)


async def test_daily_entry_only_policy_never_schedules_a_holding_close(lifecycle_case):
    c = lifecycle_case
    await use_daily_entry_policy(c, submitted_seconds_ago=10 * 86_400)
    filled_position(c)
    c.venue.entry.update(filled_at=(datetime.now(UTC) - timedelta(days=9)).isoformat())
    await asyncio.gather(*(service.reconcile() for service in c.services))
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"


async def mark_native(c, *, submitted_seconds_ago):
    """Turn the fixture signal into a marked native card: no alpha_policy, provenance marker only."""
    provenance = {NATIVE_ENTRY_LIFETIME_KEY: asdict(daily_entry_lifetime())}
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy, row.timeframe = None, "4h"
        row.decision_provenance = json.dumps(provenance, allow_nan=False)
    c.venue.entry.update(submitted_at=(datetime.now(UTC) - timedelta(seconds=submitted_seconds_ago)).isoformat())


async def test_marked_native_entry_cancelled_once_after_deadline(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)

    def response(method, path, query, body):
        if method == "DELETE":
            assert path == f"/v2/orders/{c.venue.entry['id']}"
            complete_cancel(c)
            return 204, None
        return None

    c.venue.override = response
    await asyncio.gather(*(service.reconcile() for service in c.services))
    await c.services[1].reconcile()
    cancels = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert len(cancels) == 1 and cancels[0].status == WorkStatus.ACCEPTED
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.FAILED
    assert await c.db.workflows.list_work(WorkKind.NOTIFICATION)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
    await assert_capacity_released(c)


async def assert_capacity_released(c):
    """A confirmed cancel frees the signal's reservation and no longer fences fresh admission."""
    assert c.signal_id not in {s["id"] for s in await c.db.get_active_positions()}
    candidate = await c.db.record_signal(
        contract="QQQ",
        strategy="fixture",
        direction="LONG",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        risk_dollars=50,
        quantity=10,
        asset_class="EQUITY",
    )
    request = OrderRequest(
        signal_id=candidate,
        symbol="QQQ",
        asset_class="EQUITY",
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
    )
    _item, reason = await c.db.workflows.enqueue_entry(request, c.broker.config)
    assert "cancellation" not in reason.lower()


async def test_marked_native_entry_untouched_before_deadline(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS - 60)
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.EXECUTED


async def test_partial_fill_on_marked_native_entry_halts(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)
    c.venue.entry.update(status="partially_filled", filled_qty="2")
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) == "true"


async def test_resume_blockers_include_marked_native_signals(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS - 60)
    assert not await c.services[0].resume_blockers()
    c.venue.entry.update(status="partially_filled", filled_qty="2")
    assert await c.services[0].resume_blockers() == [c.signal_id]
    assert not any(call[0] != "GET" for call in c.venue.calls)


class AllDaysCalendar:
    def __init__(self):
        self.calls = 0

    async def get_calendar_range(self, start, end):
        self.calls += 1
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(day, True, False, dt_time(0, 0), dt_time(23, 59, 59)))
            day += timedelta(days=1)
        return days


async def use_session_policy(c, *, fill_days_ago):
    """Pin the service clock to 15:50 New York today, five minutes past the 15:45 time exit.

    The fill is at noon ``fill_days_ago`` days earlier, so the outcome never depends on
    the wall-clock time of day the test runs at.
    """
    ny = ZoneInfo("America/New_York")
    policy = AprioriBracketPolicy(
        stop_atr=2.0,
        atr_window=14,
        reward_risk=3.0,
        lifetime=SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20, close_time_et="15:45"),
    )
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy, row.timeframe = json.dumps(policy.to_dict(), allow_nan=False), "1d"
    filled_position(c)
    today = datetime.now(ny).date()
    now = datetime.combine(today, dt_time(15, 50), tzinfo=ny).astimezone(UTC)
    fill = datetime.combine(today - timedelta(days=fill_days_ago), dt_time(12, 0), tzinfo=ny)
    c.venue.entry.update(filled_at=fill.astimezone(UTC).isoformat())
    for service in c.services:
        service.calendar = AllDaysCalendar()
        service.clock = lambda: now


async def test_session_policy_closes_once_on_session_twenty(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=19)  # the fill day is session 1, today is session 20
    await asyncio.gather(*(service.reconcile() for service in c.services))
    posts = [call for call in c.venue.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][3]["client_order_id"].startswith("hold-")


async def test_session_policy_waits_before_session_twenty(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=18)  # today is session 19
    await asyncio.gather(*(service.reconcile() for service in c.services))
    assert not any(call[0] != "GET" for call in c.venue.calls)
    # A REVIEW also makes only GET calls, so also pin that this is a genuine wait
    # (no halt, no journaled lifetime review) rather than an evidence review.
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
    assert not await c.db.workflows.events(f"signal/{c.signal_id}/lifetime")


async def test_session_policy_calendar_failure_reviews_and_never_closes(lifecycle_case):
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=25)

    class Down:
        async def get_calendar_range(self, start, end):
            raise RuntimeError("calendar down")

    for service in c.services:
        service.calendar = Down()
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert c.signal_id in await c.services[0].resume_blockers()
    events = await c.db.workflows.events(f"signal/{c.signal_id}/lifetime")
    assert any(
        e["kind"] == EventKind.LIFETIME_REVIEW
        and e["payload"]["reason"] == "lifecycle_evidence_unavailable:RuntimeError"
        for e in events
    )


async def test_session_policy_alpaca_calendar_provider_failure_reviews_and_never_closes(lifecycle_case):
    """The strict provider actually wired in production, not a bespoke fake, reaches review."""
    c = lifecycle_case
    await use_session_policy(c, fill_days_ago=25)

    class RaisingAlpacaTradingClient:
        def get_calendar(self, req):
            raise RuntimeError("calendar down")

    for service in c.services:
        service.calendar = AlpacaCalendarProvider(trading_client=RaisingAlpacaTradingClient())
    await c.services[0].reconcile()
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert c.signal_id in await c.services[0].resume_blockers()
    events = await c.db.workflows.events(f"signal/{c.signal_id}/lifetime")
    assert any(
        e["kind"] == EventKind.LIFETIME_REVIEW
        and e["payload"]["reason"] == "lifecycle_evidence_unavailable:RuntimeError"
        for e in events
    )


# Operator cancel: any resting desk entry, timed or not, with no deadline or policy fence.


def resting(c):
    """The fixture entry rests well inside its own deadline, so only the operator can cancel it."""
    c.venue.entry.update(submitted_at=(datetime.now(UTC) - timedelta(seconds=10)).isoformat())


def broker_confirms_cancel(c):
    def response(method, path, query, body):
        if method == "DELETE":
            assert path == f"/v2/orders/{c.venue.entry['id']}"
            complete_cancel(c)
            return 204, None
        return None

    c.venue.override = response


def deletes(c):
    return [call for call in c.venue.calls if call[0] == "DELETE"]


def mutations(c):
    return [call for call in c.venue.calls if call[0] != "GET"]


async def notice_texts(c):
    return [item.payload["arguments"]["text"] for item in await c.db.workflows.list_work(WorkKind.NOTIFICATION)]


async def test_preview_makes_no_delete(lifecycle_case):
    c = lifecycle_case
    resting(c)
    result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=False)
    assert (result.status, result.reason, result.symbol) == ("preview", None, "SPY")
    assert not mutations(c)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.EXECUTED


async def test_confirm_cancels_once_with_notice(lifecycle_case):
    c = lifecycle_case
    resting(c)
    broker_confirms_cancel(c)
    result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=True)
    assert (result.status, result.reason, result.work_status) == ("submitted", None, WorkStatus.ACCEPTED)
    assert len(deletes(c)) == 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)
    (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert item.status == WorkStatus.ACCEPTED and item.payload["cause"] == "operator"
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.FAILED
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
    texts = await notice_texts(c)
    assert any(f"Operator cancel requested for entry SPY (signal {c.signal_id})" in t for t in texts)
    assert any("operator cancellation: confirmed_unfilled_cancellation" in t for t in texts)
    await assert_capacity_released(c)


@pytest.mark.parametrize("filled", ["10", "2"])
async def test_refused_when_filled(lifecycle_case, filled):
    c = lifecycle_case
    resting(c)
    c.venue.entry.update(
        status="filled" if filled == "10" else "partially_filled",
        filled_qty=filled,
        filled_avg_price="100",
        filled_at=datetime.now(UTC).isoformat(),
    )
    for confirm in (False, True):
        result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=confirm)
        assert (result.status, result.reason) == ("refused", "not_resting")
    assert not mutations(c)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)


async def test_refused_when_close_active(lifecycle_case):
    c = lifecycle_case
    resting(c)
    claimed, _ = await c.db.claim_close_request(
        {"id": "operator-close", "symbol": "SPY", "direction": "LONG", "quantity": 10, "signal_id": c.signal_id}
    )
    assert claimed
    for confirm in (False, True):
        result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=confirm)
        assert (result.status, result.reason) == ("refused", "close_active")
    assert not mutations(c)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)


async def test_refused_unknown_signal(lifecycle_case):
    c = lifecycle_case
    result = await c.services[0].cancel_resting_entry(c.signal_id + 1000, confirm=True)
    assert (result.status, result.reason, result.symbol) == ("refused", "unknown_signal", None)
    assert not c.venue.calls


async def test_refused_when_bracket_group_changed(lifecycle_case):
    c = lifecycle_case
    resting(c)
    c.venue.stop["replaced_by"] = "00000000-0000-0000-0000-000000000099"
    for confirm in (False, True):
        result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=confirm)
        assert (result.status, result.reason) == ("refused", "incomplete_or_changed_bracket_group")
    assert not mutations(c)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)


async def test_second_confirm_is_refused_cancel_in_progress(lifecycle_case):
    c = lifecycle_case
    resting(c)
    broker_confirms_cancel(c)
    first = await c.services[0].cancel_resting_entry(c.signal_id, confirm=True)
    assert first.status == "submitted"
    for service in c.services:
        again = await service.cancel_resting_entry(c.signal_id, confirm=True)
        assert (again.status, again.reason) == ("refused", "cancel_in_progress")
    assert len(deletes(c)) == 1
    assert len(await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)) == 1


@pytest.mark.parametrize("marker", ["absent", "malformed"])
async def test_operator_cancel_of_untimed_historical_entry(lifecycle_case, marker):
    """Untimed entries, and a malformed marker that keeps a signal in resume_blockers, are cancellable."""
    c = lifecycle_case
    resting(c)
    provenance = dict(HISTORICAL_NATIVE_PROVENANCE)
    if marker == "malformed":
        provenance[NATIVE_ENTRY_LIFETIME_KEY] = "nope"
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy = None
        row.decision_provenance = json.dumps(provenance, allow_nan=False)
    broker_confirms_cancel(c)
    result = await c.services[0].cancel_resting_entry(c.signal_id, confirm=True)
    assert (result.status, result.work_status) == ("submitted", WorkStatus.ACCEPTED)
    assert len(deletes(c)) == 1
    assert (await c.db.get_signal_by_id(c.signal_id))["status"] == SignalStatus.FAILED
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"


async def test_operator_and_deadline_cancel_race_single_work_item(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)
    broker_confirms_cancel(c)
    _, result = await asyncio.gather(
        c.services[0].reconcile(), c.services[1].cancel_resting_entry(c.signal_id, confirm=True)
    )
    assert result.status == "submitted" or result.reason in ("cancel_in_progress", "not_resting")
    (item,) = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert item.status == WorkStatus.ACCEPTED
    assert len(deletes(c)) <= 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"


async def test_begin_cancel_refuses_a_marked_native_signal_with_a_mismatched_policy(lifecycle_case):
    c = lifecycle_case
    await mark_native(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)
    service = c.services[0]
    signal = await c.db.get_signal_by_id(c.signal_id)
    identity = await service._entry_identity(signal)
    orders = await c.broker.read_entry_group(identity.order_id)
    await c.db.workflows.observe_orders(orders)
    other = asdict(TradeLifetimePolicy(resting_seconds=120, holding_seconds=180))
    refused = await service.repository.begin_cancel(c.signal_id, identity, orders, other, now=datetime.now(UTC))
    assert not isinstance(refused, WorkItem)
    assert not await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    matching = asdict(daily_entry_lifetime())
    claimed = await service.repository.begin_cancel(c.signal_id, identity, orders, matching, now=datetime.now(UTC))
    assert isinstance(claimed, WorkItem)
