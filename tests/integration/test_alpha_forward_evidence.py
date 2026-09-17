"""Read-only evidence survives SQL journal replay and excludes aliases/late work."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.evidence import load_forward_evidence
from agentic_trader.research.alpha.models import AlphaDefinition, DecisionStatus
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])
@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def test_forward_report_uses_canonical_outcomes_and_is_read_only(temp_db, request, backend):
    if backend == "postgres":
        db = SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    else:
        db = temp_db
        await db.init_db()
    repo = AlphaRepository(db.workflows)
    now = datetime.now(UTC)
    definition = AlphaDefinition(
        "control",
        "Control",
        "returns",
        timeframe="15m",
        eligible_symbols=("SPY",),
        semantics_version=3,
        data_feed="alpaca:sip",
        clock=SessionClockPolicy(),
    )
    try:
        await repo.register(definition, actor="fixture")
        await repo.set_shadow(definition.version_id, actor="fixture", expected_generation=0)
        cursor = {"generation": 1, "checked_at": now.isoformat(), "enrolled_at": now.isoformat(), "calendar": {}}
        claims = [
            {
                "decision_id": f"{i:064x}",
                "claim_id": str(i),
                "version_id": definition.version_id,
                "symbol": "SPY",
                "registry_generation": 1,
                "status": status,
                "closed_at": (now - timedelta(minutes=i + 10)).isoformat(),
                "claimed_at": (now - timedelta(minutes=5)).isoformat(),
                "expires_at": (now - timedelta(minutes=1)).isoformat(),
            }
            for i, status in enumerate((DecisionStatus.MISSED, DecisionStatus.CLAIMED))
        ]
        await repo.plan_session_decisions(f"session-cursor/{definition.version_id}/SPY", None, cursor, claims)
        await repo.expire_session_decisions(now)
        await repo.finish_session_decision(
            claims[1],
            {
                "status": DecisionStatus.SCORED,
                "forecast": {"valid": False, "score": 99, "decision": 1},
            },
            clock=lambda: now,
        )
        async with db.session_factory() as session:
            count_before = await session.scalar(select(func.count()).select_from(DomainEventRecord))
        snapshot, report = await load_forward_evidence(repo, now=now)
        row = report["candidates"][0]
        assert snapshot.generation == 1 and report["rows_loaded"] == 2
        assert row["recorded_decisions"] == 2
        assert row["counts"]["missed"] == row["counts"]["interrupted"] == 1
        assert row["scores"]["count"] == 0 and row["recorded_score_fraction"] == 0
        _, limited = await load_forward_evidence(repo, now=now, limit=1)
        assert limited["truncated"] and limited["candidates"][0]["recorded_score_fraction"] is None
        async with db.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(DomainEventRecord)) == count_before
        await repo.rebuild()
        assert (await load_forward_evidence(repo, now=now))[1] == report
        assert await repo.get(f"shadow/{definition.version_id}") is None
    finally:
        await db.engine.dispose()
