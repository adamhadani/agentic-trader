"""``SignalDatabase.expire_stale_signals``: the untapped-card sweep and its CARD_EXPIRED notice.

Mirrors ``tests/storage/test_signal_freshness.py`` (the tap-path ``expire_signal``/
``replace_signal`` tests) but for the session-close sweep: a PENDING card nobody tapped
must still leave PENDING once its session ends, in the same locked transaction as its
outbox notification.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import NotificationKind, WorkKind
from agentic_trader.storage.models import SignalRecord


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


async def _issue_card(
    db,
    *,
    issued_at: datetime,
    decision_provenance: dict | None = None,
    contract: str = "SPY",
    status: str = SignalStatus.PENDING,
) -> int:
    signal_id = await db.record_signal(**_signal_kwargs(contract=contract, decision_provenance=decision_provenance))
    async with db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == signal_id).values(timestamp=issued_at))
    if status != SignalStatus.PENDING:
        await db.update_signal_status(signal_id, status)
    return signal_id


PAST_NY_DATE = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)  # 10:00 ET Sept 23
NEXT_DAY_NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)  # 11:00 ET Sept 24


@pytest.mark.asyncio
async def test_sweep_expires_stale_legacy_card_and_leaves_a_fresh_one_pending(temp_db):
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)
    fresh_id = await _issue_card(temp_db, issued_at=NEXT_DAY_NOW)

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert expired == [stale_id]
    assert (await temp_db.get_signal_by_id(stale_id))["status"] == SignalStatus.EXPIRED
    assert (await temp_db.get_signal_by_id(fresh_id))["status"] == SignalStatus.PENDING


@pytest.mark.asyncio
async def test_sweep_expires_once_valid_until_is_reached_even_on_the_same_ny_date(temp_db):
    now = datetime(2026, 9, 23, 21, 0, tzinfo=UTC)  # 17:00 ET, same NY date as issue
    valid_until = "2026-09-23T20:59:00+00:00"  # 16:59 ET, already past
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, decision_provenance={"valid_until": valid_until})

    expired = await temp_db.expire_stale_signals(now)

    assert expired == [stale_id]


@pytest.mark.asyncio
async def test_sweep_leaves_a_card_pending_before_its_own_valid_until_even_on_a_later_ny_date(temp_db):
    valid_until = (NEXT_DAY_NOW.replace(hour=23)).isoformat()  # still ahead of `now` below
    live_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, decision_provenance={"valid_until": valid_until})

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert expired == []
    assert (await temp_db.get_signal_by_id(live_id))["status"] == SignalStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [SignalStatus.EXECUTED, SignalStatus.FAILED, SignalStatus.DISMISSED, SignalStatus.EXPIRED, SignalStatus.SUBMITTING],
)
async def test_sweep_only_touches_pending_rows(temp_db, status):
    signal_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, status=status)

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert expired == []
    assert (await temp_db.get_signal_by_id(signal_id))["status"] == status


@pytest.mark.asyncio
async def test_sweep_ignores_quarantined_rows_out_of_scope(temp_db):
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)
    assert await temp_db.quarantine_signal(stale_id, "test", {"contract": "SPY", "strategy": "s"})

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert expired == []
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications == []


@pytest.mark.asyncio
async def test_sweep_enqueues_exactly_one_card_expired_notification_with_correct_reevaluable(temp_db):
    configured_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, contract="SPY")
    dynamic_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, contract="DYNAMIC_XYZ")

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW, configured_contracts=frozenset({"SPY"}))

    assert set(expired) == {configured_id, dynamic_id}
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert len(notifications) == 2
    by_signal = {n.payload["arguments"]["signal_id"]: n for n in notifications}
    assert by_signal[configured_id].payload["kind"] == NotificationKind.CARD_EXPIRED
    assert by_signal[configured_id].payload["arguments"] == {
        "signal_id": configured_id,
        "contract": "SPY",
        "reevaluable": True,
    }
    assert by_signal[dynamic_id].payload["arguments"] == {
        "signal_id": dynamic_id,
        "contract": "DYNAMIC_XYZ",
        "reevaluable": False,
    }


@pytest.mark.asyncio
async def test_sweep_defaults_to_no_reevaluable_contracts_when_none_are_passed(temp_db):
    signal_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE, contract="SPY")

    await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications[0].payload["arguments"]["signal_id"] == signal_id
    assert notifications[0].payload["arguments"]["reevaluable"] is False


@pytest.mark.asyncio
async def test_sweep_is_idempotent(temp_db):
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)

    first = await temp_db.expire_stale_signals(NEXT_DAY_NOW)
    second = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert first == [stale_id]
    assert second == []
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_sweep_rolls_back_the_status_change_when_add_notification_fails(temp_db, monkeypatch):
    # The status flip and its outbox row share one transaction (CLAUDE.md contract 6): a
    # refactor that moved them into separate transactions would silently leave a card
    # EXPIRED with no notice, or vice versa. A raising `add_notification` must roll back
    # everything, not just skip the notice.
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)
    monkeypatch.setattr(temp_db.workflows, "add_notification", AsyncMock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert (await temp_db.get_signal_by_id(stale_id))["status"] == SignalStatus.PENDING
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []


@pytest.mark.asyncio
async def test_expire_signal_tap_path_enqueues_no_card_expired_notification(temp_db):
    """Requirement 6: the tap paths edit their own markup and must never also enqueue the
    sweep's CARD_EXPIRED notice, even when they expire a card that is independently stale."""
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)

    assert await temp_db.expire_signal(stale_id) is True

    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []


@pytest.mark.asyncio
async def test_replace_signal_tap_path_enqueues_only_its_own_signal_notification(temp_db):
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)

    new_id = await temp_db.replace_signal(
        stale_id,
        notification={"contract": "SPY"},
        decision_provenance={"reprices": stale_id},
        **_signal_kwargs(entry_price=101.0),
    )

    assert new_id is not None
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert [n.payload["kind"] for n in notifications] == [NotificationKind.SIGNAL]


@pytest.mark.asyncio
async def test_sweep_ignores_rows_outside_this_environment_scope(temp_db):
    # `temp_db` (no AppConfig) has execution_mode == UNKNOWN_EXECUTION_MODE, which makes
    # `_scope()` match every execution_mode by design (see `SignalDatabase._scope`), so
    # environment is the one scope dimension a plain `temp_db` can exercise; execution_mode
    # scoping is exercised end-to-end wherever an AppConfig-backed db is already covered
    # (e.g. the duplicate-rule and freshness suites).
    stale_id = await _issue_card(temp_db, issued_at=PAST_NY_DATE)
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == stale_id).values(environment="other_env"))

    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW)

    assert expired == []
    async with temp_db.session_factory() as session:
        rec = await session.get(SignalRecord, stale_id)
    assert rec.status == SignalStatus.PENDING
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []
