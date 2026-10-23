"""Lifetime commands reuse the trading scope lock, journal and transactional outbox."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
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
from agentic_trader.execution.lifetime_policy import entry_lifetime_of
from agentic_trader.execution.lifetimes import (
    FILLED_BEFORE_CANCEL,
    INCOMPLETE_OR_CHANGED_GROUP,
    UNFILLED_TERMINAL,
    CancelCause,
    EntryIdentity,
    LifetimeAction,
    assess_lifetime,
    filled_before_cancel,
    resting_entry_refusal,
)
from agentic_trader.storage.models import (
    CloseRequestRecord,
    OrderProjectionRecord,
    SignalRecord,
    WorkItemRecord,
)
from agentic_trader.storage.workflow import WorkflowStore, encode, work_item


if TYPE_CHECKING:
    from agentic_trader.market.session import MarketCalendarDay


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

    async def order(self, order_id: str) -> OrderObservation | None:
        """The latest journaled observation of one order (its projection), or None."""
        async with self.db.session_factory() as session:
            row = await session.get(OrderProjectionRecord, (self.store.scope, order_id))
            return OrderObservation.model_validate_json(row.payload) if row else None

    async def cancel_item(self, signal_id: int) -> WorkItem | None:
        """The signal's one entry_cancel work item, in any status (``dedup_key = signal_id``)."""
        async with self.db.session_factory() as session:
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.scope == self.store.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY_CANCEL,
                    WorkItemRecord.dedup_key == str(signal_id),
                )
            )
            return work_item(row) if row else None

    async def begin_cancel(
        self,
        signal_id: int,
        identity: EntryIdentity,
        orders: list[OrderObservation],
        lifetime: dict[str, Any] | None,
        *,
        now: datetime,
        reason: CancelCause = "deadline",
        sessions: Sequence[MarketCalendarDay] | None = None,
    ) -> WorkItem | str:
        """Claim the one entry_cancel for a signal, or return the refusal token.

        ``reason="operator"`` skips only the deadline assessment and the policy-equality fence
        (the operator may cancel an untimed or malformed-marker entry) and reports a changed
        group as a refusal; the deadline path still raises for review. ``sessions`` are the
        calendar days the caller assessed the deadline with (a native entry's submission date).
        """
        operator = reason == "operator"
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
                return "cancel_in_progress"
            signal = await session.scalar(select(SignalRecord).where(*self.db._scope(), SignalRecord.id == signal_id))
            if (
                not signal
                or signal.status != SignalStatus.EXECUTED
                or signal.executed_at is not None
                or signal.broker_order_id != identity.order_id
            ):
                return "not_resting"
            stored = None
            if not operator:
                stored = entry_lifetime_of(
                    {"alpha_policy": signal.alpha_policy, "decision_provenance": signal.decision_provenance}
                )
                if stored is None or asdict(stored) != lifetime:
                    return "lifetime_mismatch"
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
                if operator:
                    return "no_durable_entry"
                raise ValueError("Lifetime cancellation lacks exact durable entry ownership")
            projection = await session.get(OrderProjectionRecord, (self.store.scope, identity.order_id))
            if projection is None:
                return INCOMPLETE_OR_CHANGED_GROUP
            parent = OrderObservation.model_validate_json(projection.payload)
            if operator:
                if refusal := resting_entry_refusal(identity, parent):
                    return refusal
            elif (
                stored is None
                or assess_lifetime(stored, identity, parent, now, sessions).action != LifetimeAction.CANCEL_ENTRY
            ):
                return "not_due"
            for observed in orders:
                latest = await session.get(OrderProjectionRecord, (self.store.scope, observed.order_id))
                if latest is None:
                    return INCOMPLETE_OR_CHANGED_GROUP
                current = OrderObservation.model_validate_json(latest.payload)
                if (
                    Decimal(current.filled_quantity) != 0
                    or current.replaces
                    or current.replaced_by
                    or current.symbol != identity.symbol
                    or Decimal(current.quantity) != Decimal(identity.quantity)
                ):
                    if not operator:
                        raise ValueError("Bracket evidence changed before cancellation claim")
                    return "not_resting" if Decimal(current.filled_quantity) != 0 else INCOMPLETE_OR_CHANGED_GROUP
            closing = await session.scalar(
                select(CloseRequestRecord.id).where(
                    CloseRequestRecord.environment == self.db.environment,
                    CloseRequestRecord.execution_mode == self.db.execution_mode,
                    CloseRequestRecord.symbol == identity.symbol,
                    CloseRequestRecord.status.in_(ACTIVE_CLOSE_STATUSES),
                )
            )
            if closing:
                return "close_active"
            now = datetime.now(UTC)
            payload = {
                "signal_id": signal_id,
                "cause": reason,
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
            intent = (
                f"Operator cancel requested for entry {identity.symbol} (signal {signal_id}); awaiting broker confirmation."
                if operator
                else f"Entry #{signal_id} reached its resting deadline. Cancellation intent recorded for {identity.symbol}; awaiting broker confirmation."
            )
            await self.store.add_notification(
                session, f"entry-cancel/{row.id}/intent", NotificationKind.MESSAGE, {"text": intent}
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
            row = await session.get(WorkItemRecord, item.id)
            # The DELETE's own error (this owner's, or one an earlier resolution recorded).
            refused = transport_error or (json.loads(row.result).get("transport_error") if row else None)
            # The broker refused the DELETE because the entry already filled, cleanly and protected.
            # Only a refused DELETE and a fresh, successful exact read decide it: a failed lookup
            # (``reason``) stays UNKNOWN, and an accepted DELETE followed by a fill keeps polling
            # within the owner's window, then halts as UNKNOWN.
            filled_first = (
                not complete
                and reason is None
                and bool(refused)
                and bool(orders)
                and filled_before_cancel(identity, effective, item.payload["order_ids"])
            )
            if not complete and not filled_first and allow_pending:
                return False
            status = WorkStatus.ACCEPTED if complete else WorkStatus.REJECTED if filled_first else WorkStatus.UNKNOWN
            reason = (
                "confirmed_unfilled_cancellation"
                if complete
                else FILLED_BEFORE_CANCEL
                if filled_first
                else (reason or "unconfirmed_cancellation_or_fill_race")
            )
            payload: dict[str, Any] = {"reason": reason, "orders": [o.model_dump(mode="json") for o in effective]}
            payload["transport_error"] = refused
            if (
                not row
                or row.scope != self.store.scope
                or row.kind != WorkKind.ENTRY_CANCEL
                or (row.status == WorkStatus.ACCEPTED and (not complete or not transport_error))
                or row.status == WorkStatus.REJECTED
                or (row.status == status and json.loads(row.result) == payload)
            ):
                return True
            previous = row.status
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
            if not complete and not filled_first:
                await self.store.halt(
                    session, f"Unconfirmed entry cancellation {row.id}; inspect fills/protection before resuming"
                )
            # Items claimed before causes were recorded were all deadline cancels.
            cause = item.payload.get("cause", "deadline")
            signal_id = item.payload["signal_id"]
            if filled_first:
                # The signal stays EXECUTED: exact entry reconciliation records the fill as usual.
                text = f"Entry #{signal_id} filled before the {cause} cancellation; the position is open and protected."
                if previous == WorkStatus.UNKNOWN:
                    text += " The earlier halt stays until /resume."
            else:
                text = f"Entry #{signal_id} {cause} cancellation: {reason}. " + (
                    "Broker confirms no fills."
                    if complete
                    else "New risk halted. Inspect exact broker fills and protective orders; no automatic retry."
                )
            await self.store.add_notification(
                session, f"entry-cancel/{row.id}/{status}", NotificationKind.MESSAGE, {"text": text}
            )

            return True

    async def missed_bound(self, signal_id: int, symbol: str | None, evidence: dict[str, Any]) -> None:
        """One journaled notice that a marked native entry filled at or after its resting deadline.

        The position is open with its bracket, so nothing halts; the key keeps it to one event and one notice.
        """
        key = f"entry-lifetime-missed/{signal_id}"
        async with self.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            await self.store.append(
                session,
                stream=f"signal/{signal_id}/lifetime",
                kind=EventKind.ENTRY_LIFETIME_MISSED,
                payload=evidence,
                key=key,
            )
            await self.store.add_notification(
                session,
                key,
                NotificationKind.MESSAGE,
                {
                    "text": f"Entry #{signal_id} ({symbol or 'unknown symbol'}) filled after its session-close cancel "
                    "deadline: the cancel was missed (reconciliation late or host asleep). The position is open "
                    "with its bracket; no halt."
                },
            )

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
