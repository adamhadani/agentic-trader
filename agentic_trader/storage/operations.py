"""Transactional incident projection and existing notification outbox integration."""

import json
from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_trader.config import TRADING_HALT_CHECK, OperationsConfig
from agentic_trader.diagnostics.incidents import (
    ComponentTally,
    IncidentPhase,
    IncidentState,
    NoticeKind,
    OpenIncident,
    OperationalNotice,
    QuietHoursSummary,
    WithheldReason,
    advance,
)
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.models import DomainEventRecord, IncidentProjectionRecord, WorkItemRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


MAX_SUMMARY_DETAIL = 120


def summary_key(window_start: datetime) -> str:
    """Event and outbox key of the summary decided at the start of an alert window."""
    return f"operations-summary/{window_start.astimezone(ET_TZ).date().isoformat()}"


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
        ready_now: Collection[str],
        policy: OperationsConfig,
    ) -> QuietHoursSummary | None:
        """Queue the one morning summary of a quiet period; None when already queued or nothing to say."""
        key = summary_key(period_end)
        # The first pass of each alert window decides once; a decided window costs one indexed read.
        async with self.store.db.session_factory() as session:
            if await self._summary_decision(session, key) is not None:
                return None
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session)
            if await self._summary_decision(session, key) is not None:
                return None
            # Every component that ever journaled an incident has a projection row, so its streams bound the read.
            projections = list(
                await session.scalars(
                    select(IncidentProjectionRecord).where(IncidentProjectionRecord.scope == self.scope)
                )
            )
            counts: dict[str, list[int]] = {}
            if projections:
                margin = timedelta(hours=1)
                events = await session.scalars(
                    select(DomainEventRecord).where(
                        DomainEventRecord.scope == self.scope,
                        DomainEventRecord.stream.in_([f"incident/{row.component}" for row in projections]),
                        DomainEventRecord.kind == EventKind.INCIDENT_CHANGED,
                        DomainEventRecord.recorded_at >= period_start - margin,
                        DomainEventRecord.recorded_at <= period_end + margin,
                    )
                )
                for event in events:
                    payload = json.loads(event.payload)
                    withheld = payload.get("withheld")
                    if not withheld or withheld.get("reason") != WithheldReason.QUIET_HOURS:
                        continue
                    if not period_start <= datetime.fromisoformat(payload["observed_at"]) < period_end:
                        continue
                    tally = counts.setdefault(payload["component"], [0, 0])
                    if withheld.get("kind") == NoticeKind.OPENED:
                        tally[0] += 1
                    elif withheld.get("kind") == NoticeKind.RECOVERED:
                        tally[1] += 1
            tallies = [
                ComponentTally(component=name, opened=o, recovered=r)
                for name, (o, r) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
                if o + r
            ]
            open_incidents = []
            for row in projections:
                state = IncidentState.model_validate_json(row.payload)
                if state.phase in (IncidentPhase.OPEN, IncidentPhase.RECOVERING) and state.failed_since:
                    open_incidents.append(
                        OpenIncident(
                            component=row.component,
                            phase=state.phase,
                            failed_since=state.failed_since,
                            detail=details.get(row.component, "")[:MAX_SUMMARY_DETAIL],
                            ready_now=row.component in ready_now,
                        )
                    )
            open_incidents.sort(key=lambda i: (i.component != TRADING_HALT_CHECK, i.failed_since, i.component))
            summary = None
            if tallies or open_incidents:
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
            await self.store.append(
                session,
                stream="operations/summary",
                kind=EventKind.OPERATIONS_SUMMARY,
                key=key,
                payload={
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "observed_at": observed_at.isoformat(),
                    "queued": summary is not None,
                },
            )
            return summary

    async def summary_decided_at(self, window_start: datetime) -> datetime | None:
        """The pass time that decided this alert window's summary: the window's first pass, or None if undecided."""
        async with self.store.db.session_factory() as session:
            decision = await self._summary_decision(session, summary_key(window_start))
        return datetime.fromisoformat(json.loads(decision.payload)["observed_at"]) if decision else None

    async def _summary_decision(self, session: AsyncSession, key: str) -> DomainEventRecord | None:
        return await session.scalar(
            select(DomainEventRecord).where(DomainEventRecord.scope == self.scope, DomainEventRecord.event_key == key)
        )

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
