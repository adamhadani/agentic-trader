"""Transactional incident projection and existing notification outbox integration."""

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import (
    ComponentTally,
    IncidentPhase,
    IncidentState,
    OpenIncident,
    OperationalNotice,
    QuietHoursSummary,
    WithheldReason,
    advance,
)
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind, WorkStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.models import DomainEventRecord, IncidentProjectionRecord, WorkItemRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


class OperationsStore:
    def __init__(self, store: WorkflowStore):
        self.store, self.scope = store, store.scope

    async def observe(
        self,
        component: str,
        *,
        ready: bool,
        observed_at: datetime,
        detail: str,
        policy: OperationsConfig,
        muted: bool = False,
        alert_since: datetime | None = None,
    ) -> OperationalNotice | None:
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            row = await session.get(IncidentProjectionRecord, (self.scope, component))
            if row and observed_at <= row.observed_at:
                return None
            previous = IncidentState.model_validate_json(row.payload) if row else IncidentState()
            delivery = await session.get(WorkItemRecord, previous.notification_id) if previous.notification_id else None
            state, kind, withheld = advance(
                previous,
                ready=ready,
                observed_at=observed_at,
                policy=policy,
                delivery_complete=bool(delivery and delivery.status == WorkStatus.DELIVERED),
                muted=muted,
                alert_since=alert_since,
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
                        "withheld": withheld.model_dump(mode="json") if withheld else None,
                    },
                )
                row.event_id = event.id
            row.payload, row.observed_at = state.model_dump_json(), observed_at
            return notice

    async def queue_quiet_summary(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        observed_at: datetime,
        details: Mapping[str, str],
        policy: OperationsConfig,
    ) -> QuietHoursSummary | None:
        """Queue the one morning summary of a quiet period; None when already queued or nothing to say."""
        key = f"operations-summary/{period_end.astimezone(ET_TZ).date().isoformat()}"
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            existing = await session.scalar(
                select(WorkItemRecord.id).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.NOTIFICATION,
                    WorkItemRecord.dedup_key == key,
                )
            )
            if existing:
                return None
            margin = timedelta(hours=1)
            events = await session.scalars(
                select(DomainEventRecord).where(
                    DomainEventRecord.scope == self.scope,
                    DomainEventRecord.stream >= "incident/",
                    DomainEventRecord.stream < "incident0",
                    DomainEventRecord.kind == EventKind.INCIDENT_CHANGED,
                    DomainEventRecord.recorded_at >= period_start - margin,
                    DomainEventRecord.recorded_at <= period_end + margin,
                )
            )
            counts: dict[str, list[int]] = {}
            for event in events:
                payload = json.loads(event.payload)
                withheld = payload.get("withheld")
                if not withheld or withheld.get("reason") != WithheldReason.QUIET_HOURS:
                    continue
                if not period_start <= datetime.fromisoformat(payload["observed_at"]) < period_end:
                    continue
                tally = counts.setdefault(payload["component"], [0, 0])
                if withheld.get("kind") == "opened":
                    tally[0] += 1
                elif withheld.get("kind") == "recovered":
                    tally[1] += 1
            tallies = [
                ComponentTally(component=name, opened=o, recovered=r)
                for name, (o, r) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
                if o + r
            ]
            open_incidents = []
            rows = await session.scalars(
                select(IncidentProjectionRecord).where(IncidentProjectionRecord.scope == self.scope)
            )
            for row in rows:
                state = IncidentState.model_validate_json(row.payload)
                if state.phase in (IncidentPhase.OPEN, IncidentPhase.RECOVERING) and state.failed_since:
                    open_incidents.append(
                        OpenIncident(
                            component=row.component,
                            phase=state.phase,
                            failed_since=state.failed_since,
                            detail=details.get(row.component, "")[:120],
                        )
                    )
            open_incidents.sort(key=lambda i: (i.component != "trading_halt", i.failed_since, i.component))
            if not tallies and not open_incidents:
                return None
            summary = QuietHoursSummary(
                period_start=period_start,
                period_end=period_end,
                observed_at=observed_at,
                failure_seconds=policy.failure_seconds,
                tallies=tallies,
                open_incidents=open_incidents,
            )
            await self.store.add_notification(
                session, key, NotificationKind.OPERATIONS_SUMMARY, summary.model_dump(mode="json")
            )
            return summary

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
