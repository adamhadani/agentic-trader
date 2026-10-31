import json

import numpy as np
import pytest
from scipy.stats import norm

from agentic_trader.research.ic import checks, statistic
from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.checks import (
    a_ic,
    ar1_base,
    c_ic,
    execute_checks,
    fixed_permutation,
    gate_read,
    planted_score,
    power_at,
    require_checks,
    session_shuffle,
)
from agentic_trader.research.ic.composite import composite_panel
from agentic_trader.research.ic.statistic import gate_verdict, rank_ic_series
from tests.research.ic.world import (
    as_builder,
    environment,
    loaded_composite,
    long_world,
    passed_check,
    sector_map,
    shipped_composite,
    shrunk,
    stub_arrays,
    world_sectors,
)


PROTOCOL = shipped_composite().protocol
FLAT = "f" * 64


def _groups(n, sectors=3):
    return np.repeat(np.array([f"g{i}" for i in range(sectors)]), n // sectors)


def _ar1(rng, s, n, phi):
    out = np.empty((s, n))
    out[0] = rng.normal(size=n)
    for t in range(1, s):
        out[t] = phi * out[t - 1] + np.sqrt(1 - phi**2) * rng.normal(size=n)
    return out


def _overlapping_target(rng, s, n, horizon=20):
    walk = np.cumsum(rng.normal(size=(s + horizon, n)), axis=0)
    return walk[horizon:] - walk[:-horizon]


def _panel(seed=0, s=1000, n=60, phi=0.98, overlap=True):
    """A persistent score and a target it knows nothing about, sector-demeaned as the real residual is."""
    rng = np.random.default_rng(seed)
    groups = _groups(n)
    composite = _ar1(rng, s, n, phi) if phi else rng.normal(size=(s, n))
    target = _overlapping_target(rng, s, n) if overlap else rng.normal(size=(s, n))
    for group in np.unique(groups):
        cols = groups == group
        target[:, cols] -= target[:, cols].mean(axis=1, keepdims=True)
    return composite, target, np.ones((s, n), bool), groups


def test_gate_read_applies_the_protocols_rule_and_bootstrap():
    composite, target, mask, _ = _panel(s=200, n=60, phi=0.5)
    protocol = shrunk(PROTOCOL, draws=100)
    expected = gate_verdict(
        rank_ic_series(composite, target, mask, min_names=protocol.min_names),
        20,
        min_t=2.0,
        blocks=4,
        min_positive_blocks=3,
        draws=100,
        seed=20261103,
    )
    read = gate_read(composite, target, mask, protocol)
    assert {key: read[key] for key in expected} == expected  # the two descriptions change nothing in the rule
    assert read["min_detectable_ic"] == 2.0 * read["se"]
    assert read["median_pairs_per_session"] == 60.0
    assert read["power_at"] == power_at(read["se"], 2.0)


def test_gate_read_counts_pairs_over_evaluated_sessions_only():
    rng = np.random.default_rng(0)
    score, target = rng.normal(size=(7, 50)), rng.normal(size=(7, 50))
    mask = np.ones((7, 50), bool)
    mask[:, 40:] = False  # 40 names in the mask
    score[:, 35] = np.nan  # one has no score
    target[:, 34] = np.nan  # one has no target
    mask[:4, :25] = False  # 13 pairs in most sessions: below min_names, so unavailable, and they must not count
    read = gate_read(score, target, mask, shrunk(PROTOCOL, draws=20))
    assert read["n_unavailable"] == 4
    assert read["median_pairs_per_session"] == 38.0


def test_a_read_with_no_evaluated_session_reports_no_detectable_effect():
    rng = np.random.default_rng(0)
    read = gate_read(rng.normal(size=(4, 10)), rng.normal(size=(4, 10)), np.ones((4, 10), bool), PROTOCOL)
    assert read["n_sessions"] == 0
    assert np.isnan(read["min_detectable_ic"])
    assert np.isnan(read["median_pairs_per_session"])
    assert read["passes"] is False
    assert set(read["power_at"]) == {"0.02", "0.03"}
    assert all(np.isnan(value) for value in read["power_at"].values())


@pytest.mark.parametrize(
    ("se", "at_002", "at_003"),
    [(0.0105, 0.462, 0.804), (0.016, 0.227, 0.450), (0.015, norm.cdf(0.02 / 0.015 - 2.0), 0.5)],
)
def test_power_at_is_the_chance_a_true_ic_reaches_the_gates_t(se, at_002, at_003):
    power = power_at(se, 2.0)
    assert list(power) == ["0.02", "0.03"]
    assert power["0.02"] == pytest.approx(at_002, abs=1e-3)
    assert power["0.03"] == pytest.approx(at_003, abs=1e-3)


@pytest.mark.parametrize("se", [float("nan"), float("inf"), 0.0])
def test_power_at_is_nan_without_a_usable_se(se):
    assert all(np.isnan(value) for value in power_at(se, 2.0).values())


def test_fixed_permutation_stays_within_sector_and_is_constant_over_time():
    groups = _groups(30)
    order = fixed_permutation(groups, np.random.default_rng(1))
    assert sorted(order.tolist()) == list(range(30))
    assert (groups[order] == groups).all()
    np.testing.assert_array_equal(order, fixed_permutation(groups, np.random.default_rng(1)))
    assert not np.array_equal(order, fixed_permutation(groups, np.random.default_rng(2)))


def test_c_ic_applies_one_permutation_to_all_sessions_and_never_reads_the_real_composite(monkeypatch):
    composite, target, mask, groups = _panel(s=120, n=45)
    seen = []
    real = checks.gate_read

    def spy(score, *args, **kwargs):
        seen.append(score.copy())
        return real(score, *args, **kwargs)

    monkeypatch.setattr(checks, "gate_read", spy)
    protocol = shrunk(PROTOCOL, c_replicates=3, shuffle_replicates=1, draws=50)
    c_ic(protocol, composite, target, mask, groups)
    assert len(seen) == 4
    orders = []
    for score in seen[:3]:
        assert not np.array_equal(score, composite)
        # Every column of the score is one column of the composite, whole, from the same sector.
        matches = [np.flatnonzero((composite == score[:, [j]]).all(axis=0)) for j in range(score.shape[1])]
        assert all(match.size == 1 for match in matches)
        order = np.array([match[0] for match in matches])
        assert (groups[order] == groups).all()
        orders.append(tuple(order.tolist()))
    assert len(set(orders)) == 3  # one seed each
    shuffled = seen[3]
    # The reported control moves names within a sector, independently each session.
    for s in range(shuffled.shape[0]):
        for group in np.unique(groups):
            cols = groups == group
            np.testing.assert_array_equal(np.sort(shuffled[s, cols]), np.sort(composite[s, cols]))
    assert not np.array_equal(shuffled, composite)
    assert not any((composite == shuffled[:, [j]]).all(axis=0).any() for j in range(shuffled.shape[1]))


def test_session_shuffle_keeps_each_session_and_sector():
    composite, _, _, groups = _panel(s=20, n=30)
    out = session_shuffle(composite, groups, np.random.default_rng(0))
    for group in np.unique(groups):
        cols = groups == group
        np.testing.assert_array_equal(np.sort(out[:, cols], axis=1), np.sort(composite[:, cols], axis=1))


def test_c_ic_passes_on_a_null_panel_and_reports_both_controls():
    composite, target, mask, groups = _panel(seed=1)
    protocol = shrunk(PROTOCOL, c_replicates=20, shuffle_replicates=3, draws=500)
    out = c_ic(protocol, composite, target, mask, groups)
    assert out["passes"] is True
    assert out["max_acceptance"] == pytest.approx(0.10)
    assert out["fixed_permutation"]["replicates"] == 20
    assert out["fixed_permutation"]["acceptance"] <= 0.10
    assert out["session_shuffle"]["replicates"] == 3
    assert out["fixed_permutation"]["sessions_evaluated"] == 1000
    assert out["fixed_permutation"]["median_pairs_per_session"] == 60.0
    assert out["session_shuffle"]["median_pairs_per_session"] == 60.0


def test_every_arm_reports_the_mean_and_sd_of_its_replicates_t(monkeypatch):
    composite, target, mask, groups = _panel(s=120, n=60, phi=0.5)
    protocol = shrunk(PROTOCOL, c_replicates=3, shuffle_replicates=2, a_replicates=3, draws=20)
    real = checks.gate_read
    reads: list[dict] = []

    def spy(*args, **kwargs):
        reads.append(real(*args, **kwargs))
        return reads[-1]

    monkeypatch.setattr(checks, "gate_read", spy)
    c_out = c_ic(protocol, composite, target, mask, groups)
    for name, arm in (("fixed_permutation", reads[:3]), ("session_shuffle", reads[3:5])):
        ts = [read["t"] for read in arm]
        assert c_out[name]["t_mean"] == pytest.approx(np.mean(ts))
        assert c_out[name]["t_sd"] == pytest.approx(np.std(ts, ddof=1))

    reads.clear()
    a_out = a_ic(protocol, composite, target, mask, groups)
    for name, position in (("fixed_permutation", 0), ("ar1", 1)):  # one lambda, each replicate reads the two bases
        ts = [read["t"] for read in reads[position::2]]
        row = a_out[name]["0.03"]
        assert row["t_mean"] == pytest.approx(np.mean(ts))
        assert row["t_sd"] == pytest.approx(np.std(ts, ddof=1))


def test_c_ic_fails_when_the_statistic_is_broken(monkeypatch):
    composite, target, mask, groups = _panel(seed=1)
    real = statistic.bootstrap_draws
    monkeypatch.setattr(statistic, "bootstrap_draws", lambda n, _block, draws, seed: real(n, 1.0, draws, seed))
    protocol = shrunk(PROTOCOL, c_replicates=20, shuffle_replicates=1, draws=500)
    out = c_ic(protocol, composite, target, mask, groups)
    assert out["fixed_permutation"]["acceptance"] > 0.10
    assert out["passes"] is False


def test_planted_score_has_lambda_weight_and_keeps_only_valid_pairs():
    rng = np.random.default_rng(0)
    base = rng.normal(size=(5, 200))
    target = rng.normal(size=(5, 200))
    mask = np.ones((5, 200), bool)
    base[1, 3] = np.nan
    target[1, 4] = np.nan
    mask[1, 5] = False
    score = planted_score(base, target, mask, 0.6)
    assert np.isnan(score[1, [3, 4, 5]]).all()
    keep = np.isfinite(score[1])
    assert keep.sum() == 197
    ranks = target[1, keep].argsort().argsort().astype(float)
    z_rank = (ranks - ranks.mean()) / ranks.std(ddof=1)
    z_base = (base[1, keep] - base[1, keep].mean()) / base[1, keep].std(ddof=1)
    np.testing.assert_allclose(score[1, keep], 0.8 * z_base + 0.6 * z_rank, atol=1e-9)


def test_a_constant_target_session_is_unavailable():
    base = np.random.default_rng(0).normal(size=(2, 40))
    target = np.random.default_rng(1).normal(size=(2, 40))
    target[0] = 1.0
    score = planted_score(base, target, np.ones((2, 40), bool), 0.1)
    assert np.isnan(score[0]).all()
    assert np.isfinite(score[1]).all()


def test_ar1_base_has_the_declared_persistence_and_independent_names():
    base = ar1_base((6000, 20), 0.97, np.random.default_rng(0))
    assert base.std() == pytest.approx(1.0, abs=0.15)
    lag1 = np.mean([np.corrcoef(base[1:, j], base[:-1, j])[0, 1] for j in range(20)])
    assert lag1 == pytest.approx(0.97, abs=0.01)
    cross = np.corrcoef(base.T)[np.triu_indices(20, 1)]
    assert np.abs(cross).mean() < 0.2


def test_a_ic_detects_a_planted_effect_end_to_end():
    """Through the real construction: member panels -> composite -> permutation -> plant -> the gate's read."""
    rng = np.random.default_rng(5)
    s, n = 800, 402
    groups = _groups(n)
    panels = {member.expression: _ar1(rng, s, n, 0.98) for member in PROTOCOL.members}
    target = _overlapping_target(rng, s, n)
    arrays = stub_arrays(panels, groups, target=target)
    composite = composite_panel(PROTOCOL, arrays)
    protocol = shrunk(PROTOCOL, a_replicates=6, draws=300)
    out = a_ic(protocol, composite, arrays.residuals[20], arrays.eligible, arrays.groups)
    assert out["power"] >= 0.70
    assert out["passes"] is True
    assert out["decisive_lambda"] == pytest.approx(0.03)
    assert out["fixed_permutation"]["0.03"]["power"] == out["power"]
    assert "0.03" in out["ar1"]


def test_a_ic_reports_every_lambda_on_both_bases():
    composite, target, mask, groups = _panel(s=300, n=60, phi=0.9)
    protocol = shrunk(PROTOCOL, a_replicates=2, reported_lambdas=(0.02, 0.04), draws=50)
    out = a_ic(protocol, composite, target, mask, groups)
    for base in ("fixed_permutation", "ar1"):
        assert set(out[base]) == {"0.02", "0.03", "0.04"}
        for row in out[base].values():
            assert set(row) >= {"power", "mean_ic", "accepted", "replicates", "median_pairs_per_session"}
            assert row["replicates"] == 2


def test_each_arm_reports_how_a_permutation_shrinks_the_pairs():
    """Scores land on names with no target, so a permuted arm has fewer pairs than the AR(1) arm's full mask."""
    composite, target, mask, groups = _panel(s=120, n=90, phi=0.5)
    mask = mask.copy()
    mask[:, ::3] = False  # a third of each sector is outside the mask, so it has no score and no target
    composite[:, ::3] = np.nan
    target[:, ::3] = np.nan
    protocol = shrunk(PROTOCOL, c_replicates=2, shuffle_replicates=1, a_replicates=1, draws=20)
    c_out = c_ic(protocol, composite, target, mask, groups)
    a_out = a_ic(protocol, composite, target, mask, groups)
    assert a_out["ar1"]["0.03"]["median_pairs_per_session"] == 60.0
    assert c_out["fixed_permutation"]["median_pairs_per_session"] < 60.0
    assert c_out["session_shuffle"]["median_pairs_per_session"] < 60.0
    assert a_out["fixed_permutation"]["0.03"]["median_pairs_per_session"] < 60.0


def test_a_ic_draws_a_new_base_for_each_replicate_and_the_same_one_across_lambdas(monkeypatch):
    composite, target, mask, groups = _panel(s=100, n=45, phi=0.5)
    seen = []
    real = checks.gate_read

    def spy(score, *args, **kwargs):
        seen.append(score.copy())
        return real(score, *args, **kwargs)

    monkeypatch.setattr(checks, "gate_read", spy)
    protocol = shrunk(PROTOCOL, a_replicates=2, reported_lambdas=(0.5,), draws=20)
    a_ic(protocol, composite, target, mask, groups)
    assert len(seen) == 2 * 2 * 2  # replicates x bases x lambdas
    first_permuted, first_ar1, second_permuted = seen[0], seen[2], seen[4]
    assert not np.array_equal(first_permuted, second_permuted, equal_nan=True)
    assert not np.array_equal(first_permuted, first_ar1, equal_nan=True)
    # Across lambdas of one replicate the plant moves and the base does not: the difference is the planted rank.
    plant = seen[1] - seen[0] * (np.sqrt(1 - 0.5**2) / np.sqrt(1 - 0.03**2))
    assert np.corrcoef(plant[10], target[10])[0, 1] > 0.8


def test_a_ic_fails_on_a_misaligned_target(monkeypatch):
    composite, target, mask, groups = _panel(s=500, n=402, phi=0.0, overlap=False)
    protocol = shrunk(PROTOCOL, a_replicates=3, draws=100)
    aligned = a_ic(protocol, composite, target, mask, groups)
    assert aligned["power"] >= 0.70
    assert aligned["fixed_permutation"]["0.03"]["mean_ic"] == pytest.approx(0.03, abs=0.005)

    real = checks.rank_ic_series
    monkeypatch.setattr(checks, "rank_ic_series", lambda score, t, m, **k: real(score, np.roll(t, 1, axis=0), m, **k))
    shifted = a_ic(protocol, composite, target, mask, groups)
    assert shifted["power"] < 0.70
    assert shifted["fixed_permutation"]["0.03"]["mean_ic"] == pytest.approx(0.0, abs=0.005)
    assert shifted["passes"] is False


def test_a_ic_fails_when_an_overlapping_target_is_shifted_by_its_horizon(monkeypatch):
    """On a 20-session target only a shift past the overlap removes the plant; test_arrays pins single sessions."""
    composite, target, mask, groups = _panel(s=600, n=402, phi=0.98, seed=1)
    real = checks.rank_ic_series
    monkeypatch.setattr(checks, "rank_ic_series", lambda score, t, m, **k: real(score, np.roll(t, 20, axis=0), m, **k))
    out = a_ic(shrunk(PROTOCOL, a_replicates=3, draws=100), composite, target, mask, groups)
    assert out["fixed_permutation"]["0.03"]["mean_ic"] == pytest.approx(0.0, abs=0.015)
    assert out["passes"] is False


def test_planted_mean_ic_is_near_lambda():
    composite, target, mask, groups = _panel(s=400, n=402, phi=0.0, overlap=False)
    protocol = shrunk(PROTOCOL, a_replicates=2, reported_lambdas=(0.02, 0.04), draws=20)
    out = a_ic(protocol, composite, target, mask, groups)
    for base in ("fixed_permutation", "ar1"):
        for lam, row in out[base].items():
            assert row["mean_ic"] == pytest.approx(float(lam), abs=0.004)


# Whole command paths, on a real score book. A small world cannot reach power at lambda 0.03, so the plant is large.


def _world_setup(tmp_path, **shrink):
    world = long_world()
    sectors = world_sectors(world)
    settings = {"decisive_lambda": 0.5, "max_acceptance": 0.5, "draws": 100} | shrink
    protocol = shrunk(PROTOCOL, window=world.window, sectors_sha256=sectors.sha256, **settings)
    return world, sectors, loaded_composite(protocol), tmp_path / "checks"


async def test_checks_pass_end_to_end_and_the_gate_accepts_their_directory(tmp_path):
    world, sectors, loaded, output = _world_setup(tmp_path)
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "passed"
    assert result["composite_sha256"] == loaded.sha256
    assert result["sectors_sha256"] == sectors.sha256
    assert result["cube_sha256"] == world.build.cube.sha256
    assert result["authorizes_promotion"] is False
    assert {"c_ic", "a_ic", "mapped_fraction"} <= set(result)
    saved = json.loads((output / "result.json").read_text())
    assert saved["status"] == "passed"
    assert "started_at" not in saved
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["check"] == "ic_checks"
    assert manifest["composite_sha256"] == loaded.sha256
    assert manifest["sectors_sha256"] == sectors.sha256
    assert manifest["environment"] == environment()
    assert manifest["authorizes_promotion"] is False
    assert manifest["started_at"]
    kept = require_checks(output, composite_sha256=loaded.sha256, sectors_sha256=sectors.sha256, revision="abc123")
    assert kept["cube_sha256"] == world.build.cube.sha256


async def test_checks_read_the_composite_against_the_residual_target_on_the_eligible_mask(tmp_path, monkeypatch):
    world, sectors, loaded, output = _world_setup(tmp_path)
    seen = {}

    def spy(protocol, composite, target, mask, groups, **_kwargs):
        seen.update(composite=composite, target=target, mask=mask, groups=groups)
        return {"passes": True, "power": 1.0, "decisive_lambda": 0.5, "fixed_permutation": {"acceptance": 0.0}}

    monkeypatch.setattr(checks, "c_ic", spy)
    monkeypatch.setattr(checks, "a_ic", spy)
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "passed"
    arrays = build_ic_arrays(world.build, world.spy, world.groups, world.window, horizons=(20,))
    np.testing.assert_array_equal(seen["target"], arrays.residuals[20])
    np.testing.assert_array_equal(seen["mask"], arrays.eligible)
    np.testing.assert_array_equal(seen["composite"], composite_panel(loaded.protocol, arrays))
    np.testing.assert_array_equal(seen["groups"], world.groups)


async def test_checks_fail_when_a_ic_has_no_power(tmp_path):
    world, sectors, loaded, output = _world_setup(tmp_path, decisive_lambda=1e-6)
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert "A-IC" in result["reason"]


async def test_checks_fail_when_c_ic_accepts_too_much(tmp_path, monkeypatch):
    world, sectors, loaded, output = _world_setup(tmp_path)
    monkeypatch.setattr(checks, "c_ic", lambda *a, **k: {"passes": False, "fixed_permutation": {"acceptance": 0.4}})
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert "C-IC" in result["reason"]


async def test_checks_refuse_below_the_mapped_floor(tmp_path, monkeypatch):
    world, _, loaded, output = _world_setup(tmp_path)
    symbols = world.build.cube.symbols
    # 30 of 48 names have a sector.
    sectors = sector_map({name: "a" for name in symbols[:30]})

    def never(*_args, **_kwargs):
        raise AssertionError("below the floor nothing is computed")

    monkeypatch.setattr(checks, "build_ic_arrays", never)
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert "62.5%" in result["reason"]
    assert json.loads((output / "result.json").read_text())["status"] == "failed"


async def test_mapped_floor_is_measured_over_the_cube_symbols_not_the_sector_file(tmp_path):
    world, _, loaded, output = _world_setup(tmp_path)
    extra = {f"X{j}": "a" for j in range(500)}  # names outside the cube do not raise the fraction
    table = {**{name: "a" for name in world.build.cube.symbols[:30]}, **extra}
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=sector_map(table), environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert "62.5%" in result["reason"]


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
async def test_a_raised_message_reaches_progress_and_never_the_result(tmp_path, error):
    """A ``ValueError`` too: only the deliberate refusals keep their text, not whatever a build raises."""
    _, sectors, loaded, output = _world_setup(tmp_path)

    async def build():
        raise error("provider-secret-text")

    said: list[str] = []
    result = await execute_checks(
        loaded, output, build=build, sectors=sectors, environment=environment(), progress=said.append
    )
    assert result["status"] == "failed"
    assert result["reason"] == error.__name__
    assert "provider-secret-text" not in (output / "result.json").read_text()
    assert "provider-secret-text" not in (output / "manifest.json").read_text()
    assert any("provider-secret-text" in line for line in said)


async def test_checks_write_manifest_before_computing(tmp_path):
    _, sectors, loaded, output = _world_setup(tmp_path)

    async def build():
        assert (output / "manifest.json").exists()
        raise RuntimeError("no cache")

    result = await execute_checks(
        loaded, output, build=build, sectors=sectors, environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert result["reason"] == "RuntimeError"
    assert (output / "manifest.json").exists()
    assert json.loads((output / "result.json").read_text())["status"] == "failed"


async def test_checks_refuse_a_dirty_revision_before_any_build(tmp_path):
    _, sectors, loaded, output = _world_setup(tmp_path)
    built = []

    async def build():
        built.append(1)
        raise RuntimeError("no cube")

    result = await execute_checks(
        loaded, output, build=build, sectors=sectors, environment=environment("abc-dirty"), progress=None
    )
    assert result["status"] == "failed"
    assert "refusing to run from a dirty" in result["reason"]
    assert not built


async def test_checks_refuse_an_unpinned_sector_map(tmp_path):
    world, _, loaded, output = _world_setup(tmp_path)
    other = world_sectors(world, sha256="0" * 64)
    result = await execute_checks(
        loaded, output, build=as_builder(world), sectors=other, environment=environment(), progress=None
    )
    assert result["status"] == "failed"
    assert "sector" in result["reason"]


async def test_checks_refuse_an_existing_output(tmp_path):
    world, sectors, loaded, output = _world_setup(tmp_path)
    output.mkdir()
    with pytest.raises(FileExistsError):
        await execute_checks(
            loaded, output, build=as_builder(world), sectors=sectors, environment=environment(), progress=None
        )


def _check_args(**overrides):
    return {"composite_sha256": "a" * 64, "sectors_sha256": FLAT, "revision": "abc123"} | overrides


def test_require_checks_accepts_a_passed_check_with_the_same_pins(tmp_path):
    directory = passed_check(tmp_path / "c", composite_sha256="a" * 64, sectors_sha256=FLAT, cube_sha256="b" * 64)
    assert require_checks(directory, **_check_args())["cube_sha256"] == "b" * 64


@pytest.mark.parametrize(
    ("written", "needle"),
    [
        ({"status": "failed"}, "not passed"),
        ({"composite_sha256": "9" * 64}, "composite"),
        ({"sectors_sha256": "9" * 64}, "sector"),
        ({"revision": "other"}, "revision"),
        ({"kind": "null_check"}, "holds"),
        ({"cube_sha256": ""}, "cube"),
    ],
)
def test_require_checks_refuses(tmp_path, written, needle):
    settings = {"composite_sha256": "a" * 64, "sectors_sha256": FLAT, "cube_sha256": "b" * 64} | written
    directory = passed_check(tmp_path / "c", **settings)
    with pytest.raises(ValueError, match=needle):
        require_checks(directory, **_check_args())


def test_require_checks_refuses_a_directory_without_a_result(tmp_path):
    with pytest.raises(ValueError, match="no readable"):
        require_checks(tmp_path / "missing", **_check_args())
