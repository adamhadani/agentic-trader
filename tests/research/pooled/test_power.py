import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agentic_trader.research.pooled import power
from agentic_trader.research.pooled.campaign import LoadedProtocol, load_campaign_protocol
from agentic_trader.research.pooled.cube import _ARRAYS, CubeView, LabelCube
from agentic_trader.research.pooled.power import (
    ar1_scores,
    execute_power_check,
    run_power,
    summarize_power,
    synthetic_cube,
    tally_confirmed,
)
from agentic_trader.research.pooled.runner import CubeBuild


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol


def calendar() -> tuple[date, ...]:
    out, d = [], PROTOCOL.windows.discovery[0]
    while d <= PROTOCOL.windows.confirmation[1]:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def base_view(n_sessions=300, n_names=40, seed=0) -> CubeView:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 1.2, (n_sessions, n_names))
    return CubeView(
        sessions=calendar()[:n_sessions],
        symbols=tuple(f"S{j}" for j in range(n_names)),
        offset=0,
        eligible=np.ones(r.shape, bool),
        labelled=np.ones(r.shape, bool),
        r_gross=r,
        r_cost=r,
        holding=rng.integers(1, 4, r.shape).astype(np.int16),
        hit=rng.integers(0, 3, r.shape).astype(np.int8),
        tiebreak=rng.integers(0, 2**62, r.shape).astype(np.uint64),
        dollar_volume=np.ones(r.shape),
    )


def tiny_protocol():
    return PROTOCOL.model_copy(
        update={
            "bootstrap": PROTOCOL.bootstrap.model_copy(
                update={"discovery_draws": 200, "selection_draws": 200, "confirmation_draws": 200}
            ),
            "power": PROTOCOL.power.model_copy(
                update={"replicates": 2, "null_formulas": 5, "deltas": (0.0, 1.5), "detection_delta": 1.5}
            ),
        }
    )


def test_synthetic_cube_resamples_whole_sessions_onto_the_calendar():
    base = base_view()
    cube = synthetic_cube(base, calendar(), np.random.default_rng(1), block_mean=20)
    view = cube.window(calendar()[0], calendar()[-1])
    assert view.sessions == calendar()
    rows = {tuple(np.round(base.r_cost[i], 9)) for i in range(len(base.sessions))}
    assert all(tuple(np.round(view.r_cost[i], 9)) in rows for i in range(0, len(view.sessions), 97))


def test_ar1_scores_are_reproducible_and_persistent():
    a = ar1_scores((500, 10), 0.95, seed=7)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal((500, 10))
    ref = np.empty_like(noise)
    ref[0] = noise[0]
    for t in range(1, 500):
        ref[t] = 0.95 * ref[t - 1] + np.sqrt(1 - 0.95**2) * noise[t]
    assert np.allclose(a, ref)
    assert np.array_equal(a, ar1_scores((500, 10), 0.95, seed=7))
    lag1 = np.corrcoef(a[1:, 0], a[:-1, 0])[0, 1]
    assert 0.85 < lag1 < 0.99


def test_power_detects_a_huge_edge_and_rejects_the_null_on_a_small_run():
    result = run_power(tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64)
    assert result["curve"]["1.5"]["detection"] == 1.0
    assert result["curve"]["0.0"]["detection"] == 0.0
    assert result["status"] == "passed"
    assert result["curve"]["0.0"]["false_acceptance"] == 0.0
    assert result["curve"]["1.5"]["false_acceptance"] == 0.0


def test_curve_is_identical_for_any_worker_count_and_progress_reports_each_replicate():
    seen: list[str] = []
    serial = run_power(tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64)
    parallel = run_power(
        tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64, workers=2, progress=seen.append
    )
    assert parallel == serial
    assert sorted(seen) == ["replicate 1/2", "replicate 2/2"]


def test_worker_task_runs_exactly_the_requested_replicate():
    protocol, base, cal = tiny_protocol(), base_view(), calendar()
    power._init_worker(protocol, base, cal, "c" * 64)
    for rep in (0, 1):
        assert power._worker_replicate(rep) == (rep, power._replicate(protocol, base, cal, "c" * 64, rep))
    # replicates are seeded differently: their synthetic cubes differ
    cubes = [
        synthetic_cube(base, cal, np.random.default_rng([protocol.power.seed, rep]), 20).window(cal[0], cal[-1]).r_cost
        for rep in (0, 1)
    ]
    assert not np.array_equal(cubes[0], cubes[1])


def test_a_failing_replicate_surfaces_from_the_pool():
    with pytest.raises(ValueError, match="no cube session"):
        run_power(tiny_protocol(), base_view(), calendar()[:50], cohort_sha256="c" * 64, workers=2)


def test_synthetic_cube_keeps_every_array_aligned_and_fills_with_distinct_resamples():
    base = base_view()
    cal = calendar()
    cube = synthetic_cube(base, cal, np.random.default_rng(3), block_mean=20)
    view = cube.window(cal[0], cal[-1])
    keys = {tuple(np.round(row, 9)): i for i, row in enumerate(base.r_cost)}
    index = np.array([keys[tuple(np.round(row, 9))] for row in view.r_cost])
    for name in _ARRAYS:
        assert np.array_equal(getattr(view, name), getattr(base, name)[index], equal_nan=True)
    n = len(base.sessions)
    assert len(index) > 2 * n
    assert not np.array_equal(index[:n], index[n : 2 * n])


def test_tally_confirmed_separates_planted_from_null_acceptance():
    assert tally_confirmed({"planted", "null-001"}) == (True, True)
    assert tally_confirmed({"planted"}) == (True, False)
    assert tally_confirmed({"null-001"}) == (False, True)
    assert tally_confirmed(set()) == (False, False)


def _spec(**update):
    return PROTOCOL.power.model_copy(update={"replicates": 2, "deltas": (0.0, 0.15), "detection_delta": 0.15, **update})


def test_gate_fails_when_a_null_is_confirmed_at_zero():
    results = {0: {0.0: (False, True), 0.15: (True, False)}, 1: {0.0: (False, False), 0.15: (True, False)}}
    out = summarize_power(results, _spec())
    assert out["false_acceptance_at_zero"] == 0.5
    assert out["curve"]["0.0"]["false_acceptance"] == 0.5
    assert out["status"] == "gate_failed"


def test_gate_fails_when_detection_is_below_the_minimum():
    results = {0: {0.0: (False, False), 0.15: (True, False)}, 1: {0.0: (False, False), 0.15: (False, False)}}
    out = summarize_power(results, _spec())
    assert out["detection_at_gate"] == 0.5
    assert out["status"] == "gate_failed"


def test_a_null_confirmed_only_above_zero_does_not_fail_the_gate():
    results = {0: {0.0: (False, False), 0.15: (True, True)}, 1: {0.0: (False, False), 0.15: (True, False)}}
    out = summarize_power(results, _spec())
    assert out["false_acceptance_at_zero"] == 0.0
    assert out["curve"]["0.15"]["false_acceptance"] == 0.5
    assert out["status"] == "passed"


def test_gate_thresholds_are_inclusive():
    spec = PROTOCOL.power.model_copy(update={"replicates": 100, "deltas": (0.0, 0.15), "detection_delta": 0.15})
    assert (spec.min_detection, spec.max_false_acceptance) == (0.8, 0.05)
    results = {
        r: {0.0: (False, r < 5), 0.15: (r < 80, False)} for r in range(100)
    }  # detection exactly 0.80, false acceptance exactly 0.05
    out = summarize_power(results, spec)
    assert (out["detection_at_gate"], out["false_acceptance_at_zero"]) == (0.8, 0.05)
    assert out["status"] == "passed"
    worse = {**results, 5: {0.0: (False, True), 0.15: (True, False)}}
    assert summarize_power(worse, spec)["status"] == "gate_failed"
    weaker = {**results, 0: {0.0: (False, True), 0.15: (False, False)}, 79: {0.0: (False, False), 0.15: (False, False)}}
    assert summarize_power(weaker, spec)["status"] == "gate_failed"


COHORT = "c" * 64


def _built() -> CubeBuild:
    cal = calendar()
    base = base_view()
    cube = synthetic_cube(base, cal, np.random.default_rng(5), block_mean=20)
    real = LabelCube(
        spec_identity="spec",
        cohort_sha256=COHORT,
        sessions=cal,
        symbols=base.symbols,
        arrays={name: getattr(cube.window(cal[0], cal[-1]), name) for name in _ARRAYS},
        coverage={"sessions": len(cal), "skipped_sessions": {}, "unlabelled_by_year": {}, "eligible_by_year": {}},
    )
    return CubeBuild(cube=real, trading_days=cal, adjusted={}, bar_failures={}, static_used=())


def _executor_inputs(cohort_sha=COHORT):
    protocol = tiny_protocol().model_copy(update={"cohort_sha256": COHORT})
    loaded = LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("campaign.json"))
    return loaded, SimpleNamespace(sha256=cohort_sha)


def test_execute_power_check_writes_manifest_first_and_a_strict_result(tmp_path):
    loaded, cohort = _executor_inputs()
    built = _built()
    seen = {}

    async def build():
        seen["manifest"] = (tmp_path / "out" / "manifest.json").exists()
        seen["result"] = (tmp_path / "out" / "result.json").exists()
        return built

    result = asyncio.run(
        execute_power_check(loaded, tmp_path / "out", cohort=cohort, build=build, environment={"python": "x"})
    )
    assert seen == {"manifest": True, "result": False}
    assert result["status"] == "passed"
    assert result["cohort_sha256"] == COHORT
    assert result["campaign_protocol_sha256"] == "p" * 64
    assert result["cube_sha256"] == built.cube.sha256
    assert result["authorizes_promotion"] is False
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["status"] == "passed"
    assert on_disk["authorizes_promotion"] is False


def test_execute_power_check_records_a_build_failure(tmp_path):
    loaded, cohort = _executor_inputs()

    async def build():
        raise RuntimeError("provider down")

    result = asyncio.run(execute_power_check(loaded, tmp_path / "out", cohort=cohort, build=build, environment={}))
    assert result["status"] == "failed"
    assert "RuntimeError: provider down" in result["error"]
    assert result["authorizes_promotion"] is False
    assert json.loads((tmp_path / "out" / "result.json").read_text())["status"] == "failed"


def test_execute_power_check_refuses_a_mismatched_cohort_without_building(tmp_path):
    loaded, cohort = _executor_inputs(cohort_sha="d" * 64)
    called = []

    async def build():
        called.append(1)
        return _built()

    result = asyncio.run(execute_power_check(loaded, tmp_path / "out", cohort=cohort, build=build, environment={}))
    assert result["status"] == "failed"
    assert "cohort" in result["error"]
    assert called == []


def test_execute_power_check_refuses_an_existing_directory(tmp_path):
    loaded, cohort = _executor_inputs()
    (tmp_path / "out").mkdir()

    async def build():
        raise AssertionError("must not build")

    with pytest.raises(FileExistsError):
        asyncio.run(execute_power_check(loaded, tmp_path / "out", cohort=cohort, build=build, environment={}))
    assert list((tmp_path / "out").iterdir()) == []


def test_ar1_prefix_matches_the_full_field():
    full = ar1_scores((200, 4), 0.95, seed=3)
    assert np.array_equal(full[:80], ar1_scores((80, 4), 0.95, seed=3))
