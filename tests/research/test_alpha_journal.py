import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import AlphaProjectionRecord


@pytest.fixture
def definition():
    return AlphaDefinition("alpha_journal", "Journal", "delta(close,3)", timeframe="1d", eligible_symbols=("SPY",))


@pytest.fixture
async def repository(temp_db):

    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def test_unqualified_version_cannot_be_promoted(repository, definition):
    await repository.register(definition, actor="test")
    with pytest.raises(ValueError, match="qualification"):
        await repository.promote(definition.version_id, actor="test", expected_generation=0)
    assert not (await repository.snapshot()).active


async def test_concurrent_registry_changes_use_compare_and_swap(repository, definition):
    # Imported history is preserved as shadow evidence, never treated as qualified.
    await repository.register(definition, actor="test")
    other = replace(definition, alpha_id="alpha_other")
    await repository.register(other, actor="test")
    results = await asyncio.gather(
        repository.set_shadow(definition.version_id, actor="test", expected_generation=0),
        repository.set_shadow(other.version_id, actor="test", expected_generation=0),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert (await repository.snapshot()).generation == 1
    loser = other if results[1].__class__ is ValueError else definition
    await repository.set_shadow(loser.version_id, actor="test", expected_generation=1)
    snapshot = await repository.snapshot()
    assert len(snapshot.shadow) == 2
    assert not snapshot.active


async def test_replay_restores_registry_without_mutations(repository, definition):

    await repository.register(definition, actor="test")
    await repository.set_shadow(definition.version_id, actor="test", expected_generation=0)
    expected = await repository.snapshot()
    async with repository.store.db.session_factory() as session, session.begin():
        await session.execute(delete(AlphaProjectionRecord))
    await repository.rebuild()
    actual = await repository.snapshot()
    assert actual == expected
    assert len(await repository.store.events()) == 2


async def test_holdout_consumption_survives_failure_and_restart(repository, definition):
    await repository.register(definition, actor="test")
    run = {
        "trial_count": 1,
        "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    manifest = {
        "symbol": "SPY",
        "timeframe": "1d",
        "feed": "test",
        "adjustment": "raw",
        "start": "2020-01-01",
        "holdout_start": "2025-01-01",
        "end": "2026-01-01",
        "content_hash": "abc",
    }
    await repository.record_run("run-1", run, manifest)
    await repository.begin_holdout("run-1", definition.version_id)
    with pytest.raises(ValueError, match="consumed"):
        await repository.begin_holdout("run-1", definition.version_id)
    with pytest.raises(ValueError, match="immutable"):
        await repository.record_run("run-1", {**run, "trial_count": 2}, manifest)


async def test_signal_attribution_and_version_aware_dedup(repository, definition):
    db = repository.store.db
    await db.record_signal(
        "SPY",
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        2,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
        decision_provenance={"candle_timestamp": "2026-09-15T00:00Z", "alpha_score": 2},
    )
    assert await db.is_duplicate_recent("SPY", definition.alpha_id, timeframe="1d", alpha_version=definition.version_id)
    assert not await db.is_duplicate_recent(
        "SPY", definition.alpha_id, timeframe="4h", alpha_version=definition.version_id
    )
    assert not await db.is_duplicate_recent("SPY", definition.alpha_id, timeframe="1d", alpha_version="different")


async def test_overlapping_holdout_cannot_be_reused_with_new_run_or_end_date(repository, definition):
    await repository.register(definition, actor="test")
    run = {
        "trial_count": 1,
        "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    manifest = {
        "symbol": "SPY",
        "timeframe": "1d",
        "feed": "test",
        "adjustment": "raw",
        "start": "2020-01-01",
        "holdout_start": "2025-01-01",
        "end": "2026-01-01",
        "content_hash": "abc",
    }
    await repository.record_run("one", run, manifest)
    await repository.begin_holdout("one", definition.version_id)
    await repository.record_run("two", run, {**manifest, "end": "2026-01-02"})
    with pytest.raises(ValueError, match="consumed"):
        await repository.begin_holdout("two", definition.version_id)


async def test_promotion_requires_decisions_and_frozen_incumbents_then_replays(repository, definition):
    definition = replace(definition, data_feed="alpaca:sip")
    await repository.register(definition, actor="test")
    await repository.set_shadow(definition.version_id, actor="test", expected_generation=0)
    run = {
        "trial_count": 1,
        "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    manifest = {
        "symbol": "SPY",
        "timeframe": "1d",
        "feed": "alpaca:sip",
        "adjustment": "raw",
        "holdout_start": "2025-01-01",
        "end": "2025-06-01",
        "incumbents": [],
    }
    await repository.record_run("qualified", run, manifest)
    await repository.begin_holdout("qualified", definition.version_id)
    # This fixture isolates registry authorization; statistical qualification is
    # exercised separately through the real simulation integration path.
    await repository.record_qualification(
        "qualified",
        definition.version_id,
        {"qualified": True, "reasons": [], "eligible_symbols": ["SPY"], "manifest": manifest},
    )
    for day in range(20):
        timestamp = (datetime.now(UTC) - timedelta(days=20 - day)).isoformat()
        await repository.record_forecast(
            str(day),
            {
                "version_id": definition.version_id,
                "valid": True,
                "symbol": "SPY",
                "observed_at": timestamp,
                "completed_at": timestamp,
                "decision": 0,
            },
        )
    with pytest.raises(ValueError, match="decisions"):
        await repository.promote(definition.version_id, actor="test", expected_generation=1)
    for decision in range(10):
        await repository.record_forecast(
            f"decision-{decision}",
            {
                "version_id": definition.version_id,
                "valid": True,
                "symbol": "SPY",
                "observed_at": timestamp,
                "completed_at": timestamp,
                "decision": 1,
            },
        )
    await repository.promote(definition.version_id, actor="test", expected_generation=1)
    snapshot = await repository.snapshot()
    assert snapshot.active == (definition,)
    assert not snapshot.shadow
    await repository.rebuild()
    assert await repository.snapshot() == snapshot


async def test_runtime_acknowledgment_detects_pending_registry_and_restart(repository, definition):
    snapshot = await repository.snapshot()
    assert not (await repository.status(run_id="first"))["ready"]
    await repository.acknowledge(snapshot, run_id="first")
    assert (await repository.status(run_id="first"))["ready"]
    assert not (await repository.status(run_id="second"))["ready"]
    await repository.register(definition, actor="test")
    await repository.set_shadow(definition.version_id, actor="test", expected_generation=0)
    assert not (await repository.status(run_id="first"))["ready"]
    await repository.acknowledge(await repository.snapshot(), run_id="first")
    assert (await repository.status(run_id="first"))["ready"]


async def test_holdout_cannot_be_recycled_by_changing_feed_or_timeframe(repository, definition):
    await repository.register(definition, actor="test")
    run = {
        "trial_count": 1,
        "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    manifest = {
        "symbol": "SPY",
        "timeframe": "1d",
        "feed": "yfinance",
        "adjustment": "raw",
        "holdout_start": "2025-01-01",
        "end": "2025-06-01",
    }
    await repository.record_run("original", run, manifest)
    await repository.begin_holdout("original", definition.version_id)
    await repository.record_run("renamed-feed", run, {**manifest, "feed": "alpaca:sip", "timeframe": "4h"})
    with pytest.raises(ValueError, match="consumed"):
        await repository.begin_holdout("renamed-feed", definition.version_id)


async def test_external_diagnostic_evidence_is_counted_and_excluded_idempotently(repository, definition):
    args = {
        "symbol": "SPY",
        "start": "2025-01-01T00:00:00+00:00",
        "end": "2025-06-01T00:00:00+00:00",
        "trials": 3,
        "reason": "diagnostic review",
        "actor": "test",
    }
    await repository.exclude_observed_interval(**args)
    await repository.exclude_observed_interval(**args)
    assert (await repository.get("family/all"))["trial_count"] == 3
    await repository.register(definition, actor="test")
    run = {
        "trial_count": 1,
        "trials": [{"definition": definition.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    manifest = {
        "symbol": "SPY",
        "timeframe": "1d",
        "feed": "alpaca:sip",
        "adjustment": "raw",
        "holdout_start": args["start"],
        "end": args["end"],
    }
    await repository.record_run("next", run, manifest)
    with pytest.raises(ValueError, match="consumed"):
        await repository.begin_holdout("next", definition.version_id)


async def test_crashed_research_budget_cannot_disappear_from_family_count(repository):
    await repository.reserve_run("crashed", symbol="SPY", timeframe="1d", trials=16)
    await repository.reserve_run("crashed", symbol="SPY", timeframe="1d", trials=16)
    assert (await repository.get("family/all"))["trial_count"] == 16
    await repository.record_failure("crashed", symbol="SPY", timeframe="1d", error="interrupted")
    assert (await repository.get("family/all"))["trial_count"] == 16
    await repository.reserve_run("completed", symbol="SPY", timeframe="1d", trials=16)
    await repository.record_run("completed", {"trial_count": 2, "trials": []}, {"symbol": "SPY", "timeframe": "1d"})
    assert (await repository.get("family/all"))["trial_count"] == 32  # no double count or refund
