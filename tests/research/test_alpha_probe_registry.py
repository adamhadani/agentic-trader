from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from agentic_trader.config import AppConfig
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.probe import policy_document
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import SignalRecord, WorkItemRecord


NOW = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)


def criterion(value, status="pass"):
    return {"value": value, "status": status}


def qualification(**overrides):
    criteria = {
        "holdout_available": criterion(True),
        "holdout_sharpe": criterion(1.2, "fail"),
        "holdout_trade_count": criterion(8, "fail"),
        "cost_stress": criterion(3.0),
        "deployment_data_contract": criterion({}),
        "intraday_session_execution_unverified": criterion("1d"),
        "recursive_feature_requires_shared_initialization": criterion(False),
    }
    criteria.update(overrides)
    return {"qualified": False, "reasons": ["holdout_dsr"], "policy": asdict(ValidationPolicy()), "criteria": criteria}


def paper_database(tmp_path, *, paper=True, mode="alpaca"):
    config = AppConfig(execution_mode=mode, alpaca_paper=paper)
    return SignalDatabase(db_path=str(tmp_path / "probe.db"), config=config)


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


@pytest.fixture
def repository(db):
    return AlphaRepository(db.workflows)


def make_definition(alpha_id="alpha_probe", symbol="AAPL", **changes):
    defaults = {"timeframe": "1d", "eligible_symbols": (symbol,), "data_feed": "alpaca:iex"}
    defaults.update(changes)
    return AlphaDefinition(alpha_id, "Probe", "delta(close,3)", **defaults)


async def seed(repository, definition, decision=None):
    await repository.register(definition, actor="test")
    async with repository.store.db.session_factory() as session, session.begin():
        await repository.store.lock(session, resource="alpha")
        await repository._append(
            session,
            f"qualification/{definition.version_id}",
            decision or qualification(),
            EventKind.ALPHA_RESEARCH,
            "fixture",
        )


async def close_trade(db, definition, pnl, *, risk=100.0, when=NOW):
    sid = await db.record_signal(
        definition.eligible_symbols[0],
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        risk,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        row.status, row.realized_pnl, row.exit_timestamp = "CLOSED_LOSS" if pnl < 0 else "CLOSED_WIN", pnl, when
    return sid


async def test_enrolment_journals_state_and_snapshot_exposes_the_probe(repository):
    definition = make_definition()
    await seed(repository, definition)
    generation = await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    assert generation == 1
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.probe == (definition,) and not snapshot.active and not snapshot.shadow
    record = await repository.get(f"probe/{definition.version_id}")
    assert record["policy"] == policy_document()
    assert record["term_days"] == 90 and record["renewals"] == 0
    assert record["expires_at"] == (NOW + timedelta(days=90)).isoformat()
    assert record["first_enrolled_at"] == record["enrolled_at"] == NOW.isoformat()
    await repository.rebuild()
    assert (await repository.snapshot(now=NOW)).probe == (definition,)


@pytest.mark.parametrize(("paper", "mode"), [(False, "alpaca"), (True, "paper")])
async def test_only_the_brokerage_paper_scope_may_enrol(tmp_path, paper, mode):
    database = paper_database(tmp_path, paper=paper, mode=mode)
    await database.init_db()
    repository = AlphaRepository(database.workflows)
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="Alpaca paper"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    await database.engine.dispose()


@pytest.mark.parametrize(
    ("definition", "decision", "message"),
    [
        (make_definition(timeframe="4h"), qualification(), "native daily"),
        (make_definition(data_feed="yfinance"), qualification(), "deployment feed"),
        (make_definition(), qualification(cost_stress=criterion(-1.0, "fail")), "cost_stress_below_probe_floor"),
        (make_definition(), None, "qualification_missing"),
    ],
)
async def test_ineligible_candidates_are_refused_with_reasons(repository, definition, decision, message):
    await repository.register(definition, actor="test")
    if decision is not None:
        async with repository.store.db.session_factory() as session, session.begin():
            await repository._append(
                session, f"qualification/{definition.version_id}", decision, EventKind.ALPHA_RESEARCH, "fixture"
            )
    with pytest.raises(ValueError, match=message):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    assert not (await repository.snapshot(now=NOW)).probe


async def test_stale_generation_and_term_bounds_are_refused(repository):
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="Registry changed"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=7, now=NOW)
    for days in (0, 181):
        with pytest.raises(ValueError, match="term"):
            await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=days, now=NOW)


async def test_symbol_ownership_and_slot_limit_ignore_expired_probes(repository):
    first, rival = make_definition(), make_definition("alpha_rival")
    await seed(repository, first)
    await seed(repository, rival)
    await repository.enrol_probe(first.version_id, actor="op", expected_generation=0, days=1, now=NOW)
    with pytest.raises(ValueError, match="already has an alpha owner"):
        await repository.enrol_probe(rival.version_id, actor="op", expected_generation=1, now=NOW)
    later = NOW + timedelta(days=2)
    # No sweep has run; the expired enrolment must not block the symbol.
    assert await repository.enrol_probe(rival.version_id, actor="op", expected_generation=1, now=later) == 2
    assert (await repository.snapshot(now=later)).probe == (rival,)


async def test_slot_limit(repository):
    repository.policy = repository.policy.model_copy(update={"max_probes": 1})
    first, second = make_definition(), make_definition("alpha_second", "MSFT")
    await seed(repository, first)
    await seed(repository, second)
    await repository.enrol_probe(first.version_id, actor="op", expected_generation=0, now=NOW)
    with pytest.raises(ValueError, match="probe slots"):
        await repository.enrol_probe(second.version_id, actor="op", expected_generation=1, now=NOW)


async def test_a_version_lives_in_exactly_one_list_and_new_versions_supersede(repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.set_shadow(definition.version_id, actor="op", expected_generation=0)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=1, now=NOW)
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.probe == (definition,) and not snapshot.shadow
    successor = replace(definition, expression="delta(close,5)")
    await seed(repository, successor)
    await repository.set_shadow(successor.version_id, actor="op", expected_generation=2)
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.shadow == (successor,) and not snapshot.probe


async def qualify_for_promotion(repository, alpha_id, symbol, *, expected_generation):
    """Build the same shadow/qualification/session/decision evidence as
    test_alpha_journal.py::test_promotion_requires_decisions_and_frozen_incumbents_then_replays,
    so only the symbol-ownership check can refuse the resulting promote().
    """
    rival = make_definition(alpha_id, symbol, data_feed="alpaca:sip")
    await repository.register(rival, actor="test")
    generation = await repository.set_shadow(rival.version_id, actor="test", expected_generation=expected_generation)
    manifest = {
        "symbol": symbol,
        "timeframe": "1d",
        "feed": "alpaca:sip",
        "adjustment": "raw",
        "holdout_start": "2025-01-01",
        "end": "2025-06-01",
        "incumbents": [],  # Probes are not incumbents; only the active set is frozen here.
    }
    run = {
        "policy": asdict(ValidationPolicy()),
        "trial_count": 1,
        "trials": [{"definition": rival.to_dict(), "status": "evaluated"}],
        "holdout_start": 600,
    }
    run_id = f"qualified-{rival.version_id}"
    await repository.record_run(run_id, run, manifest)
    await repository.begin_holdout(run_id, rival.version_id)
    await repository.record_qualification(
        run_id,
        rival.version_id,
        {
            "qualified": True,
            "reasons": [],
            "eligible_symbols": [symbol],
            "manifest": manifest,
            "policy": asdict(ValidationPolicy()),
        },
    )
    timestamp = NOW.isoformat()
    for day in range(20):
        timestamp = (NOW - timedelta(days=20 - day)).isoformat()
        await repository.record_forecast(
            f"{run_id}-session-{day}",
            {
                "version_id": rival.version_id,
                "valid": True,
                "symbol": symbol,
                "observed_at": timestamp,
                "completed_at": timestamp,
                "decision": 0,
            },
        )
    for decision in range(10):
        await repository.record_forecast(
            f"{run_id}-decision-{decision}",
            {
                "version_id": rival.version_id,
                "valid": True,
                "symbol": symbol,
                "observed_at": timestamp,
                "completed_at": timestamp,
                "decision": 1,
            },
        )
    return rival, generation


async def test_live_probe_blocks_promotion_of_a_rival_on_the_same_symbol(repository):
    # promote() has no `now=` parameter: it always consults the REAL clock internally
    # (qualification-age check, `_live_probes`). Enrol at the real clock with the
    # default 90-day term so the probe is unconditionally live moments later when
    # promote() runs, regardless of what calendar date the suite happens to run on.
    real_now = datetime.now(UTC)
    probe_definition = make_definition()
    await seed(repository, probe_definition)
    await repository.enrol_probe(probe_definition.version_id, actor="op", expected_generation=0, now=real_now)
    rival, generation = await qualify_for_promotion(repository, "alpha_rival", "AAPL", expected_generation=1)
    with pytest.raises(ValueError, match="already has an alpha owner"):
        await repository.promote(rival.version_id, actor="test", expected_generation=generation)
    assert not (await repository.snapshot(now=real_now)).active


async def test_expired_unswept_probe_does_not_block_promotion(repository):
    # Same real-clock constraint as above: enrol a probe that is already expired
    # relative to the real clock (started two days ago with a one-day term) so
    # promote()'s internal `datetime.now(UTC)` sees it as expired regardless of
    # the calendar date, with no monkeypatching required.
    real_now = datetime.now(UTC)
    probe_definition = make_definition()
    await seed(repository, probe_definition)
    await repository.enrol_probe(
        probe_definition.version_id, actor="op", expected_generation=0, days=1, now=real_now - timedelta(days=2)
    )
    rival, generation = await qualify_for_promotion(repository, "alpha_rival", "AAPL", expected_generation=1)
    # No sweep has run; the expired probe is still in the registry's probe list.
    await repository.promote(rival.version_id, actor="test", expected_generation=generation)
    snapshot = await repository.snapshot(now=real_now)
    assert snapshot.active == (rival,) and not snapshot.probe


async def test_renewal_extends_from_now_and_requires_membership(repository):
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="not a current probe"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, renew=True, now=NOW)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=30, now=NOW)
    later = NOW + timedelta(days=20)
    await repository.enrol_probe(
        definition.version_id, actor="op", expected_generation=1, days=30, renew=True, now=later
    )
    record = await repository.get(f"probe/{definition.version_id}")
    assert record["renewals"] == 1
    assert record["first_enrolled_at"] == NOW.isoformat()
    assert record["expires_at"] == (later + timedelta(days=30)).isoformat()


async def test_kill_rule_is_sticky_across_renewal_and_re_enrolment(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    for _ in range(4):
        await close_trade(db, definition, -100.0, when=NOW + timedelta(days=1))
    later = NOW + timedelta(days=2)
    assert not (await repository.snapshot(now=later)).probe
    with pytest.raises(ValueError, match="kill"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=1, renew=True, now=later)
    assert await repository.sweep_probes(now=later) == [definition.version_id]
    with pytest.raises(ValueError, match="kill"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=2, now=later)


async def test_trades_before_first_enrolment_do_not_count(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    for _ in range(5):
        await close_trade(db, definition, -100.0, when=NOW - timedelta(days=1))
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    report = await repository.probe_report(now=NOW)
    assert report[0]["forward"]["trades"] == 0 and report[0]["live"] is True


async def test_sweep_retires_expired_probes_once_with_one_notice(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=1, now=NOW)
    assert await repository.sweep_probes(now=NOW) == []
    later = NOW + timedelta(days=2)
    assert await repository.sweep_probes(now=later) == [definition.version_id]
    assert await repository.sweep_probes(now=later) == []
    registry = await repository.get("registry")
    assert registry["probe"] == [] and registry["generation"] == 2
    async with db.session_factory() as session:
        notices = (
            await session.scalars(select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.NOTIFICATION))
        ).all()
    assert len(notices) == 1 and "expired" in notices[0].payload


async def test_legacy_registry_payload_without_probe_key_still_reads(repository):
    definition = make_definition()
    await seed(repository, definition)
    async with repository.store.db.session_factory() as session, session.begin():
        await repository._append(
            session, "registry", {"generation": 3, "active": [], "shadow": []}, EventKind.ALPHA_REGISTRY, "fixture"
        )
    assert (await repository.snapshot(now=NOW)).probe == ()
    assert await repository.enrol_probe(definition.version_id, actor="op", expected_generation=3, now=NOW) == 4


async def test_demote_removes_a_probe(repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    await repository.demote(definition.version_id, actor="op", expected_generation=1)
    assert not (await repository.snapshot(now=NOW)).probe
