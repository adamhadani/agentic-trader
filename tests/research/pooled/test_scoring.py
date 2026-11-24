from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.pooled import scoring
from agentic_trader.research.pooled.events import EVENT_FIELDS
from agentic_trader.research.pooled.formula import (
    evaluate_panel,
    evaluate_prepared_hourly,
    expression_nodes,
    prepare_hourly,
)
from agentic_trader.research.pooled.insider_fields import INSIDER_FIELDS
from agentic_trader.research.pooled.scoring import (
    FrozenPanels,
    ScoreBook,
    formula_id,
    score_book,
    vet_expression,
)
from agentic_trader.research.pooled.short_fields import SHORT_FIELDS
from tests.research.pooled.mini_world import (
    ADJUSTED as MINI_ADJUSTED,
    BAR_DAYS as MINI_BAR_DAYS,
    EVENT_FRAMES,
    INSIDER_FRAMES,
    NAMES as MINI_NAMES,
    SESSIONS as MINI_SESSIONS,
    SHORT_FRAMES,
    cube_build,
    events_cube_build,
    insider_cube_build,
    intraday_cube_build,
    label_cube,
    mini_events_protocol,
    mini_fixed_set_protocol,
    mini_insider_protocol,
    mini_intraday_protocol,
    mini_short_protocol,
    short_cube_build,
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


def _events_book():
    return ScoreBook.with_fields(MINI_ADJUSTED, EVENT_FRAMES, EVENT_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES)


def test_with_fields_reads_event_columns_at_d_minus_one_like_daily_fields():
    book = _events_book()
    assert book.score_inputs == "daily"
    panel = book.panel("earnings_age")
    position = {d: i for i, d in enumerate(MINI_BAR_DAYS)}
    for row, session in ((0, MINI_SESSIONS[0]), (500, MINI_SESSIONS[500])):
        previous = MINI_BAR_DAYS[position[session] - 1]
        for col, name in enumerate(MINI_NAMES):
            expected = EVENT_FRAMES[name].loc[pd.Timestamp(previous), "earnings_age"]
            assert (np.isnan(panel[row, col]) and np.isnan(expected)) or panel[row, col] == expected
    # bar fields still work on the same book
    assert np.isfinite(book.panel("close / ts_mean(close, 20)")[-1]).all()


def test_with_fields_mixes_bar_and_event_fields_in_one_expression():
    book = _events_book()
    mixed = book.panel("if_else(earnings_age > 20, ts_sum(returns, 20), 0)")
    plain = book.panel("ts_sum(returns, 20)")
    age = book.panel("earnings_age")
    quiet = np.isfinite(age) & (age > 20)
    assert np.allclose(mixed[quiet], plain[quiet], equal_nan=True)
    assert (mixed[np.isfinite(age) & (age <= 20)] == 0).all()


def test_with_fields_without_a_symbols_frame_leaves_bar_fields_and_nans_event_fields():
    frames = {k: v for k, v in EVENT_FRAMES.items() if k != MINI_NAMES[0]}
    book = ScoreBook.with_fields(MINI_ADJUSTED, frames, EVENT_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES)
    assert np.isnan(book.panel("earnings_age")[:, 0]).all()
    assert np.isfinite(book.panel("close")[-1, 0])


def test_score_book_dispatches_events_protocol_and_refuses_a_build_without_events():
    cube = label_cube()
    protocol = mini_events_protocol()
    book = score_book(protocol, events_cube_build(cube))
    assert book.score_inputs == "daily" and np.isfinite(book.panel("dividend_yield")[-1]).any()
    with pytest.raises(ValueError, match="event"):
        score_book(protocol, cube_build(cube))


def test_frozen_panels_from_an_events_book_serve_event_expressions():
    book = _events_book()
    frozen = book.freeze(["earnings_surprise / (1 + earnings_age)"])
    assert frozen.score_inputs == "daily"
    assert frozen.panel("earnings_surprise / (1 + earnings_age)").shape == (len(MINI_SESSIONS), len(MINI_NAMES))


def test_with_fields_reads_short_columns_at_d_minus_one():
    book = ScoreBook.with_fields(MINI_ADJUSTED, SHORT_FRAMES, SHORT_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES)
    panel = book.panel("short_ratio_z")
    position = {d: i for i, d in enumerate(MINI_BAR_DAYS)}
    for row in (0, 500):
        previous = MINI_BAR_DAYS[position[MINI_SESSIONS[row]] - 1]
        for col, name in enumerate(MINI_NAMES):
            assert panel[row, col] == SHORT_FRAMES[name].loc[pd.Timestamp(previous), "short_ratio_z"]
    windowed = book.panel("-short_ratio_z / (ts_sum(returns, 5) > 0)")
    returns = book.panel("ts_sum(returns, 5)")
    z = book.panel("short_ratio_z")
    inside = np.isfinite(z) & (returns > 0)
    outside = np.isfinite(z) & np.isfinite(returns) & (returns <= 0)
    assert inside.any() and outside.any()
    assert np.isnan(windowed[outside]).all()
    assert np.allclose(windowed[inside], -z[inside])


def test_with_fields_joins_only_the_named_columns_and_refuses_a_frame_missing_one():
    # A frame column outside ``field_names`` (here another lane's field) never becomes readable.
    extra = {name: frame.assign(earnings_age=1.0) for name, frame in SHORT_FRAMES.items()}
    book = ScoreBook.with_fields(MINI_ADJUSTED, extra, SHORT_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES)
    with pytest.raises(ValueError, match="Missing required fields"):
        book.panel("earnings_age")
    assert np.isfinite(book.panel("short_ratio")[-1]).any()
    partial = dict(SHORT_FRAMES)
    partial[MINI_NAMES[1]] = SHORT_FRAMES[MINI_NAMES[1]].drop(columns=["offex_share"])
    with pytest.raises(ValueError, match=f"{MINI_NAMES[1]}.*offex_share"):
        ScoreBook.with_fields(MINI_ADJUSTED, partial, SHORT_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES)


def test_score_book_dispatches_short_protocol_and_refuses_a_build_without_short_frames():
    cube = label_cube()
    protocol = mini_short_protocol()
    book = score_book(protocol, short_cube_build(cube))
    assert book.score_inputs == "daily" and np.isfinite(book.panel("offex_share")[-1]).any()
    with pytest.raises(ValueError, match="short-volume"):
        score_book(protocol, cube_build(cube))


def test_short_coverage_document_is_the_coverage_or_none():
    cube = label_cube()
    document = scoring.short_coverage_document(short_cube_build(cube))
    assert document is not None and document["unscored_rows"] == 0
    assert scoring.short_coverage_document(cube_build(cube)) is None


def test_with_fields_reads_insider_columns_at_d_minus_one():
    book = ScoreBook.with_fields(
        MINI_ADJUSTED, INSIDER_FRAMES, INSIDER_FIELDS, MINI_BAR_DAYS, MINI_SESSIONS, MINI_NAMES
    )
    panel = book.panel("insider_buy_rel")
    position = {d: i for i, d in enumerate(MINI_BAR_DAYS)}
    for row in (0, 500):
        previous = MINI_BAR_DAYS[position[MINI_SESSIONS[row]] - 1]
        for col, name in enumerate(MINI_NAMES):
            assert panel[row, col] == INSIDER_FRAMES[name].loc[pd.Timestamp(previous), "insider_buy_rel"]
    gated = book.panel("insider_buy_rel / (insider_buyers > 1)")
    buy_rel = book.panel("insider_buy_rel")
    buyers = book.panel("insider_buyers")
    inside = np.isfinite(buy_rel) & (buyers > 1)
    outside = np.isfinite(buy_rel) & np.isfinite(buyers) & (buyers <= 1)
    assert inside.any() and outside.any()
    assert np.isnan(gated[outside]).all()
    assert np.allclose(gated[inside], buy_rel[inside])
    positive = book.panel("insider_buy_rel / (insider_buy_rel > 0)")
    assert np.isfinite(positive).any() and np.isnan(positive[np.isfinite(buy_rel) & (buy_rel == 0)]).all()


def test_score_book_dispatches_insider_protocol_and_refuses_a_build_without_insider_frames():
    cube = label_cube()
    protocol = mini_insider_protocol()
    book = score_book(protocol, insider_cube_build(cube))
    assert book.score_inputs == "daily" and np.isfinite(book.panel("insider_buy_rel")[-1]).any()
    with pytest.raises(ValueError, match="insider"):
        score_book(protocol, cube_build(cube))


def test_insider_coverage_document_is_the_coverage_or_none():
    cube = label_cube()
    document = scoring.insider_coverage_document(insider_cube_build(cube))
    assert document is not None and document["unscored_rows"] == 0
    assert scoring.insider_coverage_document(cube_build(cube)) is None
