# tests/research/pooled/test_null_check.py
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import beta

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import LoadedProtocol, NullCheckSpec
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.null_check import (
    demeaned,
    execute_null_check,
    null_replicate,
    run_null_check,
    summarize_null,
)
from agentic_trader.research.setups.study import _finite_json
from tests.research.pooled.mini_world import (
    ADJUSTED,
    BAR_DAYS,
    COHORT,
    NAMES,
    SESSIONS,
    book,
    cube_build,
    label_cube,
    mini_protocol,
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
    assert summary["status"] == "passed" and summary["false_acceptances"] == 1
    assert summary["errors_total"] == 3
    assert summary["false_acceptance_rate"] == 0.25
    assert summary["false_acceptance_upper95"] == pytest.approx(beta.ppf(0.95, 2, 3))
    assert summary["stage_counts"] == {"with_survivors": 1, "with_carried": 1, "reached_confirmation": 1}
    assert summary["seed_t"]["n"] == 3
    assert summary["seed_t"]["mean"] == pytest.approx(1.0) and summary["seed_t"]["sd"] == pytest.approx(2.0)
    assert summarize_null([*reps[:3], rep(confirmed=["y"])], spec)["status"] == "gate_failed"


def test_the_null_check_is_identical_for_any_worker_count():
    protocol = mini_protocol()
    cube = label_cube()
    discovery = cube.window(*protocol.windows.discovery)
    kwargs = {"adjusted": ADJUSTED, "trading_days": BAR_DAYS, "symbols": NAMES}
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
