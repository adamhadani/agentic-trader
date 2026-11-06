from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.apriori.catalog import BootstrapSpec
from agentic_trader.research.apriori.statistics import (
    difference_in_paired_test,
    mean_test,
    paired_test,
    trimmed_mean,
)
from agentic_trader.research.setups.study import _stationary_index_draws


SPEC = BootstrapSpec(block_mean=10, draws=400, seed=7)


def _frame(values_by_session: dict[date, list[float]]) -> pd.DataFrame:
    rows = [{"session": s, "r_cost": v} for s, values in values_by_session.items() for v in values]
    return pd.DataFrame(rows)


def _sessions(n: int) -> list[date]:
    return [date(2022, 1, 3) + timedelta(days=k) for k in range(n)]


def test_mean_test_holds_on_a_clear_edge_and_reports_counts():
    rng = np.random.default_rng(1)
    frame = _frame({s: [0.3 + 0.1 * rng.standard_normal() for _ in range(2)] for s in _sessions(300)})
    result = mean_test(frame, SPEC)
    assert result["holds"] and result["n"] == 600 and result["n_sessions"] == 300
    assert result["ci90"][0] > 0 and result["finite_draws"] == 400 and 0 <= result["p_one_sided"] <= 1


def test_mean_test_does_not_hold_around_zero():
    rng = np.random.default_rng(2)
    frame = _frame({s: [0.1 * rng.standard_normal()] for s in _sessions(300)})
    assert not mean_test(frame, SPEC)["holds"]


def test_paired_test_keys_both_sides_by_the_control_sessions():
    sessions = _sessions(200)
    leg = _frame({s: [0.5] for s in sessions})
    control = _frame({s: [0.1] for s in sessions[:150]})  # the last 50 leg sessions have no control
    result = paired_test(leg, control, SPEC)
    assert result["n_leg"] == 150 and result["n_control"] == 150 and result["n_sessions"] == 150
    assert abs(result["mean_diff"] - 0.4) < 1e-9 and result["holds"]
    assert abs(result["control_mean_r_cost"] - 0.1) < 1e-9


def test_paired_test_reports_the_standard_deviation_of_its_own_draws():
    rng = np.random.default_rng(3)
    sessions = _sessions(120)
    leg = _frame({s: [0.3 + 0.2 * rng.standard_normal()] for s in sessions})
    control = _frame({s: [0.1 * rng.standard_normal()] for s in sessions})
    result = paired_test(leg, control, SPEC)
    diffs = leg["r_cost"].to_numpy() - control["r_cost"].to_numpy()  # one row a session on each side
    draws = _stationary_index_draws(len(sessions), SPEC.block_mean, SPEC.draws, SPEC.seed)
    expected = np.std([diffs[rows].mean() for rows in draws], ddof=1)
    assert result["boot_sd"] == pytest.approx(expected, rel=1e-12) and result["boot_sd"] > 0

    constant = paired_test(_frame({s: [0.5] for s in sessions}), _frame({s: [0.1] for s in sessions}), SPEC)
    assert constant["boot_sd"] == pytest.approx(0.0, abs=1e-12)


def _noisy(sessions, mean, rng, sd=0.2):
    return _frame({s: [mean + sd * rng.standard_normal()] for s in sessions})


def test_difference_in_paired_test_holds_when_the_legs_beat_their_controls_by_more():
    rng = np.random.default_rng(4)
    sessions = _sessions(300)
    leg, leg_control = _noisy(sessions, 0.6, rng), _noisy(sessions, 0.1, rng)
    other, other_control = _noisy(sessions, 0.2, rng), _noisy(sessions, 0.1, rng)
    result = difference_in_paired_test(leg, leg_control, other, other_control, SPEC)
    leg_p2, other_p2 = paired_test(leg, leg_control, SPEC), paired_test(other, other_control, SPEC)
    assert result["leg_diff"] == pytest.approx(leg_p2["mean_diff"], rel=1e-12)
    assert result["other_diff"] == pytest.approx(other_p2["mean_diff"], rel=1e-12)
    assert result["mean_diff"] == pytest.approx(leg_p2["mean_diff"] - other_p2["mean_diff"], rel=1e-12)
    assert result["n_sessions"] == 300 and result["finite_draws"] == SPEC.draws
    assert result["ci90"][0] > 0 and result["holds"]
    assert result["boot_sd"] > 0 and 0 <= result["p_one_sided"] <= 1


def test_difference_in_paired_test_fails_when_both_differences_are_equal():
    rng = np.random.default_rng(5)
    sessions = _sessions(300)
    leg, leg_control = _noisy(sessions, 0.5, rng), _noisy(sessions, 0.1, rng)
    other, other_control = _noisy(sessions, 0.5, rng), _noisy(sessions, 0.1, rng)
    result = difference_in_paired_test(leg, leg_control, other, other_control, SPEC)
    assert abs(result["mean_diff"]) < 0.05 and result["ci90"][0] < 0 and not result["holds"]

    exact = difference_in_paired_test(leg, leg_control, leg, leg_control, SPEC)
    assert exact["mean_diff"] == 0.0 and not exact["holds"]


def test_difference_in_paired_test_resamples_the_union_of_sessions_jointly():
    sessions = _sessions(200)
    leg = _frame({s: [0.5] for s in sessions[:120]})
    leg_control = _frame({s: [0.1] for s in sessions[:100]})  # 20 leg sessions have no control: dropped
    other = _frame({s: [0.2] for s in sessions[80:]})
    other_control = _frame({s: [0.1] for s in sessions[80:]})
    result = difference_in_paired_test(leg, leg_control, other, other_control, SPEC)
    assert result["n_sessions"] == 200  # sessions 0-99 and 80-199
    assert result["n_leg"] == 100 and result["n_leg_control"] == 100
    assert result["n_other"] == 120 and result["n_other_control"] == 120
    assert result["leg_diff"] == pytest.approx(0.4) and result["other_diff"] == pytest.approx(0.1)
    assert result["mean_diff"] == pytest.approx(0.3) and result["holds"]


def test_difference_in_paired_test_without_one_side_does_not_hold():
    sessions = _sessions(50)
    leg, control = _frame({s: [0.5] for s in sessions}), _frame({s: [0.1] for s in sessions})
    empty = pd.DataFrame({"session": pd.Series([], dtype=object), "r_cost": pd.Series([], dtype=float)})
    result = difference_in_paired_test(leg, control, empty, empty, SPEC)
    assert np.isnan(result["other_diff"]) and np.isnan(result["mean_diff"]) and not result["holds"]


def test_trimmed_mean_drops_each_tail():
    values = np.array([-100.0, *([1.0] * 98), 100.0])
    assert trimmed_mean(values, 0.01) == 1.0
    assert np.isnan(trimmed_mean(np.array([np.nan]), 0.01))
