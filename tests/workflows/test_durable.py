"""Persistence contracts: exercise independent consumers against actual storage."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.storage.models import WorkItemRecord
from agentic_trader.storage.workflow import WorkflowStore


async def test_two_consumers_reserve_last_slot_atomically(store, entry, app_config):
    app_config.portfolio.max_concurrent_positions = 1
    requests = [await entry("SPY"), await entry("QQQ")]
    other = WorkflowStore(store.db)
    results = await asyncio.gather(
        *(s.enqueue_entry(r, app_config) for s, r in zip([store, other], requests, strict=True))
    )
    assert sum(item is not None for item, _ in results) == 1
    assert sum("positions" in reason.lower() for _, reason in results) == 1


async def test_fifo_and_expired_preflight_fencing(store, entry, app_config):
    app_config.portfolio.correlation_groups = {}
    first, _ = await store.enqueue_entry(await entry(), app_config)
    second, _ = await store.enqueue_entry(await entry("QQQ"), app_config)
    assert second is not None
    now = datetime.now(UTC)
    claimed = await store.claim_entry(lease_seconds=1, now=now)
    assert claimed.id == first.id
    assert await store.claim_entry(lease_seconds=1, now=now) is None
    replacement = await store.claim_entry(lease_seconds=1, now=now + timedelta(seconds=2))
    assert replacement.id == first.id and replacement.token != claimed.token
    assert not await store.begin_submission(claimed)
    assert await store.begin_submission(replacement)
    assert await store.claim_entry(lease_seconds=1, now=now + timedelta(days=1)) is None
    assert second.status == WorkStatus.QUEUED


async def test_outbox_fencing_retry_and_dead_letter(store, app_config):
    item_id = await store.enqueue_notification("test-key", "message", {"text": "hello"})
    assert await store.enqueue_notification("test-key", "message", {"text": "hello"}) == item_id
    now = datetime.now(UTC)
    item = await store.claim_notification(max_attempts=8, lease_seconds=1, now=now)
    other = await store.claim_notification(max_attempts=8, lease_seconds=1, now=now + timedelta(seconds=2))
    assert not await store.finish_notification(item, delivered=True, max_attempts=2, retry_seconds=1)
    assert await store.finish_notification(other, delivered=False, max_attempts=2, retry_seconds=1)
    rows = await store.list_work(WorkKind.NOTIFICATION)
    assert rows[0].status == WorkStatus.DEAD
    assert await store.claim_notification(max_attempts=8, lease_seconds=1, now=now + timedelta(days=1)) is None


async def test_close_and_notification_are_atomic_and_idempotent(store, entry):
    req = await entry()
    await store.db.update_signal_execution(req.signal_id, "entry-id")
    kwargs = {
        "signal_id": req.signal_id,
        "exit_price": 110,
        "exit_reason": "take_profit",
        "realized_pnl": 100,
        "status": SignalStatus.CLOSED_WIN,
        "broker_exit_order_id": "exit-id",
        "notification": {"contract": "SPY"},
    }
    assert await store.db.close_position(**kwargs)
    assert not await store.db.close_position(**kwargs)
    assert len(await store.list_work(WorkKind.NOTIFICATION)) == 1
    assert len(await store.events("signal/" + str(req.signal_id))) == 1


async def test_crashing_delivery_workers_eventually_dead_letter(store):
    await store.enqueue_notification("crash-loop", "message", {"text": "example"})
    now = datetime.now(UTC)
    first = await store.claim_notification(lease_seconds=1, max_attempts=1, now=now)
    assert first is not None
    assert await store.claim_notification(lease_seconds=1, max_attempts=1, now=now + timedelta(seconds=2)) is None
    assert (await store.get_work(first.id)).status == WorkStatus.DEAD


@pytest.mark.parametrize("state", [SignalStatus.PENDING, SignalStatus.SUBMITTING, SignalStatus.EXECUTED])
async def test_stale_dismissal_cannot_overwrite_trade_lifecycle(store, entry, state):
    request = await entry()
    await store.db.update_signal_status(request.signal_id, state)
    assert await store.db.dismiss_signal(request.signal_id) == (state == SignalStatus.PENDING)
    expected = SignalStatus.DISMISSED if state == SignalStatus.PENDING else state
    assert (await store.db.get_signal_by_id(request.signal_id))["status"] == expected


async def test_cannot_reset_signal_ids_while_work_or_events_exist(store, entry, app_config):
    await store.enqueue_entry(await entry(), app_config)
    with pytest.raises(ValueError, match="quarantine"):
        await store.db.clear_all_signals()


async def test_fifo_uses_database_sequence_even_if_client_clock_moves(store, entry, app_config):
    app_config.portfolio.correlation_groups = {}
    first, _ = await store.enqueue_entry(await entry("SPY"), app_config)
    second, _ = await store.enqueue_entry(await entry("QQQ"), app_config)
    async with store.db.session_factory() as session:
        await session.execute(
            update(WorkItemRecord)
            .where(WorkItemRecord.id == second.id)
            .values(created_at=first.created_at - timedelta(days=1))
        )
        await session.commit()
    assert (await store.claim_entry(lease_seconds=60)).id == first.id
