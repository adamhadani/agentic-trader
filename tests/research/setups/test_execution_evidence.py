"""Fill slippage and tap age per sent card, from the entry workflow's own journal evidence."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import EventKind, OrderObservation, WorkKind, WorkStatus
from agentic_trader.research.setups.execution_evidence import (
    collect_execution,
    entry_fill,
    execute_tap,
    fill_slip_r,
    observed_fill,
    reprice_target,
    summarize_execution,
)
from agentic_trader.storage.models import SignalRecord, WorkItemRecord


def test_fill_slip_r_is_adverse_positive_in_r_units():
    assert fill_slip_r("LONG", 100.0, 99.0, 100.25) == pytest.approx(0.25)
    assert fill_slip_r("LONG", 100.0, 99.0, 99.75) == pytest.approx(-0.25)
    assert fill_slip_r("SHORT", 100.0, 101.0, 99.75) == pytest.approx(0.25)
    assert fill_slip_r("SHORT", 100.0, 101.0, 100.5) == pytest.approx(-0.5)
    assert fill_slip_r("LONG", 100.0, 100.0, 100.1) is None
    assert fill_slip_r("LONG", 100.0, 99.0, None) is None


def test_entry_fill_reads_the_last_resolved_event_only():
    events = [
        {"kind": EventKind.ENTRY_SUBMITTING, "payload": {"signal_id": 1}},
        {
            "kind": EventKind.ENTRY_RESOLVED,
            "payload": {"status": "rejected", "result": {"fill_price": None, "filled_quantity": None}},
        },
        {
            "kind": EventKind.ENTRY_RESOLVED,
            "payload": {"status": "accepted", "result": {"fill_price": 100.25, "filled_quantity": 4.0}},
        },
    ]
    assert entry_fill(events) == (100.25, 4.0)
    assert entry_fill(events[:1]) == (None, None)


def test_execute_tap_is_the_applied_execute_assessment():
    events = [
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "reprice", "applied": True, "age_seconds": 900.0, "r_consumed": 0.4},
        },
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "execute", "applied": False, "age_seconds": 30.0, "r_consumed": 0.0},
        },
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "execute", "applied": True, "age_seconds": 75.5, "r_consumed": 0.1},
        },
    ]
    assert execute_tap(events) == {"age_seconds": 75.5, "r_consumed": 0.1}
    assert execute_tap(events[:2]) is None


T0 = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


async def _signal(session, temp_db, *, status=SignalStatus.EXECUTED, raw_response=None, broker_order_id=None) -> int:
    signal = SignalRecord(
        timestamp=T0,
        contract="AAPL",
        strategy="BREAKOUT",
        direction="LONG",
        entry_price=100.25,
        stop_loss=99.0,
        take_profit=102.0,
        risk_dollars=5.0,
        status=str(status),
        raw_response=raw_response,
        broker_order_id=broker_order_id,
        environment=temp_db.environment,
        execution_mode=temp_db.execution_mode,
    )
    session.add(signal)
    await session.flush()
    return signal.id


async def _entry(session, temp_db, signal_id, item_id, *, fill=None, resolved=True) -> None:
    """The signal's entry work item and, when ``resolved``, its ``entry_resolved`` acknowledgement."""
    session.add(
        WorkItemRecord(
            id=item_id,
            scope=temp_db.workflows.scope,
            dedup_key=str(signal_id),
            kind=WorkKind.ENTRY,
            status=WorkStatus.ACCEPTED,
            payload=json.dumps({"signal_id": signal_id}),
            result="{}",
            available_at=T0,
            created_at=T0,
        )
    )
    await session.flush()
    if resolved:
        price, quantity = fill or (None, None)
        await temp_db.workflows.append(
            session,
            stream=f"entry/{item_id}",
            kind=EventKind.ENTRY_RESOLVED,
            payload={
                "status": "accepted",
                "result": {"fill_price": price, "filled_quantity": quantity},
                "recovery": None,
            },
        )


async def _tap(session, temp_db, signal_id, *, outcome="execute", age=75.5, r=0.1, new_signal_id=None) -> None:
    await temp_db.workflows.append(
        session,
        stream=f"card/{signal_id}",
        kind=EventKind.CARD_TAP_ASSESSED,
        payload={
            "signal_id": signal_id,
            "outcome": outcome,
            "applied": True,
            "age_seconds": age,
            "r_consumed": r,
            "new_signal_id": new_signal_id,
        },
        key=f"card_tap_assessed/{signal_id}/{outcome}",
    )


async def _seed(temp_db, *, with_fill: bool, with_tap: bool, status=SignalStatus.EXECUTED) -> int:
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        signal_id = await _signal(session, temp_db, status=status)
        await _entry(session, temp_db, signal_id, "client-1", fill=(100.25, 4.0), resolved=with_fill)
        if with_tap:
            await _tap(session, temp_db, signal_id)
        return signal_id


def _frame(signal_id, *, planned=100.0, stop=99.0):
    return pd.DataFrame(
        [
            {
                "signal_id": signal_id,
                "contract": "AAPL",
                "direction": "LONG",
                "outcome": "sent",
                "sent": True,
                "entry": planned,
                "stop": stop,
            }
        ]
    )


async def test_collect_execution_joins_signal_fill_and_tap(temp_db):
    signal_id = await _seed(temp_db, with_fill=True, with_tap=True)
    assert await temp_db.workflows.entry_item_ids([signal_id, 999]) == {signal_id: "client-1"}

    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["status"] == "EXECUTED" and row["fill_price"] == 100.25 and row["filled_quantity"] == 4.0
    # The paper simulator's path: no order observations, the acknowledgement itself carries the fill.
    assert row["fill_source"] == "entry_resolved"
    assert row["fill_slip_r"] == pytest.approx(0.25)
    assert row["tap_age_seconds"] == 75.5 and row["tap_r_consumed"] == 0.1
    summary = summarize_execution(frame)
    assert summary == {
        "cards": 1,
        "entered": 1,
        "filled": 1,
        "missing_fill_evidence": 0,
        "repriced": 0,
        "missing_signal_rows": 0,
        "fill_source": {"order_observed": 0, "entry_resolved": 1},
        "mean_fill_slip_r": pytest.approx(0.25),
        "mean_tap_age_seconds": 75.5,
    }


async def test_missing_fill_evidence_is_counted_not_zeroed(temp_db):
    signal_id = await _seed(temp_db, with_fill=False, with_tap=False)
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["fill_price"] is None and row["fill_slip_r"] is None and row["tap_age_seconds"] is None
    summary = summarize_execution(frame)
    assert summary["entered"] == 1 and summary["filled"] == 0 and summary["missing_fill_evidence"] == 1
    assert summary["mean_fill_slip_r"] is None and summary["mean_tap_age_seconds"] is None


async def test_collect_execution_skips_rows_without_a_signal_id(temp_db):
    frame = await collect_execution(
        temp_db,
        pd.DataFrame(
            [
                {
                    "signal_id": None,
                    "contract": "AAPL",
                    "direction": "LONG",
                    "outcome": "per-scan budget spent",
                    "sent": False,
                    "entry": 100.0,
                    "stop": 99.0,
                }
            ]
        ),
    )
    assert frame.empty
    assert summarize_execution(frame) == {
        "cards": 0,
        "entered": 0,
        "filled": 0,
        "missing_fill_evidence": 0,
        "repriced": 0,
        "missing_signal_rows": 0,
        "fill_source": {"order_observed": 0, "entry_resolved": 0},
        "mean_fill_slip_r": None,
        "mean_tap_age_seconds": None,
    }


@pytest.mark.parametrize("status", [SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS, SignalStatus.CLOSED_MANUAL])
async def test_closed_trades_count_as_entered(temp_db, status):
    """A closed position's signal leaves EXECUTED; its fill is still entry evidence."""
    signal_id = await _seed(temp_db, with_fill=True, with_tap=False, status=status)
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["status"] == status and row["fill_price"] == 100.25
    summary = summarize_execution(frame)
    assert summary["entered"] == 1 and summary["missing_fill_evidence"] == 0
    assert summary["mean_fill_slip_r"] == pytest.approx(0.25)


@pytest.mark.parametrize("status", [SignalStatus.PENDING, SignalStatus.EXPIRED, SignalStatus.FAILED])
async def test_cards_that_never_entered_are_not_counted(temp_db, status):
    signal_id = await _seed(temp_db, with_fill=False, with_tap=False, status=status)
    summary = summarize_execution(await collect_execution(temp_db, _frame(signal_id)))
    assert summary["cards"] == 1 and summary["entered"] == 0 and summary["missing_fill_evidence"] == 0


def _observation(order_id: str, status: str, filled: str, price: str | None, minute: int) -> OrderObservation:
    return OrderObservation(
        order_id=order_id,
        client_order_id="client-1",
        symbol="AAPL",
        side="buy",
        status=status,
        quantity="4",
        filled_quantity=filled,
        average_fill_price=price,
        limit_price="100.00",
        order_type="limit",
        order_class="bracket",
        updated_at=datetime(2026, 3, 2, 15, minute, tzinfo=UTC),
    )


async def _seed_alpaca_entry(temp_db, *, status=SignalStatus.EXECUTED, raw_response=None) -> int:
    """The Alpaca GTC limit-bracket shape: the acknowledgement carries no fill; order observations do."""
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        signal_id = await _signal(session, temp_db, status=status, raw_response=raw_response, broker_order_id="ord-1")
        await _entry(session, temp_db, signal_id, "client-1", fill=None)
    await temp_db.workflows.observe_orders(
        [_observation("ord-1", "new", "0", None, 1), _observation("ord-1", "filled", "4", "100.25", 7)]
    )
    return signal_id


async def test_alpaca_fill_comes_from_order_observations(temp_db):
    signal_id = await _seed_alpaca_entry(temp_db)
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert (row["fill_price"], row["filled_quantity"], row["fill_source"]) == (100.25, 4.0, "order_observed")
    assert row["fill_slip_r"] == pytest.approx(0.25)
    summary = summarize_execution(frame)
    assert summary["entered"] == 1 and summary["filled"] == 1 and summary["missing_fill_evidence"] == 0
    assert summary["fill_source"] == {"order_observed": 1, "entry_resolved": 0}


def _observed(observation: OrderObservation) -> dict:
    return {"kind": EventKind.ORDER_OBSERVED, "payload": observation.model_dump(mode="json")}


def test_observed_fill_reads_the_latest_cumulative_fill():
    unfilled = _observed(_observation("ord-1", "new", "0", None, 1))
    partial = _observed(_observation("ord-1", "partially_filled", "2", "100.10", 3))
    filled = _observed(_observation("ord-1", "filled", "4", "100.25", 7))
    assert observed_fill([unfilled, partial, filled]) == (100.25, 4.0)
    # REST and stream can arrive out of order; cumulative quantity never decreases.
    assert observed_fill([unfilled, filled, partial]) == (100.25, 4.0)
    assert observed_fill([unfilled]) == (None, None)
    assert observed_fill([]) == (None, None)
    # A fill without a price is no price evidence: never inferred.
    priceless = {**filled, "payload": {**filled["payload"], "average_fill_price": None}}
    assert observed_fill([unfilled, priceless]) == (None, None)
    malformed = {**filled, "payload": {**filled["payload"], "filled_quantity": "n/a"}}
    assert observed_fill([malformed]) == (None, None)


async def test_an_unfilled_acknowledgement_is_not_fill_evidence(temp_db):
    """An order id with no filled observation and an acknowledgement without a fill: counted missing."""
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        signal_id = await _signal(session, temp_db, broker_order_id="ord-1")
        await _entry(session, temp_db, signal_id, "client-1", fill=None)
    await temp_db.workflows.observe_orders([_observation("ord-1", "new", "0", None, 1)])
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["fill_price"] is None and row["fill_source"] is None and row["fill_slip_r"] is None
    summary = summarize_execution(frame)
    assert (summary["entered"], summary["filled"], summary["missing_fill_evidence"]) == (1, 0, 1)
    assert summary["fill_source"] == {"order_observed": 0, "entry_resolved": 0}


async def test_an_acknowledged_fill_is_used_when_no_observation_filled(temp_db):
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        signal_id = await _signal(session, temp_db, broker_order_id="paper-1")
        await _entry(session, temp_db, signal_id, "client-1", fill=(100.5, 4.0))
    [row] = (await collect_execution(temp_db, _frame(signal_id))).to_dict("records")
    assert (row["fill_price"], row["fill_source"]) == (100.5, "entry_resolved")


async def test_a_repriced_card_is_followed_to_its_replacement(temp_db):
    """The journal names the original card; the trade happened on its REPRICE replacement."""
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        original = await _signal(session, temp_db, status=SignalStatus.EXPIRED)
        replacement = await _signal(
            session,
            temp_db,
            status=SignalStatus.CLOSED_WIN,
            raw_response=json.dumps({"entry_price": 100.5, "stop_loss": 99.0}),
            broker_order_id="ord-2",
        )
        await _tap(session, temp_db, original, outcome="reprice", age=900.0, r=0.4, new_signal_id=replacement)
        await _entry(session, temp_db, replacement, "client-2", fill=None)
        await _tap(session, temp_db, replacement, age=12.0, r=0.0)
    await temp_db.workflows.observe_orders([_observation("ord-2", "filled", "4", "100.75", 9)])

    frame = await collect_execution(temp_db, _frame(original))
    [row] = frame.to_dict("records")
    assert row["signal_id"] == original and row["final_signal_id"] == replacement and row["repriced"] is True
    assert row["status"] == SignalStatus.CLOSED_WIN
    assert (row["fill_price"], row["filled_quantity"], row["fill_source"]) == (100.75, 4.0, "order_observed")
    # Slippage against the replacement's own planned entry (100.5), not the original's.
    assert row["planned_entry"] == 100.5 and row["fill_slip_r"] == pytest.approx(0.25 / 1.5)
    assert row["tap_age_seconds"] == 12.0 and row["tap_r_consumed"] == 0.0
    summary = summarize_execution(frame)
    assert (summary["repriced"], summary["entered"], summary["filled"]) == (1, 1, 1)


async def test_a_reprice_cycle_terminates(temp_db):
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        first = await _signal(session, temp_db, status=SignalStatus.EXPIRED)
        second = await _signal(session, temp_db, status=SignalStatus.EXPIRED)
        await _tap(session, temp_db, first, outcome="reprice", new_signal_id=second)
        await _tap(session, temp_db, second, outcome="reprice", new_signal_id=first)
    [row] = (await collect_execution(temp_db, _frame(first))).to_dict("records")
    assert row["final_signal_id"] == second and row["repriced"] is True


def test_reprice_target_is_the_latest_applied_reprice():
    def tap(outcome, applied, new_signal_id):
        return {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": outcome, "applied": applied, "new_signal_id": new_signal_id},
        }

    assert reprice_target([tap("reprice", False, None), tap("reprice", True, 12)]) == 12
    assert reprice_target([tap("reprice", True, 12), tap("execute", True, None)]) == 12
    assert reprice_target([tap("reprice", False, None), tap("missed", True, None)]) is None
    assert reprice_target([]) is None


async def test_planned_levels_come_from_the_card_as_sent(temp_db):
    """The LLM moved the stop to 98.5; the journal row still holds the deterministic 99.0."""
    sent = json.dumps({"entry_price": 100.0, "stop_loss": 98.5, "take_profit": 102.0})
    signal_id = await _seed_alpaca_entry(temp_db, raw_response=sent)
    [row] = (await collect_execution(temp_db, _frame(signal_id, planned=100.0, stop=99.0))).to_dict("records")
    assert (row["planned_entry"], row["stop"], row["planned_source"]) == (100.0, 98.5, "raw_response")
    assert row["fill_slip_r"] == pytest.approx(0.25 / 1.5)


@pytest.mark.parametrize("raw_response", [None, "not json", json.dumps({"entry_price": 100.0}), json.dumps([1])])
async def test_planned_levels_fall_back_to_the_journal(temp_db, raw_response):
    signal_id = await _seed_alpaca_entry(temp_db, raw_response=raw_response)
    journal = _frame(signal_id, planned=100.0, stop=99.0)
    [row] = (await collect_execution(temp_db, journal)).to_dict("records")
    assert (row["planned_entry"], row["stop"], row["planned_source"]) == (100.0, 99.0, "journal")
    assert row["fill_slip_r"] == pytest.approx(0.25)
    # The applied LLM stop, when the journal recorded one, is the bracket that was sent.
    [row] = (await collect_execution(temp_db, journal.assign(llm_stop_loss=98.5))).to_dict("records")
    assert (row["stop"], row["planned_source"]) == (98.5, "journal")
    assert row["fill_slip_r"] == pytest.approx(0.25 / 1.5)


def test_fill_slip_r_is_undefined_without_a_direction():
    assert fill_slip_r("", 100.0, 99.0, 100.25) is None
    assert fill_slip_r("FLAT", 100.0, 99.0, 100.25) is None


async def test_a_missing_signal_row_is_counted(temp_db):
    journal = _frame(999).assign(direction=None)
    frame = await collect_execution(temp_db, journal)
    [row] = frame.to_dict("records")
    assert row["status"] is None and row["direction"] == "" and row["fill_slip_r"] is None
    summary = summarize_execution(frame)
    assert (summary["cards"], summary["missing_signal_rows"], summary["entered"]) == (1, 1, 0)


async def test_a_fill_without_a_defined_slip_is_not_averaged_as_nan(temp_db):
    signal_id = await _seed(temp_db, with_fill=True, with_tap=False)
    frame = await collect_execution(temp_db, _frame(signal_id).assign(direction=None))
    summary = summarize_execution(frame)
    assert summary["filled"] == 1 and summary["mean_fill_slip_r"] is None
