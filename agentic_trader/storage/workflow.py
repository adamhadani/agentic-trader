"""Transactional inbox/outbox and append-only journal repositories.

The scope row serializes short database transactions across processes. No broker
or Telegram call runs while holding it. A fenced queue claim spans external I/O;
only the claimant may cross the durable SUBMITTING boundary, which never expires.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Collection
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import String, cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_trader.accounting.risk import AccountRiskSnapshot, require_risk_checkpoint
from agentic_trader.broker.base import BrokerEntryContext, OrderRequest
from agentic_trader.constants import ACTIVE_CLOSE_STATUSES, AuditEventType, ExecutionMode, SignalStatus, SystemStateKey
from agentic_trader.execution.admission import authorization_expiry, reservation_rejection
from agentic_trader.execution.auto_entry import (
    AUTO_ENTRY_KEY,
    AUTO_ENTRY_MARKER,
    AutoEntryAttempt,
    AutoEntryDigestRow,
    ExpiredCard,
)
from agentic_trader.execution.capacity import assess_entry_capacity
from agentic_trader.execution.durable import (
    ENTRY_BLOCKING,
    LEDGER_LOCK,
    EntryTrigger,
    EventKind,
    NotificationKind,
    OrderObservation,
    WorkItem,
    WorkKind,
    WorkStatus,
)
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.research.apriori.probe import CATALOG_KIND, CATALOG_STRATEGY_IDS
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID, frozen_watch_contract
from agentic_trader.risk import drawdown_risk_factor, requires_account_risk
from agentic_trader.storage.models import (
    AlphaProjectionRecord,
    AuditEventRecord,
    CloseRequestRecord,
    DomainEventRecord,
    LedgerCheckpointRecord,
    OrderProjectionRecord,
    SignalRecord,
    SystemStateRecord,
    WorkflowLockRecord,
    WorkItemRecord,
)
from agentic_trader.storage.probe_state import CLOSED_STATUSES, probe_block_reason


if TYPE_CHECKING:
    from agentic_trader.broker.base import OrderResult
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.db import SignalDatabase


logger = logging.getLogger(__name__)


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def _event_view(row: DomainEventRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "stream": row.stream,
        "kind": row.kind,
        "schema_version": row.schema_version,
        "recorded_at": row.recorded_at.isoformat(),
        "payload": json.loads(row.payload),
    }


# The decision_provenance key of an insider watch card's event (InsiderWatchSource.provenance_key).
WATCH_PROVENANCE_KEY = "insider_watch"


def _catalog_signal_rejection(signal: SignalRecord, definition: dict) -> str | None:
    """A catalog probe has no symbol universe: its signal must carry a same-session event
    naming its own symbol, the frozen leg and the frozen bracket policy."""
    provenance = json.loads(signal.decision_provenance or "{}")
    event = provenance.get("pead_event") if isinstance(provenance, dict) else None
    session = signal.timestamp.astimezone(ET_TZ).date().isoformat()
    if (
        signal.strategy != definition["alpha_id"]
        or signal.timeframe != definition["timeframe"]
        or str(signal.direction).upper() != definition["leg"]
        or not signal.alpha_policy
        or json.loads(signal.alpha_policy) != definition["execution"]
        or not isinstance(event, dict)
        or event.get("symbol") != signal.contract.upper()
        or event.get("session") != session
    ):
        return "Catalog signal differs from its immutable strategy contract."
    return None


def _watch_signal_rejection(signal: SignalRecord) -> str | None:
    """An insider watch card has no registry entry: its signal must carry the frozen cube bracket byte
    for byte and a same-session event of the frozen entry naming its own contract."""
    try:
        contract = frozen_watch_contract()
    except Exception as exc:
        return f"Insider watch contract unavailable: {type(exc).__name__}."
    provenance = json.loads(signal.decision_provenance or "{}")
    event = provenance.get(WATCH_PROVENANCE_KEY) if isinstance(provenance, dict) else None
    session = signal.timestamp.astimezone(ET_TZ).date().isoformat()
    if (
        signal.alpha_version
        or signal.timeframe != "1d"
        or str(signal.direction).upper() != "LONG"
        or not signal.alpha_policy
        or encode(json.loads(signal.alpha_policy)) != encode(contract.policy)
        or not isinstance(event, dict)
        or event.get("entry_sha256") != contract.entry_sha256
        or event.get("symbol") != signal.contract.upper()
        or event.get("session") != session
    ):
        return "Insider watch signal differs from its frozen contract."
    return None


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


# The broker's unfilled terminal order statuses that fail a resting entry (and the only ones a digest renders).
BROKER_TERMINAL_STATUSES = frozenset({"canceled", "rejected", "expired"})


def _digest_row(
    signal: SignalRecord,
    attempt: dict[str, Any] | None,
    entry: WorkItemRecord | None,
    cancel: WorkItemRecord | None,
    terminal: str | None,
) -> AutoEntryDigestRow:
    """The digest outcome of one marked card from its signal, attempt event and entry/cancel work items.

    Details are desk-authored: the attempt's reason, a fixed phrase, or the broker's terminal order status
    (one of canceled, rejected, expired). A work item's error text is never rendered.
    """
    status = SignalStatus(signal.status)
    symbol = signal.contract
    if signal.executed_at is not None or status in CLOSED_STATUSES:
        state = "open" if status == SignalStatus.EXECUTED else "closed"
        return AutoEntryDigestRow(
            signal.id, symbol, "filled", f"{signal.quantity or 0:g} @ ${signal.entry_price:.2f} ({state})"
        )
    if cancel is not None:
        operator = json.loads(cancel.payload).get("cause", "deadline") == "operator"
        if cancel.status == WorkStatus.ACCEPTED:
            return AutoEntryDigestRow(signal.id, symbol, "cancelled_by_operator" if operator else "cancelled_unfilled")
        return AutoEntryDigestRow(signal.id, symbol, "still_resting")
    if status == SignalStatus.DISMISSED:
        return AutoEntryDigestRow(signal.id, symbol, "dismissed")
    if status in (SignalStatus.SUBMITTING, SignalStatus.EXECUTED):
        return AutoEntryDigestRow(signal.id, symbol, "still_resting")
    if attempt is not None and attempt.get("outcome") == "refused":
        reason = attempt.get("reason")
        if attempt.get("expiry_kind") == "expired":
            # The sweep's own tap assessment expired the card: say so beside its reason.
            reason = f"expired ({reason})" if reason else "expired"
        return AutoEntryDigestRow(signal.id, symbol, "refused", reason)
    if status == SignalStatus.FAILED and entry is not None:
        if entry.status == WorkStatus.REJECTED:
            return AutoEntryDigestRow(signal.id, symbol, "refused", "rejected after it was queued")
        ended = (
            f"broker ended the order {terminal} without fills" if terminal else "broker ended the order without fills"
        )
        return AutoEntryDigestRow(signal.id, symbol, "refused", ended)
    return AutoEntryDigestRow(signal.id, symbol, "abandoned")


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

    async def halt(self, session: AsyncSession, reason: str) -> None:
        """Persist a fail-closed halt inside the caller's locked workflow transaction."""
        for key, value in ((SystemStateKey.TRADING_HALTED, "true"), (SystemStateKey.TRADING_HALT_REASON, reason)):
            row = await session.get(SystemStateRecord, key)
            if row:
                row.value, row.updated_at = value, datetime.now(UTC)
            else:
                session.add(SystemStateRecord(key=key, value=value))

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

    async def record_card_tap(self, signal_id: int, payload: dict[str, Any]) -> None:
        """Journal one tap-time card assessment; a new event per tap, never deduplicated."""
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            await self.append(
                session,
                stream=f"card/{signal_id}",
                kind=EventKind.CARD_TAP_ASSESSED,
                payload=payload,
                key=f"card_tap_assessed/{signal_id}/{uuid4().hex}",
            )

    async def claim_card_reevaluation(self, signal_id: int, payload: dict[str, Any]) -> bool:
        """Atomically claim the one re-evaluation an expired card may have.

        Under the scope lock, in one transaction: an existing ``card_reevaluate/{id}``
        event means the re-evaluation was already requested (a redelivered callback, a
        double tap or the CLI) and nothing is written; otherwise the claim is appended.
        """
        key = f"card_reevaluate/{signal_id}"
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.scope, DomainEventRecord.event_key == key
                )
            )
            if existing is not None:
                return False
            await self.append(
                session,
                stream=f"card/{signal_id}",
                kind=EventKind.CARD_REEVALUATE_REQUESTED,
                payload=payload,
                key=key,
            )
            return True

    async def pending_auto_entries(self, et_date: date) -> list[dict[str, Any]]:
        """The marked cards of New York date ``et_date`` still awaiting their one auto-entry attempt.

        This scope's ``PENDING`` signals whose ``auto_entry`` marker is exactly ``AUTO_ENTRY_MARKER``,
        with no ``auto-entry/{id}`` event, ordered by recording time, then rank. A card whose entry was
        queued is ``SUBMITTING``, so a crash after queueing never selects it again.
        """
        start = datetime.combine(et_date, time.min, tzinfo=ET_TZ)
        end = datetime.combine(et_date + timedelta(days=1), time.min, tzinfo=ET_TZ)
        async with self.db.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SignalRecord).where(
                        *self.db._scope(),
                        SignalRecord.status == SignalStatus.PENDING,
                        SignalRecord.timestamp >= start,
                        SignalRecord.timestamp < end,
                    )
                )
            ).all()
            marked: list[tuple[SignalRecord, Any]] = []
            for row in rows:
                provenance = json.loads(row.decision_provenance or "{}")
                if isinstance(provenance, dict) and provenance.get(AUTO_ENTRY_KEY) == AUTO_ENTRY_MARKER:
                    marked.append((row, provenance.get("rank")))
            keys = [f"auto-entry/{row.id}" for row, _ in marked]
            attempted = (
                set(
                    await session.scalars(
                        select(DomainEventRecord.event_key).where(
                            DomainEventRecord.scope == self.scope, DomainEventRecord.event_key.in_(keys)
                        )
                    )
                )
                if keys
                else set()
            )
        selected = [(row, rank) for row, rank in marked if f"auto-entry/{row.id}" not in attempted]
        selected.sort(key=lambda pair: (pair[0].timestamp, pair[1] if isinstance(pair[1], int) else float("inf")))
        return [row.to_dict() for row, _ in selected]

    async def auto_entry_digest_rows(self, et_date: date) -> list[AutoEntryDigestRow]:
        """One digest row per marked card of New York date ``et_date``, in rank order.

        Reads only the signals table, the ``auto_entry_attempted`` events and the entry/cancel work items.
        A fill wins; then an operator-cause cancel (``still_resting`` until the broker confirms it); then
        the signal's own state, with a later broker rejection reported as the refusal.
        """
        start = datetime.combine(et_date, time.min, tzinfo=ET_TZ)
        end = datetime.combine(et_date + timedelta(days=1), time.min, tzinfo=ET_TZ)
        async with self.db.session_factory() as session:
            signals = (
                await session.scalars(
                    select(SignalRecord).where(
                        *self.db._scope(), SignalRecord.timestamp >= start, SignalRecord.timestamp < end
                    )
                )
            ).all()
            marked: list[tuple[SignalRecord, Any]] = []
            for row in signals:
                provenance = json.loads(row.decision_provenance or "{}")
                if isinstance(provenance, dict) and provenance.get(AUTO_ENTRY_KEY) == AUTO_ENTRY_MARKER:
                    marked.append((row, provenance.get("rank")))
            if not marked:
                return []
            ids = [str(row.id) for row, _ in marked]
            attempts = {
                event.event_key: json.loads(event.payload)
                for event in await session.scalars(
                    select(DomainEventRecord).where(
                        DomainEventRecord.scope == self.scope,
                        DomainEventRecord.event_key.in_([f"auto-entry/{i}" for i in ids]),
                    )
                )
            }
            entries = {
                item.dedup_key: item
                for item in await session.scalars(
                    select(WorkItemRecord).where(
                        WorkItemRecord.scope == self.scope,
                        WorkItemRecord.kind == WorkKind.ENTRY,
                        WorkItemRecord.dedup_key.in_(ids),
                    )
                )
            }
            cancels: dict[str, WorkItemRecord] = {}
            for item in await session.scalars(
                select(WorkItemRecord)
                .where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY_CANCEL,
                    WorkItemRecord.dedup_key.in_(ids),
                )
                .order_by(WorkItemRecord.created_at, WorkItemRecord.id)
            ):
                cancels[item.dedup_key] = item
            terminals: dict[int, str] = {}
            for audit in await session.scalars(
                select(AuditEventRecord)
                .where(
                    AuditEventRecord.event_type == AuditEventType.ENTRY_EXECUTION_UPDATED,
                    AuditEventRecord.signal_id.in_([row.id for row, _ in marked]),
                )
                .order_by(AuditEventRecord.id)
            ):
                evidence = json.loads(audit.payload)
                if evidence.get("filled_quantity") == "0" and evidence.get("status") in BROKER_TERMINAL_STATUSES:
                    terminals[int(audit.signal_id or 0)] = evidence["status"]
        marked.sort(key=lambda pair: (pair[0].timestamp, pair[1] if isinstance(pair[1], int) else float("inf")))
        return [
            _digest_row(
                row,
                attempts.get(f"auto-entry/{row.id}"),
                entries.get(str(row.id)),
                cancels.get(str(row.id)),
                terminals.get(row.id),
            )
            for row, _ in marked
        ]

    async def record_auto_entry_attempt(
        self,
        attempt: AutoEntryAttempt,
        *,
        recard: dict[str, Any] | None,
        entered: bool,
        expired: ExpiredCard | None = None,
    ) -> None:
        """Journal one auto-entry attempt and queue its follow-up in one transaction (delivery contract).

        The ``auto_entry_attempted`` event (stream ``card/{id}``, key ``auto-entry/{id}``) is written
        once: a later call for the same card writes nothing. ``entered`` queues the
        ``CARD_AUTO_ENTERED`` button swap. ``expired`` (a card the attempt left ``EXPIRED``) queues, only
        while the signal is ``EXPIRED`` under the lock, the card-expiry sweep's own ``CARD_EXPIRED`` strike
        (key ``signal/{id}/expired``, payload ``signal_id``, ``contract``, ``reevaluable``) and the
        ``signal/{id}/auto-not-placed`` notice, so the card stops saying it was entered. ``recard`` (its
        ``auto_entry`` and raw ``auto_refusal``) queues a fresh card: the original card's payload from its
        ``signal/{id}/created`` outbox row with those fields, only while the signal is still ``PENDING``
        under the lock. Without that row there is nothing to re-send: the attempt is still journaled and a
        warning is logged.
        """
        signal_id = attempt.signal_id
        key = f"auto-entry/{signal_id}"
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            journaled = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.scope, DomainEventRecord.event_key == key
                )
            )
            if journaled is not None:
                return
            await self.append(
                session,
                stream=f"card/{signal_id}",
                kind=EventKind.AUTO_ENTRY_ATTEMPTED,
                payload=attempt.payload(),
                key=key,
            )
            signal = await session.scalar(select(SignalRecord).where(*self.db._scope(), SignalRecord.id == signal_id))
            if signal is None:
                return
            if entered:
                await self.add_notification(
                    session,
                    f"signal/{signal_id}/auto-entered",
                    NotificationKind.CARD_AUTO_ENTERED,
                    {"signal_id": signal_id, "contract": signal.contract},
                )
            if expired is not None and signal.status == SignalStatus.EXPIRED:
                # The original card's message id is untouched on this path (a re-priced card is a new
                # signal), so the strike reaches the card that says "no tap needed".
                await self.add_notification(
                    session,
                    f"signal/{signal_id}/expired",
                    NotificationKind.CARD_EXPIRED,
                    {"signal_id": signal_id, "contract": signal.contract, "reevaluable": expired.reevaluable},
                )
                await self.add_notification(
                    session, f"signal/{signal_id}/auto-not-placed", NotificationKind.MESSAGE, {"text": expired.notice}
                )
            if recard is not None and signal.status == SignalStatus.PENDING:
                original = await session.scalar(
                    select(WorkItemRecord.payload).where(
                        WorkItemRecord.scope == self.scope,
                        WorkItemRecord.kind == WorkKind.NOTIFICATION,
                        WorkItemRecord.dedup_key == f"signal/{signal_id}/created",
                    )
                )
                if original is None:
                    logger.warning(
                        "Auto-entry re-card for card #%d skipped: its original card notification is missing",
                        signal_id,
                        extra={"event": "auto_entry_recard_missing", "signal_id": signal_id},
                    )
                    return
                await self.add_notification(
                    session,
                    f"signal/{signal_id}/auto-refused",
                    NotificationKind.SIGNAL,
                    {**json.loads(original)["arguments"], **recard},
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
            return [_event_view(r) for r in (await session.scalars(stmt)).all()]

    async def recent_events(self, kind: EventKind, *, streams: Collection[str]) -> list[dict[str, Any]]:
        """Events of ``kind`` in any of ``streams``, newest first (served by the stream index)."""
        if not streams:
            return []
        async with self.db.session_factory() as session:
            stmt = (
                select(DomainEventRecord)
                .where(
                    DomainEventRecord.scope == self.scope,
                    DomainEventRecord.stream.in_(list(streams)),
                    DomainEventRecord.kind == kind,
                )
                .order_by(DomainEventRecord.id.desc())
            )
            return [_event_view(r) for r in (await session.scalars(stmt)).all()]

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

    async def entry_item_ids(self, signal_ids: Collection[int]) -> dict[int, str]:
        """Entry work-item ids (the broker client order ids) by signal id, for journal readers."""
        if not signal_ids:
            return {}
        keys = {str(int(signal_id)): int(signal_id) for signal_id in signal_ids}
        async with self.db.session_factory() as session:
            rows = await session.execute(
                select(WorkItemRecord.dedup_key, WorkItemRecord.id).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key.in_(list(keys)),
                )
            )
            return {keys[dedup_key]: item_id for dedup_key, item_id in rows}

    async def entry_work_states(self, signal_ids: Collection[int]) -> dict[int, tuple[str, EntryTrigger]]:
        """``(work status, entry trigger)`` of each signal's entry item, for journal readers.

        A signal with no entry item is absent; an item that predates the trigger key reads as a tap.
        """
        if not signal_ids:
            return {}
        keys = {str(int(signal_id)): int(signal_id) for signal_id in signal_ids}
        async with self.db.session_factory() as session:
            rows = await session.execute(
                select(WorkItemRecord.dedup_key, WorkItemRecord.status, WorkItemRecord.payload).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key.in_(list(keys)),
                )
            )
            return {
                keys[dedup_key]: (str(status), EntryTrigger(json.loads(payload).get("entry_trigger", EntryTrigger.TAP)))
                for dedup_key, status, payload in rows
            }

    async def entry_cancel_causes(self, signal_ids: Collection[int]) -> dict[int, set[str]]:
        """The causes (``deadline`` or ``operator``) of each signal's entry-cancel items, for journal readers.

        Items claimed before causes were recorded were all deadline cancels.
        """
        if not signal_ids:
            return {}
        keys = {str(int(signal_id)): int(signal_id) for signal_id in signal_ids}
        causes: dict[int, set[str]] = {}
        async with self.db.session_factory() as session:
            rows = await session.execute(
                select(WorkItemRecord.dedup_key, WorkItemRecord.payload).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY_CANCEL,
                    WorkItemRecord.dedup_key.in_(list(keys)),
                )
            )
            for dedup_key, payload in rows:
                causes.setdefault(keys[dedup_key], set()).add(str(json.loads(payload).get("cause", "deadline")))
        return causes

    async def unresolved_cancellation(self, session: AsyncSession, *, symbol: str | None = None) -> bool:
        query = select(WorkItemRecord.id).where(
            WorkItemRecord.scope == self.scope,
            WorkItemRecord.kind == WorkKind.ENTRY_CANCEL,
            WorkItemRecord.status.in_((WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)),
        )
        if symbol is not None:
            query = query.where(
                WorkItemRecord.dedup_key.in_(
                    select(cast(SignalRecord.id, String)).where(*self.db._scope(), SignalRecord.contract == symbol)
                )
            )
        return bool(await session.scalar(query.limit(1)))

    async def _entry_risk(self, session: AsyncSession, config: AppConfig) -> AccountRiskSnapshot | None:
        if not requires_account_risk(config) and not self.db.execution_mode.startswith(f"{ExecutionMode.ALPACA}:"):
            return None
        # Lock order is trading -> ledger. Imports hold only the ledger lock;
        # no remote call occurs while either transaction lock is held.
        await self.lock(session, resource=LEDGER_LOCK)
        checkpoint = await session.get(LedgerCheckpointRecord, self.scope)
        risk = require_risk_checkpoint(
            json.loads(checkpoint.payload) if checkpoint else {}, max_age_seconds=config.accounting.max_age_seconds
        )
        if risk.equity <= 0:
            raise ValueError("Observed account equity is nonpositive; new entries blocked")
        return risk

    async def _entry_payload(self, session: AsyncSession, signal_id: int) -> str | None:
        return await session.scalar(
            select(WorkItemRecord.payload).where(
                WorkItemRecord.scope == self.scope,
                WorkItemRecord.kind == WorkKind.ENTRY,
                WorkItemRecord.dedup_key == str(signal_id),
            )
        )

    async def entry_trigger_of(self, session: AsyncSession, signal_id: int) -> EntryTrigger | None:
        """The trigger of a signal's entry work item; None without one, a tap when it predates the key.

        Strict: an unknown stored value raises ``ValueError``. A display-only caller inside a fact
        transaction uses ``display_entry_trigger`` instead.
        """
        raw = await self._entry_payload(session, signal_id)
        if raw is None:
            return None
        return EntryTrigger(json.loads(raw).get("entry_trigger", EntryTrigger.TAP))

    async def display_entry_trigger(self, session: AsyncSession, signal_id: int) -> EntryTrigger | None:
        """``entry_trigger_of`` for the fold and fill-notice decisions inside a fact transaction: never raises
        on the stored value. An unknown or unreadable value reads as ``TAP`` (never folds, no fill notice),
        with a warning naming only its error's type; the database read itself still raises."""
        raw = await self._entry_payload(session, signal_id)
        if raw is None:
            return None
        try:
            return EntryTrigger(json.loads(raw).get("entry_trigger", EntryTrigger.TAP))
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "Entry trigger of signal #%d is unreadable (%s); notices treat it as a tap",
                signal_id,
                type(exc).__name__,
                extra={"event": "entry_trigger_unreadable", "signal_id": signal_id, "reason": type(exc).__name__},
            )
            return EntryTrigger.TAP

    async def folds_into_auto_digest(self, session: AsyncSession, signal_id: int) -> bool:
        """Whether a routine notice of this signal's entry is folded into the daily auto-entry digest.

        Only an auto entry folds, and only while the digest can still report it: never once the digest of
        the signal's New York creation date is queued, nor when that date is before today (New York).
        """
        if await self.display_entry_trigger(session, signal_id) != EntryTrigger.AUTO:
            return False
        created = await session.scalar(
            select(SignalRecord.timestamp).where(*self.db._scope(), SignalRecord.id == signal_id)
        )
        if created is None:
            return False
        et_date = created.astimezone(ET_TZ).date()
        if et_date < datetime.now(UTC).astimezone(ET_TZ).date():
            return False
        queued = await session.scalar(
            select(WorkItemRecord.id).where(
                WorkItemRecord.scope == self.scope,
                WorkItemRecord.kind == WorkKind.NOTIFICATION,
                WorkItemRecord.dedup_key == f"auto-entry-digest/{et_date.isoformat()}",
            )
        )
        return queued is None

    async def entry_trigger(self, signal_id: int) -> EntryTrigger | None:
        """``entry_trigger_of`` in its own session, for a caller outside a workflow transaction."""
        async with self.db.session_factory() as session:
            return await self.entry_trigger_of(session, signal_id)

    async def enqueue_entry(
        self, request: OrderRequest, config: AppConfig, *, trigger: EntryTrigger = EntryTrigger.TAP
    ) -> tuple[WorkItem | None, str]:

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
            if await self.unresolved_cancellation(session):
                return None, "Unconfirmed entry cancellation remains; inspect broker evidence."
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
            try:
                risk = await self._entry_risk(session, config)
            except ValueError as exc:
                reason = f"Account risk unavailable: {exc}. No order submitted."
                session.add(
                    self.db._audit(
                        AuditEventType.ENTRY_ADMISSION_REJECTED,
                        request.signal_id,
                        {"reason": reason, "request": request.model_dump(mode="json")},
                    )
                )
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
            reason = reservation_rejection(
                request,
                [r.to_dict() for r in rows],
                config,
                current_drawdown_pct=float(risk.drawdown_pct) if risk else 0.0,
                current_equity=float(risk.equity) if risk else None,
            )
            if reason:
                session.add(
                    self.db._audit(
                        AuditEventType.ENTRY_ADMISSION_REJECTED,
                        request.signal_id,
                        {
                            "reason": reason,
                            "request": request.model_dump(mode="json"),
                            "risk_fingerprint": risk.fingerprint if risk else None,
                        },
                    )
                )
                return None, reason
            command_id = f"entry-{uuid4().hex}"
            request = request.model_copy(update={"client_order_id": command_id})
            now = datetime.now(UTC)
            payload = {**request.model_dump(mode="json"), "entry_trigger": str(trigger)}
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
                session,
                stream=f"entry/{command_id}",
                kind=EventKind.ENTRY_QUEUED,
                payload={**payload, "account_risk": risk.model_dump(mode="json") if risk else None},
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
        if signal.strategy == INSIDER_WATCH_STRATEGY_ID:
            # Not a registry alpha: its contract is the frozen entry and cube bracket, never the registry.
            return _watch_signal_rejection(signal)
        strategy = signal.strategy.lower()
        if not signal.alpha_version and not strategy.startswith("alpha_") and strategy not in CATALOG_STRATEGY_IDS:
            return None
        # Consistent lock order: trading admission, then alpha registry. Registry
        # changes use the alpha lock, so demotion cannot race submission commit.
        await self.lock(session, resource="alpha")
        registry = await session.get(AlphaProjectionRecord, (self.scope, "registry"))
        listed = json.loads(registry.payload) if registry else {}
        is_active = bool(signal.alpha_version) and signal.alpha_version in listed.get("active", [])
        is_probe = bool(signal.alpha_version) and signal.alpha_version in listed.get("probe", [])
        if not is_active and not is_probe:
            return "Alpha version is not active/qualified; request a fresh scan after qualification."
        assert signal.alpha_version is not None  # guaranteed by is_active/is_probe above
        row = await session.get(AlphaProjectionRecord, (self.scope, f"version/{signal.alpha_version}"))
        if row is None:
            return "Alpha version evidence is missing."
        if is_active:
            qualification = await session.get(
                AlphaProjectionRecord, (self.scope, f"qualification/{signal.alpha_version}")
            )
            if not qualification or json.loads(qualification.payload).get("policy") != asdict(ValidationPolicy()):
                return "Alpha qualification policy is obsolete; fresh research and qualification are required."
        else:
            # A probe is never authorized by qualification; only by live, current
            # enrolment. The probe policy is code-level and single-sourced: this
            # omits `policy=` so admission and the registry read the same default.
            blocked = await probe_block_reason(
                session, scope=self.scope, db=self.db, version_id=signal.alpha_version, now=datetime.now(UTC)
            )
            if blocked:
                return f"Paper probe blocked: {blocked}."
        definition = json.loads(row.payload)["definition"]
        if definition.get("kind") == CATALOG_KIND:
            if is_active:
                return "Catalog alphas run only as paper probes."
            return _catalog_signal_rejection(signal, definition)
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

    async def begin_submission(
        self,
        item: WorkItem,
        config: AppConfig,
        *,
        risk_fingerprint: str | None = None,
        broker_context: BrokerEntryContext | None = None,
    ) -> str | None:
        """Commit submission authority, or return a concrete refusal reason."""
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            halted = await session.get(SystemStateRecord, SystemStateKey.TRADING_HALTED)
            if halted and halted.value.lower() in ("true", "1", "yes"):
                return "Emergency trading halt active. No order submitted."
            if await self.unresolved_cancellation(session):
                return "Unconfirmed entry cancellation remains. No order submitted."
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
                return "Admission claim expired or changed. No order submitted; request fresh approval."
            signal = await session.scalar(
                select(SignalRecord).where(*self.db._scope(), SignalRecord.id == item.payload["signal_id"])
            )
            request = OrderRequest.model_validate(item.payload)
            if (
                signal is None
                or signal.status != SignalStatus.SUBMITTING
                or signal.contract != request.symbol
                or signal.direction != request.direction
                or signal.asset_class != request.asset_class
                or (signal.quantity, signal.entry_price, signal.stop_loss, signal.take_profit)
                != (request.quantity, request.entry_price, request.stop_loss, request.take_profit)
            ):
                return "Signal or active alpha authorization changed. No order submitted."
            # Hoisted out of the compound check above, whose other clauses are pure
            # comparisons, so the operator sees which alpha gate refused rather than
            # one message covering every clause. Same lock, same relative position.
            if reason := await self._alpha_entry_rejection(session, signal):
                return f"{reason} No order submitted."
            try:
                risk = await self._entry_risk(session, config)
            except ValueError as exc:
                return f"Account risk unavailable: {exc}. No order submitted."
            if risk and risk.fingerprint != risk_fingerprint:
                return "Account risk changed after preflight. No order submitted; request a fresh scan."
            reservations = [
                r.to_dict()
                for r in (
                    await session.scalars(
                        select(SignalRecord).where(
                            *self.db._scope(),
                            SignalRecord.status.in_((SignalStatus.SUBMITTING, SignalStatus.EXECUTED)),
                            SignalRecord.id != item.payload["signal_id"],
                        )
                    )
                ).all()
            ]
            if row.lease_until is None or row.lease_until <= datetime.now(UTC):
                return "Admission claim expired while validating account risk. No order submitted."
            if reason := authorization_expiry(row.created_at, signal.timestamp, config, now=datetime.now(UTC)):
                return reason
            capacity = None
            if risk and broker_context is None:
                return "Fresh broker capacity evidence is unavailable. No order submitted."
            if broker_context is not None:
                try:
                    capacity = assess_entry_capacity(request, reservations, broker_context, config, account_risk=risk)
                except (ValueError, TypeError, ArithmeticError) as exc:
                    return f"Entry capacity refused: {exc}. No order submitted."
            elif reason := reservation_rejection(request, reservations, config):
                return reason
            row.status, row.lease_until = WorkStatus.SUBMITTING, None
            evidence = {
                **item.payload,
                "account_risk": risk.model_dump(mode="json") if risk else None,
                "risk_fingerprint": risk_fingerprint,
                "drawdown_multiplier": drawdown_risk_factor(float(risk.drawdown_pct), config.sizing) if risk else 1.0,
                "broker_context": broker_context.model_dump(mode="json") if broker_context else None,
                "capacity": capacity.model_dump(mode="json") if capacity else None,
            }
            await self.append(session, stream=f"entry/{row.id}", kind=EventKind.ENTRY_SUBMITTING, payload=evidence)
            session.add(self.db._audit(AuditEventType.ENTRY_SUBMISSION, item.payload["signal_id"], evidence))
            return None

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
            previous = row.status
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
                await self.halt(session, f"Unconfirmed broker entry {item.id}; lookup recovery required before /resume")
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
            # An accepted auto entry is folded into the daily digest; a refusal, and the acceptance that
            # clears an UNKNOWN outcome (its halt was announced), keep their notice.
            folded = (
                row.status == WorkStatus.ACCEPTED
                and previous != WorkStatus.UNKNOWN
                and await self.folds_into_auto_digest(session, item.payload["signal_id"])
            )
            if not folded:
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

    async def auto_entry_digest_queued(self, et_date: date) -> bool:
        """Whether this date's digest is already queued: a plain read, without the trading workflow lock."""
        async with self.db.session_factory() as session:
            return bool(
                await session.scalar(
                    select(WorkItemRecord.id).where(
                        WorkItemRecord.scope == self.scope,
                        WorkItemRecord.kind == WorkKind.NOTIFICATION,
                        WorkItemRecord.dedup_key == f"auto-entry-digest/{et_date.isoformat()}",
                    )
                )
            )

    async def queue_auto_entry_digest(self, et_date: date, text: str) -> bool:
        """Queue the day's digest once: False (nothing queued) when this New York date's key already exists."""
        key = f"auto-entry-digest/{et_date.isoformat()}"
        async with self.db.session_factory() as session, session.begin():
            await self.lock(session)
            if await session.scalar(
                select(WorkItemRecord.id).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.NOTIFICATION,
                    WorkItemRecord.dedup_key == key,
                )
            ):
                return False
            await self.add_notification(session, key, NotificationKind.MESSAGE, {"text": text, "formatted": True})
            return True

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
                if current.status in BROKER_TERMINAL_STATUSES and Decimal(current.filled_quantity) == 0:
                    signal = await session.scalar(
                        select(SignalRecord).where(
                            *self.db._scope(),
                            SignalRecord.broker_order_id == observation.order_id,
                            SignalRecord.status == SignalStatus.EXECUTED,
                            SignalRecord.executed_at.is_(None),
                        )
                    )
                    if signal and not await self.unresolved_cancellation(session, symbol=signal.contract):
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
