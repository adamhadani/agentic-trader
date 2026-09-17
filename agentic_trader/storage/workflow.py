"""Transactional inbox/outbox and append-only journal repositories.

The scope row serializes short database transactions across processes. No broker
or Telegram call runs while holding it. A fenced queue claim spans external I/O;
only the claimant may cross the durable SUBMITTING boundary, which never expires.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import String, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_trader.constants import ACTIVE_CLOSE_STATUSES, AuditEventType, SignalStatus, SystemStateKey
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.execution.durable import (
    ENTRY_BLOCKING,
    EventKind,
    NotificationKind,
    OrderObservation,
    WorkItem,
    WorkKind,
    WorkStatus,
)
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.models import (
    AlphaProjectionRecord,
    CloseRequestRecord,
    DomainEventRecord,
    OrderProjectionRecord,
    SignalRecord,
    SystemStateRecord,
    WorkflowLockRecord,
    WorkItemRecord,
)


if TYPE_CHECKING:
    from agentic_trader.broker.base import OrderRequest, OrderResult
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.db import SignalDatabase


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def work_item(row: WorkItemRecord) -> WorkItem:
    return WorkItem(
        row.id,
        WorkKind(row.kind),
        WorkStatus(row.status),
        json.loads(row.payload),
        json.loads(row.result),
        row.token,
        row.attempts,
        row.created_at,
    )


class WorkflowStore:
    def __init__(self, db: SignalDatabase):
        self.db = db
        self.scope = f"{db.environment}/{db.execution_mode}"

    async def lock(self, session: AsyncSession, *, resource: str | None = None) -> None:
        scope = f"{self.scope}/{resource}" if resource else self.scope
        insert: Any
        dialect = session.bind.dialect.name
        if dialect == "postgresql":
            insert = pg_insert
        elif dialect == "sqlite":
            insert = sqlite_insert
        else:
            raise ValueError(f"Unsupported workflow database: {dialect}")
        await session.execute(insert(WorkflowLockRecord).values(scope=scope, version=0).on_conflict_do_nothing())
        await session.execute(
            update(WorkflowLockRecord)
            .where(WorkflowLockRecord.scope == scope)
            .values(version=WorkflowLockRecord.version + 1)
        )

    async def append(
        self, session: AsyncSession, *, stream: str, kind: EventKind, payload: dict[str, Any], key: str | None = None
    ) -> DomainEventRecord:
        """Caller holds the scope lock; append and projection/outbox share its transaction."""
        event_key = key or uuid4().hex
        existing = await session.scalar(
            select(DomainEventRecord).where(
                DomainEventRecord.scope == self.scope, DomainEventRecord.event_key == event_key
            )
        )
        if existing:
            return existing
        event = DomainEventRecord(
            scope=self.scope, stream=stream, kind=kind, event_key=event_key, payload=encode(payload), schema_version=1
        )
        session.add(event)
        await session.flush()
        return event

    async def record_health(self, component: str, success: bool, detail: str = "", *, run_id: str) -> None:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            await self.append(
                session,
                stream=f"health/{run_id}/{component}",
                kind=EventKind.HEALTH_OBSERVED,
                payload={"success": success, "detail": detail},
            )

    async def events(self, stream: str | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        async with self.db.session_factory() as session:
            stmt = select(DomainEventRecord).where(DomainEventRecord.scope == self.scope)
            if stream is not None:
                stmt = stmt.where(DomainEventRecord.stream == stream)
            if limit is not None:
                stmt = stmt.order_by(DomainEventRecord.id.desc()).limit(limit)
            else:
                stmt = stmt.order_by(DomainEventRecord.id)
            return [
                {
                    "id": r.id,
                    "stream": r.stream,
                    "kind": r.kind,
                    "schema_version": r.schema_version,
                    "recorded_at": r.recorded_at.isoformat(),
                    "payload": json.loads(r.payload),
                }
                for r in (await session.scalars(stmt)).all()
            ]

    async def list_work(
        self, kind: WorkKind, *, statuses: tuple[WorkStatus, ...] | None = None, limit: int = 100
    ) -> list[WorkItem]:
        async with self.db.session_factory() as session:
            stmt = select(WorkItemRecord).where(WorkItemRecord.scope == self.scope, WorkItemRecord.kind == kind)
            if statuses:
                stmt = stmt.where(WorkItemRecord.status.in_(statuses))
            rows = await session.scalars(stmt.order_by(WorkItemRecord.sequence).limit(limit))
            return [work_item(r) for r in rows]

    async def work_summary(self, kind: WorkKind) -> dict[str, dict[str, Any]]:
        async with self.db.session_factory() as session:
            rows = await session.execute(
                select(WorkItemRecord.status, func.count(), func.min(WorkItemRecord.created_at))
                .where(WorkItemRecord.scope == self.scope, WorkItemRecord.kind == kind)
                .group_by(WorkItemRecord.status)
            )
            return {status: {"count": count, "oldest": oldest} for status, count, oldest in rows}

    async def get_work(self, item_id: str) -> WorkItem | None:
        async with self.db.session_factory() as session:
            row = await session.scalar(
                select(WorkItemRecord).where(WorkItemRecord.id == item_id, WorkItemRecord.scope == self.scope)
            )
            return work_item(row) if row else None

    async def reservations(self, *, exclude_signal_id: int | None = None) -> list[dict[str, Any]]:
        async with self.db.session_factory() as session:
            stmt = select(SignalRecord).where(
                *self.db._scope(), SignalRecord.status.in_((SignalStatus.SUBMITTING, SignalStatus.EXECUTED))
            )
            if exclude_signal_id is not None:
                stmt = stmt.where(SignalRecord.id != exclude_signal_id)
            return [row.to_dict() for row in (await session.scalars(stmt)).all()]

    async def legacy_claims(self) -> list[int]:
        """Unmapped pre-migration claims must be reviewed, never silently resumed."""
        async with self.db.session_factory() as session:
            mapped = (
                select(WorkItemRecord.id)
                .where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key == cast(SignalRecord.id, String),
                )
                .exists()
            )
            return list(
                await session.scalars(
                    select(SignalRecord.id).where(
                        *self.db._scope(), SignalRecord.status == SignalStatus.SUBMITTING, ~mapped
                    )
                )
            )

    async def enqueue_entry(self, request: OrderRequest, config: AppConfig) -> tuple[WorkItem | None, str]:

        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            existing = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key == str(request.signal_id),
                )
            )
            if existing:
                return work_item(existing), "Already authorized; original request retained."
            halted = await session.get(SystemStateRecord, SystemStateKey.TRADING_HALTED)
            if halted and halted.value.lower() in ("true", "1", "yes"):
                return None, "Emergency trading halt active."
            closing = await session.scalar(
                select(CloseRequestRecord.id)
                .where(
                    CloseRequestRecord.environment == self.db.environment,
                    CloseRequestRecord.execution_mode == self.db.execution_mode,
                    CloseRequestRecord.symbol == request.symbol,
                    CloseRequestRecord.status.in_(ACTIVE_CLOSE_STATUSES),
                )
                .limit(1)
            )
            if closing:
                return None, "Symbol has an unresolved close request."
            signal = await session.scalar(
                select(SignalRecord).where(*self.db._scope(), SignalRecord.id == request.signal_id)
            )
            if not signal or signal.status != SignalStatus.PENDING:
                return None, "Signal is not pending."
            if reason := await self._alpha_entry_rejection(session, signal):
                return None, reason
            rows = list(
                (
                    await session.scalars(
                        select(SignalRecord).where(
                            *self.db._scope(), SignalRecord.status.in_((SignalStatus.SUBMITTING, SignalStatus.EXECUTED))
                        )
                    )
                ).all()
            )
            reason = reservation_rejection(request, [r.to_dict() for r in rows], config)
            if reason:
                return None, reason
            command_id = f"entry-{uuid4().hex}"
            request = request.model_copy(update={"client_order_id": command_id})
            now = datetime.now(UTC)
            payload = request.model_dump(mode="json")
            row = WorkItemRecord(
                id=command_id,
                scope=self.scope,
                dedup_key=str(request.signal_id),
                kind=WorkKind.ENTRY,
                status=WorkStatus.QUEUED,
                payload=encode(payload),
                result="{}",
                attempts=0,
                created_at=now,
                available_at=now,
            )
            session.add(row)
            signal.status = SignalStatus.SUBMITTING
            signal.quantity = request.quantity
            assert request.entry_price is not None and request.stop_loss is not None
            multiplier = config.contracts[request.symbol].multiplier if request.symbol in config.contracts else 1
            signal.notional_value = request.entry_price * request.quantity * multiplier
            signal.risk_dollars = abs(request.entry_price - request.stop_loss) * request.quantity * multiplier
            queued_event = await self.append(
                session, stream=f"entry/{command_id}", kind=EventKind.ENTRY_QUEUED, payload=payload
            )
            row.sequence = queued_event.id
            session.add(
                self.db._audit(AuditEventType.EXECUTION_CLAIMED, request.signal_id, {"client_order_id": command_id})
            )
            await session.flush()
            return work_item(row), "Queued for fresh admission checks."

    async def claim_entry(self, *, lease_seconds: float, now: datetime | None = None) -> WorkItem | None:
        now = now or datetime.now(UTC)
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(
                select(WorkItemRecord)
                .where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.status.in_(ENTRY_BLOCKING),
                )
                .order_by(WorkItemRecord.sequence)
                .limit(1)
            )
            if not row or row.status in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN):
                return None
            if row.status == WorkStatus.CHECKING and row.lease_until and row.lease_until > now:
                return None
            row.status, row.token = WorkStatus.CHECKING, uuid4().hex
            row.lease_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            await self.append(
                session,
                stream=f"entry/{row.id}",
                kind=EventKind.ENTRY_CHECKING,
                payload={"attempt": row.attempts, "token": row.token},
            )
            await session.flush()
            return work_item(row)

    async def _alpha_entry_rejection(self, session: AsyncSession, signal: SignalRecord) -> str | None:
        if not signal.alpha_version and not signal.strategy.lower().startswith("alpha_"):
            return None
        # Consistent lock order: trading admission, then alpha registry. Registry
        # changes use the alpha lock, so demotion cannot race submission commit.
        await self.lock(session, resource="alpha")
        registry = await session.get(AlphaProjectionRecord, (self.scope, "registry"))
        if (
            not signal.alpha_version
            or not registry
            or signal.alpha_version not in json.loads(registry.payload)["active"]
        ):
            return "Alpha version is not active/qualified; request a fresh scan after qualification."
        row = await session.get(AlphaProjectionRecord, (self.scope, f"version/{signal.alpha_version}"))
        if row is None:
            return "Alpha version evidence is missing."
        qualification = await session.get(AlphaProjectionRecord, (self.scope, f"qualification/{signal.alpha_version}"))
        if not qualification or json.loads(qualification.payload).get("policy") != asdict(ValidationPolicy()):
            return "Alpha qualification policy is obsolete; fresh research and qualification are required."
        definition = json.loads(row.payload)["definition"]
        if definition.get("clock") is not None:
            return "Alpha session-clock execution remains diagnostic; new risk is disabled."
        if (
            signal.strategy != definition["alpha_id"]
            or signal.timeframe != definition["timeframe"]
            or signal.contract.upper() not in (definition.get("eligible_symbols") or ())
            or not signal.alpha_policy
            or json.loads(signal.alpha_policy) != definition["execution"]
        ):
            return "Alpha signal differs from its immutable strategy contract."
        return None

    async def begin_submission(self, item: WorkItem) -> bool:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            halted = await session.get(SystemStateRecord, SystemStateKey.TRADING_HALTED)
            if halted and halted.value.lower() in ("true", "1", "yes"):
                return False
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.id == item.id,
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.status == WorkStatus.CHECKING,
                    WorkItemRecord.token == item.token,
                    WorkItemRecord.lease_until > datetime.now(UTC),
                )
            )
            if not row:
                return False
            signal = await session.scalar(
                select(SignalRecord).where(*self.db._scope(), SignalRecord.id == item.payload["signal_id"])
            )
            if signal is None or await self._alpha_entry_rejection(session, signal):
                return False
            row.status, row.lease_until = WorkStatus.SUBMITTING, None
            await self.append(session, stream=f"entry/{row.id}", kind=EventKind.ENTRY_SUBMITTING, payload=item.payload)
            session.add(self.db._audit(AuditEventType.ENTRY_SUBMISSION, item.payload["signal_id"], item.payload))
            return True

    async def resolve_entry(self, item: WorkItem, result: OrderResult, *, recovery: bool = False) -> bool:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.id == item.id,
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.status.in_(ENTRY_BLOCKING),
                )
            )
            if not row or (not recovery and row.token != item.token):
                return False
            if recovery and row.status not in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN):
                return False
            row.status = (
                WorkStatus.ACCEPTED
                if result.success
                else (WorkStatus.UNKNOWN if result.submission_uncertain else WorkStatus.REJECTED)
            )
            row.result = encode(result.model_dump(mode="json"))
            signal = await session.scalar(
                select(SignalRecord).where(
                    *self.db._scope(),
                    SignalRecord.id == item.payload["signal_id"],
                    SignalRecord.status == SignalStatus.SUBMITTING,
                )
            )
            if signal is not None and row.status != WorkStatus.UNKNOWN:
                signal.status = SignalStatus.EXECUTED if result.success else SignalStatus.FAILED
                signal.broker_order_id = result.order_id
                if result.fill_price is not None:
                    signal.entry_price = result.fill_price
            if row.status == WorkStatus.UNKNOWN:
                for key, value in (
                    (SystemStateKey.TRADING_HALTED, "true"),
                    (
                        SystemStateKey.TRADING_HALT_REASON,
                        f"Unconfirmed broker entry {item.id}; lookup recovery required before /resume",
                    ),
                ):
                    state = await session.get(SystemStateRecord, key)
                    if state:
                        state.value, state.updated_at = value, datetime.now(UTC)
                    else:
                        session.add(SystemStateRecord(key=key, value=value))
                session.add(
                    self.db._audit(
                        AuditEventType.ENTRY_SUBMISSION_UNKNOWN,
                        item.payload["signal_id"],
                        {"client_order_id": item.id, "error": result.error_message},
                    )
                )
            await self.append(
                session,
                stream=f"entry/{row.id}",
                kind=EventKind.ENTRY_RESOLVED,
                payload={"status": row.status, "result": json.loads(row.result), "recovery": recovery},
            )
            detail = (
                f"Entry #{item.payload['signal_id']}: {row.status}. "
                f"Broker order: {result.order_id or 'unconfirmed'}. "
                f"{result.error_message or ''}"
            )
            if row.status == WorkStatus.REJECTED:
                detail += (
                    " Conditions changed or the broker rejected this order. Run /scan for a new proposal and approval."
                )
            await self.add_notification(
                session, f"entry/{row.id}/{row.status}", NotificationKind.MESSAGE, {"text": detail}
            )
            return True

    async def add_notification(self, session: AsyncSession, key: str, kind: str, payload: dict[str, Any]) -> str:
        existing = await session.scalar(
            select(WorkItemRecord).where(
                WorkItemRecord.scope == self.scope,
                WorkItemRecord.kind == WorkKind.NOTIFICATION,
                WorkItemRecord.dedup_key == key,
            )
        )
        if existing:
            return existing.id
        now = datetime.now(UTC)
        item_id = uuid4().hex
        row = WorkItemRecord(
            id=item_id,
            scope=self.scope,
            dedup_key=key,
            kind=WorkKind.NOTIFICATION,
            status=WorkStatus.QUEUED,
            payload=encode({"kind": kind, "arguments": payload}),
            result="{}",
            attempts=0,
            created_at=now,
            available_at=now,
        )
        session.add(row)
        queued_event = await self.append(
            session,
            stream=f"notification/{item_id}",
            kind=EventKind.NOTIFICATION_QUEUED,
            payload={"notification_kind": kind, "dedup_key": key},
        )
        row.sequence = queued_event.id
        return item_id

    async def enqueue_notification(self, key: str, kind: str, payload: dict[str, Any]) -> str:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            return await self.add_notification(session, key, kind, payload)

    async def claim_notification(
        self, *, lease_seconds: float, max_attempts: int, now: datetime | None = None
    ) -> WorkItem | None:
        now = now or datetime.now(UTC)
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(
                select(WorkItemRecord)
                .where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.NOTIFICATION,
                    ((WorkItemRecord.status == WorkStatus.QUEUED) & (WorkItemRecord.available_at <= now))
                    | ((WorkItemRecord.status == WorkStatus.CHECKING) & (WorkItemRecord.lease_until <= now)),
                )
                .order_by(WorkItemRecord.sequence)
                .limit(1)
            )
            if not row:
                return None
            if row.attempts >= max_attempts:
                row.status = WorkStatus.DEAD
                row.result = encode({"detail": "Delivery attempt budget exhausted after worker loss"})
                await self.append(
                    session,
                    stream=f"notification/{row.id}",
                    kind=EventKind.NOTIFICATION_RESULT,
                    payload={"status": row.status, "attempt": row.attempts, "detail": "Worker lease expired"},
                )
                return None
            row.status, row.token = WorkStatus.CHECKING, uuid4().hex
            row.lease_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            await self.append(
                session,
                stream=f"notification/{row.id}",
                kind=EventKind.NOTIFICATION_ATTEMPT,
                payload={"attempt": row.attempts},
            )
            await session.flush()
            return work_item(row)

    async def finish_notification(
        self, item: WorkItem, *, delivered: bool, max_attempts: int, retry_seconds: float, detail: str = ""
    ) -> bool:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.id == item.id,
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.token == item.token,
                    WorkItemRecord.status == WorkStatus.CHECKING,
                )
            )
            if not row:
                return False
            row.status = (
                WorkStatus.DELIVERED
                if delivered
                else (WorkStatus.DEAD if row.attempts >= max_attempts else WorkStatus.QUEUED)
            )
            row.result = encode({"detail": detail})
            row.available_at = datetime.now(UTC) + timedelta(seconds=retry_seconds)
            row.lease_until = None
            await self.append(
                session,
                stream=f"notification/{row.id}",
                kind=EventKind.NOTIFICATION_RESULT,
                payload={"status": row.status, "attempt": row.attempts, "detail": detail},
            )
            return True

    async def requeue_notification(self, item_id: str) -> bool:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            row = await session.scalar(
                select(WorkItemRecord).where(
                    WorkItemRecord.id == item_id,
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.NOTIFICATION,
                    WorkItemRecord.status == WorkStatus.DEAD,
                )
            )
            if not row:
                return False
            row.status, row.attempts, row.token = WorkStatus.QUEUED, 0, None
            row.available_at = datetime.now(UTC)
            await self.append(
                session, stream=f"notification/{row.id}", kind=EventKind.NOTIFICATION_REQUEUED, payload={}
            )
            return True

    async def observe_orders(self, observations: list[OrderObservation]) -> None:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            for observation in observations:
                payload = observation.model_dump(mode="json")
                key = hashlib.sha256(encode(payload).encode()).hexdigest()
                event = await self.append(
                    session,
                    stream=f"order/{observation.order_id}",
                    kind=EventKind.ORDER_OBSERVED,
                    payload=payload,
                    key=f"order/{key}",
                )
                await self._project_order(session, event)
                projection = await session.get(OrderProjectionRecord, (self.scope, observation.order_id))
                assert projection is not None
                current = OrderObservation.model_validate_json(projection.payload)
                if current.status in {"canceled", "rejected", "expired"} and Decimal(current.filled_quantity) == 0:
                    signal = await session.scalar(
                        select(SignalRecord).where(
                            *self.db._scope(),
                            SignalRecord.broker_order_id == observation.order_id,
                            SignalRecord.status == SignalStatus.EXECUTED,
                            SignalRecord.executed_at.is_(None),
                        )
                    )
                    if signal:
                        signal.status = SignalStatus.FAILED
                        await self.add_notification(
                            session,
                            f"signal/{signal.id}/entry-terminal",
                            NotificationKind.MESSAGE,
                            {
                                "text": f"Entry #{signal.id} ended {current.status} without fills. Reservation released; run /scan for a new proposal."
                            },
                        )
                        session.add(
                            self.db._audit(
                                AuditEventType.ENTRY_EXECUTION_UPDATED,
                                signal.id,
                                {
                                    "order_id": observation.order_id,
                                    "status": current.status,
                                    "filled_quantity": "0",
                                },
                            )
                        )

    async def _project_order(self, session: AsyncSession, event: DomainEventRecord) -> bool:
        observation = OrderObservation.model_validate_json(event.payload)
        previous = await session.get(OrderProjectionRecord, (self.scope, observation.order_id))
        if previous:
            old = OrderObservation.model_validate_json(previous.payload)
            # REST and stream can be out of order. Equal-timestamp observations use
            # journal sequence; cumulative quantities must never decrease silently.
            if (
                event.id <= previous.event_id
                or observation.updated_at < old.updated_at
                or Decimal(observation.filled_quantity) < Decimal(old.filled_quantity)
            ):
                return False
            if old.parent_order_id and observation.parent_order_id is None:
                observation = observation.model_copy(update={"parent_order_id": old.parent_order_id})
            previous.event_id, previous.payload = event.id, observation.model_dump_json()
        else:
            session.add(
                OrderProjectionRecord(
                    scope=self.scope, order_id=observation.order_id, event_id=event.id, payload=event.payload
                )
            )
        await session.flush()
        return True

    async def order_views(self) -> list[OrderObservation]:
        async with self.db.session_factory() as session:
            rows = await session.scalars(
                select(OrderProjectionRecord)
                .where(OrderProjectionRecord.scope == self.scope)
                .order_by(OrderProjectionRecord.order_id)
            )
            return [OrderObservation.model_validate_json(row.payload) for row in rows]

    async def rebuild_order_views(self) -> int:
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            await session.execute(delete(OrderProjectionRecord).where(OrderProjectionRecord.scope == self.scope))
            rows = await session.scalars(
                select(DomainEventRecord)
                .where(DomainEventRecord.scope == self.scope, DomainEventRecord.kind == EventKind.ORDER_OBSERVED)
                .order_by(DomainEventRecord.id)
            )
            count = 0
            for row in rows:
                await self._project_order(session, row)
                count += 1
            return count
