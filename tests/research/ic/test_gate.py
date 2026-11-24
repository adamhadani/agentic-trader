import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from agentic_trader.research.ic import gate as gate_module
from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.checks import execute_checks
from agentic_trader.research.ic.composite import composite_panel, ex_low_risk_panel, member_panels, theme_panel
from agentic_trader.research.ic.gate import _decomposition, declared_reading, execute_gate, lagged_panel
from agentic_trader.research.ic.protocol import LoadedComposite
from agentic_trader.research.ic.sectors import LoadedSectors
from agentic_trader.research.ic.statistic import ic_inference, pearson_ic_series, rank_ic_series
from tests.research.ic.world import (
    World,
    as_builder,
    environment,
    loaded_composite,
    long_world,
    passed_check,
    poisoned_world,
    sector_map,
    shipped_composite,
    shrunk,
    stub_arrays,
    world_sectors,
)


PROTOCOL = shipped_composite().protocol


@dataclass
class _Setup:
    world: World
    secondary: World
    sectors: LoadedSectors
    loaded: LoadedComposite
    checks: Path


def _setup(tmp_path, *, revision="abc123", reported_horizons=(20,)) -> _Setup:
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
    return _Setup(world, secondary, sectors, loaded, checks)


async def _run(setup: _Setup, output: Path, *, loaded: LoadedComposite | None = None, **overrides) -> dict:
    """``execute_gate`` on the setup, with any of its arguments overridden."""
    arguments = {
        "checks_dir": setup.checks,
        "build": as_builder(setup.world),
        "build_secondary": as_builder(setup.secondary),
        "sectors": setup.sectors,
        "environment": environment(),
        "progress": None,
    }
    return await execute_gate(loaded or setup.loaded, output, **(arguments | overrides))


def _never(*_args, **_kwargs):
    raise AssertionError("a refused gate reads nothing")


async def _refused(monkeypatch, setup: _Setup, output: Path, **overrides) -> dict:
    monkeypatch.setattr(gate_module, "composite_panel", _never)
    result = await _run(setup, output, **overrides)
    assert result["status"] == "refused"
    assert "decision" not in result
    assert result["authorizes_promotion"] is False
    assert json.loads((output / "result.json").read_text())["status"] == "refused"
    assert (output / "manifest.json").exists()
    assert not (output / "tear_sheet.txt").exists()
    return result


async def test_gate_refuses_without_a_passed_check(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    failed = passed_check(
        tmp_path / "failed",
        composite_sha256=setup.loaded.sha256,
        sectors_sha256=setup.sectors.sha256,
        cube_sha256=setup.world.build.cube.sha256,
        status="failed",
    )
    result = await _refused(monkeypatch, setup, tmp_path / "a", checks_dir=failed)
    assert "not passed" in result["reason"]
    result = await _refused(monkeypatch, setup, tmp_path / "b", checks_dir=tmp_path / "nothing-here")
    assert "no readable" in result["reason"]


async def test_gate_refuses_a_check_from_another_revision(tmp_path, monkeypatch):
    setup = _setup(tmp_path, revision="older")
    result = await _refused(monkeypatch, setup, tmp_path / "gate")
    assert "revision" in result["reason"]


async def test_gate_refuses_another_composite_or_sector_digest(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    other_composite = loaded_composite(setup.loaded.protocol, sha256="1" * 64)
    result = await _refused(monkeypatch, setup, tmp_path / "a", loaded=other_composite)
    assert "composite" in result["reason"]

    # The sector map the composite pins, but not the one the checks ran on.
    other_sectors = world_sectors(setup.world, setup.secondary, sha256="2" * 64)
    repinned = loaded_composite(shrunk(setup.loaded.protocol, window=setup.world.window, sectors_sha256="2" * 64))
    result = await _refused(monkeypatch, setup, tmp_path / "b", loaded=repinned, sectors=other_sectors)
    assert "sector" in result["reason"]

    # A sector map the composite does not pin.
    result = await _refused(monkeypatch, setup, tmp_path / "c", sectors=other_sectors)
    assert "sector" in result["reason"]


async def test_gate_refuses_a_dirty_revision(tmp_path, monkeypatch):
    setup = _setup(tmp_path, revision="abc123-dirty")
    result = await _refused(monkeypatch, setup, tmp_path / "gate", environment=environment("abc123-dirty"))
    assert "dirty" in result["reason"]


async def test_gate_refuses_another_cube_and_records_both_digests(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    checks = passed_check(
        tmp_path / "other-cube",
        composite_sha256=setup.loaded.sha256,
        sectors_sha256=setup.sectors.sha256,
        cube_sha256="7" * 64,
    )
    result = await _refused(monkeypatch, setup, tmp_path / "gate", checks_dir=checks)
    assert "cube" in result["reason"]
    assert result["cube_sha256"] == setup.world.build.cube.sha256
    assert result["checks_cube_sha256"] == "7" * 64
    assert json.loads((tmp_path / "gate" / "result.json").read_text())["checks_cube_sha256"] == "7" * 64


async def test_gate_refuses_below_the_mapped_floor(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    sparse = sector_map({name: "a" for name in setup.world.build.cube.symbols[:30]})
    result = await _refused(monkeypatch, setup, tmp_path / "gate", sectors=sparse)
    assert "62.5%" in result["reason"]
    assert result["mapped_fraction"] == pytest.approx(0.625)
    assert result["cube_sha256"] == setup.world.build.cube.sha256


def _stub_composite(monkeypatch, scores_for):
    """The score each cohort gets, built from its own residual; the rule itself runs for real.

    The decomposition reads noise here: these tests are about the rule, and the real members cost a build per cohort.
    """

    def stub(protocol, arrays):
        return scores_for(arrays, arrays.residuals[protocol.horizon])

    def noise_members(protocol, arrays):
        rng = np.random.default_rng(1)
        return {member.id: rng.normal(size=arrays.eligible.shape) for member in protocol.members}

    monkeypatch.setattr(gate_module, "composite_panel", stub)
    monkeypatch.setattr(gate_module, "member_panels", noise_members)


def _with_noise(target, weight, seed=0):
    noise = np.random.default_rng(seed).normal(size=target.shape)
    scale = np.nanstd(target)
    return np.where(np.isfinite(target), weight * target / scale + noise, np.nan)


async def test_pass_requires_the_spec_rule_on_the_decisive_cohort(tmp_path, monkeypatch):
    setup = _setup(tmp_path)

    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    result = await _run(setup, tmp_path / "pass")
    assert result["status"] == "completed"
    assert result["decision"] == "pass"
    assert result["gate"]["passes"] is True
    assert result["gate"]["t"] >= 2.0
    assert result["gate"]["positive_blocks"] >= 3
    assert result["cube_sha256"] == setup.world.build.cube.sha256
    assert (tmp_path / "pass" / "tear_sheet.txt").read_text().startswith("composite-ic")

    # A negative IC is a completed read that fails.
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, -2.0))
    result = await _run(setup, tmp_path / "negative")
    assert (result["status"], result["decision"]) == ("completed", "fail")


async def test_the_gate_reads_the_residual_target_on_the_eligible_mask(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    world = setup.world
    seen = []

    def score(_arrays, target):
        seen.append(_with_noise(target, 0.5))
        return seen[-1]

    _stub_composite(monkeypatch, score)
    result = await _run(setup, tmp_path / "gate")
    arrays = build_ic_arrays(world.build, world.spy, world.groups, world.window, horizons=(20,))
    ic = rank_ic_series(seen[0], arrays.residuals[20], arrays.eligible, min_names=30)
    assert result["gate"]["mean"] == pytest.approx(np.nanmean(ic), abs=1e-12)
    assert result["gate"]["n_sessions"] == int(np.isfinite(ic).sum())
    raw = rank_ic_series(seen[0], arrays.returns[20], arrays.eligible, min_names=30)
    assert abs(result["gate"]["mean"] - np.nanmean(raw)) > 1e-3


async def test_min_detectable_ic_is_reported_for_both_cohorts_and_decides_nothing(tmp_path, monkeypatch):
    setup = _setup(tmp_path)

    def score(arrays, target):  # the two test cohorts hold the same bars, so plant different effects to tell them apart
        return _with_noise(target, 1.5 if arrays.symbols[0].startswith("T") else 0.5)

    _stub_composite(monkeypatch, score)
    result = await _run(setup, tmp_path / "gate")
    saved = json.loads((tmp_path / "gate" / "result.json").read_text())
    text = (tmp_path / "gate" / "tear_sheet.txt").read_text()
    decisive, secondary = result["gate"], result["secondary"]["gate"]
    assert f"{decisive['min_detectable_ic']:.4f}" != f"{secondary['min_detectable_ic']:.4f}"
    for gate in (decisive, secondary):
        assert gate["se"] > 0
        assert gate["min_detectable_ic"] == pytest.approx(2.0 * gate["se"], abs=1e-15)
        assert f"min detectable IC {gate['min_detectable_ic']:.4f}" in text
    assert text.count("median pairs/session 48.0") == 2
    assert saved["gate"]["min_detectable_ic"] == decisive["min_detectable_ic"]
    assert saved["secondary"]["gate"]["min_detectable_ic"] == secondary["min_detectable_ic"]
    assert saved["gate"]["median_pairs_per_session"] == 48.0
    # The rule is the spec's, with or without the new figures.
    assert decisive["passes"] == (decisive["t"] >= 2.0 and decisive["positive_blocks"] >= 3)


async def test_two_positive_blocks_of_four_fail_whatever_the_t(tmp_path, monkeypatch):
    setup = _setup(tmp_path)

    def score(_arrays, target):
        """Strong IC in the first and third blocks of the evaluated sessions, a weak negative one elsewhere."""
        finite = np.flatnonzero(np.isfinite(target).sum(axis=1) >= 30)
        parts = np.array_split(finite, 4)
        out = _with_noise(target, -0.1)
        for part in (parts[0], parts[2]):
            out[part] = _with_noise(target, 3.0)[part]
        return out

    _stub_composite(monkeypatch, score)
    result = await _run(setup, tmp_path / "gate")
    assert result["status"] == "completed"
    assert result["gate"]["t"] >= 2.0
    assert result["gate"]["positive_blocks"] == 2
    assert result["decision"] == "fail"


async def test_secondary_cohort_is_reported_and_never_decides(tmp_path, monkeypatch):
    setup = _setup(tmp_path)

    def score(arrays, target):
        return _with_noise(target, 2.0 if arrays.symbols[0].startswith("T") else -2.0)

    _stub_composite(monkeypatch, score)
    result = await _run(setup, tmp_path / "gate")
    assert result["secondary"]["gate"]["passes"] is True
    assert result["secondary"]["cube_sha256"] == setup.secondary.build.cube.sha256
    assert "20" in result["secondary"]["tear_sheet"]["horizons"]
    assert (result["status"], result["decision"]) == ("completed", "fail")

    def inverse(arrays, target):
        return _with_noise(target, -2.0 if arrays.symbols[0].startswith("T") else 2.0)

    _stub_composite(monkeypatch, inverse)
    result = await _run(setup, tmp_path / "gate2")
    assert result["secondary"]["gate"]["passes"] is False
    assert result["decision"] == "pass"


def _raising(error=RuntimeError):
    async def build():
        raise error("provider-secret-text")

    return build


async def test_a_secondary_failure_is_reported_and_leaves_the_decision_alone(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    said: list[str] = []
    result = await _run(setup, tmp_path / "gate", build_secondary=_raising(), progress=said.append)
    assert result["decision"] == "pass"
    assert result["secondary"] == {"status": "failed", "reason": "RuntimeError"}
    assert any("secondary" in line and "provider-secret-text" in line for line in said)
    assert "provider-secret-text" not in (tmp_path / "gate" / "result.json").read_text()
    assert "provider-secret-text" not in (tmp_path / "gate" / "tear_sheet.txt").read_text()


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
async def test_a_build_failure_is_not_a_decision_and_keeps_its_message_out_of_the_result(tmp_path, error):
    """A ``ValueError`` too: it is a fault here, not one of the deliberate refusals."""
    setup = _setup(tmp_path)
    said: list[str] = []
    result = await _run(setup, tmp_path / "gate", build=_raising(error), progress=said.append)
    assert result["status"] == "failed"
    assert "decision" not in result
    assert result["reason"] == error.__name__
    assert "provider-secret-text" not in (tmp_path / "gate" / "result.json").read_text()
    assert any("provider-secret-text" in line for line in said)


async def test_result_never_authorizes_promotion(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    result = await _run(setup, tmp_path / "gate")
    assert result["decision"] == "pass"
    for path in (tmp_path / "gate" / "result.json", tmp_path / "gate" / "manifest.json"):
        assert json.loads(path.read_text())["authorizes_promotion"] is False
    assert result["authorizes_promotion"] is False


async def test_gate_writes_manifest_before_computing_and_holds_no_timestamp_in_its_result(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    output = tmp_path / "gate"
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))

    async def build():
        assert (output / "manifest.json").exists()
        return setup.world.build, setup.world.spy

    result = await _run(setup, output, build=build)
    assert result["status"] == "completed"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["check"] == "ic_gate"
    assert manifest["composite_sha256"] == setup.loaded.sha256
    assert manifest["sectors_sha256"] == setup.sectors.sha256
    assert manifest["environment"] == environment()
    assert manifest["started_at"]
    assert "started_at" not in json.loads((output / "result.json").read_text())


async def test_poisoned_reserve_prices_leave_the_result_unchanged(tmp_path):
    """End to end through the real composite, statistic and tear sheet: bars past the window change nothing."""
    setup = _setup(tmp_path, reported_horizons=PROTOCOL.reported_horizons)
    world, secondary = setup.world, setup.secondary
    await _run(setup, tmp_path / "clean")

    day_after = world.build.trading_days[world.build.trading_days.index(world.window[1]) + 1]
    await _run(
        setup,
        tmp_path / "poisoned",
        build=as_builder(poisoned_world(world, day_after)),
        build_secondary=as_builder(poisoned_world(secondary, day_after)),
    )
    clean = (tmp_path / "clean" / "result.json").read_bytes()
    assert json.loads(clean)["status"] == "completed"
    assert (tmp_path / "poisoned" / "result.json").read_bytes() == clean
    assert (tmp_path / "poisoned" / "tear_sheet.txt").read_bytes() == (
        tmp_path / "clean" / "tear_sheet.txt"
    ).read_bytes()

    # The same poison inside the window does change the decisive read, so the comparison above can fail.
    inside_day = world.build.trading_days[world.build.trading_days.index(world.window[1]) - 30]
    inside = await _run(
        setup, tmp_path / "inside", build=as_builder(poisoned_world(world, inside_day)), build_secondary=_raising()
    )
    assert inside["gate"]["mean"] != json.loads(clean)["gate"]["mean"]


async def test_checks_then_gate_at_one_revision_run_end_to_end(tmp_path):
    setup = _setup(tmp_path)
    protocol = shrunk(
        PROTOCOL,
        window=setup.world.window,
        reported_horizons=(20,),
        sectors_sha256=setup.sectors.sha256,
        draws=100,
        decisive_lambda=0.5,
        max_acceptance=0.5,
    )
    loaded = loaded_composite(protocol)
    result = await execute_checks(
        loaded,
        tmp_path / "real-checks",
        build=as_builder(setup.world),
        sectors=setup.sectors,
        environment=environment(),
        progress=None,
    )
    assert result["status"] == "passed"
    gate = await _run(setup, tmp_path / "gate", loaded=loaded, checks_dir=tmp_path / "real-checks")
    assert gate["status"] == "completed"
    assert gate["decision"] in {"pass", "fail"}


@pytest.mark.parametrize(
    ("passes", "t", "expected"),
    [
        (True, 1.99, "carried_by_low_risk"),
        (True, 2.0, None),
        (True, 5.0, None),
        (True, float("nan"), "carried_by_low_risk"),  # it did not reach the line either
        (False, 1.0, None),
        (False, 5.0, None),
        (False, float("nan"), None),
    ],
)
def test_declared_reading_needs_a_pass_whose_ex_low_risk_does_not_reach_the_line(passes, t, expected):
    assert declared_reading(passes, t, 2.0) == expected


def test_the_lag_shifts_by_exactly_252_rows():
    scores = np.arange(300.0 * 4).reshape(300, 4)
    out = lagged_panel(scores)
    assert np.isnan(out[:252]).all()
    np.testing.assert_array_equal(out[252:], scores[:48])
    assert out[252, 0] == scores[0, 0]
    assert out[299, 3] == scores[47, 3]


def test_a_panel_no_longer_than_the_lag_has_nothing_left():
    for rows in (200, 252):
        assert np.isnan(lagged_panel(np.ones((rows, 4)))).all()


S, N = 320, 60
SECTORS = np.repeat(np.array(["a", "b", "c", "d"]), N // 4)
LOW_RISK_EXPRESSION = next(member.expression for member in PROTOCOL.members if member.id == "ivol_capm_252d")


def _themed_arrays(carrying: tuple[str, ...], seed: int = 21):
    """Member panels of noise, with the target in the members whose ids are named, on the member's own sign."""
    rng = np.random.default_rng(seed)
    target = rng.normal(size=(S, N))
    panels = {}
    for member in PROTOCOL.members:
        loading = 0.3 * member.sign * target if member.id in carrying else 0.0
        panels[member.expression] = loading + rng.normal(size=(S, N))
    return stub_arrays(panels, SECTORS, target=target, seed=seed + 1)  # beta and log_adv draw apart from the target


def _on_arrays(setup: _Setup, monkeypatch, arrays) -> None:
    """The cohorts' reads run on these arrays; the rule, the composite and the decomposition all run for real."""
    monkeypatch.setattr(gate_module, "build_ic_arrays", lambda *_args, **_kwargs: arrays)


async def test_a_world_where_only_low_risk_carries_is_recorded_as_carried_by_it(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _on_arrays(setup, monkeypatch, _themed_arrays(("ivol_capm_252d",)))
    result = await _run(setup, tmp_path / "gate")
    decomposition = result["decomposition"]
    assert result["decision"] == "pass"
    assert result["declared_reading"] == "carried_by_low_risk"
    assert decomposition["themes"]["low_risk"]["t"] >= 2.0
    assert decomposition["ex_low_risk"]["t"] < 2.0
    assert decomposition["themes"]["momentum"]["t"] < 2.0
    assert decomposition["themes"]["short_term_reversal"]["t"] < 2.0
    assert "declared_reading" not in result["secondary"]
    saved = json.loads((tmp_path / "gate" / "result.json").read_text())
    assert saved["declared_reading"] == "carried_by_low_risk"
    text = (tmp_path / "gate" / "tear_sheet.txt").read_text()
    assert "declared reading: carried_by_low_risk" in text


async def test_a_pass_the_other_themes_carry_has_no_declared_reading(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    carrying = ("ret_3_1", "ret_6_1", "ret_9_1", "ret_12_1", "ret_1_0", "ivol_capm_252d")
    _on_arrays(setup, monkeypatch, _themed_arrays(carrying))
    result = await _run(setup, tmp_path / "gate", build_secondary=_raising())
    assert result["decision"] == "pass"
    assert result["decomposition"]["ex_low_risk"]["t"] >= 2.0
    assert result["declared_reading"] is None
    assert json.loads((tmp_path / "gate" / "result.json").read_text())["declared_reading"] is None
    assert "declared reading: none" in (tmp_path / "gate" / "tear_sheet.txt").read_text()


async def test_a_fail_has_no_declared_reading(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _on_arrays(setup, monkeypatch, _themed_arrays(()))
    result = await _run(setup, tmp_path / "gate", build_secondary=_raising())
    assert result["decision"] == "fail"
    assert result["decomposition"]["ex_low_risk"]["t"] < 2.0
    assert result["declared_reading"] is None


async def test_the_decision_does_not_change_when_the_decomposition_does(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _on_arrays(setup, monkeypatch, _themed_arrays(("ivol_capm_252d",)))
    plain = await _run(setup, tmp_path / "plain", build_secondary=_raising())

    def other_members(protocol, _arrays):
        rng = np.random.default_rng(99)
        return {member.id: rng.normal(size=(S, N)) for member in protocol.members}

    monkeypatch.setattr(gate_module, "member_panels", other_members)  # what the decomposition reads, nothing else
    changed = await _run(setup, tmp_path / "changed", build_secondary=_raising())
    assert (
        changed["decomposition"]["themes"]["low_risk"]["mean"] != plain["decomposition"]["themes"]["low_risk"]["mean"]
    )
    assert changed["decomposition"]["ex_low_risk"]["mean"] != plain["decomposition"]["ex_low_risk"]["mean"]
    for key in ("status", "decision", "gate", "tear_sheet"):
        assert changed[key] == plain[key]


def test_decomposition_reads_each_part_on_the_gates_target_and_mask():
    rng = np.random.default_rng(8)
    arrays = dataclasses.replace(
        _themed_arrays(("ivol_capm_252d",)),
        returns={20: rng.normal(size=(S, N))},  # not the target the gate reads
        eligible=rng.random((S, N)) > 0.1,  # changes over time, so a lagged score can land on an ineligible name
    )
    protocol = shrunk(PROTOCOL, draws=100)
    target, mask = arrays.residuals[20], arrays.eligible
    scores = composite_panel(protocol, arrays)
    out = _decomposition(protocol, arrays, scores)
    panels = member_panels(protocol, arrays)

    def inference(panel):
        return ic_inference(rank_ic_series(panel, target, mask), 20, draws=100, seed=protocol.bootstrap.seed)

    def pearson(panel):
        return float(np.nanmean(pearson_ic_series(panel, target, mask)))

    assert set(out) == {"themes", "ex_low_risk", "composite_pearson", "lagged_252"}
    assert list(out["themes"]) == ["momentum", "short_term_reversal", "low_risk"]
    for theme in protocol.themes:
        panel = theme_panel(protocol, theme, panels)
        assert out["themes"][theme.theme] == {**inference(panel), "pearson_mean": pytest.approx(pearson(panel))}
    panel = ex_low_risk_panel(protocol, panels)
    assert out["ex_low_risk"] == {**inference(panel), "pearson_mean": pytest.approx(pearson(panel))}
    assert out["composite_pearson"] == pytest.approx(pearson(scores))
    assert out["lagged_252"] == inference(lagged_panel(scores))


def test_the_lagged_read_finds_a_score_only_at_exactly_252_sessions():
    """A score that is the target 252 sessions early: its lagged read is perfect, and its own read is nothing."""
    arrays = _themed_arrays(())
    protocol = shrunk(PROTOCOL, draws=100)
    target = arrays.residuals[20]
    scores = np.full(target.shape, np.nan)
    scores[:-252] = target[252:]
    out = _decomposition(protocol, arrays, scores)
    assert out["lagged_252"]["n_sessions"] == S - 252
    assert out["lagged_252"]["mean"] == pytest.approx(1.0)
    assert abs(np.nanmean(rank_ic_series(scores, target, arrays.eligible))) < 0.05


async def test_both_cohorts_report_the_decomposition_and_the_text_shows_it(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 2.0))
    result = await _run(setup, tmp_path / "gate")
    saved = json.loads((tmp_path / "gate" / "result.json").read_text())
    text = (tmp_path / "gate" / "tear_sheet.txt").read_text()
    assert text.count("decomposition (reported, never decides)") == 2
    assert text.count("excluding low risk: rank IC") == 2
    assert text.count("composite lagged 252 sessions: rank IC") == 2
    assert text.count("composite Pearson mean") == 2
    for block in (result, result["secondary"]):
        decomposition = block["decomposition"]
        assert set(decomposition["themes"]) == {"momentum", "short_term_reversal", "low_risk"}
        for name, read in decomposition["themes"].items():
            assert set(read) >= {"mean", "se", "t", "n_sessions", "pearson_mean"}
            assert f"theme {name}: rank IC mean {read['mean']:.4f} se {read['se']:.4f} t {read['t']:.2f}" in text
        assert set(decomposition["ex_low_risk"]) >= {"mean", "se", "t", "pearson_mean"}
        assert set(decomposition["lagged_252"]) >= {"mean", "se", "t"}
        assert "pearson_mean" not in decomposition["lagged_252"]
        assert isinstance(decomposition["composite_pearson"], float)
    # This window's lagged scores all fall on sessions whose 20-session target runs past its end: null, not NaN.
    assert result["decomposition"]["lagged_252"]["n_sessions"] == 0
    assert saved["decomposition"]["lagged_252"]["mean"] is None
    assert "composite lagged 252 sessions: rank IC mean nan" in text


async def test_the_power_figures_sit_beside_the_minimum_detectable_ic_in_the_text(tmp_path, monkeypatch):
    setup = _setup(tmp_path)
    _stub_composite(monkeypatch, lambda _arrays, target: _with_noise(target, 0.5))
    result = await _run(setup, tmp_path / "gate")
    text = (tmp_path / "gate" / "tear_sheet.txt").read_text()
    for gate in (result["gate"], result["secondary"]["gate"]):
        power = gate["power_at"]
        assert set(power) == {"0.02", "0.03"}
        assert (
            f"min detectable IC {gate['min_detectable_ic']:.4f} power at IC "
            f"0.02: {power['0.02']:.3f} 0.03: {power['0.03']:.3f} median"
        ) in text
    assert json.loads((tmp_path / "gate" / "result.json").read_text())["gate"]["power_at"] == result["gate"]["power_at"]
