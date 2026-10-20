# tests/research/pooled/test_null_check.py
import asyncio
import json
import pickle
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import beta

from agentic_trader.research.alpha.search import TypedGeneticSearch, canonical_expression
from agentic_trader.research.pooled import null_check
from agentic_trader.research.pooled.campaign import LoadedProtocol, NullCheckSpec, load_campaign_protocol
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.null_check import (
    demeaned,
    execute_null_check,
    null_replicate,
    run_null_check,
    summarize_null,
)
from agentic_trader.research.pooled.scoring import FrozenPanels, ScoreBook, score_book
from agentic_trader.research.setups.study import _finite_json
from tests.research.pooled.mini_world import (
    ADJUSTED,
    BAR_DAYS,
    COHORT,
    NAMES,
    SESSIONS,
    book,
    cube_build,
    events_cube_build,
    hourly_bars,
    intraday_cube_build,
    label_cube,
    mini_events_protocol,
    mini_fixed_set_protocol,
    mini_intraday_protocol,
    mini_protocol,
    mini_short_protocol,
    short_cube_build,
)


def uneven_view(s=200, n=6, seed=0) -> CubeView:
    rng = np.random.default_rng(seed)
    labelled = rng.random((s, n)) > 0.2
    r = rng.normal(0.0, 1.0, (s, n)) + np.arange(n) * 0.3  # persistent name effects
    r = np.where(labelled, r, np.nan)
    return CubeView(
        sessions=SESSIONS[:s],
        symbols=NAMES[:n],
        offset=0,
        eligible=labelled | (rng.random((s, n)) > 0.5),
        labelled=labelled,
        r_gross=r + 0.01,
        r_cost=r,
        holding=np.ones((s, n), np.int16),
        hit=np.ones((s, n), np.int8),
        tiebreak=rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        dollar_volume=np.ones((s, n)),
    )


def test_demeaning_removes_name_means_and_keeps_each_session_cross_section():
    base = uneven_view()
    out = demeaned(base)
    labelled = base.labelled
    for column in ("r_cost", "r_gross"):
        before, after = getattr(base, column), getattr(out, column)
        overall = before[labelled].mean()
        for j in range(labelled.shape[1]):
            cells = labelled[:, j]
            assert after[cells, j].mean() == pytest.approx(overall)
            shift = after[cells, j] - before[cells, j]
            assert np.allclose(shift, shift[0])  # one constant per name: co-movement is untouched
        assert np.isnan(after[~labelled]).all()
    assert out.eligible is base.eligible and out.labelled is base.labelled and out.holding is base.holding


def test_a_null_replicate_runs_the_whole_campaign_and_reports_every_seed_t():
    protocol = mini_protocol()
    cube = label_cube()
    base = demeaned(cube.window(*protocol.windows.discovery))
    rep = null_replicate(protocol, base, cube.sessions, COHORT, book(), 0)
    seeds = {canonical_expression(seed) for family in protocol.families for seed in family.seeds}
    assert rep["replicate"] == 0 and rep["evaluated"] == 9 and rep["errors"] == 0
    assert set(rep["seed_t"]) == seeds
    assert rep["status"] in {"no_finalists", "no_confirmation_candidates", "none_confirmed", "confirmed"}
    assert _finite_json(null_replicate(protocol, base, cube.sessions, COHORT, book(), 0)) == _finite_json(rep)


def test_the_summary_gates_on_the_false_acceptance_count_and_reports_seed_t():
    spec = NullCheckSpec(replicates=4, max_false_acceptances=1, seed=1)

    def rep(confirmed=(), survivors=0, carried=0, frozen=0, seed_t=None, errors=0):
        return {
            "confirmed": list(confirmed),
            "survivors": survivors,
            "carried": carried,
            "frozen": frozen,
            "seed_t": seed_t or {},
            "errors": errors,
        }

    reps = [
        rep(seed_t={"a": 1.0, "b": -1.0}),
        rep(confirmed=["x"], survivors=2, carried=1, frozen=1, seed_t={"a": float("nan")}, errors=2),
        rep(seed_t={"a": 3.0}, errors=1),
        rep(),
    ]
    summary = summarize_null(reps, spec)
    # Three formulas raised: the check fails closed whatever the false-acceptance count.
    assert summary["status"] == "gate_failed" and summary["false_acceptances"] == 1
    assert summary["errors_total"] == 3 and "3 formula evaluation" in summary["reason"]
    assert summary["false_acceptance_rate"] == 0.25
    assert summary["false_acceptance_upper95"] == pytest.approx(beta.ppf(0.95, 2, 3))
    assert summary["stage_counts"] == {"with_survivors": 1, "with_carried": 1, "reached_confirmation": 1}
    assert summary["seed_t"]["n"] == 3
    assert summary["seed_t"]["mean"] == pytest.approx(1.0) and summary["seed_t"]["sd"] == pytest.approx(2.0)
    assert summarize_null([*reps[:3], rep(confirmed=["y"])], spec)["status"] == "gate_failed"
    clean = [rep(seed_t={"a": 1.0}), rep(confirmed=["x"]), rep(), rep()]
    passed = summarize_null(clean, spec)
    assert passed["status"] == "passed" and passed["errors_total"] == 0 and passed["reason"] is None
    too_many = summarize_null([rep(confirmed=["x"]) for _ in range(4)], spec)
    assert too_many["status"] == "gate_failed" and "false acceptance" in too_many["reason"]


def test_the_null_check_is_identical_for_any_worker_count():
    protocol = mini_protocol()
    cube = label_cube()
    discovery = cube.window(*protocol.windows.discovery)
    kwargs = {"book": book(), "adjusted": ADJUSTED, "trading_days": BAR_DAYS, "symbols": NAMES}
    serial = run_null_check(protocol, discovery, cube.sessions, COHORT, workers=1, **kwargs)
    pooled = run_null_check(protocol, discovery, cube.sessions, COHORT, workers=2, **kwargs)
    assert _finite_json(serial) == _finite_json(pooled)
    assert serial["replicates"] == 3 and len(serial["replicates_detail"]) == 3
    assert serial["errors_total"] == 0  # the mini world evaluates every formula cleanly
    assert serial["status"] == ("passed" if serial["false_acceptances"] == 0 else "gate_failed")


def test_execute_null_check_writes_its_manifest_first_and_binds_the_cube(tmp_path):
    cube = label_cube()
    seen = {}

    async def build():
        seen["manifest"] = (tmp_path / "out" / "manifest.json").exists()
        return cube_build(cube)

    power_result = {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": cube.sha256,
    }
    result = asyncio.run(
        execute_null_check(
            LoadedProtocol(protocol=mini_protocol(), sha256="p" * 64, path=Path("campaign.json")),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result=power_result,
            environment={},
        )
    )
    assert seen == {"manifest": True}
    assert result["status"] in {"passed", "gate_failed"} and result["cube_sha256"] == cube.sha256
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["check"] == "null_check"
    json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)


def test_a_fixed_set_null_check_scores_exactly_the_seeds_and_pins_the_protocol_file(tmp_path, monkeypatch):
    path = tmp_path / "campaign-fixed.json"
    path.write_text(mini_fixed_set_protocol().model_dump_json())
    loaded = load_campaign_protocol(path)  # a real file hash, as a v4 run's artifacts carry
    protocol = loaded.protocol
    seeds = list(protocol.seed_expressions)

    def mutate(self, expression):
        raise AssertionError("a fixed-set search never mutates")

    monkeypatch.setattr(TypedGeneticSearch, "mutate", mutate)
    searches = []
    real = null_check.run_search_stages

    def recording(*args, **kwargs):
        outcome, search = real(*args, **kwargs)
        searches.append(search)
        return outcome, search

    monkeypatch.setattr(null_check, "run_search_stages", recording)
    cube = label_cube()
    power_result = {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": loaded.sha256,
        "cube_sha256": cube.sha256,
    }

    async def build():
        return cube_build(cube)

    result = asyncio.run(
        execute_null_check(
            loaded,
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result=power_result,
            environment={},
        )
    )
    # Every replicate's search scored exactly the predeclared seeds, in file order, and nothing else.
    assert len(searches) == protocol.null_check.replicates == 3
    for search in searches:
        assert [(r["expression"], r["status"]) for r in search.records] == [(seed, "evaluated") for seed in seeds]
        assert [run.summary()["mutations"] for run in search.runs] == [0, 0, 0]
        assert all(run.stopped_short is None for run in search.runs)
    details = result["replicates_detail"]
    assert all(rep["evaluated"] == len(seeds) and set(rep["seed_t"]) == set(seeds) for rep in details)
    # The gate counts false acceptances exactly as for a genetic protocol.
    false = sum(1 for rep in details if rep["confirmed"])
    assert result["false_acceptances"] == false and result["errors_total"] == 0
    assert result["status"] == ("passed" if false <= protocol.null_check.max_false_acceptances else "gate_failed")
    assert result["campaign_protocol_sha256"] == loaded.sha256 and result["cube_sha256"] == cube.sha256
    out = tmp_path / "out"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["check"] == "null_check" and manifest["campaign_protocol_sha256"] == loaded.sha256
    dumped = json.loads((out / "protocol.json").read_text())
    assert dumped["sha256"] == loaded.sha256 and dumped["search_mode"] == "fixed_set"
    assert dumped["power_search"] is None
    for name in ("manifest.json", "result.json"):
        assert "search_power" not in (out / name).read_text()


class _InlinePool:
    """Stands in for ProcessPoolExecutor: runs the initializer and every task in this process."""

    initargs: tuple = ()

    def __init__(self, *, max_workers, initializer, initargs):
        type(self).initargs = initargs
        initializer(*initargs)

    def submit(self, fn, *args):
        future: Future = Future()
        future.set_result(fn(*args))
        return future

    def shutdown(self, **kwargs):
        return None


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        (mini_intraday_protocol(), FrozenPanels),
        (mini_fixed_set_protocol(), FrozenPanels),
        (mini_protocol(), ScoreBook),
    ],
)
def test_the_pool_gets_frozen_seed_panels_for_a_fixed_set_and_a_rebuilt_book_for_a_genetic_search(
    monkeypatch, protocol, expected
):
    monkeypatch.setattr(null_check, "ProcessPoolExecutor", _InlinePool)
    cube = label_cube()
    built = intraday_cube_build(cube) if protocol.score_inputs == "hourly" else cube_build(cube)
    discovery = cube.window(*protocol.windows.discovery)
    kwargs = {"adjusted": built.adjusted, "trading_days": built.trading_days, "symbols": cube.symbols}
    pooled = run_null_check(
        protocol, discovery, cube.sessions, COHORT, book=score_book(protocol, built), workers=2, **kwargs
    )
    assert type(null_check._WORKER["book"]) is expected
    if expected is FrozenPanels:
        assert null_check._WORKER["book"].score_inputs == protocol.score_inputs
        assert _InlinePool.initargs[5] == {}  # the bars are not shipped to the workers
    serial = run_null_check(protocol, discovery, cube.sessions, COHORT, book=score_book(protocol, built), **kwargs)
    assert _finite_json(serial) == _finite_json(pooled)
    assert serial["errors_total"] == 0


def test_a_hourly_null_check_pickles_without_the_bars():
    protocol = mini_intraday_protocol()
    frozen = score_book(protocol, intraday_cube_build(label_cube())).freeze(protocol.seed_expressions)
    assert (
        pickle.loads(pickle.dumps(frozen)).panel(protocol.seed_expressions[0]).shape
        == frozen.panel(protocol.seed_expressions[0]).shape
    )
    assert hourly_bars()  # the hourly world exists; only the panels cross the process boundary


def test_the_null_check_manifest_records_its_score_inputs(tmp_path):
    protocol = mini_intraday_protocol()
    cube = label_cube()

    async def build():
        return intraday_cube_build(cube)

    asyncio.run(
        execute_null_check(
            LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("campaign.json")),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result={
                "status": "passed",
                "cohort_sha256": COHORT,
                "campaign_protocol_sha256": "p" * 64,
                "cube_sha256": cube.sha256,
            },
            environment={},
        )
    )
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["score_inputs"] == "hourly" and manifest["score_read"] == {"bars_before_decision": 1}
    result = json.loads((tmp_path / "out" / "result.json").read_text())
    assert result["status"] in {"passed", "gate_failed"}, result


def test_the_null_check_records_event_inputs_and_event_coverage(tmp_path):
    protocol = mini_events_protocol()
    cube = label_cube()
    built = events_cube_build(cube)

    async def build():
        return built

    result = asyncio.run(
        execute_null_check(
            LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("events.json")),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result={
                "status": "passed",
                "cohort_sha256": COHORT,
                "campaign_protocol_sha256": "p" * 64,
                "cube_sha256": cube.sha256,
            },
            environment={},
        )
    )
    assert result["status"] in {"passed", "gate_failed"}, result
    assert built.events is not None and result["event_coverage"] == asdict(built.events.coverage)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["event_inputs"] == protocol.event_inputs.model_dump(mode="json")
    assert manifest["short_inputs"] is None and result["short_coverage"] is None
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["event_coverage"]["identity_breaks"] == {}


def test_the_null_check_records_short_inputs_and_short_coverage(tmp_path):
    protocol = mini_short_protocol()
    cube = label_cube()
    built = short_cube_build(cube)

    async def build():
        return built

    result = asyncio.run(
        execute_null_check(
            LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("short.json")),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result={
                "status": "passed",
                "cohort_sha256": COHORT,
                "campaign_protocol_sha256": "p" * 64,
                "cube_sha256": cube.sha256,
            },
            environment={},
        )
    )
    assert result["status"] in {"passed", "gate_failed"}, result
    assert built.short is not None and result["short_coverage"] == asdict(built.short.coverage)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["short_inputs"] == protocol.short_inputs.model_dump(mode="json")
    assert manifest["event_inputs"] is None and result["event_coverage"] is None
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert set(on_disk["short_coverage"]["first_finite"]) == set(built.cube.symbols)
