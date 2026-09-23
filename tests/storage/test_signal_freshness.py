from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import WorkKind


def _signal_kwargs(**overrides):
    kwargs = {
        "contract": "SPY",
        "strategy": "s",
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "risk_dollars": 2.0,
        "asset_class": "EQUITY",
        "quantity": 1,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_expire_signal_transitions_pending_to_expired(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())

    assert await temp_db.expire_signal(signal_id) is True

    rec = await temp_db.get_signal_by_id(signal_id)
    assert rec["status"] == SignalStatus.EXPIRED


@pytest.mark.asyncio
async def test_expire_signal_is_conditional_on_pending_status(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_signal_status(signal_id, SignalStatus.EXECUTED)

    assert await temp_db.expire_signal(signal_id) is False

    rec = await temp_db.get_signal_by_id(signal_id)
    assert rec["status"] == SignalStatus.EXECUTED


@pytest.mark.asyncio
async def test_replace_signal_commits_expiry_new_row_and_one_outbox_notification(temp_db):
    old_id = await temp_db.record_signal(**_signal_kwargs())

    new_id = await temp_db.replace_signal(
        old_id,
        notification={"contract": "SPY", "header": "updated from #1"},
        decision_provenance={"reprices": old_id},
        **_signal_kwargs(entry_price=101.0, quantity=1),
    )

    assert new_id is not None
    assert new_id != old_id

    old_rec = await temp_db.get_signal_by_id(old_id)
    assert old_rec["status"] == SignalStatus.EXPIRED

    new_rec = await temp_db.get_signal_by_id(new_id)
    assert new_rec["status"] == SignalStatus.PENDING
    assert new_rec["entry_price"] == 101.0
    assert new_rec["decision_provenance"] == {"reprices": old_id}

    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert len(notifications) == 1
    assert notifications[0].payload["arguments"]["signal_id"] == new_id


@pytest.mark.asyncio
async def test_replace_signal_returns_none_and_writes_nothing_when_old_signal_not_pending(temp_db):
    old_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_signal_status(old_id, SignalStatus.EXECUTED)

    result = await temp_db.replace_signal(
        old_id,
        notification={"contract": "SPY"},
        decision_provenance={"reprices": old_id},
        **_signal_kwargs(entry_price=101.0),
    )

    assert result is None

    old_rec = await temp_db.get_signal_by_id(old_id)
    assert old_rec["status"] == SignalStatus.EXECUTED

    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications == []
    all_signals = await temp_db.signals_since(datetime.now(UTC) - timedelta(minutes=5))
    assert len(all_signals) == 1


@pytest.mark.asyncio
async def test_replace_signal_rolls_back_on_insert_error_leaving_old_signal_pending(temp_db):
    old_id = await temp_db.record_signal(**_signal_kwargs())

    with pytest.raises((TypeError, ValueError)):
        await temp_db.replace_signal(
            old_id,
            notification={"contract": "SPY"},
            decision_provenance={"reprices": old_id},
            **_signal_kwargs(entry_price="not-a-number"),
        )

    old_rec = await temp_db.get_signal_by_id(old_id)
    assert old_rec["status"] == SignalStatus.PENDING

    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications == []
    all_signals = await temp_db.signals_since(datetime.now(UTC) - timedelta(minutes=5))
    assert len(all_signals) == 1


@pytest.mark.asyncio
async def test_signals_since_excludes_replacement_but_includes_expired_original(temp_db):
    old_id = await temp_db.record_signal(**_signal_kwargs(notification={"contract": "SPY"}))
    await temp_db.record_signal(**_signal_kwargs(contract="QQQ", notification={"contract": "QQQ"}))

    new_id = await temp_db.replace_signal(
        old_id,
        notification={"contract": "SPY", "header": "updated from #1"},
        decision_provenance={"reprices": old_id},
        **_signal_kwargs(entry_price=101.0),
    )

    rows = await temp_db.signals_since(datetime.now(UTC) - timedelta(minutes=5))
    contracts = [r["contract"] for r in rows]
    # Both the EXPIRED original and the untouched QQQ signal count toward budget;
    # the replacement (decision_provenance.reprices is non-null) is excluded.
    assert contracts.count("SPY") == 1
    assert contracts.count("QQQ") == 1
    ids_present = {r["contract"] for r in rows}
    assert "SPY" in ids_present and "QQQ" in ids_present
    assert len(rows) == 2

    old_rec = await temp_db.get_signal_by_id(old_id)
    assert old_rec["status"] == SignalStatus.EXPIRED
    new_rec = await temp_db.get_signal_by_id(new_id)
    assert new_rec["status"] == SignalStatus.PENDING
