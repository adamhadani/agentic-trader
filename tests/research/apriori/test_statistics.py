from datetime import date, timedelta

import numpy as np
import pandas as pd

from agentic_trader.research.apriori.catalog import BootstrapSpec
from agentic_trader.research.apriori.statistics import mean_test, paired_test, trimmed_mean


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


def test_trimmed_mean_drops_each_tail():
    values = np.array([-100.0, *([1.0] * 98), 100.0])
    assert trimmed_mean(values, 0.01) == 1.0
    assert np.isnan(trimmed_mean(np.array([np.nan]), 0.01))
