"""Account-bound activity projection; all changes replay from the shared journal."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from agentic_trader.accounting.risk import advance_risk_checkpoint
from agentic_trader.execution.durable import LEDGER_LOCK, EventKind
from agentic_trader.storage.models import ActivityProjectionRecord, DomainEventRecord, LedgerCheckpointRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


LEDGER_STREAM = "account/ledger"


class LedgerStore:
    def __init__(self, store: WorkflowStore):
        self.store, self.scope = store, store.scope

    async def begin(self, account_id: str | None = None) -> str:
        """Fence the entire refresh, including failures of the first remote read."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource=LEDGER_LOCK)
            row = await session.get(LedgerCheckpointRecord, self.scope)
            if row and row.account_id and account_id and row.account_id != account_id:
                raise ValueError("Broker account differs from the ledger binding")
            token = uuid4().hex
            if row is None:
                row = LedgerCheckpointRecord(scope=self.scope, account_id="", token=token, payload="{}")
                session.add(row)
            row.token = token
            if account_id and not row.account_id:
                row.account_id = account_id
                await self.store.append(
                    session, stream=LEDGER_STREAM, kind=EventKind.ACCOUNT_BOUND, payload={"account_id": account_id}
                )
            return token

    async def bind(self, token: str, account_id: str) -> None:
        if not account_id:
            raise ValueError("Missing broker account identity")
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource=LEDGER_LOCK)
            row = await session.get(LedgerCheckpointRecord, self.scope)
            if row is None or row.token != token:
                raise RuntimeError("Ledger refresh superseded by another importer")
            if row.account_id and row.account_id != account_id:
                raise ValueError("Broker account differs from the ledger binding")
            if not row.account_id:
                row.account_id = account_id
                await self.store.append(
                    session, stream=LEDGER_STREAM, kind=EventKind.ACCOUNT_BOUND, payload={"account_id": account_id}
                )

    async def commit(self, token: str, activities: list[dict[str, Any]], checkpoint: dict[str, Any]) -> bool:
        by_id = {a["id"]: a for a in activities}
        if len(by_id) != len(activities) or not all(by_id):
            raise ValueError("Duplicate/missing activity identity")
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource=LEDGER_LOCK)
            row = await session.get(LedgerCheckpointRecord, self.scope)
            if row is None or row.token != token:
                return False
            if not row.account_id:
                raise ValueError("Ledger requires a bound broker account")
            current = {
                a.activity_id: a
                for a in await session.scalars(
                    select(ActivityProjectionRecord).where(ActivityProjectionRecord.scope == self.scope)
                )
            }
            for activity_id, payload in by_id.items():
                encoded = encode(payload)
                previous = current.get(activity_id)
                if previous and previous.payload == encoded:
                    continue
                event = await self.store.append(
                    session, stream=f"activity/{activity_id}", kind=EventKind.ACTIVITY_OBSERVED, payload=payload
                )
                if previous:
                    previous.payload, previous.event_id = encoded, event.id
                else:
                    session.add(
                        ActivityProjectionRecord(
                            scope=self.scope, activity_id=activity_id, event_id=event.id, payload=encoded
                        )
                    )
            for activity_id in current.keys() - by_id.keys():
                await self.store.append(
                    session,
                    stream=f"activity/{activity_id}",
                    kind=EventKind.ACTIVITY_RETRACTED,
                    payload={"id": activity_id},
                )
                await session.delete(current[activity_id])
            payload = {**checkpoint, "error": None, "checked_at": datetime.now(UTC).isoformat()}
            payload.update(
                advance_risk_checkpoint(json.loads(row.payload), payload, activities, account_id=row.account_id)
            )
            await self.store.append(session, stream=LEDGER_STREAM, kind=EventKind.LEDGER_CHECKPOINT, payload=payload)
            row.payload = encode(payload)
            return True

    async def fail(self, token: str, error: str) -> None:
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource=LEDGER_LOCK)
            row = await session.get(LedgerCheckpointRecord, self.scope)
            if row and row.token == token:
                payload = {**json.loads(row.payload), "error": error, "checked_at": datetime.now(UTC).isoformat()}
                await self.store.append(
                    session, stream=LEDGER_STREAM, kind=EventKind.LEDGER_CHECKPOINT, payload=payload
                )
                row.payload = encode(payload)

    async def status(self) -> dict[str, Any]:
        async with self.store.db.session_factory() as session:
            row = await session.get(LedgerCheckpointRecord, self.scope)
            return json.loads(row.payload) if row else {}

    async def activities(self) -> list[dict[str, Any]]:
        async with self.store.db.session_factory() as session:
            rows = await session.scalars(
                select(ActivityProjectionRecord)
                .where(ActivityProjectionRecord.scope == self.scope)
                .order_by(ActivityProjectionRecord.activity_id)
            )
            return [json.loads(r.payload) for r in rows]

    async def rebuild(self) -> int:
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource=LEDGER_LOCK)
            events = list(
                await session.scalars(
                    select(DomainEventRecord)
                    .where(
                        DomainEventRecord.scope == self.scope,
                        DomainEventRecord.kind.in_(
                            (
                                EventKind.ACCOUNT_BOUND,
                                EventKind.ACTIVITY_OBSERVED,
                                EventKind.ACTIVITY_RETRACTED,
                                EventKind.LEDGER_CHECKPOINT,
                            )
                        ),
                    )
                    .order_by(DomainEventRecord.id)
                )
            )
            await session.execute(delete(ActivityProjectionRecord).where(ActivityProjectionRecord.scope == self.scope))
            await session.execute(delete(LedgerCheckpointRecord).where(LedgerCheckpointRecord.scope == self.scope))
            views: dict[str, tuple[int, str]] = {}
            account_id, checkpoint = None, "{}"
            for event in events:
                if event.schema_version != 1:
                    raise ValueError("Unsupported ledger event schema")
                payload = json.loads(event.payload)
                if event.kind == EventKind.ACCOUNT_BOUND:
                    account_id = payload["account_id"]
                elif event.kind == EventKind.ACTIVITY_OBSERVED:
                    views[payload["id"]] = (event.id, event.payload)
                elif event.kind == EventKind.ACTIVITY_RETRACTED:
                    views.pop(payload["id"], None)
                else:
                    checkpoint = event.payload
            if events:
                session.add(
                    LedgerCheckpointRecord(
                        scope=self.scope, account_id=account_id or "", token=uuid4().hex, payload=checkpoint
                    )
                )
            for activity_id, (event_id, payload_text) in views.items():
                session.add(
                    ActivityProjectionRecord(
                        scope=self.scope, activity_id=activity_id, event_id=event_id, payload=payload_text
                    )
                )
            return len(events)
