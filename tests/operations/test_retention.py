from datetime import UTC, datetime, timedelta

from agentic_trader.config import OperationsConfig
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.maintenance import RetentionService
from agentic_trader.storage.models import DomainEventRecord
from agentic_trader.storage.workflow import encode


async def test_retention_keeps_failures_recoveries_latest_and_financial_evidence(temp_db):
    now = datetime.now(UTC)
    async with temp_db.session_factory() as session, session.begin():
        for i, success in enumerate([True, True, True, False, True, True, True]):
            session.add(
                DomainEventRecord(
                    scope=temp_db.workflows.scope,
                    stream="health/old/worker",
                    kind=EventKind.HEALTH_OBSERVED,
                    event_key=str(i),
                    payload=encode({"success": success, "detail": ""}),
                    recorded_at=now - timedelta(days=40) + timedelta(seconds=i),
                )
            )
        session.add(
            DomainEventRecord(
                scope=temp_db.workflows.scope,
                stream="order/exact",
                kind=EventKind.ORDER_OBSERVED,
                event_key="order",
                payload="{}",
                recorded_at=now - timedelta(days=40),
            )
        )
    service = RetentionService(temp_db.workflows, OperationsConfig())
    preview = await service.sweep(now=now)
    assert preview["eligible"] == 3 and not preview["applied"]
    assert len(await temp_db.workflows.events("health/old/worker")) == 7
    applied = await service.sweep(now=now, apply=True)
    assert applied["deleted"] == 3
    assert len(await temp_db.workflows.events("health/old/worker")) == 4
    assert len(await temp_db.workflows.events("order/exact")) == 1
    assert (await service.sweep(now=now, apply=True))["deleted"] == 0


async def test_retention_batch_and_unknown_schema_boundary(temp_db):
    now = datetime.now(UTC)
    async with temp_db.session_factory() as session, session.begin():
        for i, version in enumerate((1, 2, 1, 1, 1, 1)):
            session.add(
                DomainEventRecord(
                    scope=temp_db.workflows.scope,
                    stream="health/old/worker",
                    kind=EventKind.HEALTH_OBSERVED,
                    schema_version=version,
                    event_key=str(i),
                    payload=encode({"success": True, "detail": ""}),
                    recorded_at=now - timedelta(days=40),
                )
            )
    service = RetentionService(temp_db.workflows, OperationsConfig(retention_batch_size=1))
    result = await service.sweep(apply=True, now=now)
    assert result["eligible"] == 2 and result["deleted"] == 1
    assert len(await temp_db.workflows.events("health/old/worker")) == 5
    assert (await service.sweep(apply=True, now=now, only_if_due=True))["deferred"]
