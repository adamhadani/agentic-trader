"""Lifetime commands reuse the trading scope lock, journal and transactional outbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from agentic_trader.constants import ACTIVE_CLOSE_STATUSES, SignalStatus
from agentic_trader.execution.durable import (
    EventKind,
    NotificationKind,
    OrderObservation,
    WorkItem,
    WorkKind,
    WorkStatus,
)
from agentic_trader.execution.lifetime_policy import TradeLifetimePolicy
from agentic_trader.execution.lifetimes import UNFILLED_TERMINAL, EntryIdentity, LifetimeAction, assess_lifetime
from agentic_trader.storage.models import (
    CloseRequestRecord,
    OrderProjectionRecord,
    SignalRecord,
    WorkItemRecord,
)
from agentic_trader.storage.workflow import WorkflowStore, encode, work_item


class LifetimeRepository:
    """An application repository over existing workflow tables, with no second queue."""

    def __init__(self, store: WorkflowStore):
        self.store = store
        self.db = store.db

    async def entry(self, signal_id: int) -> WorkItem | None:
        async with self.db.session_factory() as session:
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.scope == self.store.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key == str(signal_id),
                    WorkItemRecord.status == WorkStatus.ACCEPTED,
                )
            )
            return work_item(row) if row else None

    async def begin_cancel(
        self,
        signal_id: int,
        identity: EntryIdentity,
        orders: list[OrderObservation],
        lifetime: dict[str, Any],
        *,
        now: datetime,
    ) -> WorkItem | None:
        async with self.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            existing = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.scope == self.store.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY_CANCEL,
                    WorkItemRecord.dedup_key == str(signal_id),
                )
            )
            if existing:
                return None
            signal = await session.scalar(select(SignalRecord).where(*self.db._scope(), SignalRecord.id == signal_id))
            if (
                not signal
                or signal.status != SignalStatus.EXECUTED
                or signal.executed_at is not None
                or signal.broker_order_id != identity.order_id
                or not signal.alpha_policy
                or json.loads(signal.alpha_policy).get("lifetime") != lifetime
            ):
                return None
            entry = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.scope == self.store.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key == str(signal_id),
                    WorkItemRecord.status == WorkStatus.ACCEPTED,
                )
            )
            if (
                not entry
                or json.loads(entry.payload)["client_order_id"] != identity.client_order_id
                or json.loads(entry.result).get("order_id") != identity.order_id
            ):
                raise ValueError("Lifetime cancellation lacks exact durable entry ownership")
            projection = await session.get(OrderProjectionRecord, (self.store.scope, identity.order_id))
            if (
                projection is None
                or assess_lifetime(
                    TradeLifetimePolicy(**lifetime),
                    identity,
                    OrderObservation.model_validate_json(projection.payload),
                    now,
                ).action
                != LifetimeAction.CANCEL_ENTRY
            ):
                return None
            for observed in orders:
                latest = await session.get(OrderProjectionRecord, (self.store.scope, observed.order_id))
                if latest is None:
                    return None
                current = OrderObservation.model_validate_json(latest.payload)
                if (
                    Decimal(current.filled_quantity) != 0
                    or current.replaces
                    or current.replaced_by
                    or current.symbol != identity.symbol
                    or Decimal(current.quantity) != Decimal(identity.quantity)
                ):
                    raise ValueError("Bracket evidence changed before cancellation claim")
            closing = await session.scalar(
                select(CloseRequestRecord.id).where(
                    CloseRequestRecord.environment == self.db.environment,
                    CloseRequestRecord.execution_mode == self.db.execution_mode,
                    CloseRequestRecord.symbol == identity.symbol,
                    CloseRequestRecord.status.in_(ACTIVE_CLOSE_STATUSES),
                )
            )
            if closing:
                return None
            now = datetime.now(UTC)
            payload = {
                "signal_id": signal_id,
                "identity": identity.__dict__,
                "lifetime": lifetime,
                "order_ids": [o.order_id for o in orders],
                "before": [o.model_dump(mode="json") for o in orders],
            }
            row = WorkItemRecord(
                id=f"cancel-{uuid4().hex}",
                scope=self.store.scope,
                dedup_key=str(signal_id),
                kind=WorkKind.ENTRY_CANCEL,
                status=WorkStatus.SUBMITTING,
                payload=encode(payload),
                result="{}",
                attempts=1,
                created_at=now,
                available_at=now,
            )
            event = await self.store.append(
                session, stream=f"entry-cancel/{row.id}", kind=EventKind.ENTRY_CANCEL_REQUESTED, payload=payload
            )
            row.sequence = event.id
            session.add(row)
            await self.store.add_notification(
                session,
                f"entry-cancel/{row.id}/intent",
                NotificationKind.MESSAGE,
                {
                    "text": f"Entry #{signal_id} reached its resting deadline. Cancellation intent recorded for {identity.symbol}; awaiting broker confirmation."
                },
            )
            await session.flush()
            return work_item(row)

    async def resolve_cancel(
        self,
        item: WorkItem,
        orders: list[OrderObservation],
        reason: str | None = None,
        *,
        transport_error: str | None = None,
        allow_pending: bool = False,
    ) -> bool:
        identity = EntryIdentity(**item.payload["identity"])
        async with self.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            effective = []
            for order_id in set(item.payload["order_ids"]) | {o.order_id for o in orders}:
                projection = await session.get(OrderProjectionRecord, (self.store.scope, order_id))
                if projection:
                    effective.append(OrderObservation.model_validate_json(projection.payload))
            effective.sort(key=lambda o: o.order_id)
            by_id = {o.order_id: o for o in effective}
            parent = by_id.get(identity.order_id)
            complete = (
                parent is not None
                and identity.matches(parent)
                and set(item.payload["order_ids"]) <= by_id.keys()
                and all(
                    o.status in UNFILLED_TERMINAL
                    and Decimal(o.filled_quantity) == 0
                    and not o.replaces
                    and not o.replaced_by
                    for o in effective
                )
            )
            if not complete and allow_pending:
                return False
            status = WorkStatus.ACCEPTED if complete else WorkStatus.UNKNOWN
            reason = (
                "confirmed_unfilled_cancellation" if complete else (reason or "unconfirmed_cancellation_or_fill_race")
            )
            payload: dict[str, Any] = {"reason": reason, "orders": [o.model_dump(mode="json") for o in effective]}
            row = await session.get(WorkItemRecord, item.id)
            payload["transport_error"] = transport_error or (
                json.loads(row.result).get("transport_error") if row else None
            )
            if (
                not row
                or row.scope != self.store.scope
                or row.kind != WorkKind.ENTRY_CANCEL
                or (row.status == WorkStatus.ACCEPTED and (not complete or not transport_error))
                or (row.status == status and json.loads(row.result) == payload)
            ):
                return True
            row.status, row.result = status, encode(payload)
            await self.store.append(
                session,
                stream=f"entry-cancel/{row.id}",
                kind=EventKind.ENTRY_CANCEL_RESOLVED,
                payload={"status": status, **payload},
            )
            if complete:
                signal = await session.scalar(
                    select(SignalRecord).where(
                        *self.db._scope(),
                        SignalRecord.id == item.payload["signal_id"],
                        SignalRecord.status == SignalStatus.EXECUTED,
                        SignalRecord.executed_at.is_(None),
                        SignalRecord.broker_order_id == identity.order_id,
                    )
                )
                if signal:
                    signal.status = SignalStatus.FAILED
            if not complete:
                await self.store.halt(
                    session, f"Unconfirmed entry cancellation {row.id}; inspect fills/protection before resuming"
                )
            await self.store.add_notification(
                session,
                f"entry-cancel/{row.id}/{status}",
                NotificationKind.MESSAGE,
                {
                    "text": f"Entry #{item.payload['signal_id']} cancellation: {reason}. "
                    + (
                        "Broker confirms no fills."
                        if complete
                        else "New risk halted. Inspect exact broker fills and protective orders; no automatic retry."
                    )
                },
            )

            return True

    async def review(self, signal_id: int, reason: str, evidence: dict[str, Any]) -> None:
        async with self.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            await self.store.halt(session, f"Lifetime review for signal #{signal_id}: {reason}")
            key = f"lifetime-review/{signal_id}/{reason}"
            await self.store.append(
                session,
                stream=f"signal/{signal_id}/lifetime",
                kind=EventKind.LIFETIME_REVIEW,
                payload={"reason": reason, "evidence": evidence},
                key=key,
            )
            await self.store.add_notification(
                session,
                key,
                NotificationKind.MESSAGE,
                {
                    "text": f"Lifetime review required for signal #{signal_id}: {reason}. New risk halted; inspect broker quantity/protection."
                },
            )
