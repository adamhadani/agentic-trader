"""PostgreSQL transaction ordering between risk publication and final admission."""

import asyncio
from decimal import Decimal

import pytest

from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import ExecutionMode, SignalStatus
from agentic_trader.execution.durable import LEDGER_LOCK, EventKind, WorkStatus
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.mark.parametrize("ledger_desk", [pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_committing_worse_risk_fences_submission_waiting_for_postgres_lock(ledger_desk, app_config, monkeypatch):
    ledger, venue, state = ledger_desk
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.portfolio.cash = 10000
    venue.position = None
    state["account"]["cash"] = "10000"
    state["activities"] = [{"id": "deposit", "activity_type": "CSD", "net_amount": "10000"}]
    await ledger.refresh()
    checked = await ledger.current_risk()

    db = ledger.store.store.db
    store = db.workflows
    signal_id = await db.record_signal(
        contract="SPY",
        direction="LONG",
        strategy="risk-race",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        risk_dollars=40,
        quantity=10,
        asset_class="EQUITY",
        status=SignalStatus.PENDING,
    )
    request = OrderRequest(
        signal_id=signal_id,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        quantity=10,
    )
    queued, reason = await store.enqueue_entry(request, app_config)
    assert queued is not None, reason
    claim = await store.claim_entry(lease_seconds=60)
    assert claim is not None and claim.id == queued.id

    # Independent engines/sessions model two application processes using one PG DB.
    second_db = SignalDatabase(db_url=db.db_url)
    writer = AccountLedgerService(ledger.broker, LedgerStore(second_db.workflows), ledger.config)
    assert writer.store.scope == store.scope
    writer_holds_lock = asyncio.Event()
    release_writer = asyncio.Event()
    reader_requested_lock = asyncio.Event()
    reader_acquired_lock = asyncio.Event()
    append = second_db.workflows.append
    lock = store.lock

    async def paused_checkpoint(session, *, stream, kind, payload, key=None):
        event = await append(session, stream=stream, kind=kind, payload=payload, key=key)
        if kind == EventKind.LEDGER_CHECKPOINT:
            assert payload["risk"]["drawdown_pct"] == "0.07"
            writer_holds_lock.set()
            await release_writer.wait()
        return event

    async def observed_lock(session, *, resource=None):
        if resource == LEDGER_LOCK:
            reader_requested_lock.set()
        await lock(session, resource=resource)
        if resource == LEDGER_LOCK:
            reader_acquired_lock.set()

    async def submit_if_admitted():
        refusal = await store.begin_submission(claim, app_config, risk_fingerprint=checked.fingerprint)
        if refusal is None:
            # A faulty admission would reach the real SDK against the local venue.
            await ledger.broker.submit_entry_order(OrderRequest.model_validate(claim.payload))
        return refusal

    monkeypatch.setattr(second_db.workflows, "append", paused_checkpoint)
    monkeypatch.setattr(store, "lock", observed_lock)
    state["account"]["cash"] = "9300"
    state["activities"].append({"id": "fee", "activity_type": "FEE", "net_amount": "-700"})
    tasks = []
    try:
        async with asyncio.timeout(10):
            publishing = asyncio.create_task(writer.refresh())
            tasks.append(publishing)
            await writer_holds_lock.wait()
            admitting = asyncio.create_task(submit_if_admitted())
            tasks.append(admitting)
            await reader_requested_lock.wait()
            assert not reader_acquired_lock.is_set()
            assert not admitting.done(), "Final admission must wait for the checkpoint publisher"
            release_writer.set()
            assert (await publishing).ready
            refusal = await admitting
        assert reader_acquired_lock.is_set()
        assert refusal is not None and "risk changed after preflight" in refusal.lower()
        current = await ledger.current_risk()
        assert current.drawdown_pct == Decimal(".07")
        assert current.fingerprint != checked.fingerprint
        assert (await store.get_work(claim.id)).status == WorkStatus.CHECKING
        assert not any(event["kind"] == EventKind.ENTRY_SUBMITTING for event in await store.events())
        assert not [call for call in venue.calls if call[0] in {"POST", "PATCH", "DELETE"}]
    finally:
        release_writer.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second_db.engine.dispose()
