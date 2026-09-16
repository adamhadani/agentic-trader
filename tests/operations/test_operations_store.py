import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.config import OperationsConfig
from agentic_trader.execution.durable import WorkKind
from agentic_trader.storage.operations import OperationsStore


@pytest.fixture
async def desk(temp_db):
    return (
        OperationsStore(temp_db.workflows),
        OperationsConfig(failure_seconds=10, recovery_seconds=5),
        datetime(2026, 9, 16, tzinfo=UTC),
    )


async def test_atomic_concurrent_open_stale_observation_and_replay(desk):
    store, policy, now = desk

    async def observe(at, ready=False):
        return await store.observe("outbox", ready=ready, observed_at=at, detail="Dead letters", policy=policy)

    await observe(now)
    results = await asyncio.gather(*(observe(now + timedelta(seconds=10)) for _ in range(2)))
    assert sum(x is not None for x in results) == 1
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    assert await observe(now, True) is None
    before = await store.incidents()
    await store.rebuild()
    assert await store.incidents() == before
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    await observe(now + timedelta(seconds=11), True)
    await observe(now + timedelta(seconds=16), True)
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 2


async def test_healthy_samples_do_not_grow_journal_and_alert_disable_is_explicit(desk):
    store, policy, now = desk
    for seconds in (0, 1, 2):
        await store.observe(
            "worker", ready=True, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.events("incident/worker")
    policy.notifications_enabled = False
    for seconds in (3, 13):
        await store.observe(
            "worker", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    assert (await store.incidents())[0]["phase"] == "open"
