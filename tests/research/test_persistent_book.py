"""Self-financing daily research book; no broker or promotion authority."""

import json

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.persistent_book import (
    BookMode,
    BookPolicy,
    CostScenario,
    dependence_blend,
    simulate_book,
)


@pytest.fixture
def book_input():
    clock = pd.date_range("2023-01-05", periods=5, freq="B", tz="America/New_York")
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    prices = pd.DataFrame(100.0, index=clock, columns=["A", "B"])
    targets = pd.DataFrame([[0.5, -0.5]] * len(clock), index=clock, columns=prices.columns)
    return prices, targets, sessions


@pytest.mark.parametrize("mode,turnover", [(BookMode.RESET, 8), (BookMode.PERSISTENT, 2), (BookMode.BUFFERED, 2)])
def test_constant_book_netting_roundtrips_and_terminal_inventory(book_input, mode, turnover):
    prices, targets, sessions = book_input
    result = simulate_book(prices, prices, targets, sessions, BookPolicy(mode, 1, 0.1), CostScenario(0, 0, 0))
    assert result["turnover_initial_capital"] == pytest.approx(turnover)
    assert result["net_return"] == 0
    assert result["terminal_inventory"] == {"A": 0.0, "B": 0.0}
    assert len(result["daily"]) == len(prices)
    assert result["daily"][0]["units"] == {"A": 0.0, "B": 0.0}


def test_exact_gap_day_fee_borrow_cash_and_self_financing(book_input):
    prices, targets, sessions = book_input
    opens = prices.copy()
    closes = prices.copy()
    opens.iloc[2:, 0] = 110
    closes.iloc[2:, 0] = 112
    costs = CostScenario(5, 300, 500)
    result = simulate_book(opens, closes, targets, sessions, BookPolicy(BookMode.PERSISTENT, 20, 0.1), costs)
    # Only one rebalance, quantities based on the preceding close, never tomorrow's opening price.
    np.testing.assert_allclose(list(result["daily"][1]["units"].values()), [0.005, -0.005])
    prior_equity = 1.0
    for row in result["daily"]:
        assert row["equity"] == pytest.approx(row["cash"] + sum(row["marked_inventory"].values()))
        assert row["equity"] - prior_equity == pytest.approx(
            row["price_pnl"] - row["fees"] - row["borrow"] - row["funding"]
        )
        prior_equity = row["equity"]
    # Friday-to-Monday borrowing includes the weekend, using the actual calendar clock.
    monday = result["daily"][2]
    assert monday["borrow"] == pytest.approx(0.5 * 0.03 * 3 / 365)
    assert result["fees_initial_capital"] == pytest.approx(0.0005 * (1 + 0.005 * 112 + 0.005 * 100))
    assert result["net_return"] == pytest.approx(
        0.06 - result["fees_initial_capital"] - result["borrow_initial_capital"]
    )


def test_future_prices_scores_and_split_rescaling_cannot_change_prior_decisions(book_input):
    prices, targets, sessions = book_input
    args = (BookPolicy(BookMode.PERSISTENT, 1, 0.1), CostScenario(1, 100, 300))
    a = simulate_book(prices, prices, targets, sessions, *args)
    changed = prices.copy()
    changed.iloc[3:] *= [2, 0.5]
    b = simulate_book(changed, changed, targets, sessions, *args)
    assert a["daily"][:3] == b["daily"][:3]
    assert a["daily"][3]["submitted_units"] == b["daily"][3]["submitted_units"]
    # Back-adjustment by a later event is a per-symbol constant on the past prefix.
    scaled = prices * [0.25, 3]
    c = simulate_book(scaled, scaled, targets, sessions, *args)
    assert c["net_return"] == pytest.approx(a["net_return"])
    assert c["turnover_initial_capital"] == pytest.approx(a["turnover_initial_capital"])


@pytest.mark.parametrize("bad", ["missing_price", "future_target_clock", "missing_score", "wrong_session"])
def test_missing_or_misaligned_evidence_fails(book_input, bad):
    prices, targets, sessions = book_input
    if bad == "missing_price":
        prices.iloc[2, 0] = np.nan
    if bad == "future_target_clock":
        targets.index += pd.Timedelta(days=1)
    if bad == "missing_score":
        targets.iloc[1, 0] = np.nan
    if bad == "wrong_session":
        sessions = sessions[:-1]
    with pytest.raises(ValueError):
        simulate_book(
            prices, prices, targets, sessions, BookPolicy(BookMode.PERSISTENT, 1, 0.1), CostScenario(1, 100, 300)
        )


def test_buffer_skips_small_reallocations(book_input):
    prices, targets, sessions = book_input
    targets.iloc[1:] = [0.49, -0.49]
    results = {
        mode: simulate_book(prices, prices, targets, sessions, BookPolicy(mode, 1, 0.1), CostScenario(0, 0, 0))
        for mode in [BookMode.PERSISTENT, BookMode.BUFFERED]
    }
    assert results[BookMode.BUFFERED]["rebalances"] == 1
    assert results[BookMode.PERSISTENT]["rebalances"] == 2


def test_dependence_blend_duplicate_invariance_complements_and_causality():
    clock = pd.date_range("2021-01-01", periods=40, freq="B", tz="America/New_York")
    a = pd.DataFrame(np.random.default_rng(17).normal(size=(40, 8)), index=clock)
    b = pd.DataFrame(np.random.default_rng(18).normal(size=(40, 8)), index=clock)
    original, evidence = dependence_blend({"a": a, "b": b}, window=10, shrinkage=0.2)
    duplicate, _ = dependence_blend({"a": a, "b": b, "clone": a.copy()}, window=10, shrinkage=0.2)
    pd.testing.assert_frame_equal(original, duplicate)
    assert all(len(e["weights"]) == 2 and sum(e["weights"].values()) == pytest.approx(1) for e in evidence)
    b.iloc[30:] *= -10
    changed, _ = dependence_blend({"a": a, "b": b}, window=10, shrinkage=0.2)
    pd.testing.assert_frame_equal(original.iloc[:30], changed.iloc[:30])


@pytest.mark.parametrize("edge", [False, True])
def test_null_and_planted_edge_controls(edge):
    clock = pd.date_range("2021-01-01", periods=260, freq="B", tz="America/New_York")
    # Deterministic economic control: no movement vs known causal, persistent spread.
    steps = np.arange(len(clock))[:, None] * np.array([[0.001, -0.001]]) if edge else np.zeros((len(clock), 2))
    prices = pd.DataFrame(100 * np.exp(steps), index=clock, columns=["A", "B"])
    targets = pd.DataFrame([[0.5, -0.5]] * len(clock), index=clock, columns=prices.columns)
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    result = simulate_book(
        prices, prices, targets, sessions, BookPolicy(BookMode.BUFFERED, 20, 0.1), CostScenario(5, 300, 500)
    )
    assert (result["net_return"] > 0) == edge


def test_serialized_covariance_retains_explicit_factor_axis_order():
    clock = pd.date_range("2022-01-03", periods=12, freq="B", tz="America/New_York")
    rng = np.random.default_rng(86)
    components = {name: pd.DataFrame(rng.normal(size=(12, 4)), index=clock) for name in ["zeta", "alpha"]}
    _, evidence = dependence_blend(components, window=10, shrinkage=0.2)
    saved = json.loads(json.dumps(evidence, sort_keys=True))
    assert saved[0]["factors"] == ["zeta", "alpha"]
    assert set(saved[0]["factors"]) == set(saved[0]["weights"])
