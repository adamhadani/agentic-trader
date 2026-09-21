"""Two independent clients: enrolment is compare-and-swap; symbol ownership is exclusive."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import LedgerCheckpointRecord
from tests.research.probe_fixtures import make_definition, seed  # created in Task 6
from tests.workflows.test_alpha_probe_admission import _funded_ledger_checkpoint


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


async def test_concurrent_enrolments_for_one_symbol_admit_exactly_one(postgres_test_db):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a, b = AlphaRepository(first.workflows), AlphaRepository(second.workflows)
    one, two = make_definition("alpha_one"), make_definition("alpha_two")
    await seed(a, one)
    await seed(a, two)
    results = await asyncio.gather(
        a.enrol_probe(one.version_id, actor="op", expected_generation=0),
        b.enrol_probe(two.version_id, actor="op", expected_generation=0),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert len((await a.snapshot()).probe) == 1
    await first.engine.dispose()
    await second.engine.dispose()


async def test_probe_demoted_between_claim_and_submission_blocks_commit_across_clients(postgres_test_db, app_config):
    """Enrolment vs. submission: demotion committed by one client must be seen by another.

    Reuses the fixture construction from tests/workflows/test_alpha_probe_admission.py
    (the funded ledger checkpoint helper) instead of reinventing the account-risk
    admission evidence; that module's own test pins the single-client (SQLite)
    version of this same invariant.
    """
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a, b = AlphaRepository(first.workflows), AlphaRepository(second.workflows)
    definition = make_definition("alpha_probe_race", "SPY")
    await seed(a, definition)
    generation = await a.enrol_probe(definition.version_id, actor="op", expected_generation=0)
    async with first.session_factory() as session, session.begin():
        session.add(
            LedgerCheckpointRecord(
                scope=first.workflows.scope,
                account_id="probe-test-account",
                token=uuid4().hex,
                payload=json.dumps(_funded_ledger_checkpoint(datetime.now(UTC))),
            )
        )
    sid = await first.record_signal(
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
    item, reason = await first.workflows.enqueue_entry(request, app_config)
    assert item, reason
    claim = await first.workflows.claim_entry(lease_seconds=60)
    assert claim is not None
    await b.demote(definition.version_id, actor="op", expected_generation=generation)
    reason = await first.workflows.begin_submission(claim, app_config)
    assert reason is not None and "alpha" in reason.lower()
    await first.engine.dispose()
    await second.engine.dispose()
