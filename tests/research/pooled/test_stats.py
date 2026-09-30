# tests/research/pooled/test_stats.py
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.pooled.stats import (
    bootstrap_draws,
    calendar_time_newey_west,
    design_effect,
    leg_mean_test,
    paired_edge_test,
    session_table,
    trimmed_mean,
    two_way_clustered,
)


def cube_view(r: np.ndarray, *, holding=1, labelled=None) -> CubeView:
    s, n = r.shape
    sessions = tuple(date(2021, 3, 1) + timedelta(days=i) for i in range(s))
    lab = np.isfinite(r) if labelled is None else labelled
    return CubeView(
        sessions=sessions,
        symbols=tuple(f"S{j}" for j in range(n)),
        offset=0,
        eligible=np.ones((s, n), bool),
        labelled=lab,
        r_gross=r.copy(),
        r_cost=r.copy(),
        holding=np.full((s, n), holding, dtype=np.int16) if np.isscalar(holding) else holding,
        hit=np.ones((s, n), np.int8),
        tiebreak=np.zeros((s, n), np.uint64),
        dollar_volume=np.ones((s, n)),
    )


def picks(cells) -> Picks:
    rows, cols = zip(*cells, strict=True)
    return Picks(np.array(rows), np.array(cols))


def test_paired_edge_is_pick_mean_minus_all_eligible_mean_per_session():
    r = np.array([[1.0, 0.0, -1.0], [2.0, 0.0, 1.0]])
    table = session_table(picks([(0, 0), (1, 2)]), cube_view(r), purge=False)
    assert table.control_mean.tolist() == [0.0, 1.0]
    assert table.edge.tolist() == [1.0, 0.0]
    result = paired_edge_test(table, bootstrap_draws(2, 20, 50, 1))
    assert result["mean"] == pytest.approx(0.5)
    assert result["n_sessions"] == 2


def test_sessions_without_picks_do_not_dilute_the_edge():
    r = np.array([[1.0, 0.0], [5.0, 5.0], [1.0, 0.0]])
    table = session_table(picks([(0, 0), (2, 0)]), cube_view(r), purge=False)
    assert np.isnan(table.edge[1])
    result = paired_edge_test(table, bootstrap_draws(3, 20, 200, 3))
    assert result["mean"] == pytest.approx(0.5)
    finite = result["ci90"]
    assert finite[0] == pytest.approx(0.5) and finite[1] == pytest.approx(0.5)  # every draw averages 0.5s only


def test_purge_drops_cells_whose_hold_crosses_the_window_end():
    r = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
    holding = np.array([[1, 1], [3, 1], [1, 2]], dtype=np.int16)
    table = session_table(picks([(0, 0), (1, 0), (2, 0)]), cube_view(r, holding=holding), purge=True)
    assert table.dropped_purged == 1  # session 1's pick exits at index 3 > last (2)
    assert table.pick_n.tolist() == [1, 0, 1]
    assert table.control_mean[2] == pytest.approx(1.0)  # (2,1) exits at 3: purged from the control too


def test_unlabelled_picks_are_dropped_and_counted():
    r = np.array([[np.nan, 0.0, 1.0]])
    table = session_table(picks([(0, 0), (0, 2)]), cube_view(r), purge=False)
    assert table.dropped_unlabelled == 1
    assert table.pick_n.tolist() == [1]


def test_leg_mean_weights_each_pick():
    r = np.array([[3.0, 1.0], [0.0, 0.0]])
    table = session_table(picks([(0, 0), (0, 1), (1, 0)]), cube_view(r), purge=False)
    assert leg_mean_test(table, bootstrap_draws(2, 20, 50, 1))["mean"] == pytest.approx(4.0 / 3.0)


def test_holds_requires_a_positive_lower_bound():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0, 1.0, (400, 20))
    r[:, 0] += 0.8
    table = session_table(picks([(i, 0) for i in range(400)]), cube_view(r), purge=False)
    good = paired_edge_test(table, bootstrap_draws(400, 20, 500, 7))
    assert good["holds"] and good["t"] > 3
    flat = np.zeros((400, 20))
    none = session_table(picks([(i, 1) for i in range(400)]), cube_view(flat), purge=False)
    result = paired_edge_test(none, bootstrap_draws(400, 20, 500, 7))
    assert result["mean"] == 0.0 and not result["holds"]  # a zero edge never holds


def test_trimmed_mean_matches_pead_semantics():
    assert trimmed_mean(np.array([-100.0, 1.0, 2.0, 3.0, 100.0]), 0.2) == pytest.approx(2.0)


def test_two_way_clustering_and_design_effect_on_a_reference_case():
    rows = pd.DataFrame(
        {
            "session_idx": [0, 0, 1, 1, 2, 2],
            "symbol_idx": [0, 1, 0, 1, 0, 1],
            "residual": [1.0, 1.0, -1.0, -1.0, 0.5, 0.5],
        }
    )
    clustered = two_way_clustered(rows)
    e = rows["residual"] - rows["residual"].mean()
    by_date = sum(g.sum() ** 2 for _, g in e.groupby(rows["session_idx"]))
    by_symbol = sum(g.sum() ** 2 for _, g in e.groupby(rows["symbol_idx"]))
    white = float((e**2).sum())
    variance = by_date + by_symbol - white
    variance = variance if variance > 0 else max(by_date, by_symbol)  # Thompson; fall back when non-positive
    assert clustered["se"] == pytest.approx(np.sqrt(variance) / len(rows))
    deff = design_effect(rows)
    assert deff["icc"] == pytest.approx(1.0)  # identical residuals within each session
    assert deff["deff"] == pytest.approx(2.0)


def test_calendar_time_series_is_finite_and_reports_t():
    rows = pd.DataFrame({"session_idx": [0, 1, 2, 3], "holding": [2, 2, 1, 1], "residual": [1.0, 0.5, 0.2, 0.1]})
    result = calendar_time_newey_west(rows, n_sessions=5, lag=2)
    assert np.isfinite(result["mean"]) and np.isfinite(result["t"])
