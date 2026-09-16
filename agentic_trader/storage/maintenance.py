"""Bounded compaction of redundant healthy heartbeats; financial evidence stays."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from agentic_trader.config import OperationsConfig
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.models import DomainEventRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


MAINTENANCE_STREAM = "maintenance/health"


class RetentionService:
    def __init__(self, store: WorkflowStore, policy: OperationsConfig):
        self.store, self.policy = store, policy

    async def sweep(
        self, *, apply: bool = False, now: datetime | None = None, only_if_due: bool = False
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=self.policy.healthy_observation_days)
        result = {"applied": apply, "eligible": 0, "deleted": 0, "cutoff": cutoff.isoformat(), "deferred": False}
        async with self.store.db.session_factory() as session, session.begin():
            # Separate resource lock: maintenance cannot hold entry admission's mutex.
            await self.store.lock(session, resource="retention")
            if only_if_due:
                last = await session.scalar(
                    select(DomainEventRecord.recorded_at)
                    .where(
                        DomainEventRecord.scope == self.store.scope,
                        DomainEventRecord.kind == EventKind.HEALTH_COMPACTED,
                    )
                    .order_by(DomainEventRecord.id.desc())
                    .limit(1)
                )
                if last and (now - last).total_seconds() < self.policy.retention_interval_seconds:
                    return {**result, "deferred": True}
            observations = (
                select(
                    DomainEventRecord.id,
                    DomainEventRecord.recorded_at,
                    DomainEventRecord.payload,
                    DomainEventRecord.schema_version,
                    func.lag(DomainEventRecord.schema_version)
                    .over(partition_by=DomainEventRecord.stream, order_by=DomainEventRecord.id)
                    .label("previous_version"),
                    func.lag(DomainEventRecord.payload)
                    .over(partition_by=DomainEventRecord.stream, order_by=DomainEventRecord.id)
                    .label("previous"),
                    func.lead(DomainEventRecord.id)
                    .over(partition_by=DomainEventRecord.stream, order_by=DomainEventRecord.id)
                    .label("next_id"),
                )
                .where(
                    DomainEventRecord.scope == self.store.scope,
                    DomainEventRecord.kind == EventKind.HEALTH_OBSERVED,
                )
                .subquery()
            )
            healthy = encode({"success": True, "detail": ""})
            candidates = select(observations.c.id).where(
                observations.c.schema_version == 1,
                observations.c.previous_version == 1,
                observations.c.recorded_at < cutoff,
                observations.c.payload == healthy,
                observations.c.previous == healthy,
                observations.c.next_id.is_not(None),
            )
            result["eligible"] = int(await session.scalar(select(func.count()).select_from(candidates.subquery())) or 0)
            if apply:
                ids = list(
                    await session.scalars(
                        candidates.order_by(observations.c.id).limit(self.policy.retention_batch_size)
                    )
                )
                if ids:
                    await session.execute(
                        delete(DomainEventRecord).where(
                            DomainEventRecord.scope == self.store.scope, DomainEventRecord.id.in_(ids)
                        )
                    )
                result["deleted"] = len(ids)
                event = await self.store.append(
                    session,
                    stream=MAINTENANCE_STREAM,
                    kind=EventKind.HEALTH_COMPACTED,
                    payload={**result, "first_id": min(ids) if ids else None, "last_id": max(ids) if ids else None},
                )
                event.recorded_at = now
            return result
