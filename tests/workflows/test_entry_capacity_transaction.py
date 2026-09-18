"""Final admission must recompute commitments while holding the trading lock."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.storage.models import SignalRecord, WorkItemRecord


@pytest.mark.parametrize(
    "changed,reason", [({"risk_dollars": 151}, "aggregate"), ({"notional_value": 60000}, "notional")]
)
async def test_economic_change_after_claim_is_rechecked(store, entry, app_config, changed, reason):
    app_config.portfolio.cash = 10000
    head, _ = await store.enqueue_entry(await entry("SPY"), app_config)
    later_request = await entry("IBM")
    later, _ = await store.enqueue_entry(later_request, app_config)
    assert head and later
    claim = await store.claim_entry(lease_seconds=60)
    await store.db.update_signal_execution(later_request.signal_id, "exact-later-entry", **changed)
    rejection = await store.begin_submission(claim, app_config)
    assert rejection and reason in rejection.lower()
    assert (await store.get_work(head.id)).status == WorkStatus.CHECKING
    assert not any(e["kind"] == EventKind.ENTRY_SUBMITTING for e in await store.events())


async def test_final_capacity_counts_head_exactly_once(store, entry, app_config):
    app_config.portfolio.cash = 10000
    app_config.portfolio.max_stop_risk_pct = 0.005
    head, _ = await store.enqueue_entry(await entry(), app_config)
    assert head
    claim = await store.claim_entry(lease_seconds=60)
    assert await store.begin_submission(claim, app_config) is None


@pytest.mark.parametrize("change", ["status", "quantity"])
async def test_current_approval_cannot_change_while_checked(store, entry, app_config, change):
    request = await entry()
    item, _ = await store.enqueue_entry(request, app_config)
    claim = await store.claim_entry(lease_seconds=60)
    if change == "status":
        await store.db.update_signal_status(request.signal_id, "DISMISSED")
    else:
        await store.db.update_signal_execution(request.signal_id, None, status="SUBMITTING", quantity=20)
    reason = await store.begin_submission(claim, app_config)
    assert reason and "changed" in reason.lower()
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING


@pytest.mark.parametrize("source", ["queue", "signal"])
async def test_final_admission_rechecks_approval_age_even_with_valid_lease(store, entry, app_config, source):
    request = await entry()
    item, _ = await store.enqueue_entry(request, app_config)
    claim = await store.claim_entry(lease_seconds=60)
    model, field, identity, max_age = (
        (WorkItemRecord, "created_at", item.id, app_config.execution.entry_queue_max_age_seconds)
        if source == "queue"
        else (SignalRecord, "timestamp", request.signal_id, app_config.execution.signal_max_age_seconds)
    )
    async with store.db.session_factory() as session, session.begin():
        await session.execute(
            update(model)
            .where(model.id == identity)
            .values(**{field: datetime.now(UTC) - timedelta(seconds=max_age + 1)})
        )
    reason = await store.begin_submission(claim, app_config)
    assert reason and ("expired" in reason.lower() if source == "queue" else "stale" in reason.lower())
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING
    assert not any(event["kind"] == EventKind.ENTRY_SUBMITTING for event in await store.events())
