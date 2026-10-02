import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_trader.research.pooled import campaign_run
from agentic_trader.research.pooled.campaign import CampaignWindows, LoadedProtocol
from agentic_trader.research.pooled.campaign_run import check_gate, execute_campaign, require_clean_revision
from agentic_trader.research.pooled.formula import Formula, FormulaFilter
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.pooled.mini_world import COHORT, cube_build, mini_protocol, planted_cube


PLANTED = "-1.0 * roc(close, 21)"
CAMPAIGN = "pooled-campaign-v1-" + "p" * 16
LITERATURE = [
    SimpleNamespace(entry=SimpleNamespace(id="lit-reversal", formula=Formula(score=PLANTED, k=3)), sha256="l" * 64)
]


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def run(
    tmp_path, repository, cube, *, out="out", gates_cube=None, revision="abc1234", protocol=None, entries=LITERATURE
):
    loaded = LoadedProtocol(protocol=protocol or mini_protocol(), sha256="p" * 64, path=Path("campaign.json"))
    sha = gates_cube or cube.sha256
    gates = {name: {"status": "passed", "cube_sha256": sha} for name in ("power", "search_power", "null_check")}

    async def build():
        return cube_build(cube)

    return await execute_campaign(
        loaded,
        tmp_path / out,
        cohort=SimpleNamespace(sha256=COHORT),
        build=build,
        gates=gates,
        repository=repository,
        entries=entries,
        environment={"runtime": {"revision": revision}},
    )


async def test_the_campaign_reserves_charges_freezes_consumes_and_completes(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0))
    target = formula_id(PLANTED)
    assert result["status"] == "confirmed" and target in result["confirmed"]
    # The literature entry is the same formula: already tested, keeps its Holm slot, never probe-eligible.
    assert result["already_tested"][target] == "lit-reversal"
    assert target not in result["probe_eligible"]
    assert any(row["formula_id"] == target for row in result["confirmation"])
    assert await repository.get("pooled/ledger") == {"formulas_charged": 9, "campaigns": 1, "confirmations": 1}
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert campaign["status"] == "completed" and campaign["code_revision"] == "abc1234"
    assert target in [doc["formula_id"] for doc in campaign["details"]["frozen"]["candidates"]]
    frozen = json.loads((tmp_path / "out" / "frozen" / f"{target}.json").read_text())
    assert frozen["expression"] == PLANTED and frozen["formula"]["k"] == 3
    lines = [json.loads(line) for line in (tmp_path / "out" / "formulas.jsonl").read_text().splitlines()]
    assert sum(line["status"] in ("evaluated", "error") for line in lines) == 9
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["status"] == "confirmed" and on_disk["authorizes_promotion"] is False


async def test_a_finalist_no_literature_entry_overlaps_is_probe_eligible(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), entries=[])
    assert formula_id(PLANTED) in result["probe_eligible"] and result["already_tested"] == {}


async def test_confirmation_is_read_only_after_the_journal_consumed_it(tmp_path, repository, monkeypatch):
    created = []

    class Watched(CampaignWindows):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(campaign_run, "CampaignWindows", Watched)
    real = repository.consume_pooled_confirmation
    opened_at_consumption = []

    at_consumption = {}

    async def consume(**kwargs):
        opened_at_consumption.append(created[0].opened)
        record = await repository.get(f"pooled/campaign/{CAMPAIGN}")
        at_consumption["status"] = record["status"]
        at_consumption["already_tested"] = record["details"]["frozen"]["already_tested"]
        at_consumption["files"] = sorted(p.stem for p in (tmp_path / "out" / "frozen").glob("*.json"))
        at_consumption["candidates"] = sorted(kwargs["candidates"])
        await real(**kwargs)

    monkeypatch.setattr(repository, "consume_pooled_confirmation", consume)
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0))
    assert result["status"] == "confirmed"
    assert opened_at_consumption == [("discovery", "selection")]
    assert at_consumption["status"] == "frozen"
    assert at_consumption["files"] == at_consumption["candidates"] != []
    assert at_consumption["already_tested"] == {formula_id(PLANTED): "lit-reversal"}
    assert created[0].opened == ("discovery", "selection", "confirmation")


async def test_a_crash_mid_family_keeps_its_charges_and_a_rerun_resumes_without_recharging(
    tmp_path, repository, monkeypatch
):
    cube = planted_cube(PLANTED, 1.0)
    real = ScoreBook.formula
    calls = {"n": 0}

    def flaky(self, expression):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("worker died")
        return real(self, expression)

    monkeypatch.setattr(ScoreBook, "formula", flaky)
    first = await run(tmp_path, repository, cube, out="one")
    assert first["status"] == "failed" and "worker died" in first["error"]
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 4
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "failed"
    monkeypatch.setattr(ScoreBook, "formula", real)
    second = await run(tmp_path, repository, cube, out="two")
    assert second["status"] == "confirmed"
    ledger = await repository.get("pooled/ledger")
    assert (ledger["formulas_charged"], ledger["campaigns"]) == (9, 1)
    third = await run(tmp_path, repository, cube, out="three")
    assert third["status"] == "failed" and "cannot run again" in third["error"]


async def test_the_campaign_refuses_a_cube_the_gates_did_not_run_on(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), gates_cube="x" * 64)
    assert result["status"] == "failed" and "not the cube checks A, B and C ran on" in result["error"]
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 0


async def test_a_dirty_revision_or_a_v1_protocol_is_refused_before_anything_is_reserved(tmp_path, repository):
    dirty = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), out="one", revision="abc1234-dirty")
    assert dirty["status"] == "failed" and "dirty" in dirty["error"]
    v1_like = await run(
        tmp_path, repository, planted_cube(PLANTED, 1.0), out="two", protocol=mini_protocol(null_check=None)
    )
    assert v1_like["status"] == "failed" and "checks B and C" in v1_like["error"]
    assert await repository.get("pooled/ledger") is None


def _gate(tmp_path, *, check="power_a", revision="abc1234", **result_override) -> Path:
    directory = tmp_path / "gate"
    directory.mkdir()
    manifest = {"check": check, "environment": {"runtime": {"revision": revision}}}
    result = {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": "k" * 64,
        **result_override,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "result.json").write_text(json.dumps(result))
    return directory


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"check": "search_power"}, "not 'power_a'"),
        ({"status": "gate_failed"}, "has not passed"),
        ({"cohort_sha256": "d" * 64}, "different cohort"),
        ({"campaign_protocol_sha256": "q" * 64}, "different campaign protocol"),
        ({"revision": "def5678"}, "ran at code revision 'def5678'"),
        ({"cube_sha256": None}, "records no cube"),
    ],
)
def test_check_gate_refuses_each_mismatch(tmp_path, override, message):
    with pytest.raises(ValueError, match=message):
        check_gate(
            _gate(tmp_path, **override),
            check="power_a",
            cohort_sha256=COHORT,
            protocol_sha256="p" * 64,
            revision="abc1234",
        )


def test_check_gate_returns_a_matching_result(tmp_path):
    result = check_gate(
        _gate(tmp_path), check="power_a", cohort_sha256=COHORT, protocol_sha256="p" * 64, revision="abc1234"
    )
    assert result["cube_sha256"] == "k" * 64


@pytest.mark.parametrize("revision", ["abc1234-dirty", "unavailable"])
def test_require_clean_revision_refuses_dirty_or_unknown_code(revision):
    with pytest.raises(ValueError, match="dirty or unknown"):
        require_clean_revision({"runtime": {"revision": revision}})
    assert require_clean_revision({"runtime": {"revision": "abc1234"}}) == "abc1234"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("cohort", "cohort file does not match"),
        ("missing", "checks A, B and C must all be given"),
        ("split", "did not all run on one cube"),
        ("status", "has not passed"),
        ("nocube", "records no cube"),
    ],
)
async def test_bad_gate_inputs_are_refused_before_reserving(tmp_path, repository, case, message):
    cube = planted_cube(PLANTED, 1.0)
    loaded = LoadedProtocol(protocol=mini_protocol(), sha256="p" * 64, path=Path("campaign.json"))
    gates = {name: {"status": "passed", "cube_sha256": cube.sha256} for name in ("power", "search_power", "null_check")}
    cohort = COHORT
    if case == "cohort":
        cohort = "d" * 64
    elif case == "missing":
        del gates["null_check"]
    elif case == "split":
        gates["null_check"] = {"status": "passed", "cube_sha256": "y" * 64}
    elif case == "status":
        gates["power"] = {"status": "gate_failed", "cube_sha256": cube.sha256}
    else:
        gates["power"] = {"status": "passed", "cube_sha256": ""}

    async def build():
        return cube_build(cube)

    result = await execute_campaign(
        loaded,
        tmp_path / "out",
        cohort=SimpleNamespace(sha256=cohort),
        build=build,
        gates=gates,
        repository=repository,
        entries=LITERATURE,
        environment={"runtime": {"revision": "abc1234"}},
    )
    assert result["status"] == "failed" and message in result["error"]
    assert await repository.get("pooled/ledger") is None


def test_a_missing_revision_is_unknown():
    with pytest.raises(ValueError, match="dirty or unknown"):
        require_clean_revision({"runtime": {"revision": None}})
    with pytest.raises(ValueError, match="dirty or unknown"):
        require_clean_revision({})


async def test_a_failure_after_consumption_keeps_the_outcome(tmp_path, repository, monkeypatch):
    def boom(path, records):
        raise OSError("disk full")

    monkeypatch.setattr(campaign_run, "_write_records", boom)
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0))
    target = formula_id(PLANTED)
    assert result["status"] == "failed" and "disk full" in result["error"]
    assert target in result["outcome"]["confirmed"] and result["outcome_file"] == "outcome.json"
    saved = json.loads((tmp_path / "out" / "outcome.json").read_text())
    assert any(row["formula_id"] == target for row in saved["confirmation"])
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert "confirmation_consumed" in campaign["stages"]


def test_a_literature_entry_with_a_filter_yields_cells():
    build = cube_build(planted_cube(PLANTED, 1.0))
    book = ScoreBook(build.adjusted, build.trading_days, build.cube.sessions, build.cube.symbols)
    formula = Formula(score=PLANTED, filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=3)
    entry = SimpleNamespace(entry=SimpleNamespace(id="filtered", formula=formula))
    view = build.cube.window(*mini_protocol().windows.discovery)
    cells = campaign_run.literature_cells([entry], view, book)
    assert isinstance(cells["filtered"], frozenset)
