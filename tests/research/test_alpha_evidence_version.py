"""Changed statistical semantics cannot reuse old qualifications or variance samples."""

from dataclasses import asdict

import pytest

from agentic_trader.execution.durable import EventKind
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository


@pytest.fixture
async def research(temp_db):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    definition = AlphaDefinition("clock", "Clock", "close", timeframe="1d", eligible_symbols=("SPY",))
    await repo.register(definition, actor="fixture")
    manifest = {"symbol": "SPY", "timeframe": "1d", "holdout_start": "2024-01-01", "end": "2025-01-01"}
    run = {
        "trial_count": 2,
        "policy": asdict(ValidationPolicy()),
        "trials": [
            {
                "definition": definition.to_dict(),
                "status": "evaluated",
                "candidate": {"metrics": {"per_bar_sharpe": value}},
            }
            for value in (0.1, 0.3)
        ],
    }
    yield repo, definition, run, manifest
    await temp_db.engine.dispose()


@pytest.mark.parametrize("stage", ["record", "consume"])
async def test_old_clock_cannot_be_recorded_as_current_or_consume_holdout(research, stage):
    repo, definition, run, manifest = research
    old = {**run, "policy": {}}
    if stage == "record":
        await repo.reserve_run("old", symbol="SPY", timeframe="1d", trials=2)
        with pytest.raises(ValueError, match="policy"):
            await repo.record_run("old", old, manifest)
        assert (await repo.get("family/all"))["trial_count"] == 2
    else:
        # Represent a preserved historical journal row; no runtime compatibility fallback.
        async with repo.store.db.session_factory() as session, session.begin():
            await repo._append(
                session, "run/old", {"run": old, "manifest": manifest}, EventKind.ALPHA_RESEARCH, "fixture"
            )
        with pytest.raises(ValueError, match="policy"):
            await repo.begin_holdout("old", definition.version_id)
        assert await repo.get("consumption/old") is None


async def test_trial_count_spans_versions_but_variance_uses_current_clock_and_replays(research):
    repo, definition, run, manifest = research
    async with repo.store.db.session_factory() as session, session.begin():
        await repo._append(session, "family/all", {"trial_count": 100}, EventKind.ALPHA_RESEARCH, "fixture")
        await repo._append(
            session, "family/1d", {"trial_count": 100, "sharpes": [90, -90]}, EventKind.ALPHA_RESEARCH, "fixture"
        )
    await repo.reserve_run("new", symbol="SPY", timeframe="1d", trials=2)
    await repo.record_run("new", run, manifest)
    result = await repo.begin_holdout("new", definition.version_id)
    assert result["trial_count"] == 102
    assert result["trial_variance"] == pytest.approx(0.02)
    assert result["variance_observations"] == 2
    assert result["return_timeline"] == ValidationPolicy().return_timeline
    old_family = await repo.get("family/1d")
    await repo.rebuild()
    assert await repo.get("family/1d") == old_family
    assert await repo.get("consumption/new") == result


async def test_unknown_variance_is_not_fabricated_as_zero(research):
    repo, definition, run, manifest = research
    await repo.reserve_run("unobserved", symbol="SPY", timeframe="1d", trials=100)
    run = {**run, "trial_count": 1, "trials": run["trials"][:1]}
    await repo.record_run("single", run, manifest)
    result = await repo.begin_holdout("single", definition.version_id)
    assert result["trial_count"] == 101
    assert result["trial_variance"] is None
    assert result["variance_observations"] == 1
