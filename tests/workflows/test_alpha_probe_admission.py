"""A probe reserves new risk only while live, and only on the Alpaca paper scope."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentic_trader.accounting.ledger import AccountSnapshot, reconcile
from agentic_trader.accounting.risk import advance_risk_checkpoint
from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.probe import policy_document
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import LedgerCheckpointRecord
from agentic_trader.storage.workflow import WorkflowStore


def _funded_ledger_checkpoint(now):
    """A minimal genuine reconciled account-risk checkpoint.

    Binding the fixture's store to an Alpaca scope (needed for the paper-probe
    liveness check) also activates the unrelated account-risk admission gate.
    This supplies real reconciled evidence for that gate so it does not block
    before the alpha gate under test ever runs.
    """
    activities = [{"id": "initial", "activity_type": "CSD", "net_amount": "10000"}]
    snapshot = AccountSnapshot(account_id="probe-test-account", cash=Decimal(10000), positions={}, observed_at=now)
    report = reconcile(activities, snapshot)
    payload = {"snapshot": snapshot.model_dump(mode="json"), "report": report.model_dump(mode="json")}
    payload.update(advance_risk_checkpoint({}, payload, activities, account_id="probe-test-account", now=now))
    return payload


def enrolment(*, expires_in_days=30, policy=None):
    now = datetime.now(UTC)
    return {
        "policy": policy if policy is not None else policy_document(),
        "assessment": {"eligible": True, "reasons": [], "observations": {}},
        "first_enrolled_at": now.isoformat(),
        "enrolled_at": now.isoformat(),
        "expires_at": (now + timedelta(days=expires_in_days)).isoformat(),
        "term_days": 30,
        "renewals": 0,
        "actor": "fixture",
    }


async def build(tmp_path, *, paper=True, record=None, listed=True):
    # This config only shapes the database's scope (…/alpaca:paper vs …/alpaca:live);
    # the standard `app_config` fixture is what tests pass into store calls that need
    # a real config for risk checks, exactly as tests/workflows/test_alpha_admission.py does.
    scope_config = AppConfig(execution_mode="alpaca", alpaca_paper=paper)
    db = SignalDatabase(db_path=str(tmp_path / "admission.db"), config=scope_config)
    await db.init_db()
    store = WorkflowStore(db)
    async with db.session_factory() as session, session.begin():
        session.add(
            LedgerCheckpointRecord(
                scope=store.scope,
                account_id="probe-test-account",
                token=uuid4().hex,
                payload=json.dumps(_funded_ledger_checkpoint(datetime.now(UTC))),
            )
        )
    definition = AlphaDefinition(
        "alpha_probe_admission", "Probe", "close", timeframe="1d", eligible_symbols=("SPY",), data_feed="alpaca:iex"
    )
    repository = AlphaRepository(store)
    await repository.register(definition, actor="fixture")
    async with db.session_factory() as session, session.begin():
        await store.lock(session, resource="alpha")
        await repository._append(
            session,
            "registry",
            {"generation": 1, "active": [], "shadow": [], "probe": [definition.version_id] if listed else []},
            EventKind.ALPHA_REGISTRY,
            "fixture",
        )
        if record is not None:
            await repository._append(
                session, f"probe/{definition.version_id}", record, EventKind.ALPHA_REGISTRY, "fixture"
            )
    sid = await db.record_signal(
        "SPY",
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        2,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    request = OrderRequest(
        signal_id=sid,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=1,
        entry_price=100,
        stop_loss=98,
        take_profit=104,
    )
    return db, store, repository, definition, request


async def test_live_probe_passes_the_alpha_gate(tmp_path, app_config):
    db, store, _, _, request = await build(tmp_path, record=enrolment())
    item, reason = await store.enqueue_entry(request, app_config)
    assert item, reason
    await db.engine.dispose()


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"record": enrolment(expires_in_days=-1)}, "expired"),
        ({"record": enrolment(policy={"version": "old"})}, "policy changed"),
        ({"record": None}, "evidence is missing"),
        ({"record": enrolment(), "listed": False}, "not active"),
        ({"record": enrolment(), "paper": False}, "Alpaca paper"),
    ],
)
async def test_non_live_probe_cannot_reserve_risk(tmp_path, app_config, kwargs, fragment):
    db, store, _, _, request = await build(tmp_path, **kwargs)
    item, reason = await store.enqueue_entry(request, app_config)
    assert item is None
    assert fragment in reason
    await db.engine.dispose()


async def test_probe_demoted_during_preflight_blocks_submission_commit(tmp_path, app_config):
    db, store, repository, definition, request = await build(tmp_path, record=enrolment())
    item, reason = await store.enqueue_entry(request, app_config)
    assert item, reason
    claim = await store.claim_entry(lease_seconds=60)
    await repository.demote(definition.version_id, actor="test", expected_generation=1)
    reason = await store.begin_submission(claim, app_config)
    assert reason is not None and "alpha" in reason.lower()
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING
    await db.engine.dispose()
