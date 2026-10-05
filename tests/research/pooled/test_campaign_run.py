import asyncio
import json
import os
import signal
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled import campaign as campaign_module, campaign_run
from agentic_trader.research.pooled.campaign import CampaignWindows, DiscoveryEvaluator, LoadedProtocol
from agentic_trader.research.pooled.campaign_run import (
    check_gate,
    execute_campaign,
    execute_campaign_recovery,
    preflight,
    require_clean_revision,
)
from agentic_trader.research.pooled.formula import Formula, FormulaFilter, allowed_mask, jaccard_codes
from agentic_trader.research.pooled.ledger import JournalLedger
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.pooled.mini_world import (
    COHORT,
    FAMILIES,
    book as mini_book,
    cube_build,
    label_cube,
    mini_protocol,
    mini_v3_protocol,
    planted_cube,
)


PLANTED = "-1.0 * roc(close, 21)"
CAMPAIGN = "pooled-campaign-v1-" + "p" * 16
SEEDS = sum(len(family.seeds) for family in FAMILIES)  # the preflight builds each seed's formula once
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


async def recover(tmp_path, repository, cube, *, out="recovered", revision="abc1234"):
    loaded = LoadedProtocol(protocol=mini_protocol(), sha256="p" * 64, path=Path("campaign.json"))

    async def build():
        return cube_build(cube)

    return await execute_campaign_recovery(
        loaded,
        tmp_path / out,
        cohort=SimpleNamespace(sha256=COHORT),
        build=build,
        repository=repository,
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
    assert campaign["journal_scope"] == repository.store.scope
    assert target in [doc["formula_id"] for doc in campaign["details"]["frozen"]["candidates"]]
    journal = {"scope": repository.store.scope, "dialect": "sqlite", "database": repository.store.db.db_path}
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["journal"] == journal
    assert result["journal"] == journal and result["confirmation_consumed"] is True
    frozen = json.loads((tmp_path / "out" / "frozen" / f"{target}.json").read_text())
    assert frozen["expression"] == PLANTED and frozen["formula"]["selection"] == {
        "rule": "top_k",
        "k": 3,
        "fraction": None,
    }
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
        if calls["n"] == SEEDS + 5:  # the fifth formula of the search
            raise RuntimeError("worker died")
        return real(self, expression)

    monkeypatch.setattr(ScoreBook, "formula", flaky)
    first = await run(tmp_path, repository, cube, out="one")
    assert first["status"] == "failed" and "worker died" in first["error"]
    assert first["confirmation_consumed"] is False
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 4
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "failed"
    monkeypatch.setattr(ScoreBook, "formula", real)
    second = await run(tmp_path, repository, cube, out="two")
    assert second["status"] == "confirmed"
    ledger = await repository.get("pooled/ledger")
    assert (ledger["formulas_charged"], ledger["campaigns"]) == (9, 1)
    third = await run(tmp_path, repository, cube, out="three")
    assert third["status"] == "failed" and "cannot run again" in third["error"]
    assert third["confirmation_consumed"] is True


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
    assert result["confirmation_consumed"] is True


def test_a_literature_entry_with_a_filter_yields_codes():
    build = cube_build(planted_cube(PLANTED, 1.0))
    book = ScoreBook(build.adjusted, build.trading_days, build.cube.sessions, build.cube.symbols)
    formula = Formula(score=PLANTED, filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=3)
    entry = SimpleNamespace(entry=SimpleNamespace(id="filtered", formula=formula))
    view = build.cube.window(*mini_protocol().windows.discovery)
    codes = campaign_run.literature_codes([entry], view, book, mini_protocol())
    assert codes["filtered"].dtype == np.int64 and np.all(np.diff(codes["filtered"]) > 0)


# --- cancellation (Ctrl-C, SIGTERM) ---


def _hold_the_search(monkeypatch, *, at: int, on_reach):
    """Pause the worker inside its ``at``-th discovery score until the campaign aborts its ledger."""
    released = threading.Event()
    real_score = DiscoveryEvaluator.score
    real_abort = JournalLedger.abort
    calls = {"n": 0}

    def score(self, formula):
        calls["n"] += 1
        if calls["n"] == at:
            on_reach()
            assert released.wait(30), "the campaign never aborted its ledger"
        return real_score(self, formula)

    def abort(self):
        real_abort(self)
        released.set()

    monkeypatch.setattr(DiscoveryEvaluator, "score", score)
    monkeypatch.setattr(JournalLedger, "abort", abort)
    return real_score


async def test_cancelling_mid_search_consumes_nothing_and_a_rerun_charges_the_budget_once(
    tmp_path, repository, monkeypatch
):
    cube = planted_cube(PLANTED, 1.0)
    reached = threading.Event()
    real_score = _hold_the_search(monkeypatch, at=4, on_reach=reached.set)
    task = asyncio.create_task(run(tmp_path, repository, cube, out="one"))
    assert await asyncio.to_thread(reached.wait, 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads((tmp_path / "one" / "result.json").read_text())
    assert result["status"] == "cancelled" and result["confirmation_consumed"] is False
    assert "outcome" not in result and not (tmp_path / "one" / "outcome.json").exists()
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert campaign["status"] == "failed" and campaign["details"]["failed"] == {"error": "cancelled"}
    assert "confirmation_consumed" not in campaign["stages"]
    assert await repository.get("pooled/confirmation") is None
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 4  # the held formula, nothing after
    monkeypatch.setattr(DiscoveryEvaluator, "score", real_score)
    rerun = await run(tmp_path, repository, cube, out="two")
    assert rerun["status"] == "confirmed"
    assert await repository.get("pooled/ledger") == {"formulas_charged": 9, "campaigns": 1, "confirmations": 1}


async def test_cancelling_after_consumption_keeps_the_outcome(tmp_path, repository, monkeypatch):
    real = repository.consume_pooled_confirmation
    running = {}

    async def consume(**kwargs):
        await real(**kwargs)
        running["task"].cancel()  # the operator presses Ctrl-C just after the window was consumed

    monkeypatch.setattr(repository, "consume_pooled_confirmation", consume)
    running["task"] = task = asyncio.create_task(run(tmp_path, repository, planted_cube(PLANTED, 1.0)))
    with pytest.raises(asyncio.CancelledError):
        await task
    target = formula_id(PLANTED)
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["status"] == "cancelled" and result["confirmation_consumed"] is True
    assert target in result["outcome"]["confirmed"] and result["outcome_file"] == "outcome.json"
    saved = json.loads((tmp_path / "out" / "outcome.json").read_text())
    assert any(row["formula_id"] == target for row in saved["confirmation"])
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert "confirmation_consumed" in campaign["stages"] and campaign["status"] == "failed"


async def test_sigterm_mid_search_follows_the_cancellation_path(tmp_path, repository, monkeypatch):
    def terminate():
        os.kill(os.getpid(), signal.SIGTERM)

    _hold_the_search(monkeypatch, at=4, on_reach=terminate)

    def survive(signum, frame):  # a safety net: a missing handler fails the test instead of killing pytest
        raise AssertionError("SIGTERM reached the process default instead of the campaign")

    previous = signal.signal(signal.SIGTERM, survive)
    try:
        task = asyncio.create_task(run(tmp_path, repository, planted_cube(PLANTED, 1.0)))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert signal.getsignal(signal.SIGTERM) is survive  # the campaign's handler was removed
    finally:
        signal.signal(signal.SIGTERM, previous)
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["status"] == "cancelled" and result["confirmation_consumed"] is False
    assert await repository.get("pooled/confirmation") is None


# --- a failure after consumption, and recovery ---


async def test_a_failure_inside_confirmation_is_marked_consumed_and_recovery_completes(
    tmp_path, repository, monkeypatch
):
    cube = planted_cube(PLANTED, 1.0)
    real = campaign_module._confirmation

    def boom(*args, **kwargs):
        raise FloatingPointError("confirmation blew up")

    monkeypatch.setattr(campaign_module, "_confirmation", boom)
    failed = await run(tmp_path, repository, cube, out="one")
    assert failed["status"] == "failed" and "confirmation blew up" in failed["error"]
    assert failed["confirmation_consumed"] is True
    assert json.loads((tmp_path / "one" / "result.json").read_text())["confirmation_consumed"] is True
    monkeypatch.setattr(campaign_module, "_confirmation", real)
    counts = await repository.get("pooled/ledger")
    recovered = await recover(tmp_path, repository, cube, out="two")
    target = formula_id(PLANTED)
    assert recovered["status"] == "confirmed" and recovered["recovered"] is True, recovered.get("error")
    assert target in recovered["confirmed"] and recovered["already_tested"] == {target: "lit-reversal"}
    assert target not in recovered["probe_eligible"]
    saved = json.loads((tmp_path / "two" / "outcome.json").read_text())
    assert saved["recovered"] is True and any(row["formula_id"] == target for row in saved["confirmation"])
    on_disk = json.loads((tmp_path / "two" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["status"] == "confirmed" and on_disk["recovered"] is True
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert campaign["status"] == "completed" and campaign["details"]["completed"]["recovered"] is True
    assert await repository.get("pooled/ledger") == counts  # nothing charged, nothing consumed again
    again = await recover(tmp_path, repository, cube, out="three")
    assert again["status"] == "failed" and "completed" in again["error"]


async def test_recovery_is_refused_for_a_campaign_that_never_consumed(tmp_path, repository, monkeypatch):
    cube = planted_cube(PLANTED, 1.0)
    missing = await recover(tmp_path, repository, cube, out="none")
    assert missing["status"] == "failed" and "no pooled campaign" in missing["error"]
    real = ScoreBook.formula
    calls = {"n": 0}

    def flaky(self, expression):
        calls["n"] += 1
        if calls["n"] == SEEDS + 2:
            raise RuntimeError("worker died")
        return real(self, expression)

    monkeypatch.setattr(ScoreBook, "formula", flaky)
    assert (await run(tmp_path, repository, cube, out="one"))["status"] == "failed"
    monkeypatch.setattr(ScoreBook, "formula", real)
    refused = await recover(tmp_path, repository, cube, out="two")
    assert refused["status"] == "failed" and "never consumed" in refused["error"]
    assert refused["confirmation_consumed"] is False
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "failed"


async def test_recovery_is_refused_for_a_completed_campaign(tmp_path, repository):
    cube = planted_cube(PLANTED, 1.0)
    assert (await run(tmp_path, repository, cube))["status"] == "confirmed"
    counts = await repository.get("pooled/ledger")
    refused = await recover(tmp_path, repository, cube)
    assert refused["status"] == "failed" and "completed" in refused["error"]
    assert await repository.get("pooled/ledger") == counts


async def test_recovery_refuses_another_revision_or_cube(tmp_path, repository, monkeypatch):
    cube = planted_cube(PLANTED, 1.0)

    def boom(*args, **kwargs):
        raise FloatingPointError("confirmation blew up")

    monkeypatch.setattr(campaign_module, "_confirmation", boom)
    assert (await run(tmp_path, repository, cube, out="one"))["confirmation_consumed"] is True
    revision = await recover(tmp_path, repository, cube, out="two", revision="def5678")
    assert revision["status"] == "failed" and "code_revision" in revision["error"]
    other = await recover(tmp_path, repository, planted_cube(PLANTED, 1.0, seed=2), out="three")
    assert other["status"] == "failed" and "not the cube the campaign ran on" in other["error"]
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "failed"


# --- the label-blind preflight ---


async def test_the_preflight_refuses_a_failing_seed_before_any_charge_and_the_campaign_resumes(
    tmp_path, repository, monkeypatch
):
    cube = planted_cube(PLANTED, 1.0)
    target = canonical_expression("volume / ts_mean(volume, 50)")
    real = ScoreBook.panel

    def panel(self, expression):
        if canonical_expression(expression) == target:
            raise FloatingPointError("overflow in volume")
        return real(self, expression)

    monkeypatch.setattr(ScoreBook, "panel", panel)
    first = await run(tmp_path, repository, cube, out="one")
    assert first["status"] == "failed" and "preflight" in first["error"] and target in first["error"]
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 0
    assert await repository.get(f"pooled/campaign/{CAMPAIGN}/family/reversal") is None
    monkeypatch.setattr(ScoreBook, "panel", real)
    second = await run(tmp_path, repository, cube, out="two")
    assert second["status"] == "confirmed"
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 9


@pytest.mark.parametrize(
    ("broken", "message"),
    [
        ("-1.0 * roc(close, 5)", "entirely NaN"),
        ("ts_mean((close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20) + 1e-6), 10)", "ZeroDivisionError"),
    ],
)
def test_the_preflight_names_an_all_nan_seed_or_a_failing_mutation(monkeypatch, broken, message):
    protocol = mini_protocol()
    scores = mini_book()
    view = label_cube().window(*protocol.windows.discovery)
    real = ScoreBook.panel
    target = canonical_expression(broken)

    def panel(self, expression):
        if canonical_expression(expression) != target:
            return real(self, expression)
        if message == "entirely NaN":
            return np.full((len(self.sessions), len(self.symbols)), np.nan)
        raise ZeroDivisionError("bad window")

    monkeypatch.setattr(ScoreBook, "panel", panel)
    with pytest.raises(ValueError, match=message) as caught:
        preflight(protocol, scores, view)
    assert target in str(caught.value)


def test_the_preflight_passes_the_mini_world_without_reading_labels():
    protocol = mini_protocol()
    view = label_cube().window(*protocol.windows.discovery)
    labels = ("labelled", "r_gross", "r_cost", "holding", "hit", "tiebreak", "dollar_volume")
    blind = replace(view, **dict.fromkeys(labels))
    checked = preflight(protocol, mini_book(), blind)
    assert checked == sum(len(f.seeds) + len(f.mutation_operators) for f in protocol.families)


async def test_a_cube_without_an_edge_ends_with_no_finalists_and_consumes_nothing(tmp_path, repository):
    result = await run(tmp_path, repository, label_cube())
    assert result["status"] == "no_finalists" and result["confirmation_consumed"] is False
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "completed"
    assert await repository.get("pooled/confirmation") is None


async def test_a_planted_decile_edge_is_confirmed_under_v3(tmp_path, repository):
    protocol = mini_v3_protocol()
    result = await run(
        tmp_path, repository, planted_cube(PLANTED, 1.0, protocol=protocol), protocol=protocol, entries=[]
    )
    target = formula_id(PLANTED)
    assert result["status"] == "confirmed" and target in result["confirmed"]
    frozen = json.loads((tmp_path / "out" / "frozen" / f"{target}.json").read_text())
    assert frozen["formula"]["selection"] == {"rule": "top_fraction", "k": None, "fraction": 0.1}


def test_literature_overlap_is_evaluated_under_the_campaign_rule():
    protocol = mini_v3_protocol()
    cube = label_cube()
    view = cube.window(*protocol.windows.discovery)
    entry = SimpleNamespace(entry=SimpleNamespace(id="lit", formula=Formula(score=PLANTED, k=3)))
    literature = campaign_run.literature_codes([entry], view, mini_book(), protocol)
    own = protocol.select(*mini_book().formula(PLANTED).panel(view), view).codes(len(view.symbols))
    assert np.array_equal(literature["lit"], own)  # decile picks, not the entry's own top 3
    assert campaign_run.already_tested(["f"], {"f": own}, literature, protocol.dedupe_jaccard) == {"f": "lit"}


def test_a_filtered_literature_entry_sizes_its_basket_from_its_filtered_pool():
    protocol = mini_v3_protocol()
    cube = label_cube()
    view = cube.window(*protocol.windows.discovery)
    book = mini_book()
    stop = view.offset + len(view.sessions)
    flt = FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5)
    filtered = SimpleNamespace(entry=SimpleNamespace(id="f", formula=Formula(score=PLANTED, filters=(flt,), k=3)))
    plain = SimpleNamespace(entry=SimpleNamespace(id="p", formula=Formula(score=PLANTED, k=3)))
    codes = campaign_run.literature_codes([filtered, plain], view, book, protocol)

    n_names = len(view.symbols)
    scores = book.panel(PLANTED)[view.offset : stop]
    filter_values = [book.panel(flt.expression)[view.offset : stop]]
    allowed = allowed_mask(filtered.entry.formula, filter_values, view.eligible)
    n_filtered = (allowed & np.isfinite(scores)).sum(axis=1)
    n_plain = (view.eligible & np.isfinite(scores)).sum(axis=1)
    assert (n_filtered < n_plain).any()

    def per_session(c):
        return np.bincount(c // n_names, minlength=len(view.sessions))

    picked_filtered, picked_plain = per_session(codes["f"]), per_session(codes["p"])
    live = n_filtered > 0
    assert np.array_equal(picked_filtered[live], np.ceil(np.round(0.10 * n_filtered[live], 9)).astype(int))
    assert np.array_equal(picked_plain[n_plain > 0], np.ceil(np.round(0.10 * n_plain[n_plain > 0], 9)).astype(int))
    assert picked_filtered.sum() < picked_plain.sum()
    assert (picked_filtered <= picked_plain).all()
    # Jaccard with the unfiltered basket is capped at |entry| / |finalist|.
    assert jaccard_codes(codes["f"], codes["p"]) <= codes["f"].size / codes["p"].size
