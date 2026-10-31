import json

import numpy as np
import pytest

from agentic_trader.research.ic import gate as gate_module
from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.checks import execute_checks
from agentic_trader.research.ic.gate import execute_gate
from agentic_trader.research.ic.statistic import rank_ic_series
from tests.research.ic.world import (
    as_builder,
    environment,
    loaded_composite,
    long_world,
    passed_check,
    poisoned_world,
    sector_map,
    shipped_composite,
    shrunk,
    world_sectors,
)


PROTOCOL = shipped_composite().protocol


def _setup(tmp_path, *, revision="abc123", reported_horizons=(20,)):
    world = long_world()
    secondary = long_world(prefix="T")
    sectors = world_sectors(world, secondary)
    protocol = shrunk(
        PROTOCOL, window=world.window, reported_horizons=reported_horizons, sectors_sha256=sectors.sha256, draws=100
    )
    loaded = loaded_composite(protocol)
    checks = passed_check(
        tmp_path / "checks",
        composite_sha256=loaded.sha256,
        sectors_sha256=sectors.sha256,
        cube_sha256=world.build.cube.sha256,
        revision=revision,
    )
    return world, secondary, sectors, loaded, checks


async def _run(loaded, output, checks, world, secondary, sectors, *, revision="abc123"):
    return await execute_gate(
        loaded,
        output,
        checks_dir=checks,
        build=as_builder(world),
        build_secondary=as_builder(secondary),
        sectors=sectors,
        environment=environment(revision),
        progress=None,
    )


def _never(*_args, **_kwargs):
    raise AssertionError("a refused gate reads nothing")


async def _refused(tmp_path, monkeypatch, result_dir, *, checks, loaded, sectors, world, secondary, revision="abc123"):
    monkeypatch.setattr(gate_module, "composite_panel", _never)
    result = await _run(loaded, result_dir, checks, world, secondary, sectors, revision=revision)
    assert result["status"] == "refused"
    assert "decision" not in result
    assert result["authorizes_promotion"] is False
    assert json.loads((result_dir / "result.json").read_text())["status"] == "refused"
    assert (result_dir / "manifest.json").exists()
    assert not (result_dir / "tear_sheet.txt").exists()
    return result


async def test_gate_refuses_without_a_passed_check(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, _ = _setup(tmp_path)
    failed = passed_check(
        tmp_path / "failed",
        composite_sha256=loaded.sha256,
        sectors_sha256=sectors.sha256,
        cube_sha256=world.build.cube.sha256,
        status="failed",
    )
    args = {"loaded": loaded, "sectors": sectors, "world": world, "secondary": secondary}
    result = await _refused(tmp_path, monkeypatch, tmp_path / "a", checks=failed, **args)
    assert "not passed" in result["reason"]
    result = await _refused(tmp_path, monkeypatch, tmp_path / "b", checks=tmp_path / "nothing-here", **args)
    assert "no readable" in result["reason"]


async def test_gate_refuses_a_check_from_another_revision(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path, revision="older")
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "gate",
        checks=checks,
        loaded=loaded,
        sectors=sectors,
        world=world,
        secondary=secondary,
    )
    assert "revision" in result["reason"]


async def test_gate_refuses_another_composite_or_sector_digest(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)
    other_composite = loaded_composite(loaded.protocol, sha256="1" * 64)
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "a",
        checks=checks,
        loaded=other_composite,
        sectors=sectors,
        world=world,
        secondary=secondary,
    )
    assert "composite" in result["reason"]

    # The sector map the composite pins, but not the one the checks ran on.
    other_sectors = world_sectors(world, secondary, sha256="2" * 64)
    repinned = loaded_composite(shrunk(loaded.protocol, window=world.window, sectors_sha256="2" * 64))
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "b",
        checks=checks,
        loaded=repinned,
        sectors=other_sectors,
        world=world,
        secondary=secondary,
    )
    assert "sector" in result["reason"]

    # A sector map the composite does not pin.
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "c",
        checks=checks,
        loaded=loaded,
        sectors=other_sectors,
        world=world,
        secondary=secondary,
    )
    assert "sector" in result["reason"]


async def test_gate_refuses_a_dirty_revision(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path, revision="abc123-dirty")
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "gate",
        checks=checks,
        loaded=loaded,
        sectors=sectors,
        world=world,
        secondary=secondary,
        revision="abc123-dirty",
    )
    assert "dirty" in result["reason"]


async def test_gate_refuses_another_cube(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, _ = _setup(tmp_path)
    checks = passed_check(
        tmp_path / "other-cube", composite_sha256=loaded.sha256, sectors_sha256=sectors.sha256, cube_sha256="7" * 64
    )
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "gate",
        checks=checks,
        loaded=loaded,
        sectors=sectors,
        world=world,
        secondary=secondary,
    )
    assert "cube" in result["reason"]


async def test_gate_refuses_below_the_mapped_floor(tmp_path, monkeypatch):
    world, secondary, _, loaded, checks = _setup(tmp_path)
    sparse = sector_map({name: "a" for name in world.build.cube.symbols[:30]})
    result = await _refused(
        tmp_path,
        monkeypatch,
        tmp_path / "gate",
        checks=checks,
        loaded=loaded,
        sectors=sparse,
        world=world,
        secondary=secondary,
    )
    assert "62.5%" in result["reason"]


def _stub_composite(monkeypatch, scores_for):
    """The score each cohort gets, built from its own residual; the rule itself runs for real."""

    def stub(protocol, arrays):
        return scores_for(arrays, arrays.residuals[protocol.horizon])

    monkeypatch.setattr(gate_module, "composite_panel", stub)


def _with_noise(target, weight, seed=0):
    noise = np.random.default_rng(seed).normal(size=target.shape)
    scale = np.nanstd(target)
    return np.where(np.isfinite(target), weight * target / scale + noise, np.nan)


async def test_pass_requires_the_spec_rule_on_the_decisive_cohort(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)

    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    result = await _run(loaded, tmp_path / "pass", checks, world, secondary, sectors)
    assert result["status"] == "completed"
    assert result["decision"] == "pass"
    assert result["gate"]["passes"] is True
    assert result["gate"]["t"] >= 2.0
    assert result["gate"]["positive_blocks"] >= 3
    assert result["cube_sha256"] == world.build.cube.sha256
    assert (tmp_path / "pass" / "tear_sheet.txt").read_text().startswith("composite-ic")

    # A negative IC is a completed read that fails.
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, -2.0))
    result = await _run(loaded, tmp_path / "negative", checks, world, secondary, sectors)
    assert (result["status"], result["decision"]) == ("completed", "fail")


async def test_the_gate_reads_the_residual_target_on_the_eligible_mask(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)
    seen = []

    def score(_arrays, target):
        seen.append(_with_noise(target, 0.5))
        return seen[-1]

    _stub_composite(monkeypatch, score)
    result = await _run(loaded, tmp_path / "gate", checks, world, secondary, sectors)
    arrays = build_ic_arrays(world.build, world.spy, world.groups, world.window, horizons=(20,))
    ic = rank_ic_series(seen[0], arrays.residuals[20], arrays.eligible, min_names=30)
    assert result["gate"]["mean"] == pytest.approx(np.nanmean(ic), abs=1e-12)
    assert result["gate"]["n_sessions"] == int(np.isfinite(ic).sum())
    raw = rank_ic_series(seen[0], arrays.returns[20], arrays.eligible, min_names=30)
    assert abs(result["gate"]["mean"] - np.nanmean(raw)) > 1e-3


async def test_two_positive_blocks_of_four_fail_whatever_the_t(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)

    def score(arrays, target):
        """Strong IC in the first and third blocks of the evaluated sessions, a weak negative one elsewhere."""
        finite = np.flatnonzero(np.isfinite(target).sum(axis=1) >= 30)
        parts = np.array_split(finite, 4)
        out = _with_noise(target, -0.1)
        for part in (parts[0], parts[2]):
            out[part] = _with_noise(target, 3.0)[part]
        return out

    _stub_composite(monkeypatch, score)
    result = await _run(loaded, tmp_path / "gate", checks, world, secondary, sectors)
    assert result["status"] == "completed"
    assert result["gate"]["t"] >= 2.0
    assert result["gate"]["positive_blocks"] == 2
    assert result["decision"] == "fail"


async def test_secondary_cohort_is_reported_and_never_decides(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)

    def score(arrays, target):
        weight = 2.0 if arrays.symbols[0].startswith("T") else -2.0
        return _with_noise(target, weight)

    _stub_composite(monkeypatch, score)
    result = await _run(loaded, tmp_path / "gate", checks, world, secondary, sectors)
    assert result["secondary"]["gate"]["passes"] is True
    assert result["secondary"]["cube_sha256"] == secondary.build.cube.sha256
    assert "20" in result["secondary"]["tear_sheet"]["horizons"]
    assert (result["status"], result["decision"]) == ("completed", "fail")

    def inverse(arrays, target):
        weight = -2.0 if arrays.symbols[0].startswith("T") else 2.0
        return _with_noise(target, weight)

    _stub_composite(monkeypatch, inverse)
    result = await _run(loaded, tmp_path / "gate2", checks, world, secondary, sectors)
    assert result["secondary"]["gate"]["passes"] is False
    assert result["decision"] == "pass"


async def test_a_secondary_failure_is_reported_and_leaves_the_decision_alone(tmp_path, monkeypatch):
    world, _, sectors, loaded, checks = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))

    async def broken():
        raise RuntimeError("no secondary cache")

    result = await execute_gate(
        loaded,
        tmp_path / "gate",
        checks_dir=checks,
        build=as_builder(world),
        build_secondary=broken,
        sectors=sectors,
        environment=environment(),
        progress=None,
    )
    assert result["decision"] == "pass"
    assert result["secondary"]["status"] == "failed"
    assert "RuntimeError" in result["secondary"]["reason"]


async def test_a_build_failure_is_not_a_decision(tmp_path):
    _, secondary, sectors, loaded, checks = _setup(tmp_path)

    async def broken():
        raise RuntimeError("no cache")

    result = await execute_gate(
        loaded,
        tmp_path / "gate",
        checks_dir=checks,
        build=broken,
        build_secondary=as_builder(secondary),
        sectors=sectors,
        environment=environment(),
        progress=None,
    )
    assert result["status"] == "failed"
    assert "decision" not in result
    assert "RuntimeError" in result["reason"]


async def test_result_never_authorizes_promotion(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    result = await _run(loaded, tmp_path / "gate", checks, world, secondary, sectors)
    assert result["decision"] == "pass"
    for path in (tmp_path / "gate" / "result.json", tmp_path / "gate" / "manifest.json"):
        assert json.loads(path.read_text())["authorizes_promotion"] is False
    assert result["authorizes_promotion"] is False


async def test_gate_writes_manifest_before_computing_and_holds_no_timestamp_in_its_result(tmp_path, monkeypatch):
    world, secondary, sectors, loaded, checks = _setup(tmp_path)
    output = tmp_path / "gate"
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))

    async def build():
        assert (output / "manifest.json").exists()
        return world.build, world.spy

    await execute_gate(
        loaded,
        output,
        checks_dir=checks,
        build=build,
        build_secondary=as_builder(secondary),
        sectors=sectors,
        environment=environment(),
        progress=None,
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["check"] == "ic_gate"
    assert manifest["composite_sha256"] == loaded.sha256
    assert manifest["sectors_sha256"] == sectors.sha256
    assert manifest["environment"] == environment()
    assert manifest["started_at"]
    assert "started_at" not in json.loads((output / "result.json").read_text())


async def test_poisoned_reserve_prices_leave_the_result_unchanged(tmp_path):
    """End to end through the real composite, statistic and tear sheet: bars past the window change nothing."""
    world, secondary, sectors, loaded, checks = _setup(tmp_path, reported_horizons=PROTOCOL.reported_horizons)
    await _run(loaded, tmp_path / "clean", checks, world, secondary, sectors)

    day_after = world.build.trading_days[world.build.trading_days.index(world.window[1]) + 1]
    await _run(
        loaded,
        tmp_path / "poisoned",
        checks,
        poisoned_world(world, day_after),
        poisoned_world(secondary, day_after),
        sectors,
    )
    clean = (tmp_path / "clean" / "result.json").read_bytes()
    assert json.loads(clean)["status"] == "completed"
    assert (tmp_path / "poisoned" / "result.json").read_bytes() == clean
    assert (tmp_path / "poisoned" / "tear_sheet.txt").read_bytes() == (
        tmp_path / "clean" / "tear_sheet.txt"
    ).read_bytes()

    # The same poison inside the window does change the decisive read, so the comparison above can fail.
    async def skipped():
        raise RuntimeError("not needed for this comparison")

    inside_day = world.build.trading_days[world.build.trading_days.index(world.window[1]) - 30]
    inside = await execute_gate(
        loaded,
        tmp_path / "inside",
        checks_dir=checks,
        build=as_builder(poisoned_world(world, inside_day)),
        build_secondary=skipped,
        sectors=sectors,
        environment=environment(),
        progress=None,
    )
    assert inside["gate"]["mean"] != json.loads(clean)["gate"]["mean"]


async def test_checks_then_gate_at_one_revision_run_end_to_end(tmp_path):
    world, secondary, sectors, _, _ = _setup(tmp_path)
    protocol = shrunk(
        PROTOCOL,
        window=world.window,
        reported_horizons=(20,),
        sectors_sha256=sectors.sha256,
        draws=100,
        decisive_lambda=0.5,
        max_acceptance=0.5,
    )
    loaded = loaded_composite(protocol)
    result = await execute_checks(
        loaded,
        tmp_path / "real-checks",
        build=as_builder(world),
        sectors=sectors,
        environment=environment(),
        progress=None,
    )
    assert result["status"] == "passed"
    gate = await _run(loaded, tmp_path / "gate", tmp_path / "real-checks", world, secondary, sectors)
    assert gate["status"] == "completed"
    assert gate["decision"] in {"pass", "fail"}
