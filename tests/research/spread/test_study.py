# tests/research/spread/test_study.py
import asyncio
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.study import _finite_json
from agentic_trader.research.spread import study
from agentic_trader.research.spread.panel import PanelBuild, SpreadPanel, synthetic_panel
from agentic_trader.research.spread.protocol import load_spread_cohort
from agentic_trader.research.spread.study import (
    StageRun,
    execute_spread_null,
    execute_spread_power,
    execute_spread_study,
    run_stage,
    spread_gate,
    truncate_panel,
)
from tests.research.spread.test_schedule import weekdays
from tests.research.spread.world import COHORT_DOC, ENVIRONMENT, gate_dir, write_world


SESSIONS = weekdays(date(2016, 1, 4), date(2019, 12, 31))


@pytest.fixture
def world(tmp_path):
    return write_world(tmp_path / "world")


def planted_world(loaded, cohort, seed=5):
    return synthetic_panel(cohort.cohort, SESSIONS, loaded.protocol.power, seed=seed)


def test_run_stage_on_a_planted_world_passes_and_reports_windows(world):
    loaded, cohort = world
    panel, planted = planted_world(loaded, cohort)
    run = run_stage(
        truncate_panel(panel, through=date(2018, 12, 31)), loaded.protocol, cohort.cohort, "discovery", planted=planted
    )
    assert isinstance(run, StageRun) and run.status == "completed" and run.error is None
    assert run.evaluation["passes"], run.evaluation
    assert len(run.windows) >= 15 and run.windows[0]["index"] == 0
    first = run.windows[0]
    assert set(first) >= {"index", "trading", "formation_counts", "selected", "planted_recall", "planted_share"}
    assert first["trading"][0] >= "2016-06-01" and run.windows[-1]["trading"][1] <= "2018-12-31"
    assert np.mean([w["planted_share"] for w in run.windows]) > 0.5
    length = sum(1 for _ in run.market)
    assert all(len(v) == length for v in run.lane_by_cost.values()) and set(run.lane_by_cost) == {0.0, 5.0, 10.0}
    assert run.trades and all(t.exit_reason in ("reverted", "stop", "window_end") for t in run.trades)


def test_truncate_panel_keeps_only_sessions_through_the_date(world):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    cut = truncate_panel(panel, through=date(2018, 12, 31))
    assert cut.sessions[-1] <= date(2018, 12, 31) < panel.sessions[-1]
    assert len(cut.closes) == len(cut.sessions) == len(cut.opens)


def test_stage_fails_closed_on_thin_coverage(world):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    closes = panel.closes.copy()
    closes.loc[closes.index[100], [c for c in closes.columns if c != "SPY"][:11]] = np.nan  # 3 names remain complete
    thin = SpreadPanel(sessions=panel.sessions, closes=closes, opens=panel.opens)
    run = run_stage(thin, loaded.protocol, cohort.cohort, "discovery")
    assert (
        run.status == "coverage_failed"
        and "eligible names" in (run.error or "")
        and not run.evaluation.get("passes", False)
    )


def test_power_check_writes_manifest_first_and_passes_on_the_planted_world(world, tmp_path):
    loaded, cohort = world
    out = tmp_path / "power"
    result = asyncio.run(execute_spread_power(loaded, out, cohort=cohort, environment=ENVIRONMENT))
    manifest = json.loads((out / "manifest.json").read_text())
    assert (
        manifest["check"] == "power_a"
        and manifest["protocol_sha256"] == loaded.sha256
        and manifest["authorizes_promotion"] is False
    )
    assert result["status"] == "passed" and result["passes"] >= 1 and len(result["seeds"]) == 2
    assert json.loads((out / "result.json").read_text())["check"] == "power_a"
    assert result["authorizes_promotion"] is False
    with pytest.raises(FileExistsError):
        asyncio.run(execute_spread_power(loaded, out, cohort=cohort, environment=ENVIRONMENT))


def test_power_check_records_a_cohort_mismatch_as_failed(world, tmp_path):
    loaded, _ = world
    other_path = tmp_path / "other-cohort.json"
    other_path.write_text(json.dumps({**COHORT_DOC, "survivorship": "another cohort"}))
    other_cohort = load_spread_cohort(other_path)
    result = asyncio.run(execute_spread_power(loaded, tmp_path / "p2", cohort=other_cohort, environment=ENVIRONMENT))
    assert result["status"] == "failed" and result["error"].startswith("ValueError")


def _build(panel):
    async def build():
        return PanelBuild(panel=panel, bar_failures={"ZZZ": "empty"})

    return build


def test_null_check_runs_on_shifted_discovery_bars_only(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    poisoned = panel.closes.copy()
    poisoned.loc[poisoned.index > pd.Timestamp("2018-12-31")] = -1.0  # confirmation sessions must never be read
    result = asyncio.run(
        execute_spread_null(
            loaded,
            tmp_path / "null",
            cohort=cohort,
            build=_build(SpreadPanel(panel.sessions, poisoned, panel.opens)),
            environment=ENVIRONMENT,
        )
    )
    assert result["status"] in ("passed", "failed") and result["check"] == "null_c" and len(result["seeds"]) == 2
    assert result["bar_failures"] == {"ZZZ": "empty"} and "error" not in result
    manifest = json.loads((tmp_path / "null" / "manifest.json").read_text())
    assert manifest["check"] == "null_c" and manifest["reads"].startswith("discovery")


def test_spread_gate_checks_kind_status_hashes_and_revision(world, tmp_path):
    loaded, cohort = world
    good = gate_dir(tmp_path / "g", check="power_a", loaded=loaded, cohort=cohort)
    assert (
        spread_gate(
            good, check="power_a", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )["status"]
        == "passed"
    )
    with pytest.raises(ValueError, match="not 'null_c'"):
        spread_gate(
            good, check="null_c", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )
    with pytest.raises(ValueError, match="revision"):
        spread_gate(
            good, check="power_a", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="def5678"
        )
    with pytest.raises(ValueError, match="protocol"):
        spread_gate(good, check="power_a", protocol_sha256="0" * 64, cohort_sha256=cohort.sha256, revision="abc1234")
    failed = gate_dir(tmp_path / "f", check="null_c", loaded=loaded, cohort=cohort, status="failed")
    with pytest.raises(ValueError, match="not passed"):
        spread_gate(
            failed, check="null_c", protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, revision="abc1234"
        )


class Confirm:
    def __init__(self, error: Exception | None = None, directory=None):
        self.calls: list[tuple] = []
        self.error = error
        self.directory = directory

    async def __call__(self, interval, detail):
        self.calls.append((interval, detail))
        if self.directory is not None:
            assert not (self.directory / "confirmation-lane.csv.gz").exists()
        if self.error:
            raise self.error
        return {"start": interval[0].isoformat(), "end": interval[1].isoformat(), "consumed_at": "t"}


def _gates(tmp_path, loaded, cohort):
    return gate_dir(tmp_path / "pa", check="power_a", loaded=loaded, cohort=cohort), gate_dir(
        tmp_path / "nc", check="null_c", loaded=loaded, cohort=cohort
    )


def test_study_confirms_after_journaling_and_records_both_stages(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm(directory=tmp_path / "study")
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={"scope": "test", "dialect": "sqlite", "database": "x"},
            confirm=confirm,
        )
    )
    assert result["status"] == "completed", result
    assert result["decision"] in ("confirmed", "failed_confirmation") and result["discovery"]["evaluation"]["passes"]
    assert confirm.calls == [((date(2019, 1, 2), date(2019, 12, 31)), confirm.calls[0][1])]
    assert confirm.calls[0][1]["protocol_sha256"] == loaded.sha256 and "discovery" in confirm.calls[0][1]
    assert confirm.calls[0][1]["code_revision"] == "abc1234"
    assert set(confirm.calls[0][1]["discovery"]) == {"s1", "s2", "s3", "s4"}
    assert (
        result["confirmation_record"]["consumed_at"] == "t"
        and result["confirmation"]["evaluation"]["s1"]["n_sessions"] > 0
    )
    manifest = json.loads((tmp_path / "study" / "manifest.json").read_text())
    assert (
        manifest["journal"] == {"scope": "test", "dialect": "sqlite", "database": "x"}
        and manifest["authorizes_promotion"] is False
    )
    assert (tmp_path / "study" / "discovery-trades.csv.gz").exists() and (
        tmp_path / "study" / "confirmation-lane.csv.gz"
    ).exists()
    assert result["gates"]["power"]["status"] == "passed" and result["authorizes_promotion"] is False


def test_study_records_confirmation_refusal_without_reading_confirmation(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm(ValueError("spread confirmation interval already consumed"))
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=confirm,
        )
    )
    assert (
        result["status"] == "failed" and result["error"] == "ValueError: spread confirmation interval already consumed"
    )
    assert result.get("confirmation") is None and len(confirm.calls) == 1
    assert result["discovery"]["evaluation"]["passes"] is True
    assert result["gates"]["power"]["status"] == "passed"
    assert (tmp_path / "study" / "discovery-trades.csv.gz").exists() and not (
        tmp_path / "study" / "confirmation-lane.csv.gz"
    ).exists()


def test_study_failing_discovery_never_calls_confirm(tmp_path):
    strict = {
        "min_closed_trades": 100000,
        "min_windows": 2,
        "min_positive_window_fraction": 0.6,
        "max_abs_market_beta": 0.2,
        "trim_fraction": 0.01,
    }
    loaded, cohort = write_world(tmp_path / "strict", pass_rules=strict)
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    confirm = Confirm()
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=confirm,
        )
    )
    assert result["status"] == "completed" and result["decision"] == "failed_discovery" and confirm.calls == []
    assert result["discovery"]["evaluation"]["s3"]["holds"] is False and result["confirmation"] is None


def test_study_refuses_dirty_revision_and_mismatched_gates_before_building(world, tmp_path):
    loaded, cohort = world
    power, null = _gates(tmp_path, loaded, cohort)
    built = []

    async def build():
        built.append(1)
        raise AssertionError("must not build")

    dirty = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "d",
            cohort=cohort,
            build=build,
            environment={"runtime": {"revision": "abc1234-dirty"}},
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=Confirm(),
        )
    )
    assert dirty["status"] == "failed" and "dirty" in dirty["error"] and built == []
    wrong = gate_dir(tmp_path / "wrong", check="null_c", loaded=loaded, cohort=cohort, revision="other")
    mismatched = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "m",
            cohort=cohort,
            build=build,
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=wrong,
            journal={},
            confirm=Confirm(),
        )
    )
    assert mismatched["status"] == "failed" and "revision" in mismatched["error"] and built == []


def test_null_check_fails_when_a_seed_cannot_complete(world, tmp_path):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    closes = panel.closes.copy()
    closes.loc[closes.index[100], [c for c in closes.columns if c != "SPY"][:11]] = np.nan
    thin = SpreadPanel(sessions=panel.sessions, closes=closes, opens=panel.opens)
    result = asyncio.run(
        execute_spread_null(loaded, tmp_path / "null", cohort=cohort, build=_build(thin), environment=ENVIRONMENT)
    )
    assert result["status"] == "failed" and result["incomplete_seeds"] == [0, 1] and result["passes"] == 0


def test_study_keeps_confirmation_record_and_discovery_when_confirmation_stage_raises(world, tmp_path, monkeypatch):
    loaded, cohort = world
    panel, _ = planted_world(loaded, cohort)
    power, null = _gates(tmp_path, loaded, cohort)
    real = study.run_stage

    def flaky(panel_, protocol, cohort_, stage, **kwargs):
        if stage == "confirmation":
            raise RuntimeError("boom")
        return real(panel_, protocol, cohort_, stage, **kwargs)

    monkeypatch.setattr(study, "run_stage", flaky)
    result = asyncio.run(
        execute_spread_study(
            loaded,
            tmp_path / "study",
            cohort=cohort,
            build=_build(panel),
            environment=ENVIRONMENT,
            power_dir=power,
            null_dir=null,
            journal={},
            confirm=Confirm(),
        )
    )
    assert result["status"] == "failed" and result["error"] == "RuntimeError: boom"
    assert result["confirmation_record"]["consumed_at"] == "t" and result["confirmation"] is None
    assert result["discovery"]["evaluation"]["passes"] is True
    saved = json.loads((tmp_path / "study" / "result.json").read_text())
    assert saved["confirmation_record"]["consumed_at"] == "t"


def test_finite_json_converts_numpy_scalars_and_nan():
    out = _finite_json({"a": np.int64(3), "b": np.bool_(True), "c": (np.float64(1.5), float("nan"))})
    assert out == {"a": 3, "b": True, "c": [1.5, None]}
    assert type(out["a"]) is int and type(out["b"]) is bool
