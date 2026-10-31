import numpy as np
import pytest

from agentic_trader.research.ic import composite as composite_module
from agentic_trader.research.ic.arrays import build_ic_arrays
from agentic_trader.research.ic.composite import composite_panel, member_panel
from tests.research.ic.world import LONG_DAYS, long_world, poisoned_world, shipped_composite, stub_arrays


PROTOCOL = shipped_composite().protocol
MEMBERS = PROTOCOL.members
REVERSAL = next(member for member in MEMBERS if member.id == "ret_1_0")  # sign -1
MOMENTUM = next(member for member in MEMBERS if member.id == "ret_3_1")  # sign +1
S, N = 40, 60
GROUPS = np.repeat(np.array(["a", "b", "c"]), 20)


def _panels(seed=0, s=S, n=N):
    rng = np.random.default_rng(seed)
    return {member.expression: rng.normal(size=(s, n)) for member in MEMBERS}


def _spy_on_group_zscore(monkeypatch):
    seen = []
    real = composite_module.group_zscore

    def spy(panel, groups, *, min_names):
        seen.append(panel.to_numpy(dtype=float).copy())
        return real(panel, groups, min_names=min_names)

    monkeypatch.setattr(composite_module, "group_zscore", spy)
    return seen


def test_sign_is_applied_before_z_scoring(monkeypatch):
    panels = _panels()
    seen = _spy_on_group_zscore(monkeypatch)
    arrays = stub_arrays(panels, GROUPS)
    out = member_panel(REVERSAL, PROTOCOL, arrays)
    raw = panels[REVERSAL.expression]
    expected = -(raw - raw.mean(axis=1, keepdims=True)) / raw.std(axis=1, ddof=1, keepdims=True)
    np.testing.assert_allclose(seen[0], np.clip(expected, -3.0, 3.0), atol=1e-12)
    # A sign of -1 makes a name that is high on the raw value low in the member.
    for s in range(S):
        assert np.corrcoef(out[s], raw[s])[0, 1] < 0
    same_data = stub_arrays({MOMENTUM.expression: raw}, GROUPS)
    np.testing.assert_allclose(out, -member_panel(MOMENTUM, PROTOCOL, same_data), atol=1e-9)


def test_each_member_is_clipped_at_three(monkeypatch):
    panels = _panels()
    panels[MOMENTUM.expression][5, 7] = 1e9
    panels[MOMENTUM.expression][6, 8] = -1e9
    seen = _spy_on_group_zscore(monkeypatch)
    member_panel(MOMENTUM, PROTOCOL, stub_arrays(panels, GROUPS))
    assert seen[0][5, 7] == 3.0
    assert seen[0][6, 8] == -3.0
    assert np.nanmax(np.abs(seen[0])) == 3.0


def test_each_member_is_uncorrelated_with_beta_and_log_adv():
    rng = np.random.default_rng(1)
    beta = rng.normal(1.0, 0.3, (S, N))
    log_adv = rng.normal(16.0, 1.0, (S, N))
    beta[3, 5] = np.nan  # a name with no beta that session drops out of the regression
    panels = _panels()
    # A member loading on both, so the regression has something to remove.
    panels[MOMENTUM.expression] = panels[MOMENTUM.expression] + 2.0 * beta + 0.5 * log_adv
    panels[MOMENTUM.expression][3, 5] = 1.0
    arrays = stub_arrays(panels, GROUPS, beta=beta, log_adv=log_adv)
    out = member_panel(MOMENTUM, PROTOCOL, arrays)
    assert np.isnan(out[3, 5])
    for s in range(S):
        keep = np.isfinite(out[s])
        assert keep.sum() >= 30
        assert abs(np.corrcoef(out[s][keep], beta[s][keep])[0, 1]) < 1e-9
        assert abs(np.corrcoef(out[s][keep], log_adv[s][keep])[0, 1]) < 1e-9


def test_regression_needs_thirty_rows():
    eligible = np.ones((S, N), bool)
    eligible[2, 29:] = False  # 29 names left that session
    out = member_panel(MOMENTUM, PROTOCOL, stub_arrays(_panels(), GROUPS, eligible=eligible))
    assert np.isnan(out[2]).all()
    assert np.isfinite(out[3]).all()


def test_each_member_has_unit_scale_before_averaging():
    arrays = stub_arrays(_panels(), GROUPS)
    for member in MEMBERS:
        out = member_panel(member, PROTOCOL, arrays)
        np.testing.assert_allclose(out.std(axis=1, ddof=1), 1.0, atol=1e-12)
        np.testing.assert_allclose(out.mean(axis=1), 0.0, atol=1e-12)


def _member_table(arrays):
    return {member.id: member_panel(member, PROTOCOL, arrays) for member in MEMBERS}


def test_a_theme_of_four_weighs_the_same_as_a_theme_of_one():
    arrays = stub_arrays(_panels(), GROUPS)
    table = _member_table(arrays)
    out = composite_panel(PROTOCOL, arrays)
    order = [member.id for member in MEMBERS]
    coefficients, *_ = np.linalg.lstsq(np.column_stack([table[i][7] for i in order]), out[7], rcond=None)
    np.testing.assert_allclose(coefficients, [1 / 12] * 4 + [1 / 3, 1 / 3], atol=1e-9)


def test_a_theme_averages_only_its_available_members():
    panels = _panels()
    panels["roc(delay(close, 21), 231)"][4, 9] = np.nan  # ret_12_1 is missing for this one name
    arrays = stub_arrays(panels, GROUPS)
    table = _member_table(arrays)
    out = composite_panel(PROTOCOL, arrays)
    momentum = np.mean([table[i][4, 9] for i in ("ret_3_1", "ret_6_1", "ret_9_1")])
    expected = (momentum + table["ret_1_0"][4, 9] + table["ivol_capm_252d"][4, 9]) / 3
    assert out[4, 9] == pytest.approx(expected, abs=1e-12)


def test_a_theme_with_no_member_leaves_the_mean_over_the_others():
    panels = _panels()
    panels["roc(close, 21)"][4, 9] = np.nan  # the reversal theme has no member for this name
    arrays = stub_arrays(panels, GROUPS)
    table = _member_table(arrays)
    out = composite_panel(PROTOCOL, arrays)
    momentum = np.mean([table[i][4, 9] for i in ("ret_3_1", "ret_6_1", "ret_9_1", "ret_12_1")])
    assert out[4, 9] == pytest.approx((momentum + table["ivol_capm_252d"][4, 9]) / 2, abs=1e-12)


def test_name_needs_four_of_six_members():
    panels = _panels()
    expressions = [member.expression for member in MEMBERS]
    for expression in expressions[:3]:  # name 11 has three members left that session
        panels[expression][4, 11] = np.nan
    for expression in expressions[:2]:  # name 12 has four
        panels[expression][4, 12] = np.nan
    out = composite_panel(PROTOCOL, stub_arrays(panels, GROUPS))
    assert np.isnan(out[4, 11])
    assert np.isfinite(out[4, 12])
    assert np.isfinite(out[3, 11])


def test_small_sector_is_nan_that_session():
    groups = np.array(["big"] * 55 + ["small"] * 5)
    eligible = np.ones((S, N), bool)
    eligible[6, 55] = False  # four names left in the small sector, that session only
    out = composite_panel(PROTOCOL, stub_arrays(_panels(), groups, eligible=eligible))
    assert np.isnan(out[6, 55:]).all()
    assert np.isfinite(out[6, :55]).all()
    assert np.isfinite(out[5, 55:]).all()
    assert np.isfinite(out[7, 55:]).all()


def test_an_ineligible_name_gets_no_score_and_moves_no_one_else():
    panels = _panels()
    eligible = np.ones((S, N), bool)
    eligible[8, 3] = False
    kept = composite_panel(PROTOCOL, stub_arrays(panels, GROUPS, eligible=eligible))
    assert np.isnan(kept[8, 3])
    panels[MOMENTUM.expression][8, 3] = 1e12  # an ineligible name's value does not reach anyone's z-score
    again = composite_panel(PROTOCOL, stub_arrays(panels, GROUPS, eligible=eligible))
    np.testing.assert_array_equal(kept, again)


def test_composite_does_not_change_when_bars_from_d_on_change():
    world = long_world()
    protocol = PROTOCOL.model_copy(update={"window": world.window})
    base = build_ic_arrays(world.build, world.spy, world.groups, world.window, horizons=(20,))
    position = 100  # a session inside the window
    decision = base.sessions[position]
    assert decision > LONG_DAYS[260]
    before = composite_panel(protocol, base)
    assert np.isfinite(before[position]).sum() >= 30

    changed = poisoned_world(world, decision)
    arrays = build_ic_arrays(changed.build, changed.spy, changed.groups, changed.window, horizons=(20,))
    after = composite_panel(protocol, arrays)
    np.testing.assert_array_equal(after[: position + 1], before[: position + 1])
    assert not np.array_equal(after[position + 1 :], before[position + 1 :], equal_nan=True)

    # The read is D-1 and no later: a change at D-1 does reach the score for D.
    earlier = poisoned_world(world, base.sessions[position - 1])
    arrays = build_ic_arrays(earlier.build, earlier.spy, earlier.groups, earlier.window, horizons=(20,))
    moved = composite_panel(protocol, arrays)
    np.testing.assert_array_equal(moved[:position], before[:position])
    assert not np.array_equal(moved[position], before[position], equal_nan=True)
