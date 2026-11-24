"""Admission of an insider watch signal: the frozen cube bracket byte for byte and a same-session event.

A watch card has no registry entry, so its contract is the frozen ``insider`` v1 entry and its pooled
protocol's bracket (``research/apriori/watch_contract.py``). Admission reads the real clock, so each
signal's provenance is aligned to its own New York session.
"""

import json
from datetime import date, timedelta

import pytest

from agentic_trader.agent.insider_watch import InsiderWatchSource
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID, frozen_watch_contract
from agentic_trader.storage import workflow as workflow_module
from agentic_trader.storage.models import SignalRecord
from tests.research.probe_fixtures import paper_database


CONTRACT = "Insider watch signal differs from its frozen contract."
WATCH = frozen_watch_contract()


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


def event(session: date, symbol="NVDA", entry_sha256=WATCH.entry_sha256):
    return {"insider_watch": {"symbol": symbol, "session": session.isoformat(), "entry_sha256": entry_sha256}}


async def rejection(
    db,
    *,
    direction="LONG",
    strategy=INSIDER_WATCH_STRATEGY_ID,
    timeframe="1d",
    policy=None,
    alpha_version=None,
    provenance=event,
):
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
        timeframe=timeframe,
        alpha_version=alpha_version,
        alpha_policy=policy if policy is not None else WATCH.policy,
    )
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        document = provenance(row.timestamp.astimezone(ET_TZ).date())
        row.decision_provenance = json.dumps(document) if document is not None else None
        await session.flush()
        return await db.workflows._alpha_entry_rejection(session, row)


async def test_a_matching_watch_signal_is_admitted_without_any_registry_entry(db):
    assert await rejection(db) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "SHORT"},
        {"timeframe": "4h"},
        {"alpha_version": "apriori:insider:v1:long:abc"},
        {"policy": "stop"},
        {"policy": "lifetime"},
        {"provenance": lambda session: event(session, "AMD")},
        {"provenance": lambda session: event(session - timedelta(days=1))},  # yesterday's event
        {"provenance": lambda session: event(session, entry_sha256="0" * 64)},
        {"provenance": lambda session: None},
        {"provenance": lambda session: {"pead_event": {"symbol": "NVDA", "session": session.isoformat()}}},
    ],
    ids=[
        "short",
        "timeframe",
        "alpha-version",
        "stop",
        "lifetime",
        "other-symbol",
        "other-session",
        "other-entry",
        "no-provenance",
        "pead-provenance",
    ],
)
async def test_each_contract_mutation_is_refused(db, changes):
    if changes.get("policy") == "stop":
        changes = {"policy": {**WATCH.policy, "stop_atr": 1.5}}
    elif changes.get("policy") == "lifetime":
        changes = {"policy": {**WATCH.policy, "lifetime": {**WATCH.policy["lifetime"], "holding_sessions": 21}}}
    assert await rejection(db, **changes) == CONTRACT


async def test_a_native_strategy_carrying_watch_provenance_is_not_judged_as_a_watch_card(db):
    # Only the watch strategy id selects the watch contract; any other strategy keeps its own checks.
    assert await rejection(db, strategy="TREND_PULLBACK", policy={}) is None


async def test_an_unloadable_contract_refuses_with_a_type_name(db, monkeypatch):
    def broken():
        raise FileNotFoundError("insider-v1.json")

    monkeypatch.setattr(workflow_module, "frozen_watch_contract", broken)
    assert await rejection(db) == "Insider watch contract unavailable: FileNotFoundError."


def test_the_watch_provenance_key_is_the_source_s():
    assert InsiderWatchSource.provenance_key == workflow_module.WATCH_PROVENANCE_KEY
