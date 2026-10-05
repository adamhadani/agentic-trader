"""Fill slippage and tap age per sent card, from the entry workflow's own journal evidence."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.execution.durable import EventKind, WorkKind, WorkStatus
from agentic_trader.research.setups.execution_evidence import (
    collect_execution,
    entry_fill,
    execute_tap,
    fill_slip_r,
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


async def _seed(temp_db, *, with_fill: bool, with_tap: bool) -> int:
    async with temp_db.session_factory() as session, session.begin():
        signal = SignalRecord(
            timestamp=datetime(2026, 3, 2, 15, 0, tzinfo=UTC),
            contract="AAPL",
            strategy="BREAKOUT",
            direction="LONG",
            entry_price=100.25,
            stop_loss=99.0,
            take_profit=102.0,
            risk_dollars=5.0,
            status="EXECUTED",
            environment=temp_db.environment,
            execution_mode=temp_db.execution_mode,
        )
        session.add(signal)
        await session.flush()
        item = WorkItemRecord(
            id="client-1",
            scope=temp_db.workflows.scope,
            dedup_key=str(signal.id),
            kind=WorkKind.ENTRY,
            status=WorkStatus.ACCEPTED,
            payload=json.dumps({"signal_id": signal.id}),
            result="{}",
            available_at=datetime(2026, 3, 2, 15, 1, tzinfo=UTC),
            created_at=datetime(2026, 3, 2, 15, 1, tzinfo=UTC),
        )
        session.add(item)
        await temp_db.workflows.lock(session)
        if with_fill:
            await temp_db.workflows.append(
                session,
                stream="entry/client-1",
                kind=EventKind.ENTRY_RESOLVED,
                payload={
                    "status": "accepted",
                    "result": {"fill_price": 100.25, "filled_quantity": 4.0},
                    "recovery": None,
                },
            )
        if with_tap:
            await temp_db.workflows.append(
                session,
                stream=f"card/{signal.id}",
                kind=EventKind.CARD_TAP_ASSESSED,
                payload={
                    "signal_id": signal.id,
                    "outcome": "execute",
                    "applied": True,
                    "age_seconds": 75.5,
                    "r_consumed": 0.1,
                },
                key=f"card_tap_assessed/{signal.id}/t1",
            )
        return signal.id


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
    assert row["fill_slip_r"] == pytest.approx(0.25)
    assert row["tap_age_seconds"] == 75.5 and row["tap_r_consumed"] == 0.1
    summary = summarize_execution(frame)
    assert summary == {
        "cards": 1,
        "executed": 1,
        "with_fill_evidence": 1,
        "missing_fill_evidence": 0,
        "mean_fill_slip_r": pytest.approx(0.25),
        "mean_tap_age_seconds": 75.5,
    }


async def test_missing_fill_evidence_is_counted_not_zeroed(temp_db):
    signal_id = await _seed(temp_db, with_fill=False, with_tap=False)
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["fill_price"] is None and row["fill_slip_r"] is None and row["tap_age_seconds"] is None
    summary = summarize_execution(frame)
    assert summary["executed"] == 1 and summary["with_fill_evidence"] == 0 and summary["missing_fill_evidence"] == 1
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
        "executed": 0,
        "with_fill_evidence": 0,
        "missing_fill_evidence": 0,
        "mean_fill_slip_r": None,
        "mean_tap_age_seconds": None,
    }
