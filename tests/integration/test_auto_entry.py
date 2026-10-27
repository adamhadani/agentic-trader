"""A paper auto-entry attempt on PostgreSQL: its journal event and its follow-up share one transaction."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from agentic_trader.broker.base import OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.auto_entry import AUTO_ENTRY_KEY, AUTO_ENTRY_MARKER, AutoEntryAttempt
from agentic_trader.execution.durable import EntryTrigger, EventKind, NotificationKind, WorkKind, WorkStatus
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord, WorkItemRecord
from agentic_trader.storage.workflow import encode


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


def _paper() -> AppConfig:
    return AppConfig(execution_mode="alpaca", alpaca_paper=True)


async def _marked_card(db: SignalDatabase) -> int:
    return await db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=120.0,
        risk_dollars=50.0,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        decision_provenance={"rank": 1, AUTO_ENTRY_KEY: dict(AUTO_ENTRY_MARKER)},
        notification={"eval_res": {}, "strategy": "TREND_PULLBACK", "auto_entry": "placing"},
    )


def _attempt(signal_id: int, outcome: str, reason: str) -> AutoEntryAttempt:
    return AutoEntryAttempt(
        signal_id=signal_id,
        scan_id=None,
        outcome=outcome,
        reason=reason,
        work_status=None,
        signal_status="PENDING",
        attempted_at=datetime.now(UTC),
    )


async def _rows(db: SignalDatabase, signal_id: int) -> tuple[list[str], dict[str, dict]]:
    async with db.session_factory() as session:
        events = (
            await session.scalars(
                select(DomainEventRecord.event_key).where(DomainEventRecord.kind == EventKind.AUTO_ENTRY_ATTEMPTED)
            )
        ).all()
        notices = (
            await session.scalars(
                select(WorkItemRecord).where(
                    WorkItemRecord.kind == WorkKind.NOTIFICATION,
                    WorkItemRecord.dedup_key.like(f"signal/{signal_id}/auto-%"),
                )
            )
        ).all()
        return list(events), {row.dedup_key: json.loads(row.payload) for row in notices}


async def test_attempt_event_and_notification_share_a_transaction(postgres_test_db, monkeypatch):
    db = SignalDatabase(db_url=postgres_test_db, config=_paper())
    try:
        await db.init_db()
        sid = await _marked_card(db)
        add_notification = db.workflows.add_notification

        async def fail_after_the_event(session, key, kind, payload):
            raise RuntimeError("outbox write failed")

        monkeypatch.setattr(db.workflows, "add_notification", fail_after_the_event)
        recard = {"auto_entry": "refused", "auto_refusal": "cap reached"}
        with pytest.raises(RuntimeError):
            await db.workflows.record_auto_entry_attempt(
                _attempt(sid, "refused", "cap reached"), recard=recard, entered=False
            )
        assert await _rows(db, sid) == ([], {})  # the event rolled back with its notification

        monkeypatch.setattr(db.workflows, "add_notification", add_notification)
        await db.workflows.record_auto_entry_attempt(
            _attempt(sid, "refused", "cap reached"), recard=recard, entered=False
        )
        events, notices = await _rows(db, sid)
        assert events == [f"auto-entry/{sid}"]
        assert notices[f"signal/{sid}/auto-refused"] == {
            "kind": NotificationKind.SIGNAL,
            "arguments": {
                "eval_res": {},
                "strategy": "TREND_PULLBACK",
                "auto_entry": "refused",
                "auto_refusal": "cap reached",
                "signal_id": sid,
            },
        }
    finally:
        await db.engine.dispose()


async def test_concurrent_attempts_journal_one_event_and_one_notification(postgres_test_db):
    """The event key is idempotent across two daemons' stores: one event, one button swap."""
    first = SignalDatabase(db_url=postgres_test_db, config=_paper())
    second = SignalDatabase(db_url=postgres_test_db, config=_paper())
    try:
        await first.init_db()
        sid = await _marked_card(first)
        await asyncio.gather(
            *[
                (first if i % 2 else second).workflows.record_auto_entry_attempt(
                    _attempt(sid, "entered", "queued"), recard=None, entered=True
                )
                for i in range(6)
            ]
        )
        events, notices = await _rows(first, sid)
        assert events == [f"auto-entry/{sid}"]
        assert notices == {
            f"signal/{sid}/auto-entered": {
                "kind": NotificationKind.CARD_AUTO_ENTERED,
                "arguments": {"signal_id": sid, "contract": "SPY"},
            }
        }
    finally:
        await first.engine.dispose()
        await second.engine.dispose()


async def test_fill_notice_and_signal_update_share_a_transaction_and_folded_notices_are_absent(postgres_test_db):
    """The ``auto-filled`` notice rolls back with the fill; an accepted auto entry queues no resolution notice."""
    db = SignalDatabase(db_url=postgres_test_db, config=_paper())
    try:
        await db.init_db()
        sid = await _marked_card(db)
        request = OrderRequest(
            signal_id=sid,
            symbol="SPY",
            contract="SPY",
            asset_class="EQUITY",
            direction="LONG",
            quantity=10,
            entry_price=100,
            stop_loss=95,
            take_profit=120,
        )
        # Alpaca admission needs a fresh account journal, so the queued auto entry is planted directly.
        now = datetime.now(UTC)
        async with db.session_factory() as session, session.begin():
            await db.workflows.lock(session)
            event = await db.workflows.append(
                session, stream="fixture/queued-entry", kind=EventKind.ENTRY_QUEUED, payload={"fixture": True}
            )
            item = WorkItemRecord(
                id="client-auto-1",
                scope=db.workflows.scope,
                kind=WorkKind.ENTRY,
                status=WorkStatus.QUEUED,
                dedup_key=str(sid),
                sequence=event.id,
                payload=encode({**request.model_dump(mode="json"), "entry_trigger": str(EntryTrigger.AUTO)}),
                result=encode({}),
                attempts=0,
                created_at=now,
                available_at=now,
            )
            session.add(item)
        claimed = await db.workflows.claim_entry(lease_seconds=60)
        await db.update_signal_status(sid, SignalStatus.SUBMITTING)
        await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id="order-1"))

        add_notification = db.workflows.add_notification

        async def fail(session, key, kind, payload):
            raise RuntimeError("outbox write failed")

        db.workflows.add_notification = fail
        with pytest.raises(RuntimeError):
            await db.update_signal_execution(
                sid, "order-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC)
            )
        assert (await db.get_signal_by_id(sid))["executed_at"] is None  # the update rolled back with the notice

        db.workflows.add_notification = add_notification
        await db.update_signal_execution(sid, "order-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC))
        assert (await db.get_signal_by_id(sid))["executed_at"] is not None
        async with db.session_factory() as session:
            keys = set(
                await session.scalars(
                    select(WorkItemRecord.dedup_key).where(WorkItemRecord.kind == WorkKind.NOTIFICATION)
                )
            )
        assert f"signal/{sid}/auto-filled" in keys
        assert "entry/client-auto-1/accepted" not in keys
    finally:
        await db.engine.dispose()
