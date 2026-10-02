# tests/integration/test_pooled_ledger_postgres.py
"""Two independent clients: pooled charges never exceed a reservation; a confirmation interval is consumed once."""

import asyncio
from datetime import date

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]

RECORD = {
    "protocol_sha256": "p" * 64,
    "cohort_sha256": "c" * 64,
    "cube_sha256": "k" * 64,
    "code_revision": "abc1234",
    "budget": 200,
    "journal_scope": "test/alpaca:paper",
}


async def _clients(url):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=url, config=config)
    second = SignalDatabase(db_url=url, config=config)
    await first.init_db()
    return first, second, AlphaRepository(first.workflows), AlphaRepository(second.workflows)


async def test_concurrent_charges_never_exceed_a_family_reservation(postgres_test_db):
    first, second, a, b = await _clients(postgres_test_db)
    await a.reserve_pooled_campaign("cmp", RECORD)
    await a.reserve_pooled_family("cmp", "rev", 3)
    results = await asyncio.gather(
        *[
            (a if i % 2 else b).charge_pooled_formula("cmp", "rev", formula_id=f"f{i}", expression=f"e{i}", nodes=2)
            for i in range(8)
        ],
        return_exceptions=True,
    )
    assert sum(result is True for result in results) == 3
    assert sum(isinstance(result, ValueError) for result in results) == 5
    assert (await a.get("pooled/campaign/cmp/family/rev"))["charged"] == 3
    assert (await b.get("pooled/ledger"))["formulas_charged"] == 3
    await first.engine.dispose()
    await second.engine.dispose()


async def test_concurrent_confirmation_consumption_admits_exactly_one(postgres_test_db):
    first, second, a, b = await _clients(postgres_test_db)
    await a.reserve_pooled_campaign("one", RECORD)
    await a.reserve_pooled_campaign("two", {**RECORD, "protocol_sha256": "q" * 64})
    interval = (date(2024, 1, 2), date(2026, 7, 31))
    results = await asyncio.gather(
        a.consume_pooled_confirmation(campaign_id="one", cohort_sha256="c" * 64, interval=interval, candidates=()),
        b.consume_pooled_confirmation(campaign_id="two", cohort_sha256="c" * 64, interval=interval, candidates=()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len((await a.get("pooled/confirmation"))["intervals"]) == 1
    await first.engine.dispose()
    await second.engine.dispose()
