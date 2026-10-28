"""Paper auto-entry notices: one fill notice, and the routine notices folded into the daily digest.

An entry whose work item carries ``entry_trigger: auto`` queues ``signal/{id}/auto-filled`` when its fill
is first recorded, and skips four routine notices (accepted resolution, deadline-cause cancel intent and
unfilled result, "ended without fills"). Journal events are unchanged; tap entries, operator cancels,
refusals, UNKNOWN outcomes and exits keep every notice.
"""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from agentic_trader.broker.base import OrderRequest, OrderResult
from agentic_trader.config import load_config
from agentic_trader.constants import ExitReason, SignalStatus
from agentic_trader.execution.durable import EntryTrigger, EventKind, OrderObservation, WorkKind
from agentic_trader.execution.lifetime_policy import NATIVE_ENTRY_LIFETIME_KEY, NativeEntryLifetimePolicy
from agentic_trader.execution.lifetimes import EntryIdentity
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.lifetimes import LifetimeRepository
from agentic_trader.storage.models import SignalRecord, WorkItemRecord


@pytest.fixture
def db(tmp_path) -> SignalDatabase:
    return SignalDatabase(str(tmp_path / "notices.db"))


async def notice_keys(db) -> dict[str, dict]:
    async with db.session_factory() as session:
        rows = (await session.scalars(select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.NOTIFICATION))).all()
        return {row.dedup_key: json.loads(row.payload)["arguments"] for row in rows}


async def event_kinds(db) -> list[str]:
    return [event["kind"] for event in await db.workflows.events()]


def observation(
    *,
    status: str,
    submitted_at: datetime,
    client_order_id: str,
    order_id: str = "entry-1",
    filled: str = "0",
    symbol: str = "SPY",
) -> OrderObservation:
    return OrderObservation(
        order_id=order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side="buy",
        status=status,
        quantity="10",
        filled_quantity=filled,
        order_type="limit",
        order_class="bracket",
        submitted_at=submitted_at,
        updated_at=datetime.now(UTC),
    )


async def staged_entry(db, trigger: EntryTrigger, *, resolve: bool = True, order_id: str = "entry-1"):
    """A native-marked signal with its entry work item; accepted and EXECUTED (resting) when ``resolve``."""
    sid = await db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        quantity=10.0,
        notional_value=1000.0,
        asset_class="EQUITY",
        decision_provenance={NATIVE_ENTRY_LIFETIME_KEY: asdict(NativeEntryLifetimePolicy())},
    )
    request = OrderRequest(
        signal_id=sid,
        symbol="SPY",
        contract="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        client_order_id="client-1",
    )
    item, _ = await db.workflows.enqueue_entry(request, load_config(), trigger=trigger)
    if resolve:
        claimed = await db.workflows.claim_entry(lease_seconds=60)
        await db.update_signal_status(sid, SignalStatus.SUBMITTING)
        await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id=order_id))
    return sid, item


async def accept_resolution(db, sid, item):
    return f"entry/{item.id}/accepted"


async def observe_terminal(db, sid, item, status="canceled"):
    await db.workflows.observe_orders(
        [observation(status=status, submitted_at=datetime.now(UTC), client_order_id=item.payload["client_order_id"])]
    )
    return f"signal/{sid}/entry-terminal"


async def deadline_intent(db, sid, item):
    cancel = await begin_cancel(db, sid, item, "deadline")
    return f"entry-cancel/{cancel.id}/intent"


async def deadline_unfilled_result(db, sid, item):
    cancel = await begin_cancel(db, sid, item, "deadline")
    await resolve_cancel(db, cancel, item)
    return f"entry-cancel/{cancel.id}/accepted"


async def begin_cancel(db, sid, item, cause, *, order_id: str = "entry-1", symbol: str = "SPY"):
    client = item.payload["client_order_id"]
    submitted = datetime.now(UTC) - timedelta(hours=17)
    repo = LifetimeRepository(db.workflows)
    resting = observation(
        status="new", submitted_at=submitted, client_order_id=client, order_id=order_id, symbol=symbol
    )
    await db.workflows.observe_orders([resting])
    identity = EntryIdentity(order_id, client, symbol, "buy", "10")
    orders = [resting]
    item = await repo.begin_cancel(
        sid, identity, orders, asdict(NativeEntryLifetimePolicy()), now=datetime.now(UTC), reason=cause
    )
    assert not isinstance(item, str), item
    return item


async def resolve_cancel(db, cancel, item, *, order_id: str = "entry-1", symbol: str = "SPY"):
    repo = LifetimeRepository(db.workflows)
    canceled = observation(
        status="canceled",
        submitted_at=datetime.now(UTC) - timedelta(hours=17),
        client_order_id=item.payload["client_order_id"],
        order_id=order_id,
        symbol=symbol,
    )
    await db.workflows.observe_orders([canceled])
    assert await repo.resolve_cancel(cancel, [canceled])


@pytest.mark.parametrize("step", [accept_resolution, deadline_intent, deadline_unfilled_result])
async def test_folded_notices_skipped_for_auto_and_kept_for_tap(db, step):
    tap_sid, tap_item = await staged_entry(db, EntryTrigger.TAP, resolve=step is not accept_resolution)
    if step is accept_resolution:
        claimed = await db.workflows.claim_entry(lease_seconds=60)
        await db.update_signal_status(tap_sid, SignalStatus.SUBMITTING)
        await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id="entry-1"))
    else:
        tap_key = await step(db, tap_sid, tap_item)
    if step is accept_resolution:
        tap_key = f"entry/{tap_item.id}/accepted"
    assert tap_key in await notice_keys(db)


@pytest.mark.parametrize("step", [accept_resolution, deadline_intent, deadline_unfilled_result])
async def test_auto_entry_folds_its_routine_notices_but_journals_them(tmp_path, step):
    database = SignalDatabase(str(tmp_path / "auto.db"))
    sid, item = await staged_entry(database, EntryTrigger.AUTO, resolve=step is not accept_resolution)
    if step is accept_resolution:
        claimed = await database.workflows.claim_entry(lease_seconds=60)
        await database.update_signal_status(sid, SignalStatus.SUBMITTING)
        await database.workflows.resolve_entry(claimed, OrderResult(success=True, order_id="entry-1"))
        key = f"entry/{item.id}/accepted"
    else:
        key = await step(database, sid, item)
    assert key not in await notice_keys(database)
    kinds = await event_kinds(database)
    expected = {
        accept_resolution: EventKind.ENTRY_RESOLVED,
        deadline_intent: EventKind.ENTRY_CANCEL_REQUESTED,
        deadline_unfilled_result: EventKind.ENTRY_CANCEL_RESOLVED,
    }[step]
    assert expected in kinds


async def test_auto_fill_notice_queued_once_in_the_fill_transaction(db):
    sid, _ = await staged_entry(db, EntryTrigger.AUTO)
    now = datetime.now(UTC)

    await db.update_signal_execution(sid, "entry-1", fill_price=100.25, quantity=10, executed_at=now)
    await db.update_signal_execution(sid, "entry-1", fill_price=100.25, quantity=10, executed_at=now)

    notices = await notice_keys(db)
    fills = {key: value for key, value in notices.items() if key.endswith("/auto-filled")}
    assert fills == {
        f"signal/{sid}/auto-filled": {
            "text": f"🤖 Auto-entry #{sid} SPY filled 10 @ $100.25; stop $95.00, target $110.00."
        }
    }
    async with db.session_factory() as session:
        row = await session.scalar(
            select(WorkItemRecord).where(WorkItemRecord.dedup_key == f"signal/{sid}/auto-filled")
        )
        assert json.loads(row.payload)["kind"] == "message"


async def test_a_refused_update_queues_no_fill_notice(db):
    sid, _ = await staged_entry(db, EntryTrigger.AUTO)
    await db.update_signal_status(sid, SignalStatus.DISMISSED)  # the guarded UPDATE matches no row

    await db.update_signal_execution(sid, "entry-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC))

    assert f"signal/{sid}/auto-filled" not in await notice_keys(db)


async def test_tap_fill_has_no_fill_notice(db):
    sid, _ = await staged_entry(db, EntryTrigger.TAP)

    await db.update_signal_execution(sid, "entry-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC))

    assert f"signal/{sid}/auto-filled" not in await notice_keys(db)


async def test_operator_cancel_of_auto_entry_keeps_its_notices(db):
    sid, item = await staged_entry(db, EntryTrigger.AUTO)
    cancel = await begin_cancel(db, sid, item, "operator")
    await resolve_cancel(db, cancel, item)

    notices = await notice_keys(db)

    assert f"entry-cancel/{cancel.id}/intent" in notices
    assert f"entry-cancel/{cancel.id}/accepted" in notices


async def test_rejected_resolution_of_auto_entry_still_notifies(db):
    _sid, item = await staged_entry(db, EntryTrigger.AUTO, resolve=False)
    claimed = await db.workflows.claim_entry(lease_seconds=60)

    await db.workflows.resolve_entry(claimed, OrderResult(success=False, error_message="preflight refused"))

    notices = await notice_keys(db)
    assert f"entry/{item.id}/rejected" in notices
    assert "preflight refused" in notices[f"entry/{item.id}/rejected"]["text"]


async def test_unknown_outcome_notice_unchanged_for_auto(db):
    _sid, item = await staged_entry(db, EntryTrigger.AUTO, resolve=False)
    claimed = await db.workflows.claim_entry(lease_seconds=60)
    await db.workflows.resolve_entry(
        claimed, OrderResult(success=False, submission_uncertain=True, error_message="TimeoutError")
    )
    assert f"entry/{item.id}/unknown" in await notice_keys(db)


async def test_exit_notice_unchanged_for_auto(db):
    sid, _ = await staged_entry(db, EntryTrigger.AUTO)
    await db.update_signal_execution(sid, "entry-1", fill_price=100.0, quantity=10, executed_at=datetime.now(UTC))

    assert await db.close_position(sid, 110.0, ExitReason.TAKE_PROFIT, 100.0, SignalStatus.CLOSED_WIN)

    assert f"signal/{sid}/closed" in await notice_keys(db)


@pytest.mark.parametrize("status", ["canceled", "rejected", "expired"])
async def test_broker_ended_auto_entry_keeps_its_entry_terminal_notice(db, status):
    sid, item = await staged_entry(db, EntryTrigger.AUTO)

    key = await observe_terminal(db, sid, item, status)

    notices = await notice_keys(db)
    assert f"ended {status} without fills" in notices[key]["text"]
    assert (await db.get_signal_by_id(sid))["status"] == SignalStatus.FAILED


async def test_unknown_entry_resolved_by_recovery_keeps_its_notice_for_auto(db):
    _sid, item = await staged_entry(db, EntryTrigger.AUTO, resolve=False)
    claimed = await db.workflows.claim_entry(lease_seconds=60)
    await db.workflows.resolve_entry(
        claimed, OrderResult(success=False, submission_uncertain=True, error_message="TimeoutError")
    )

    assert await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id="entry-1"), recovery=True)

    assert f"entry/{item.id}/accepted" in await notice_keys(db)


async def test_unknown_deadline_cancel_resolved_keeps_its_result_notice_for_auto(db):
    sid, item = await staged_entry(db, EntryTrigger.AUTO)
    cancel = await begin_cancel(db, sid, item, "deadline")
    repo = LifetimeRepository(db.workflows)
    assert await repo.resolve_cancel(cancel, [], "lookup failed")  # no evidence: UNKNOWN, the halt notice
    assert f"entry-cancel/{cancel.id}/unknown" in await notice_keys(db)

    await resolve_cancel(db, cancel, item)

    assert f"entry-cancel/{cancel.id}/accepted" in await notice_keys(db)


async def test_partial_fill_cancel_notice_unchanged_for_auto(db):
    sid, item = await staged_entry(db, EntryTrigger.AUTO)
    cancel = await begin_cancel(db, sid, item, "deadline")
    partial = observation(
        status="canceled",
        submitted_at=datetime.now(UTC) - timedelta(hours=17),
        client_order_id=item.payload["client_order_id"],
        filled="2",
    )
    await db.workflows.observe_orders([partial])

    assert await LifetimeRepository(db.workflows).resolve_cancel(cancel, [partial])

    assert f"entry-cancel/{cancel.id}/unknown" in await notice_keys(db)


async def test_trailing_stop_ratchet_notice_unchanged_for_auto(db):
    sid, _ = await staged_entry(db, EntryTrigger.AUTO)
    await db.update_signal_execution(sid, "entry-1", fill_price=100.0, quantity=10, executed_at=datetime.now(UTC))

    assert await db.update_position_stop(sid, 97.0, reason="ratchet", notification={"contract": "SPY"})

    assert f"signal/{sid}/stop/97.0" in await notice_keys(db)


# --- a fold never loses a notice the digest can no longer report (M1) ---------------------------


async def queue_todays_digest(db, sid):
    """The digest of the entry's New York date is already queued: a later routine notice must be delivered."""
    signal = await db.get_signal_by_id(sid)
    et_date = datetime.fromisoformat(signal["timestamp"]).replace(tzinfo=UTC).astimezone(ET_TZ).date()
    assert await db.workflows.queue_auto_entry_digest(et_date, "digest")


async def created_yesterday(db, sid):
    """An entry from an earlier New York date: its digest was due long ago."""
    async with db.session_factory() as session, session.begin():
        await session.execute(
            update(SignalRecord)
            .where(SignalRecord.id == sid)
            .values(timestamp=datetime.now(UTC).astimezone(ET_TZ) - timedelta(days=1))
        )


async def staged_accepting(db, sid, item):
    claimed = await db.workflows.claim_entry(lease_seconds=60)
    await db.update_signal_status(sid, SignalStatus.SUBMITTING)
    await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id="entry-1"))
    return f"entry/{item.id}/accepted"


@pytest.mark.parametrize("after", [queue_todays_digest, created_yesterday], ids=["digest_queued", "earlier_date"])
@pytest.mark.parametrize("step", [staged_accepting, deadline_intent, deadline_unfilled_result])
async def test_a_routine_auto_notice_the_digest_can_no_longer_report_is_delivered(db, step, after):
    sid, item = await staged_entry(db, EntryTrigger.AUTO, resolve=step is not staged_accepting)
    await after(db, sid)

    key = await step(db, sid, item)

    assert key in await notice_keys(db)


async def test_deadline_cancel_result_before_the_digest_is_folded_and_after_it_delivered(db):
    first_sid, first_item = await staged_entry(db, EntryTrigger.AUTO)
    folded = await deadline_unfilled_result(db, first_sid, first_item)
    assert folded not in await notice_keys(db)

    await queue_todays_digest(db, first_sid)
    second_sid, second_item = await staged_entry(db, EntryTrigger.AUTO, order_id="entry-2")
    cancel = await begin_cancel(db, second_sid, second_item, "deadline", order_id="entry-2")
    assert f"entry-cancel/{cancel.id}/intent" in await notice_keys(db)
    await resolve_cancel(db, cancel, second_item, order_id="entry-2")

    assert f"entry-cancel/{cancel.id}/accepted" in await notice_keys(db)
    assert folded not in await notice_keys(db)


# --- display-only trigger reads never raise inside a fact transaction (M3) ----------------------


async def corrupt_trigger(db, sid):
    async with db.session_factory() as session, session.begin():
        row = await session.scalar(
            select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.ENTRY, WorkItemRecord.dedup_key == str(sid))
        )
        row.payload = json.dumps({**json.loads(row.payload), "entry_trigger": "robot"})


async def fill(db, sid, item):
    await db.update_signal_execution(sid, "entry-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC))


@pytest.mark.parametrize("step", [staged_accepting, deadline_intent, deadline_unfilled_result, fill])
async def test_an_unknown_stored_trigger_reads_as_tap_with_a_warning(db, step, caplog):
    sid, item = await staged_entry(db, EntryTrigger.AUTO, resolve=step is not staged_accepting)
    await corrupt_trigger(db, sid)

    with caplog.at_level(logging.WARNING, logger="agentic_trader.storage.workflow"):
        key = await step(db, sid, item)

    notices = await notice_keys(db)
    if key is not None:
        assert key in notices  # never folded
    assert f"signal/{sid}/auto-filled" not in notices  # no fill notice
    if step is fill:
        assert (await db.get_signal_by_id(sid))["executed_at"] is not None  # the fact still committed
    warnings = [r for r in caplog.records if r.__dict__.get("event") == "entry_trigger_unreadable"]
    assert warnings and all(r.__dict__.get("reason") == "ValueError" for r in warnings)
    assert all("robot" not in r.getMessage() for r in warnings)


async def test_entry_trigger_of_stays_strict(db):
    sid, _ = await staged_entry(db, EntryTrigger.AUTO)
    await corrupt_trigger(db, sid)

    with pytest.raises(ValueError):
        await db.workflows.entry_trigger(sid)
    async with db.session_factory() as session:
        assert await db.workflows.display_entry_trigger(session, sid) == EntryTrigger.TAP
