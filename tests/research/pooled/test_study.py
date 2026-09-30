# tests/research/pooled/test_study.py
import asyncio
import gzip
import io
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled import study as study_module
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, LoadedCohort
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.entry import REPO_ROOT, load_pooled_entry
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.study import check_power_gate, execute_pooled_study


SYMBOLS = tuple(f"S{chr(65 + j // 26)}{chr(65 + j % 26)}" for j in range(40))
LOADED = load_pooled_entry(REPO_ROOT / "config/research/pooled/reversal-lowmax-v1.json")
COHORT = LoadedCohort(
    cohort=Cohort(
        id="pooled-cohort",
        version=1,
        survivorship="t",
        sources=(CohortSource(kind="config_groups", description="t", identity="x", symbols=SYMBOLS),),
        excluded={},
        symbols=SYMBOLS,
    ),
    sha256=LOADED.entry.cohort_sha256,
    path=Path("pooled-cohort.json"),
)


def _build(gross_shift: float = 0.0) -> CubeBuild:
    days = []
    d = date(2016, 1, 4)
    while d <= date(2026, 7, 31):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    sessions = tuple(x for x in days if x >= LOADED.entry.window.decisions[0])
    rng = np.random.default_rng(0)
    s, n = len(sessions), 40
    r = rng.normal(0, 1, (s, n))
    arrays = {
        "eligible": np.ones((s, n), bool),
        "labelled": np.ones((s, n), bool),
        "r_gross": r + gross_shift,
        "r_cost": r,
        "holding": np.full((s, n), 5, np.int16),
        "hit": np.ones((s, n), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        "dollar_volume": np.ones((s, n)),
    }
    cube = LabelCube(
        spec_identity=LOADED.entry.cube_spec().identity,
        cohort_sha256=COHORT.sha256,
        sessions=sessions,
        symbols=COHORT.cohort.symbols,
        arrays=arrays,
        coverage={"sessions": s, "skipped_sessions": {}, "eligible_by_year": {}, "unlabelled_by_year": {}},
    )
    index = pd.DatetimeIndex([datetime.combine(x, time(0), tzinfo=ET_TZ).astimezone(UTC) for x in days])
    adjusted = {}
    for symbol in COHORT.cohort.symbols:
        close = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        adjusted[symbol] = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=index
        )
    return CubeBuild(
        cube=cube,
        trading_days=tuple(days),
        adjusted=adjusted,
        bar_failures={"ZZZ": "1d/raw: empty"},
        static_used=("R00", "R01"),
    )


def _power(cube_sha256: str | None = None) -> dict:
    return {
        "status": "passed",
        "cohort_sha256": LOADED.entry.cohort_sha256,
        "campaign_protocol_sha256": LOADED.entry.campaign_protocol_sha256,
        "cube_sha256": cube_sha256,
    }


def _strict_json(path) -> dict:
    def refuse(token):
        raise AssertionError(f"non-finite JSON constant {token}")

    return json.loads(path.read_text(), parse_constant=refuse)


def _run_gated(tmp_path, power_result):
    build = _build()
    calls = []

    async def tracked():
        calls.append(1)
        return build

    result = asyncio.run(
        execute_pooled_study(
            LOADED, tmp_path / "out", cohort=COHORT, build=tracked, power_result=power_result, environment={}
        )
    )
    saved = _strict_json(tmp_path / "out" / "result.json")
    assert result["status"] == "failed" and saved["status"] == "failed"
    assert saved["authorizes_promotion"] is False
    assert not (tmp_path / "out" / "picks.csv.gz").exists()
    return result, calls, build


@pytest.mark.parametrize(
    "override",
    [
        {"status": "gate_failed"},
        {"cohort_sha256": "0" * 64},
        {"campaign_protocol_sha256": "1" * 64},
    ],
)
def test_executor_refuses_without_a_matching_passed_power_check(tmp_path, override):
    result, calls, _ = _run_gated(tmp_path, {**_power(), **override})
    assert "power" in result["error"]
    assert calls == []


def test_executor_refuses_a_power_check_on_a_different_cube(tmp_path):
    result, calls, _ = _run_gated(tmp_path, _power("f" * 64))
    assert calls == [1]
    assert "cube" in result["error"]


def test_newey_west_lag_follows_the_hold_not_the_bootstrap(tmp_path, monkeypatch):
    lags = []
    real = study_module.calendar_time_newey_west

    def spy(rows, n_sessions, lag):
        lags.append(lag)
        return real(rows, n_sessions, lag=lag)

    monkeypatch.setattr(study_module, "calendar_time_newey_west", spy)
    build = _build()

    async def fake_build():
        return build

    result = asyncio.run(
        execute_pooled_study(
            LOADED,
            tmp_path / "out",
            cohort=COHORT,
            build=fake_build,
            power_result=_power(build.cube.sha256),
            environment={},
        )
    )
    assert result["status"] == "completed", result.get("error")
    assert lags == [LOADED.entry.bracket.max_hold_sessions - 1]


def test_power_gate_refuses_a_failed_or_mismatched_result():
    kwargs = {
        "cohort_sha256": LOADED.entry.cohort_sha256,
        "campaign_protocol_sha256": LOADED.entry.campaign_protocol_sha256,
    }
    check_power_gate(_power(), **kwargs)
    for bad in ({**_power(), "status": "gate_failed"}, {**_power(), "cohort_sha256": "0" * 64}):
        with pytest.raises(ValueError, match="power"):
            check_power_gate(bad, **kwargs)
    with pytest.raises(ValueError, match="cube"):
        check_power_gate(_power("a" * 64), **kwargs, cube_sha256="b" * 64)


def test_study_writes_manifest_first_and_a_complete_result(tmp_path):
    build = _build()
    seen = {}

    async def fake_build():
        seen["manifest_existed"] = (tmp_path / "out" / "manifest.json").exists()
        return build

    result = asyncio.run(
        execute_pooled_study(
            LOADED,
            tmp_path / "out",
            cohort=COHORT,
            build=fake_build,
            power_result=_power(build.cube.sha256),
            environment={"test": True},
        )
    )
    assert seen["manifest_existed"]
    assert result["status"] == "completed", result.get("error")
    assert result["decision"] in ("eligible_for_probe", "failed")
    assert set(result["pass_rule"]) == {"p1", "p2", "p3", "p4", "passes"}
    assert "two_way_clustered" in result["cross_checks"]
    _strict_json(tmp_path / "out" / "result.json")
    picks = pd.read_csv(io.BytesIO(gzip.decompress((tmp_path / "out" / "picks.csv.gz").read_bytes())))
    assert {"session", "symbol"} <= set(picks.columns)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["authorizes_promotion"] is False
    assert manifest["campaign_protocol_sha256"] == LOADED.entry.campaign_protocol_sha256
    assert (tmp_path / "out" / "picks.csv.gz").exists()
    # The study reports what the cube's build recorded.
    assert result["bar_failures"] == {"ZZZ": "1d/raw: empty"} and result["static_used"] == ["R00", "R01"]
    assert result["cube"] == {"sha256": build.cube.sha256, "coverage": build.cube.coverage}


def _completed(tmp_path, build: CubeBuild) -> dict:
    async def fake_build():
        return build

    result = asyncio.run(
        execute_pooled_study(
            LOADED,
            tmp_path / "out",
            cohort=COHORT,
            build=fake_build,
            power_result=_power(build.cube.sha256),
            environment={},
        )
    )
    assert result["status"] == "completed", result.get("error")
    return result


def test_the_zero_cost_edge_is_reported_alongside_and_equals_the_decisive_one_without_costs(tmp_path):
    result = _completed(tmp_path, _build())  # r_gross == r_cost in this fixture
    assert result["gross"] == {"paired_edge": result["pass_rule"]["p2"], "leg_mean": result["pass_rule"]["p1"]}
    assert _strict_json(tmp_path / "out" / "result.json")["gross"]["paired_edge"]["n_sessions"] > 0


def test_the_zero_cost_edge_reads_the_gross_column(tmp_path):
    result = _completed(tmp_path, _build(gross_shift=0.1))  # every cell: R at 0 bps = R at 5 bps + 0.1
    cost_leg, cost_edge = result["pass_rule"]["p1"], result["pass_rule"]["p2"]
    assert result["gross"]["leg_mean"]["mean"] == pytest.approx(cost_leg["mean"] + 0.1)
    assert result["gross"]["leg_mean"]["n"] == cost_leg["n"]
    # The control moves with the picks, so the paired edge is unchanged.
    assert result["gross"]["paired_edge"]["mean"] == pytest.approx(cost_edge["mean"])
    assert result["gross"]["paired_edge"]["n_sessions"] == cost_edge["n_sessions"]


def test_study_records_failure_instead_of_raising(tmp_path):
    async def broken():
        raise RuntimeError("provider down")

    result = asyncio.run(
        execute_pooled_study(
            LOADED, tmp_path / "out", cohort=COHORT, build=broken, power_result=_power(), environment={}
        )
    )
    assert result["status"] == "failed" and "provider down" in result["error"]
    saved = _strict_json(tmp_path / "out" / "result.json")
    assert saved["status"] == "failed" and saved["authorizes_promotion"] is False
