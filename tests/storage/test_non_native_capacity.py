"""Admission of a non-native entry under ``portfolio.max_non_native_positions`` (operator decision O3).

Open non-native positions (EXECUTED) plus non-native entries still in flight (SUBMITTING) plus the entry being
admitted may not exceed the cap. A native entry is never judged. A refusal is a business refusal: no halt, and
the signal stays pending/unreserved.
"""

import json
from datetime import date

import pytest
from sqlalchemy import select

from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import SignalStatus, SystemStateKey
from agentic_trader.execution.durable import WorkStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.probe import PAPER_PROBE_TAG
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID, frozen_watch_contract
from agentic_trader.storage.models import AuditEventRecord, SignalRecord, SystemStateRecord
from agentic_trader.storage.workflow import NON_NATIVE_CAPACITY_REASON
from tests.research.probe_fixtures import paper_database
from tests.workflows.test_alpha_probe_admission import build, enrolment


WATCH = frozen_watch_contract()


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


async def record(
    db, symbol="NVDA", *, strategy="TREND_PULLBACK", status=SignalStatus.PENDING, probe=False, policy=None
):
    return await db.record_signal(
        symbol,
        strategy,
        "LONG",
        100,
        95,
        115,
        5.0,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        status=status,
        alpha_policy=policy,
        decision_provenance={PAPER_PROBE_TAG: True} if probe else None,
    )


async def fill(db, *, open_=0, in_flight=0, strategy=INSIDER_WATCH_STRATEGY_ID, policy=WATCH.policy):
    for index in range(open_):
        await record(db, f"OP{index}", strategy=strategy, status=SignalStatus.EXECUTED, policy=policy)
    for index in range(in_flight):
        await record(db, f"IF{index}", strategy=strategy, status=SignalStatus.SUBMITTING, policy=policy)


async def judge(db, config, signal_id, cap):
    config.portfolio.max_non_native_positions = cap
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, signal_id)
        return await db.workflows._non_native_capacity_rejection(session, row, config)


async def candidate(db, *, strategy=INSIDER_WATCH_STRATEGY_ID, policy=WATCH.policy, probe=False):
    return await record(db, "NEW", strategy=strategy, policy=policy, probe=probe)


async def test_the_entry_that_reaches_the_cap_is_admitted(db, app_config):
    await fill(db, open_=4)
    assert await judge(db, app_config, await candidate(db), 5) is None


async def test_the_sixth_non_native_entry_is_refused_at_five_open(db, app_config):
    await fill(db, open_=5)
    reason = await judge(db, app_config, await candidate(db), 5)
    assert reason is not None
    assert reason.startswith(NON_NATIVE_CAPACITY_REASON)
    assert NON_NATIVE_CAPACITY_REASON == "non_native_capacity"


async def test_four_open_plus_one_in_flight_refuses_the_next(db, app_config):
    await fill(db, open_=4)
    await record(db, "FLY", strategy=INSIDER_WATCH_STRATEGY_ID, status=SignalStatus.SUBMITTING, policy=WATCH.policy)
    assert (await judge(db, app_config, await candidate(db), 5)).startswith(NON_NATIVE_CAPACITY_REASON)


async def test_the_entry_being_admitted_is_not_counted_against_itself(db, app_config):
    # begin_submission sees its own signal as SUBMITTING: 4 others + itself is the 5th, admitted.
    await fill(db, open_=4)
    own = await record(
        db, "OWN", strategy=INSIDER_WATCH_STRATEGY_ID, status=SignalStatus.SUBMITTING, policy=WATCH.policy
    )
    assert await judge(db, app_config, own, 5) is None


async def test_a_native_entry_is_admitted_with_the_non_native_slots_full(db, app_config):
    await fill(db, open_=5)
    native = await record(db, "NATV")
    assert await judge(db, app_config, native, 5) is None


async def test_a_probe_or_alpha_entry_counts_as_non_native(db, app_config):
    await fill(db, open_=5)
    assert await judge(db, app_config, await candidate(db, strategy="TREND_PULLBACK", policy=None, probe=True), 5)
    assert await judge(db, app_config, await candidate(db, strategy="alpha_x", policy={"kind": "bracket"}), 5)


async def test_native_closed_and_pending_signals_do_not_fill_the_slots(db, app_config):
    for index in range(5):
        await record(db, f"NAT{index}", status=SignalStatus.EXECUTED)  # native positions
        await record(
            db, f"CLS{index}", strategy=INSIDER_WATCH_STRATEGY_ID, status=SignalStatus.CLOSED_WIN, policy=WATCH.policy
        )
        await record(
            db, f"PND{index}", strategy=INSIDER_WATCH_STRATEGY_ID, policy=WATCH.policy
        )  # a card, no reservation
    assert await judge(db, app_config, await candidate(db), 5) is None


async def test_no_cap_means_no_check(db, app_config):
    await fill(db, open_=8)
    assert await judge(db, app_config, await candidate(db), None) is None


def _event(session: date) -> dict:
    return {"insider_watch": {"symbol": "NEW", "session": session.isoformat(), "entry_sha256": WATCH.entry_sha256}}


async def test_enqueue_refuses_a_full_non_native_book_without_reserving_or_halting(db, app_config):

    app_config.portfolio.max_non_native_positions = 5
    await fill(db, open_=5)
    sid = await candidate(db)
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        row.decision_provenance = json.dumps(_event(row.timestamp.astimezone(ET_TZ).date()))
    request = OrderRequest(
        signal_id=sid, symbol="NEW", asset_class="EQUITY", direction="LONG", quantity=1, entry_price=100,
        stop_loss=95, take_profit=115,
    )  # fmt: skip

    item, reason = await db.workflows.enqueue_entry(request, app_config)

    assert item is None
    assert reason.startswith(NON_NATIVE_CAPACITY_REASON)
    async with db.session_factory() as session:
        assert (await session.get(SignalRecord, sid)).status == SignalStatus.PENDING
        halted = await session.get(SystemStateRecord, SystemStateKey.TRADING_HALTED)
        assert halted is None or halted.value.lower() not in ("true", "1", "yes")
        audits = (await session.scalars(select(AuditEventRecord))).all()
    assert any(NON_NATIVE_CAPACITY_REASON in a.payload for a in audits)


async def test_the_submission_commit_refuses_when_the_slots_filled_after_the_claim(tmp_path, app_config):

    db, store, _, definition, request = await build(tmp_path, record=enrolment())
    item, reason = await store.enqueue_entry(request, app_config)  # the cap is off: admitted and queued
    assert item, reason
    claim = await store.claim_entry(lease_seconds=60)
    await record(db, "HELD", strategy=definition.alpha_id, status=SignalStatus.EXECUTED, policy={"kind": "bracket"})
    app_config.portfolio.max_non_native_positions = 1

    reason = await store.begin_submission(claim, app_config)

    assert reason is not None and reason.startswith(NON_NATIVE_CAPACITY_REASON)
    assert reason.endswith("No order submitted.")
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING  # dispatch resolves it as a refusal
    await db.engine.dispose()
