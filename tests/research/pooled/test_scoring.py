from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from agentic_trader.research.pooled import scoring
from agentic_trader.research.pooled.formula import (
    evaluate_panel,
    evaluate_prepared_hourly,
    expression_nodes,
    prepare_hourly,
)
from agentic_trader.research.pooled.scoring import (
    FrozenPanels,
    ScoreBook,
    formula_id,
    score_book,
    vet_expression,
)
from tests.research.pooled.mini_world import (
    cube_build,
    intraday_cube_build,
    label_cube,
    mini_fixed_set_protocol,
    mini_intraday_protocol,
)
from tests.research.pooled.test_formula import daily, hourly, weekdays


DAYS = weekdays(date(2021, 1, 4), 60)
SESSIONS = tuple(DAYS[20:])
SYMBOLS = ("AAA", "BBB", "CCC")  # CCC has no bars
ADJUSTED = {
    "AAA": daily(DAYS, list(np.linspace(50, 80, 60))),
    "BBB": daily(DAYS, list(np.linspace(90, 40, 60))),
}
FORBIDDEN = ("REALIZED_VOL", "TS_STD", "TS_MAD")


class BlindView:
    """A view whose label arrays raise when touched: a score panel may read none of them."""

    def __init__(self, sessions, symbols, offset):
        self.sessions = tuple(sessions)
        self.symbols = tuple(symbols)
        self.offset = offset
        self.eligible = np.ones((len(sessions), len(symbols)), bool)

    def __getattr__(self, name):
        raise AssertionError(f"a score panel read {name}")


@pytest.mark.parametrize(
    ("expression", "seen", "canonical", "reason"),
    [
        ("roc(close,5)", (), "roc(close, 5)", None),
        ("roc(close,5)", ("roc(close, 5)",), "roc(close, 5)", "duplicate"),
        ("zscore(ts_std(returns, 20), 10)", (), "zscore(ts_std(returns, 20), 10)", "forbidden operator"),
        ("ts_slope(close, 20)", (), "ts_slope(close, 20)", "not dimensionless"),
        ("roc(close, ", (), "roc(close, ", "does not compile"),
        ("nosuchop(close, 5)", (), "nosuchop(close, 5)", "does not compile"),
    ],
)
def test_vet_expression_decides_before_anything_is_charged(expression, seen, canonical, reason):
    vetted = vet_expression(expression, FORBIDDEN, set(seen))
    assert (vetted.expression, vetted.reason) == (canonical, reason)


def test_formula_ids_hash_the_canonical_expression():
    assert formula_id("roc(close,5)") == formula_id("roc(close, 5)")
    assert len(formula_id("roc(close, 5)")) == 16
    assert formula_id("roc(close, 5)") != formula_id("roc(close, 10)")


def test_score_panels_never_touch_labels_and_slice_the_full_calendar_at_the_view_offset():
    formula = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS).formula("roc(close, 5)")
    scores, allowed = formula.panel(BlindView(SESSIONS[10:20], SYMBOLS, offset=10))
    expected = evaluate_panel("roc(close, 5)", ADJUSTED, DAYS, SESSIONS[10:20], SYMBOLS)
    np.testing.assert_array_equal(scores, expected)
    assert allowed.shape == (10, 3) and allowed.all()
    assert np.isnan(scores[:, 2]).all()
    assert formula.formula_id == formula_id("roc(close,5)")
    assert formula.nodes == expression_nodes("roc(close, 5)")


def test_the_book_keeps_a_bounded_lru_and_recomputes_evicted_panels_identically(monkeypatch):
    calls = []
    real = scoring.evaluate_prepared

    def counting(expression, *args):
        calls.append(expression)
        return real(expression, *args)

    monkeypatch.setattr(scoring, "evaluate_prepared", counting)
    book = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS, capacity=2)
    first = book.panel("roc(close, 5)").copy()
    book.panel("roc(close, 10)")
    book.panel("roc(close, 5)")  # a hit refreshes it
    book.panel("roc(close, 20)")  # evicts roc(close, 10), the least recently used
    book.panel("roc(close, 5)")  # still cached
    book.panel("roc(close, 10)")  # recomputed
    assert calls == ["roc(close, 5)", "roc(close, 10)", "roc(close, 20)", "roc(close, 10)"]
    np.testing.assert_array_equal(book.panel("roc(close, 5)"), first)
    assert not book.panel("roc(close, 5)").flags.writeable


def test_a_view_from_another_calendar_or_cohort_is_refused():
    formula = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS).formula("roc(close, 5)")
    with pytest.raises(ValueError, match="calendar"):
        formula.panel(BlindView(DAYS[:5], SYMBOLS, offset=0))
    with pytest.raises(ValueError, match="symbols"):
        formula.panel(BlindView(SESSIONS[:5], ("AAA",), offset=0))


HDAYS = weekdays(date(2021, 1, 4), 30)
HSESSIONS = tuple(HDAYS[5:])
HOURLY = {"AAA": hourly(HDAYS), "BBB": hourly(HDAYS, closes_by_bar=list(np.linspace(300, 100, 30 * 7)))}


def test_hourly_book_matches_the_evaluator_and_is_label_blind():
    book = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1)
    formula = book.formula("oc_spread")
    scores, allowed = formula.panel(BlindView(HSESSIONS[3:9], SYMBOLS, offset=3))
    expected = evaluate_prepared_hourly(
        "oc_spread", prepare_hourly(HOURLY), HSESSIONS[3:9], SYMBOLS, decision_time_et="10:35", bars_before_decision=1
    )
    np.testing.assert_array_equal(np.nan_to_num(scores, nan=-1), np.nan_to_num(expected, nan=-1))
    assert allowed.all() and np.isnan(scores[:, 2]).all()  # CCC has no bars
    assert book.panel("oc_spread") is book.panel("oc_spread")


def test_frozen_panels_serve_only_the_frozen_expressions():
    book = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1)
    frozen = book.freeze(("oc_spread", "open_gap"))
    assert isinstance(frozen, FrozenPanels)
    np.testing.assert_array_equal(
        np.nan_to_num(frozen.panel("oc_spread"), nan=-1), np.nan_to_num(book.panel("oc_spread"), nan=-1)
    )
    scores, _ = frozen.formula("open_gap").panel(BlindView(HSESSIONS[2:5], SYMBOLS, offset=2))
    assert scores.shape == (3, 3)
    assert frozen.sessions == book.sessions and frozen.symbols == book.symbols


def test_frozen_panels_refuse_unknown_expressions():
    frozen = ScoreBook.hourly(HOURLY, HSESSIONS, SYMBOLS, decision_time_et="10:35", bars_before_decision=1).freeze(
        ("oc_spread",)
    )
    with pytest.raises(ValueError, match="frozen"):
        frozen.panel("open_gap")
    with pytest.raises(ValueError, match="frozen"):
        frozen.formula("roc(close, 5)")
    # Canonical form, not the literal string, is the key.
    assert frozen.panel("oc_spread ") is frozen.panel("oc_spread")


def test_daily_book_is_unchanged():
    book = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS)
    assert book.formula("roc(close, 5)").formula_id == formula_id("roc(close, 5)")


def test_score_book_dispatches_on_protocol_inputs():
    cube = label_cube()
    assert score_book(mini_fixed_set_protocol(), cube_build(cube)).score_inputs == "daily"
    assert score_book(mini_intraday_protocol(), intraday_cube_build(cube)).score_inputs == "hourly"
    with pytest.raises(ValueError, match="hourly frames"):
        score_book(mini_intraday_protocol(), replace(intraday_cube_build(cube), hourly={}))
