"""Economic signal writers share final admission's PostgreSQL scope lock."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text, update

from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import SignalStatus
from agentic_trader.execution import capacity
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import SignalRecord


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.enable_socket,
    pytest.mark.allow_hosts(["127.0.0.1", "localhost"]),
]


@pytest.fixture
async def capacity_clients(postgres_test_db):
    reader = SignalDatabase(db_url=postgres_test_db)
    writer = SignalDatabase(db_url=postgres_test_db)
    signal_id = await reader.record_signal(
        contract="SPY",
        strategy="capacity-race",
        direction="LONG",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        risk_dollars=50,
        quantity=10,
        notional_value=1000,
        status=SignalStatus.PENDING,
    )
    try:
        yield reader, writer, signal_id
    finally:
        await writer.engine.dispose()
        await reader.engine.dispose()


@pytest.mark.parametrize(
    "operation,terminal_status",
    [
        ("execution", None),
        ("status", None),
        ("quarantine", None),
        ("execution", SignalStatus.CLOSED_WIN),
        ("execution", SignalStatus.CLOSED_LOSS),
        ("execution", SignalStatus.FAILED),
    ],
)
async def test_economic_writer_waits_for_final_admission_scope_lock(capacity_clients, operation, terminal_status):
    reader, writer, signal_id = capacity_clients
    backend_pid = None
    submitted = asyncio.Event()

    def observe_write(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal backend_pid
        if "workflow_locks" in statement or statement.startswith("UPDATE signals"):
            backend_pid = connection.connection.driver_connection.get_server_pid()
            submitted.set()

    event.listen(writer.engine.sync_engine, "before_cursor_execute", observe_write)
    updating = None
    try:
        async with asyncio.timeout(10):
            async with reader.session_factory() as session, session.begin():
                await reader.workflows.lock(session)
                lock_owner = await session.scalar(text("SELECT pg_backend_pid()"))
                if operation == "execution":
                    mutation = writer.update_signal_execution(
                        signal_id,
                        "exact-entry-id",
                        fill_price=101,
                        quantity=20,
                        notional_value=2020,
                        risk_dollars=120,
                    )
                elif operation == "status":
                    mutation = writer.update_signal_status(signal_id, SignalStatus.EXECUTED)
                else:
                    mutation = writer.quarantine_signal(
                        signal_id, "fixture contaminant", {"status": SignalStatus.PENDING}
                    )
                updating = asyncio.create_task(mutation)
                await submitted.wait()
                # Observe an actual PG lock wait. No timing assumption or mocked
                # lock call can substitute for these independent connections.
                while not await session.scalar(
                    text("SELECT :owner = ANY(pg_blocking_pids(:writer))"),
                    {"owner": lock_owner, "writer": backend_pid},
                ):
                    if updating.done():
                        await updating  # Surface unrelated SQL failures directly.
                        pytest.fail("Economic writer committed while final admission held the scope lock")
                    await asyncio.sleep(0.01)
                assert not updating.done()
                before = await reader.get_signal_by_id(signal_id)
                assert before["status"] == SignalStatus.PENDING
                assert before["risk_dollars"] == 50 and before["quantity"] == 10
                if terminal_status is not None:
                    # A concurrent lifecycle owner can finish the signal before
                    # the delayed fill writer gets its turn; it must stay terminal.
                    await session.execute(
                        update(SignalRecord).where(SignalRecord.id == signal_id).values(status=terminal_status)
                    )
            await updating
        after = await reader.get_signal_by_id(signal_id)
        if operation == "quarantine":
            assert after is None, "A committed quarantine must hide the signal from admission"
            async with reader.session_factory() as session:
                preserved = await session.get(SignalRecord, signal_id)
                assert preserved is not None and preserved.is_quarantined
                assert preserved.status == SignalStatus.PENDING and preserved.risk_dollars == 50
            return
        assert after["status"] == (terminal_status or SignalStatus.EXECUTED)
        if terminal_status is not None:
            assert after == {**before, "status": terminal_status}
        elif operation == "execution":
            assert after["broker_order_id"] == "exact-entry-id"
            assert after["risk_dollars"] == 120 and after["quantity"] == 20
            assert after["notional_value"] == 2020 and after["entry_price"] == 101
        else:
            assert after["risk_dollars"] == 50 and after["quantity"] == 10
    finally:
        if updating is not None:
            if not updating.done():
                updating.cancel()
            await asyncio.gather(updating, return_exceptions=True)
        event.remove(writer.engine.sync_engine, "before_cursor_execute", observe_write)


@pytest.mark.parametrize("ledger_desk", ["postgres"], indirect=True)
@pytest.mark.parametrize("change,new_risk,reason", [("aggregate", 201, "aggregate"), ("expired", 40, "stale")])
async def test_final_admission_rechecks_capacity_after_concurrent_writer(
    risk_execution_desk, monkeypatch, change, new_risk, reason
):
    copilot, venue, _state, order = risk_execution_desk
    reader = copilot.db
    store = reader.workflows
    head, detail = await store.enqueue_entry(order, copilot.config)
    assert head is not None, detail
    later_signal = await reader.record_signal(
        contract="IBM",
        strategy="capacity-race",
        direction="LONG",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        risk_dollars=40,
        quantity=10,
        asset_class="EQUITY",
        status=SignalStatus.PENDING,
    )
    later_request = OrderRequest(
        symbol="IBM",
        signal_id=later_signal,
        asset_class="EQUITY",
        direction="LONG",
        entry_price=99,
        stop_loss=95,
        take_profit=110,
        quantity=10,
    )
    later, detail = await store.enqueue_entry(later_request, copilot.config)
    assert later is not None, detail
    claim = await store.claim_entry(lease_seconds=60)
    assert claim is not None and claim.id == head.id
    risk = await copilot.ledger.current_risk()
    context = await copilot.broker.entry_market_context(order)
    initial = capacity.assess_entry_capacity(
        order,
        await store.reservations(exclude_signal_id=order.signal_id),
        context,
        copilot.config,
        account_risk=risk,
    )
    assert initial.aggregate_stop_risk == 80

    writer = SignalDatabase(db_url=reader.db_url)
    writer_holds_lock = asyncio.Event()
    release_writer = asyncio.Event()
    admission_requested_lock = asyncio.Event()
    writer_pid = admission_pid = None
    original_lock = writer.workflows.lock

    async def pause_writer(session, *, resource=None):
        nonlocal writer_pid
        await original_lock(session, resource=resource)
        writer_pid = await session.scalar(text("SELECT pg_backend_pid()"))
        writer_holds_lock.set()
        await release_writer.wait()

    def observe_admission(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal admission_pid
        if "workflow_locks" in statement:
            admission_pid = connection.connection.driver_connection.get_server_pid()
            admission_requested_lock.set()

    monkeypatch.setattr(writer.workflows, "lock", pause_writer)
    event.listen(reader.engine.sync_engine, "before_cursor_execute", observe_admission)
    tasks = []
    try:
        async with asyncio.timeout(10):
            publishing = asyncio.create_task(
                writer.update_signal_execution(
                    later_signal, None, status=SignalStatus.SUBMITTING, risk_dollars=new_risk
                )
            )
            tasks.append(publishing)
            await writer_holds_lock.wait()
            admitting = asyncio.create_task(
                store.begin_submission(claim, copilot.config, risk_fingerprint=risk.fingerprint, broker_context=context)
            )
            tasks.append(admitting)
            await admission_requested_lock.wait()
            async with reader.session_factory() as monitor:
                while not await monitor.scalar(
                    text("SELECT :owner = ANY(pg_blocking_pids(:reader))"),
                    {"owner": writer_pid, "reader": admission_pid},
                ):
                    if admitting.done():
                        await admitting
                        pytest.fail("Final admission bypassed the economic writer's PostgreSQL lock")
                    await asyncio.sleep(0.01)
            assert not admitting.done()
            if change == "expired":
                clock = MagicMock(wraps=datetime)
                clock.now.return_value = datetime.now(UTC) + timedelta(
                    seconds=copilot.config.execution.entry_evidence_max_age_seconds + 1
                )
                monkeypatch.setattr(capacity, "datetime", clock)
            release_writer.set()
            await publishing
            refusal = await admitting
        assert refusal is not None and reason in refusal.lower()
        assert (await reader.get_signal_by_id(later_signal))["risk_dollars"] == new_risk
        assert (await store.get_work(head.id)).status == WorkStatus.CHECKING
        assert not any(e["kind"] == EventKind.ENTRY_SUBMITTING for e in await store.events())
        assert not any(method in ("POST", "PATCH", "DELETE") for method, *_ in venue.calls)
    finally:
        release_writer.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        event.remove(reader.engine.sync_engine, "before_cursor_execute", observe_admission)
        await writer.engine.dispose()
