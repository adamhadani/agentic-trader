"""A passing catalog leg's paper-probe identity, enrolment and registry handling.

A catalog alpha is not a DSL formula: its identity is the frozen entry file's SHA-256
plus the leg, pinned to the study's own manifest/result. It shares the DSL probe lane's
single liveness rule (paper scope, renewable term, sticky -4R kill, slot limit) and can
never be promoted or shadowed.
"""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner
from sqlalchemy import select

from agentic_trader.cli.main import cli
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.research.alpha.promotion import AlphaPromotionService
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.research.apriori.probe import catalog_version_id, is_catalog_definition, load_catalog_probe
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import SignalRecord, WorkItemRecord
from tests.research.probe_fixtures import make_definition, paper_database, seed
from tests.research.test_alpha_probe_registry import qualify_for_promotion


ENTRY = Path("config/research/apriori/pead-v2.json")
SHA = hashlib.sha256(ENTRY.read_bytes()).hexdigest()
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


def study(tmp_path, *, sha=SHA, long="eligible_for_probe", status="completed", entry_id="pead", version=2):
    directory = tmp_path / "study"
    directory.mkdir(exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"entry_id": entry_id, "version": version, "sha256": sha, "authorizes_promotion": False})
    )
    (directory / "result.json").write_text(
        json.dumps(
            {
                "status": status,
                "authorizes_promotion": False,
                "decisions": {"LONG": long, "SHORT": "failed"},
                "legs": {"LONG": {"p1": {"mean_r_cost": 0.1489}}, "SHORT": {"p1": {"mean_r_cost": -0.064}}},
            }
        )
    )
    return directory


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


@pytest.fixture
def repository(db):
    return AlphaRepository(db.workflows)


@pytest.fixture
def definition(tmp_path):
    return load_catalog_probe(ENTRY, study(tmp_path), "long")


async def notification_payloads(db):
    async with db.session_factory() as session:
        rows = (
            await session.scalars(
                select(WorkItemRecord)
                .where(WorkItemRecord.kind == WorkKind.NOTIFICATION)
                .order_by(WorkItemRecord.sequence)
            )
        ).all()
    return [row.payload for row in rows]


def test_definition_pins_entry_study_and_policy(tmp_path):
    directory = study(tmp_path)
    definition = load_catalog_probe(ENTRY, directory, "long")
    assert definition["version_id"] == catalog_version_id("pead", 2, "LONG", SHA) == f"apriori:pead:v2:long:{SHA[:16]}"
    assert definition["alpha_id"] == "pead_long"
    assert definition["kind"] == "apriori" and definition["leg"] == "LONG" and definition["timeframe"] == "1d"
    assert definition["entry_id"] == "pead" and definition["entry_version"] == 2 and definition["entry_sha256"] == SHA
    assert definition["execution"]["kind"] == "apriori_bracket_v1"
    assert definition["execution"]["lifetime"]["version"] == "session_count_v1"
    assert definition["study_mean_r"] == 0.1489
    assert definition["study_result_sha256"] == hashlib.sha256((directory / "result.json").read_bytes()).hexdigest()
    assert definition["study_manifest_sha256"] == hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    assert definition["eligible_symbols"] is None and definition["clock"] is None
    assert is_catalog_definition(definition)
    assert not is_catalog_definition(None) and not is_catalog_definition(make_definition().to_dict())


@pytest.mark.parametrize(
    ("leg", "changes", "message"),
    [
        ("SHORT", {}, "did not pass its study"),
        ("sideways", {}, "leg"),
        ("LONG", {"sha": "0" * 64}, "manifest SHA-256"),
        ("LONG", {"status": "failed"}, "not completed"),
        ("LONG", {"long": "failed"}, "did not pass its study"),
        ("LONG", {"entry_id": "other"}, "manifest names"),
    ],
)
def test_an_unpassed_or_mismatched_study_is_refused(tmp_path, leg, changes, message):
    with pytest.raises(ValueError, match=message):
        load_catalog_probe(ENTRY, study(tmp_path, **changes), leg)


async def test_enrolment_lists_the_catalog_probe_and_snapshot_separates_it(repository, definition):
    dsl = make_definition()
    await seed(repository, dsl)
    generation = await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    assert generation == 1
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.probe == ()
    assert [d["version_id"] for d in snapshot.catalog_probes] == [definition["version_id"]]
    assert snapshot.catalog_probes[0] == definition
    record = await repository.get(f"probe/{definition['version_id']}")
    assert record["term_days"] == 30 and record["renewals"] == 0
    assert record["expires_at"] == (NOW + timedelta(days=30)).isoformat()
    assert record["assessment"] == {
        "study_result_sha256": definition["study_result_sha256"],
        "study_mean_r": 0.1489,
    }
    assert (await repository.get(f"version/{definition['version_id']}"))["definition"] == definition
    assert await repository.versions() == [dsl]
    await repository.rebuild()
    assert [d["version_id"] for d in (await repository.snapshot(now=NOW)).catalog_probes] == [definition["version_id"]]


async def test_enrolment_emits_one_durable_notice(db, repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    notices = await notification_payloads(db)
    assert len(notices) == 1
    assert "pead_long enrolled by op" in notices[0] and "any liquid reporter" in notices[0]
    assert "paper account only" in notices[0]


async def test_only_the_brokerage_paper_scope_may_enrol_a_catalog_probe(tmp_path, definition):
    database = paper_database(tmp_path, paper=False)
    await database.init_db()
    with pytest.raises(ValueError, match="Alpaca paper"):
        await AlphaRepository(database.workflows).enrol_catalog_probe(
            definition, actor="op", expected_generation=0, now=NOW
        )
    await database.engine.dispose()


async def test_a_dsl_definition_is_not_a_catalog_enrolment(repository):
    with pytest.raises(ValueError, match="requires a catalog definition"):
        await repository.enrol_catalog_probe(make_definition().to_dict(), actor="op", expected_generation=0, now=NOW)


async def test_the_repository_itself_refuses_a_leg_the_live_path_does_not_trade(repository, definition):
    """SHORT failed its study; a hand-built SHORT definition is refused at the repository, not only the loader."""
    short = {
        **definition,
        "leg": "SHORT",
        "alpha_id": "pead_short",
        "version_id": catalog_version_id("pead", 2, "SHORT", SHA),
    }
    with pytest.raises(ValueError, match="live path trades only LONG"):
        await repository.enrol_catalog_probe(short, actor="op", expected_generation=0, now=NOW)
    assert await repository.get("registry") is None
    assert await repository.get(f"version/{short['version_id']}") is None


async def test_stale_generation_and_term_bounds_are_refused(repository, definition):
    with pytest.raises(ValueError, match="Registry changed"):
        await repository.enrol_catalog_probe(definition, actor="op", expected_generation=4, now=NOW)
    for days in (0, 181, True):
        with pytest.raises(ValueError, match="term"):
            await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, days=days, now=NOW)
    assert await repository.get("registry") is None


async def test_re_enrolment_with_changed_evidence_is_refused(repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    changed = {**definition, "study_result_sha256": "f" * 64}
    with pytest.raises(ValueError, match="Catalog version evidence differs"):
        await repository.enrol_catalog_probe(changed, actor="op", expected_generation=1, now=NOW)
    assert (await repository.get(f"version/{definition['version_id']}"))["definition"] == definition


async def close_catalog_trade(db, definition, pnl, *, risk=100.0, when=NOW):
    sid = await db.record_signal(
        "NVDA",
        definition["alpha_id"],
        "LONG",
        100,
        95,
        115,
        risk,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition["version_id"],
        alpha_policy=definition["execution"],
    )
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        row.status, row.realized_pnl, row.risk_dollars, row.exit_timestamp = (
            SignalStatus.CLOSED_LOSS,
            pnl,
            risk,
            when,
        )


async def test_catalog_probe_shares_slots_kill_and_renewal_rules(db, repository, definition):
    dsl = [make_definition(f"alpha_{name}", symbol) for name, symbol in (("a", "AAPL"), ("b", "MSFT"), ("c", "TSLA"))]
    for generation, each in enumerate(dsl):
        await seed(repository, each)
        await repository.enrol_probe(each.version_id, actor="op", expected_generation=generation, now=NOW)
    assert repository.policy.max_probes == 3
    with pytest.raises(ValueError, match="probe slots are in use"):
        await repository.enrol_catalog_probe(definition, actor="op", expected_generation=3, now=NOW)
    with pytest.raises(ValueError, match="not a current probe"):
        await repository.enrol_catalog_probe(definition, actor="op", expected_generation=3, renew=True, now=NOW)
    await repository.demote(dsl[0].version_id, actor="op", expected_generation=3)
    assert await repository.enrol_catalog_probe(definition, actor="op", expected_generation=4, now=NOW) == 5
    await close_catalog_trade(db, definition, -450.0)
    assert (await repository.snapshot(now=NOW)).catalog_probes == ()
    for renew in (False, True):
        with pytest.raises(ValueError, match="kill rule"):
            await repository.enrol_catalog_probe(definition, actor="op", expected_generation=5, renew=renew, now=NOW)


async def test_catalog_versions_cannot_be_promoted_or_shadowed_only_demoted(repository, definition):
    version_id = definition["version_id"]
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    for operation in (repository.promote, repository.set_shadow):
        with pytest.raises(ValueError, match="Catalog alphas run only as paper probes"):
            await operation(version_id, actor="op", expected_generation=1)
    with pytest.raises(ValueError, match="Catalog alphas run only as paper probes"):
        await repository.enrol_probe(version_id, actor="op", expected_generation=1, now=NOW)
    assert await repository.demote(version_id, actor="op", expected_generation=1) == 2
    registry = await repository.get("registry")
    assert registry["probe"] == [] and registry["active"] == [] and registry["shadow"] == []
    assert (await repository.snapshot(now=NOW)).catalog_probes == ()


async def test_dsl_enrolment_and_promotion_ignore_catalog_owners(repository, definition):
    # promote() reads the real clock internally, so every enrolment uses it too.
    real_now = datetime.now(UTC)
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=real_now)
    probe = make_definition("alpha_spy", "SPY")
    await seed(repository, probe)
    assert await repository.enrol_probe(probe.version_id, actor="op", expected_generation=1, now=real_now) == 2
    rival, generation = await qualify_for_promotion(repository, "alpha_rival", "MSFT", expected_generation=2)
    await repository.promote(rival.version_id, actor="test", expected_generation=generation)
    snapshot = await repository.snapshot(now=real_now)
    assert snapshot.active == (rival,) and snapshot.probe == (probe,)
    assert [d["version_id"] for d in snapshot.catalog_probes] == [definition["version_id"]]


async def test_a_catalog_version_is_never_qualified(repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    bars = pd.DataFrame({"close": [100.0, 101.0]}, index=pd.date_range("2026-01-02", periods=2, tz="UTC"))
    async with repository.store.db.session_factory() as session, session.begin():
        await repository.store.lock(session, resource="alpha")
        run = {"run": {}, "manifest": {"content_hash": frame_digest(bars)}}
        await repository._append(session, "run/fixture", run, EventKind.ALPHA_RESEARCH, "fixture")
    with pytest.raises(ValueError, match="never qualified"):
        await AlphaPromotionService(repository).qualify("fixture", definition["version_id"], bars)
    assert await repository.get(f"qualification/{definition['version_id']}") is None


async def test_probe_report_marks_catalog_rows(repository, definition):
    dsl = make_definition()
    await seed(repository, dsl)
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    await repository.enrol_probe(dsl.version_id, actor="op", expected_generation=1, now=NOW)
    rows = {row["version_id"]: row for row in await repository.probe_report(now=NOW)}
    row = rows[definition["version_id"]]
    assert row["kind"] == "apriori" and row["alpha_id"] == "pead_long" and row["symbols"] == []
    assert row["study_mean_r"] == 0.1489 and row["live"] is True
    assert rows[dsl.version_id]["kind"] == "dsl" and rows[dsl.version_id]["study_mean_r"] is None


async def test_sweep_retires_an_expired_catalog_probe_with_one_notice(db, repository, definition):
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=0, now=NOW)
    assert await repository.sweep_probes(now=NOW) == []
    later = NOW + timedelta(days=31)
    assert await repository.sweep_probes(now=later) == [definition["version_id"]]
    assert await repository.sweep_probes(now=later) == []
    retirements = [payload for payload in await notification_payloads(db) if "retired" in payload]
    assert len(retirements) == 1 and "pead_long" in retirements[0] and "expired" in retirements[0]


async def test_acknowledge_names_live_catalog_probes_only_when_present(repository, definition):
    # status() reads the real clock, so every enrolment uses it too.
    real_now = datetime.now(UTC)
    dsl = make_definition()
    await seed(repository, dsl)
    await repository.enrol_probe(dsl.version_id, actor="op", expected_generation=0, now=real_now)
    await repository.acknowledge(await repository.snapshot(now=real_now), run_id="run-1")
    # Without a catalog probe the installed record keeps its exact pre-catalog shape.
    assert await repository.get("runtime/registry") == {
        "generation": 1,
        "run_id": "run-1",
        "active": [],
        "probe": [dsl.version_id],
    }
    status = await repository.status(run_id="run-1")
    assert (status["ready"], status["probe"], status["catalog_probe"]) == (True, 1, 0)
    await repository.enrol_catalog_probe(definition, actor="op", expected_generation=1, now=real_now)
    await repository.acknowledge(await repository.snapshot(now=real_now), run_id="run-1")
    assert await repository.get("runtime/registry") == {
        "generation": 2,
        "run_id": "run-1",
        "active": [],
        "probe": [dsl.version_id],
        "catalog_probe": [definition["version_id"]],
    }
    status = await repository.status(run_id="run-1")
    assert (status["ready"], status["probe"], status["catalog_probe"]) == (True, 1, 1)


def test_cli_enrols_a_catalog_probe_once(tmp_path, monkeypatch):
    """Plain function: the CLI owns its own event loop (see tests/cli/test_alpha_probe_cli.py)."""
    directory = study(tmp_path)
    version_id = catalog_version_id("pead", 2, "LONG", SHA)

    async def init():
        database = paper_database(tmp_path)
        await database.init_db()
        await database.engine.dispose()

    asyncio.run(init())

    @asynccontextmanager
    async def manager():
        fresh = paper_database(tmp_path)
        try:
            yield AlphaRepository(fresh.workflows)
        finally:
            await fresh.engine.dispose()

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.alpha_repository", manager)
    runner = CliRunner()
    arguments = ["alpha", "apriori-probe", str(ENTRY), "--study", str(directory), "--generation", "0"]
    result = runner.invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert version_id in result.output and "generation 1" in result.output
    assert SHA in result.output and "+0.149R" in result.output
    again = runner.invoke(cli, arguments)
    assert again.exit_code != 0 and "Registry changed" in again.output
    refused = runner.invoke(cli, [*arguments[:-1], "1", "--leg", "short"])
    assert refused.exit_code != 0 and "did not pass its study" in refused.output
    inspected = runner.invoke(cli, ["alpha", "inspect", version_id])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["definition"]["kind"] == "apriori"

    async def registry():
        database = paper_database(tmp_path)
        try:
            return await AlphaRepository(database.workflows).get("registry")
        finally:
            await database.engine.dispose()

    listed = asyncio.run(registry())
    assert listed["probe"] == [version_id] and listed["generation"] == 1


def test_cli_enrolment_warns_when_the_decision_time_is_not_scheduled(tmp_path, monkeypatch, app_config):
    directory = study(tmp_path)

    async def init():
        database = paper_database(tmp_path)
        await database.init_db()
        await database.engine.dispose()

    asyncio.run(init())

    @asynccontextmanager
    async def manager():
        fresh = paper_database(tmp_path)
        try:
            yield AlphaRepository(fresh.workflows)
        finally:
            await fresh.engine.dispose()

    app_config.scheduler.suggestion_scan_times_et = ["14:35"]
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.alpha_repository", manager)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.load_config", lambda: app_config)
    arguments = ["alpha", "apriori-probe", str(ENTRY), "--study", str(directory), "--generation", "0"]
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert "Warning: PEAD decision time 10:35 New York is not a scheduled suggestion scan time" in result.output
    assert "14:35" in result.output

    app_config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    renewed = CliRunner().invoke(cli, [*arguments[:-1], "1", "--renew"])
    assert renewed.exit_code == 0, renewed.output
    assert "Warning" not in renewed.output
