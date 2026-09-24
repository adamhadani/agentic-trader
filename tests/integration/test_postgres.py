"""Explicitly opted-in integration tests against a disposable test_ PostgreSQL database."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg2
import pytest
from sqlalchemy import update

from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import CloseRequestStatus
from agentic_trader.diagnostics.incidents import IncidentPhase
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind, WorkStatus
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore
from agentic_trader.storage.maintenance import RetentionService
from agentic_trader.storage.migrations import (
    downgrade_migrations,
    get_current_revision,
    run_migrations_head,
    to_sync_url,
)
from agentic_trader.storage.models import SignalRecord
from agentic_trader.storage.operations import OperationsStore
from agentic_trader.storage.workflow import WorkflowStore


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


def test_postgres_migrations_lifecycle(postgres_test_db: str):
    db_url = postgres_test_db
    sync_url = to_sync_url(db_url)

    # 1. Unmigrated / empty database
    assert get_current_revision(db_url) is None

    # 2. Upgrade to head
    run_migrations_head(db_url)
    assert get_current_revision(db_url) == "008_alpha_pipeline"

    # Verify tables in PostgreSQL
    with psycopg2.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = {row[0] for row in cur.fetchall()}
        assert "signals" in tables
        assert "system_state" in tables
        assert "alembic_version" in tables

        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'signals';")
        cols = {row[0] for row in cur.fetchall()}
        expected_cols = {
            "id",
            "timestamp",
            "contract",
            "strategy",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "risk_dollars",
            "reward_dollars",
            "notional_value",
            "status",
            "telegram_message_id",
            "raw_response",
            "exit_price",
            "exit_timestamp",
            "realized_pnl",
            "exit_reason",
            "broker_order_id",
            "asset_class",
            "quantity",
        }
        assert expected_cols.issubset(cols)

        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'signals';")
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_recent_signals" in indexes

    # 3. Downgrade to 001_initial
    downgrade_migrations("001_initial", db_url)
    assert get_current_revision(db_url) == "001_initial"

    with psycopg2.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = {row[0] for row in cur.fetchall()}
        assert "signals" in tables
        assert "system_state" not in tables

    # 4. Downgrade to base
    downgrade_migrations("base", db_url)
    assert get_current_revision(db_url) is None

    with psycopg2.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = {row[0] for row in cur.fetchall()}
        assert "signals" not in tables
        assert "system_state" not in tables

    # 5. Re-upgrade to head
    run_migrations_head(db_url)
    assert get_current_revision(db_url) == "008_alpha_pipeline"


@pytest.mark.asyncio
async def test_postgres_signal_database_operations(postgres_test_db: str):
    db_url = postgres_test_db
    db = SignalDatabase(db_url=db_url)

    assert get_current_revision(db_url) == "008_alpha_pipeline"

    sig_id = await db.record_signal(
        contract="NQ",
        strategy="MIGRATION_TEST",
        direction="LONG",
        entry_price=19500.0,
        stop_loss=19400.0,
        take_profit=19700.0,
        risk_dollars=500.0,
        reward_dollars=1000.0,
        notional_value=390000.0,
        quantity=1.0,
        asset_class="FUTURES",
    )
    assert sig_id == 1

    rec = await db.get_signal_by_id(sig_id)
    assert rec is not None
    assert rec["contract"] == "NQ"
    assert rec["strategy"] == "MIGRATION_TEST"

    await db.update_signal_execution(sig_id, broker_order_id="ALPACAPOSTGRES1", status="EXECUTED")
    updated = await db.get_signal_by_id(sig_id)
    assert updated is not None
    assert updated["status"] == "EXECUTED"
    assert updated["broker_order_id"] == "ALPACAPOSTGRES1"

    await db.set_state("migration_key", "migration_val")
    val = await db.get_state("migration_key")
    assert val == "migration_val"

    cleared = await db.clear_all_signals()
    assert cleared == 1

    await db.engine.dispose()


def test_postgres_cli_db_commands(postgres_test_db: str):
    env = os.environ.copy()
    env["DATABASE_URL"] = postgres_test_db
    env["DB_PATH"] = ""

    # 1. Current on empty
    r1 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "current"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r1.returncode == 0
    assert "Current database revision:" in r1.stdout

    # 2. Upgrade to 001_initial
    r2 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "upgrade", "001_initial"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r2.returncode == 0
    assert "001_initial" in r2.stdout

    # 3. Upgrade to head
    r3 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "upgrade"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r3.returncode == 0
    assert "008_alpha_pipeline" in r3.stdout

    # 4. Downgrade to 001_initial
    r4 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "downgrade", "001_initial"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r4.returncode == 0
    assert "001_initial" in r4.stdout

    # 5. Downgrade to base
    r5 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "downgrade", "base"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r5.returncode == 0
    assert "<base>" in r5.stdout

    # 6. Re-upgrade
    r6 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "upgrade"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r6.returncode == 0
    assert "008_alpha_pipeline" in r6.stdout

    # 7. History
    r7 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "history"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r7.returncode == 0
    assert "008_alpha_pipeline" in r7.stdout
    assert "001_initial" in r7.stdout

    # 8. Clear
    r8 = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "db", "clear", "--yes"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r8.returncode == 0
    assert "Successfully cleared" in r8.stdout


@pytest.mark.asyncio
async def test_postgres_database_operations(postgres_test_db):
    """Test connection and ORM operations against local PostgreSQL if running."""
    db = SignalDatabase(db_url=postgres_test_db)

    assert "postgresql" in db.db_url
    assert db.engine.pool.size() == 10

    # Ensure clean state for test record
    await db.set_state("pg_test_key", "active_123")
    val = await db.get_state("pg_test_key")
    assert val == "active_123"

    # Record and retrieve signal
    sig_id = await db.record_signal(
        contract="SPY",
        strategy="ALPHA_TEST_PG",
        direction="LONG",
        entry_price=580.0,
        stop_loss=570.0,
        take_profit=600.0,
        risk_dollars=100.0,
        reward_dollars=200.0,
        notional_value=5800.0,
        status="PENDING",
    )
    assert sig_id > 0

    sig = await db.get_signal_by_id(sig_id)
    assert sig is not None
    assert sig["contract"] == "SPY"
    assert sig["strategy"] == "ALPHA_TEST_PG"

    # Update status and check active exposure
    await db.update_signal_status(sig_id, "EXECUTED")
    active_positions = await db.get_active_positions()
    assert any(p["id"] == sig_id for p in active_positions)

    # Clean up test signal
    await db.update_signal_status(sig_id, "DISMISSED")
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_postgres_close_claim_is_exclusive_between_database_clients(postgres_test_db):
    first = SignalDatabase(db_url=postgres_test_db)
    second = SignalDatabase(db_url=postgres_test_db)
    try:

        async def claim(db):
            return await db.claim_close_request(
                {"id": str(uuid4()), "symbol": "IWM", "direction": "SHORT", "quantity": 105, "signal_id": None}
            )

        results = await asyncio.gather(claim(first), claim(second))
        assert sorted(won for won, _ in results) == [False, True]
        request_id = next(record["id"] for won, record in results if won)
        await first.update_close_request(request_id, CloseRequestStatus.FAILED, "Preflight rejected")
        assert (await claim(second))[0] is True
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


async def test_postgres_entry_reservation_and_outbox_exclusivity(postgres_test_db, app_config):

    app_config.portfolio.max_concurrent_positions = 1
    first = SignalDatabase(db_url=postgres_test_db)
    second = SignalDatabase(db_url=postgres_test_db)
    try:
        requests = []
        for symbol in ("SPY", "QQQ"):
            sid = await first.record_signal(
                contract=symbol,
                strategy="race",
                direction="LONG",
                entry_price=100,
                stop_loss=95,
                take_profit=110,
                risk_dollars=50,
                quantity=10,
                asset_class="EQUITY",
                notional_value=1000,
            )
            requests.append(
                OrderRequest(
                    signal_id=sid,
                    symbol=symbol,
                    asset_class="EQUITY",
                    direction="LONG",
                    quantity=10,
                    entry_price=100,
                    stop_loss=95,
                    take_profit=110,
                )
            )
        results = await asyncio.gather(
            first.workflows.enqueue_entry(requests[0], app_config),
            second.workflows.enqueue_entry(requests[1], app_config),
        )
        assert sum(item is not None for item, _ in results) == 1
        claims = await asyncio.gather(
            first.workflows.claim_entry(lease_seconds=60), second.workflows.claim_entry(lease_seconds=60)
        )
        assert sum(item is not None for item in claims) == 1
        await first.workflows.enqueue_notification("one", "message", {"text": "test"})
        notices = await asyncio.gather(
            first.workflows.claim_notification(max_attempts=8, lease_seconds=60),
            second.workflows.claim_notification(max_attempts=8, lease_seconds=60),
        )
        assert sum(item is not None for item in notices) == 1
        assert (await first.workflows.list_work(WorkKind.NOTIFICATION))[0].status == WorkStatus.CHECKING
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


async def test_postgres_workflow_upgrade_preserves_existing_trading_state(postgres_test_db):
    db = SignalDatabase(db_url=postgres_test_db)
    try:
        signal_id = await db.record_signal(
            contract="IWM",
            strategy="existing",
            direction="SHORT",
            entry_price=200,
            stop_loss=205,
            take_profit=190,
            risk_dollars=50,
            quantity=10,
            asset_class="EQUITY",
        )
        await db.update_signal_execution(signal_id, "existing-entry")
        await db.set_state("trading_halted", "true")
        claimed, _ = await db.claim_close_request(
            {"id": str(uuid4()), "symbol": "IWM", "direction": "SHORT", "quantity": 10, "signal_id": signal_id}
        )
        assert claimed
    finally:
        await db.engine.dispose()
    # Rehearse the installed schema's upgrade with existing positions, claims and audits.
    downgrade_migrations("004_close_requests", postgres_test_db)

    def snapshot():
        with psycopg2.connect(to_sync_url(postgres_test_db)) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT 'signals', (to_jsonb(t) - ARRAY['timeframe','alpha_version','alpha_policy','decision_provenance'])::text FROM signals t UNION ALL "
                "SELECT 'close_requests', row_to_json(t)::text FROM close_requests t UNION ALL "
                "SELECT 'audit_events', row_to_json(t)::text FROM audit_events t UNION ALL "
                "SELECT 'system_state', row_to_json(t)::text FROM system_state t ORDER BY 1, 2"
            )
            return cursor.fetchall()

    before = snapshot()
    assert before
    run_migrations_head(postgres_test_db)
    assert snapshot() == before
    assert get_current_revision(postgres_test_db) == "008_alpha_pipeline"


async def test_postgres_account_ledger_fences_independent_importers(postgres_test_db):

    first = SignalDatabase(db_url=postgres_test_db)
    second = SignalDatabase(db_url=postgres_test_db)
    try:
        a, b = LedgerStore(first.workflows), LedgerStore(second.workflows)
        tokens = await asyncio.gather(a.begin("account"), b.begin("account"))
        results = await asyncio.gather(
            a.commit(tokens[0], [{"id": "a"}], {"source": "a"}),
            b.commit(tokens[1], [{"id": "b"}], {"source": "b"}),
        )
        assert sum(results) == 1
        assert len(await a.activities()) == 1
        before = await a.status()
        await b.rebuild()
        assert await a.status() == before
        assert not await a.commit(tokens[0], [], {})
        assert not await b.commit(tokens[1], [], {})
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


async def test_postgres_ledger_transaction_does_not_block_entry_admission_lock(postgres_test_db):
    db = SignalDatabase(db_url=postgres_test_db)
    try:
        async with db.session_factory() as ledger_session, ledger_session.begin():
            await db.workflows.lock(ledger_session, resource="ledger")
            async with db.session_factory() as entry_session, entry_session.begin():
                await asyncio.wait_for(db.workflows.lock(entry_session), timeout=2)
    finally:
        await db.engine.dispose()


async def test_postgres_incidents_and_retention_between_independent_clients(postgres_test_db, app_config):

    first = SignalDatabase(db_url=postgres_test_db)
    second = SignalDatabase(db_url=postgres_test_db)
    try:
        a, b = OperationsStore(first.workflows), OperationsStore(second.workflows)
        now = datetime.now(UTC)
        policy = app_config.operations
        await a.observe("worker", ready=False, observed_at=now, detail="", policy=policy)
        opened = now + timedelta(seconds=policy.failure_seconds)
        notices = await asyncio.gather(
            *(store.observe("worker", ready=False, observed_at=opened, detail="", policy=policy) for store in (a, b))
        )
        assert sum(notice is not None for notice in notices) == 1
        assert len(await first.workflows.list_work(WorkKind.NOTIFICATION)) == 1
        await b.rebuild()
        assert (await a.incidents())[0]["phase"] == IncidentPhase.OPEN
        sweeps = await asyncio.gather(
            *(
                RetentionService(db.workflows, policy).sweep(apply=True, now=now, only_if_due=True)
                for db in (first, second)
            )
        )
        assert sum(result["deferred"] for result in sweeps) == 1
        async with first.session_factory() as session, session.begin():
            await first.workflows.lock(session, resource="retention")
            async with second.session_factory() as other, other.begin():
                await asyncio.wait_for(second.workflows.lock(other), timeout=2)
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


async def test_postgres_expire_stale_signals_sweeps_only_stale_pending_cards(postgres_test_db):
    """The sweep's multi-statement transaction (a SELECT, per-row conditional UPDATEs and
    ``add_notification`` inserts, all sharing one ``AsyncSession``) against real
    PostgreSQL/asyncpg, not just SQLite: two stale cards expire and enqueue one
    CARD_EXPIRED notification each with the correct ``reevaluable`` flag, a fresh card is
    untouched, and a second call is a no-op."""
    db = SignalDatabase(db_url=postgres_test_db)
    try:
        past_ny_date = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)  # 10:00 ET Sept 23
        next_day_now = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)  # 11:00 ET Sept 24

        async def issue_card(contract: str, issued_at: datetime) -> int:
            signal_id = await db.record_signal(
                contract=contract,
                strategy="s",
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=2.0,
                asset_class="EQUITY",
                quantity=1,
            )
            async with db.session_factory() as session, session.begin():
                await session.execute(
                    update(SignalRecord).where(SignalRecord.id == signal_id).values(timestamp=issued_at)
                )
            return signal_id

        configured_stale = await issue_card("SPY", past_ny_date)
        dynamic_stale = await issue_card("DYNAMIC_XYZ", past_ny_date)
        fresh = await issue_card("QQQ", next_day_now)

        expired = await db.expire_stale_signals(next_day_now, configured_contracts=frozenset({"SPY"}))

        assert set(expired) == {configured_stale, dynamic_stale}
        assert (await db.get_signal_by_id(configured_stale))["status"] == "EXPIRED"
        assert (await db.get_signal_by_id(dynamic_stale))["status"] == "EXPIRED"
        assert (await db.get_signal_by_id(fresh))["status"] == "PENDING"

        notifications = await db.workflows.list_work(WorkKind.NOTIFICATION)
        card_expired = [n for n in notifications if n.payload["kind"] == NotificationKind.CARD_EXPIRED]
        assert len(card_expired) == 2
        by_signal = {n.payload["arguments"]["signal_id"]: n for n in card_expired}
        assert by_signal[configured_stale].payload["arguments"]["reevaluable"] is True
        assert by_signal[dynamic_stale].payload["arguments"]["reevaluable"] is False

        second_call = await db.expire_stale_signals(next_day_now, configured_contracts=frozenset({"SPY"}))
        assert second_call == []
        assert len(await db.workflows.list_work(WorkKind.NOTIFICATION)) == 2
    finally:
        await db.engine.dispose()


async def test_postgres_recent_events_reads_one_kind_from_named_streams_in_scope(postgres_test_db):
    """`/scan SYMBOL`'s journaled liquidity-reference read against real PostgreSQL/asyncpg:
    the ``stream IN (...)`` + kind filter, newest first, isolated to the workflow scope."""
    db = SignalDatabase(db_url=postgres_test_db)
    try:
        await db.init_db()
        other = WorkflowStore(db)
        other.scope = "other-environment/paper"
        for store, stream, kind, n in [
            (db.workflows, "scan/2026-09-20", EventKind.DYNAMIC_UNIVERSE_BUILT, 1),
            (db.workflows, "scan/2026-09-21", EventKind.SCAN_CANDIDATES_RANKED, 2),
            (db.workflows, "scan/2026-09-21", EventKind.DYNAMIC_UNIVERSE_BUILT, 3),
            (db.workflows, "scan/2026-09-01", EventKind.DYNAMIC_UNIVERSE_BUILT, 4),
            (other, "scan/2026-09-21", EventKind.DYNAMIC_UNIVERSE_BUILT, 5),
        ]:
            async with db.session_factory() as session, session.begin():
                await store.lock(session)
                await store.append(session, stream=stream, kind=kind, payload={"n": n})

        events = await db.workflows.recent_events(
            EventKind.DYNAMIC_UNIVERSE_BUILT, streams=["scan/2026-09-20", "scan/2026-09-21"]
        )

        assert [e["payload"]["n"] for e in events] == [3, 1]
        assert await db.workflows.recent_events(EventKind.DYNAMIC_UNIVERSE_BUILT, streams=[]) == []
    finally:
        await db.engine.dispose()
