import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import ComponentTally
from agentic_trader.execution.durable import WorkKind
from agentic_trader.storage.models import DomainEventRecord, WorkItemRecord
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


async def test_reenabling_notifications_reports_existing_open_incident(desk):
    store, policy, now = desk
    policy.notifications_enabled = False
    for seconds in (0, 10):
        await store.observe(
            "worker", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    policy.notifications_enabled = True
    notice = await store.observe(
        "worker", ready=False, observed_at=now + timedelta(seconds=11), detail="", policy=policy
    )
    assert notice is not None and notice.kind == "opened"
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    assert (
        await store.observe("worker", ready=False, observed_at=now + timedelta(seconds=12), detail="", policy=policy)
        is None
    )


async def test_muted_opening_journals_withheld_and_queues_nothing(desk):
    store, policy, now = desk
    for seconds in (0, 10):
        await store.observe(
            "scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy, muted=True
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    events = await store.store.events("incident/scan")
    assert events[-1]["payload"]["withheld"] == {"kind": "opened", "reason": "quiet_hours"}
    before = await store.incidents()
    await store.rebuild()
    assert await store.incidents() == before


async def test_unmuted_transition_journals_null_withheld(desk):
    store, policy, now = desk
    for seconds in (0, 10):
        await store.observe("scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    events = await store.store.events("incident/scan")
    assert events[-1]["payload"]["withheld"] is None


async def test_reenabled_notifications_do_not_send_recovery_for_unannounced_incident(desk):
    store, policy, now = desk
    policy.notifications_enabled = False
    for seconds in (0, 10):
        await store.observe("scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    policy.notifications_enabled = True
    for seconds in (11, 16):
        await store.observe("scan", ready=True, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    assert not await store.store.list_work(WorkKind.NOTIFICATION)


START = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
END = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)


async def backdate_events(store):
    """Events record wall-clock time; align it with the injected observation instants."""
    async with store.store.db.session_factory() as session, session.begin():
        for row in await session.scalars(select(DomainEventRecord)):
            row.recorded_at = datetime.fromisoformat(json.loads(row.payload)["observed_at"])


async def quiet_cycle(store, policy, component, at, *, recover=True):
    """Open (withheld) at ``at``; recover (withheld) after it when ``recover``."""
    steps = [(False, 0), (False, 10)] + ([(True, 11), (True, 16)] if recover else [])
    for ready, seconds in steps:
        await store.observe(
            component,
            ready=ready,
            observed_at=at + timedelta(seconds=seconds),
            detail="",
            policy=policy,
            muted=True,
        )


def summary_kwargs(policy, **extra):
    return {
        "period_start": START,
        "period_end": END,
        "observed_at": END,
        "details": {},
        "ready_now": (),
        "policy": policy,
        **extra,
    }


async def test_summary_tallies_withheld_notices_and_lists_open_incidents(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=1))
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=2))
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=3), recover=False)
    await backdate_events(store)
    detail = "Observation age: 40000s; limit: 18000s"
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, details={"scan": detail}))
    assert summary is not None
    assert summary.tallies == [
        ComponentTally(component="accounting", opened=2, recovered=2),
        ComponentTally(component="scan", opened=1, recovered=0),
    ]
    assert [i.component for i in summary.open_incidents] == ["scan"]
    assert summary.open_incidents[0].detail == detail
    assert summary.open_incidents[0].ready_now is False
    items = await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i.payload["kind"] for i in items] == ["operations_summary"]
    async with store.store.db.session_factory() as session:
        keys = list(await session.scalars(select(WorkItemRecord.dedup_key).where(WorkItemRecord.id == items[0].id)))
    assert keys == ["operations-summary/2026-11-03"]


async def test_summary_is_queued_once_per_window(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await backdate_events(store)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is not None
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    decisions = await store.store.events("operations/summary")
    assert [e["payload"]["queued"] for e in decisions] == [True]


async def test_empty_decision_is_final_for_the_window(desk):
    store, policy, _ = desk
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    decisions = await store.store.events("operations/summary")
    assert [e["payload"]["queued"] for e in decisions] == [False]
    for seconds in (45 * 60, 45 * 60 + 130):
        await store.observe("scan", ready=False, observed_at=END + timedelta(seconds=seconds), detail="", policy=policy)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    items = await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i.payload["kind"] for i in items] == ["operational"]
    assert len(await store.store.events("operations/summary")) == 1


async def test_summary_counts_an_event_exactly_at_period_start(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START - timedelta(seconds=10), recover=False)  # opens at START
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy))
    assert summary is not None
    assert summary.tallies == [ComponentTally(component="scan", opened=1, recovered=0)]


async def test_summary_ignores_events_outside_the_period_and_other_reasons(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START - timedelta(minutes=1))
    await quiet_cycle(store, policy, "worker", END)
    policy.notifications_enabled = False
    inside = START + timedelta(hours=2)
    for seconds in (0, 10):
        await store.observe(
            "accounting", ready=False, observed_at=inside + timedelta(seconds=seconds), detail="", policy=policy
        )
    policy.notifications_enabled = True
    for seconds in (11, 16):
        await store.observe(
            "accounting", ready=True, observed_at=inside + timedelta(seconds=seconds), detail="", policy=policy
        )
    await backdate_events(store)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    assert not await store.store.list_work(WorkKind.NOTIFICATION)


async def test_summary_lists_trading_halt_first(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await quiet_cycle(store, policy, "trading_halt", START + timedelta(hours=2), recover=False)
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy))
    assert summary is not None
    assert [i.component for i in summary.open_incidents] == ["trading_halt", "scan"]


async def test_summary_decided_at_reads_the_first_pass_of_the_window(desk):
    store, policy, _ = desk
    assert await store.summary_decided_at(END) is None
    first = END + timedelta(minutes=75)
    await store.queue_quiet_summary(**summary_kwargs(policy, observed_at=first))
    await store.queue_quiet_summary(**summary_kwargs(policy, observed_at=first + timedelta(minutes=1)))
    assert await store.summary_decided_at(END) == first
    assert await store.summary_decided_at(END + timedelta(days=1)) is None


async def test_summary_marks_open_incidents_whose_check_is_ready_now(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=2), recover=False)
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, ready_now={"scan"}))
    assert summary is not None
    assert [(i.component, i.ready_now) for i in summary.open_incidents] == [("scan", True), ("accounting", False)]
