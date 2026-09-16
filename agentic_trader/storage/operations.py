"""Transactional incident projection and existing notification outbox integration."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import IncidentState, OperationalNotice, advance
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkStatus
from agentic_trader.storage.models import DomainEventRecord, IncidentProjectionRecord, WorkItemRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


class OperationsStore:
    def __init__(self, store: WorkflowStore):
        self.store, self.scope = store, store.scope

    async def observe(
        self, component: str, *, ready: bool, observed_at: datetime, detail: str, policy: OperationsConfig
    ) -> OperationalNotice | None:
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            row = await session.get(IncidentProjectionRecord, (self.scope, component))
            if row and observed_at <= row.observed_at:
                return None
            previous = IncidentState.model_validate_json(row.payload) if row else IncidentState()
            delivery = await session.get(WorkItemRecord, previous.notification_id) if previous.notification_id else None
            state, kind = advance(
                previous,
                ready=ready,
                observed_at=observed_at,
                policy=policy,
                delivery_complete=bool(delivery and delivery.status == WorkStatus.DELIVERED),
            )
            notice = None
            if kind:
                assert state.incident_id and state.failed_since
                notice = OperationalNotice(
                    component=component,
                    incident_id=state.incident_id,
                    kind=kind,
                    observed_at=observed_at,
                    failed_since=state.failed_since,
                    detail=detail,
                )
                if policy.notifications_enabled:
                    state.notification_id = await self.store.add_notification(
                        session,
                        f"incident/{state.incident_id}/{kind}/{observed_at.isoformat()}",
                        NotificationKind.OPERATIONAL,
                        notice.model_dump(mode="json"),
                    )
            if row is None:
                row = IncidentProjectionRecord(
                    scope=self.scope, component=component, observed_at=observed_at, payload="{}"
                )
                session.add(row)
            if state != previous:
                event = await self.store.append(
                    session,
                    stream=f"incident/{component}",
                    kind=EventKind.INCIDENT_CHANGED,
                    payload={
                        "component": component,
                        "observed_at": observed_at.isoformat(),
                        "state": state.model_dump(mode="json"),
                        "notice": notice.model_dump(mode="json") if notice else None,
                    },
                )
                row.event_id = event.id
            row.payload, row.observed_at = state.model_dump_json(), observed_at
            return notice

    async def incidents(self) -> list[dict[str, Any]]:
        async with self.store.db.session_factory() as session:
            rows = await session.scalars(
                select(IncidentProjectionRecord)
                .where(IncidentProjectionRecord.scope == self.scope)
                .order_by(IncidentProjectionRecord.component)
            )
            return [
                {"component": r.component, "observed_at": r.observed_at.isoformat(), **json.loads(r.payload)}
                for r in rows
            ]

    async def rebuild(self) -> int:
        """Replay lifecycle only; retain newer ephemeral observation watermarks."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            rows = {
                r.component: r
                for r in await session.scalars(
                    select(IncidentProjectionRecord).where(IncidentProjectionRecord.scope == self.scope)
                )
            }
            events = list(
                await session.scalars(
                    select(DomainEventRecord)
                    .where(DomainEventRecord.scope == self.scope, DomainEventRecord.kind == EventKind.INCIDENT_CHANGED)
                    .order_by(DomainEventRecord.id)
                )
            )
            for existing in rows.values():
                existing.payload, existing.event_id = IncidentState().model_dump_json(), None
            for event in events:
                if event.schema_version != 1:
                    raise ValueError("Unsupported incident event schema")
                payload = json.loads(event.payload)
                component = payload["component"]
                observed_at = datetime.fromisoformat(payload["observed_at"])
                row = rows.get(component)
                if row is None:
                    row = IncidentProjectionRecord(
                        scope=self.scope, component=component, observed_at=observed_at, payload="{}"
                    )
                    session.add(row)
                    rows[component] = row
                row.payload, row.event_id = encode(payload["state"]), event.id
                row.observed_at = max(row.observed_at, observed_at)
            return len(events)
