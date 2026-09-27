"""Admission of a catalog probe's signal: live enrolment plus its immutable strategy contract.

A catalog definition has no symbol universe; its contract is the leg, the frozen bracket
policy and a same-session event naming the signal's own symbol. Admission reads the real
clock, so enrolments here use real time (expired ones start in the past).
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.probe import load_catalog_probe
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import SignalRecord
from tests.research.probe_fixtures import paper_database
from tests.research.test_apriori_catalog_probe import ENTRY, study


CONTRACT = "Catalog signal differs from its immutable strategy contract."


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


@pytest.fixture
def repository(db):
    return AlphaRepository(db.workflows)


@pytest.fixture
def definition(tmp_path):
    return load_catalog_probe(Path(ENTRY), study(tmp_path), "LONG")


def event(session: date, symbol="NVDA"):
    return {"pead_event": {"symbol": symbol, "session": session.isoformat()}}


async def rejection(db, definition, *, direction="LONG", strategy="pead_long", policy=None, provenance=event):
    """Record a signal, align its provenance to its own New York session, then run the gate.

    ``provenance`` maps the signal's New York session date to its decision provenance
    (``None`` for none), so no test depends on the wall clock crossing midnight.
    """
    sid = await db.record_signal(
        "NVDA",
        strategy,
        direction,
        100,
        95,
        115,
        100.0,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition["version_id"],
        alpha_policy=policy or definition["execution"],
    )
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        document = provenance(row.timestamp.astimezone(ET_TZ).date())
        row.decision_provenance = json.dumps(document) if document is not None else None
        await session.flush()
        return await db.workflows._alpha_entry_rejection(session, row)


async def test_a_matching_signal_is_admitted(db, repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
    assert await rejection(db, definition) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "SHORT"},
        {"strategy": "pead_short"},
        {"policy": "stop"},
        {"provenance": lambda session: event(session, "AMD")},
        {"provenance": lambda session: event(session - timedelta(days=1))},
        {"provenance": lambda session: None},
        {"provenance": lambda session: {"other": {}}},
    ],
)
async def test_each_contract_mutation_is_refused(db, repository, definition, changes):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
    if changes.get("policy") == "stop":
        changes = {"policy": {**definition["execution"], "stop_atr": 1.5}}
    assert await rejection(db, definition, **changes) == CONTRACT


async def test_a_demoted_catalog_probe_is_refused(db, repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
    await repository.demote(definition["version_id"], actor="op", expected_generation=1)
    assert (await rejection(db, definition)).startswith("Alpha version is not active/qualified")


async def test_an_expired_catalog_probe_is_blocked(db, repository, definition):
    await repository.enrol_catalog_probe(
        definition, actor="op", expected_generation=0, days=1, now=datetime.now(UTC) - timedelta(days=2)
    )
    assert (await rejection(db, definition)).startswith("Paper probe blocked:")


async def gate(db, strategy, *, alpha_version=None, alpha_policy=None):
    sid = await db.record_signal(
        "NVDA",
        strategy,
        "LONG",
        100,
        95,
        115,
        100.0,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=alpha_version,
        alpha_policy=alpha_policy,
    )
    async with db.session_factory() as session, session.begin():
        return await db.workflows._alpha_entry_rejection(session, await session.get(SignalRecord, sid))


@pytest.mark.parametrize("strategy", ["pead_long", "PEAD_LONG", "pead_short"])
async def test_a_catalog_strategy_signal_without_its_version_fails_closed(db, repository, definition, strategy):
    """A producer bug or rebuilt card must not bypass probe liveness and the contract check."""
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
    reason = await gate(db, strategy, alpha_policy=definition["execution"])
    assert reason == "Alpha version is not active/qualified; request a fresh scan after qualification."


async def test_a_native_strategy_without_a_version_stays_outside_the_alpha_gate(db, repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0)
    assert await gate(db, "pullback") is None
