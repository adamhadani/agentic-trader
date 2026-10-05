from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import (
    Formula,
    FormulaFilter,
    Picks,
    allowed_mask,
    evaluate_panel,
    expression_nodes,
    jaccard_codes,
    select_picks,
    select_top_fraction,
)


def weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def daily(days: list[date], closes: Sequence[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=ET_TZ).astimezone(UTC) for d in days])
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6}, index=index)


def view(scores_shape, *, holding=1, tiebreak=None) -> CubeView:
    s, n = scores_shape
    sessions = tuple(weekdays(date(2021, 3, 1), s))
    tb = np.arange(n, dtype=np.uint64)[None, :].repeat(s, 0) if tiebreak is None else tiebreak
    return CubeView(
        sessions=sessions,
        symbols=tuple(f"S{j}" for j in range(n)),
        offset=0,
        eligible=np.ones((s, n), bool),
        labelled=np.ones((s, n), bool),
        r_gross=np.zeros((s, n)),
        r_cost=np.zeros((s, n)),
        holding=np.full((s, n), holding, dtype=np.int16),
        hit=np.ones((s, n), np.int8),
        tiebreak=tb,
        dollar_volume=np.ones((s, n)),
    )


def test_scores_must_be_dimensionless():
    Formula(score="close / ts_max(high, 252)", k=3)
    with pytest.raises(ValidationError, match="dimensionless"):
        Formula(score="ts_slope(close, 20)", k=3)
    with pytest.raises(ValidationError, match="dimensionless"):
        Formula(score="roc(close, 5)", filters=(FormulaFilter(expression="volume", max_quantile=0.5),), k=3)


def test_formula_identity_and_lookback():
    f = Formula(
        score="-1.0 * roc(close, 21)", filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=3
    )
    assert f.lookback >= 21
    assert f.identity == Formula.model_validate(f.model_dump()).identity
    assert expression_nodes("roc(close, 5)") > expression_nodes("close")


def test_scores_use_bars_through_the_previous_session_only():
    days = weekdays(date(2021, 1, 4), 40)
    closes = list(np.linspace(50, 60, 40))
    base = {"AAA": daily(days, closes)}
    shocked = {"AAA": daily(days, closes[:30] + [1.0] * 10)}  # collapses from the decision day on
    sessions = [days[30]]
    a = evaluate_panel("roc(close, 5)", base, days, sessions, ["AAA"])
    b = evaluate_panel("roc(close, 5)", shocked, days, sessions, ["AAA"])
    assert a[0, 0] == pytest.approx(closes[29] / closes[24] - 1)
    assert a[0, 0] == b[0, 0]


def test_the_first_trading_day_has_no_previous_bar():
    days = weekdays(date(2021, 1, 4), 10)
    out = evaluate_panel("roc(close, 2)", {"AAA": daily(days, list(range(1, 11)))}, days, [days[0], days[5]], ["AAA"])
    assert np.isnan(out[0, 0])
    assert np.isfinite(out[1, 0])


def test_quantile_filters_are_cross_sectional_among_eligible_finite_names():
    values = np.array([[1.0, 2.0, 3.0, 4.0, np.nan]])
    eligible = np.array([[True, True, True, True, True]])
    f = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=1
    )
    mask = allowed_mask(f, [values], eligible)
    assert mask.tolist() == [[True, True, False, False, False]]


def test_top_k_by_score_with_hash_tiebreak():
    scores = np.array([[0.5, 0.9, 0.9, 0.1]])
    tb = np.array([[4, 7, 3, 1]], dtype=np.uint64)
    picks = select_picks(scores, np.ones_like(scores, bool), view(scores.shape, tiebreak=tb), k=2)
    assert picks.cells() == {(0, 2), (0, 1)}  # both 0.9s; the lower hash first
    assert list(picks.symbol_idx) == [2, 1]


def test_a_held_name_is_skipped_until_after_its_exit_session():
    scores = np.tile(np.array([[0.9, 0.5, 0.1]]), (4, 1))
    picks = select_picks(scores, np.ones_like(scores, bool), view(scores.shape, holding=2), k=1)
    # Picked on 0, held through 1 (2 sessions), free again on 2.
    assert list(zip(picks.session_idx, picks.symbol_idx, strict=True)) == [(0, 0), (1, 1), (2, 0), (3, 1)]


def test_ineligible_or_nan_scores_are_never_picked():
    scores = np.array([[np.nan, 0.2, 0.9]])
    v = view(scores.shape)
    v.eligible[0, 2] = False
    picks = select_picks(scores, np.ones_like(scores, bool), v, k=3)
    assert picks.cells() == {(0, 1)}


def test_a_missing_previous_session_bar_gives_no_score_not_an_older_one():
    days = weekdays(date(2021, 1, 4), 30)
    closes = list(np.linspace(50, 60, 30))
    frame = daily(days[:24] + days[25:], closes[:24] + closes[25:])  # no bar on days[24]
    out = evaluate_panel("roc(close, 5)", {"AAA": frame}, days, [days[25], days[26]], ["AAA"])
    assert np.isnan(out[0, 0])  # D-1 = days[24] has no bar
    assert np.isfinite(out[1, 0])


def test_quantile_filter_bounds_are_inclusive_and_min_quantile_keeps_the_top():
    values = np.array([[1.0, 2.0, 3.0, 4.0, 5.0]])
    eligible = np.ones((1, 5), bool)
    low = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=1
    )
    high = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", min_quantile=0.5),), k=1
    )
    assert allowed_mask(low, [values], eligible).tolist() == [[True, True, True, False, False]]
    assert allowed_mask(high, [values], eligible).tolist() == [[False, False, True, True, True]]


def test_a_filter_quantile_is_taken_over_eligible_names_only():
    values = np.array([[1.0, 2.0, 3.0, 100.0]])
    eligible = np.array([[True, True, True, False]])
    high = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", min_quantile=0.5),), k=1
    )
    assert allowed_mask(high, [values], eligible).tolist() == [[False, True, True, False]]


def test_top_fraction_picks_the_ceiling_share_of_each_session_without_hold_skipping():
    row = [0.1, 0.9, 0.5, 0.7, 0.3, 0.2, 0.8, 0.4, 0.6, 0.0, 1.0]  # 11 names: ceil(1.1) = 2 picks
    scores = np.array([row, row])
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape, holding=5), 0.10)
    # The same two names again on the next session: a daily basket, no hold-skipping.
    assert list(zip(picks.session_idx.tolist(), picks.symbol_idx.tolist(), strict=True)) == [
        (0, 10),
        (0, 1),
        (1, 10),
        (1, 1),
    ]


def test_top_fraction_count_is_exact_where_float_noise_would_round_up():
    scores = np.arange(30, dtype=float)[None, :]  # 0.1 * 30 == 3.0000000000000004
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape), 0.10)
    assert sorted(picks.symbol_idx.tolist()) == [27, 28, 29]


def test_top_fraction_counts_only_eligible_allowed_finite_names():
    scores = np.array([[5.0, 4.0, np.nan, 3.0, 2.0, 1.0, 0.5, 0.4, 0.3, 0.2, 0.1, 9.0]])
    v = view(scores.shape)
    v.eligible[0, 11] = False  # the top score is ineligible
    allowed = np.ones_like(scores, bool)
    allowed[0, 0] = False  # the next is filtered out
    picks = select_top_fraction(scores, allowed, v, 0.10)
    # Candidates are names 1, 3-10 (9 names): ceil(0.9) = 1 pick, the best of them.
    assert picks.symbol_idx.tolist() == [1]


def test_top_fraction_breaks_score_ties_by_the_hash_key():
    scores = np.ones((1, 10))
    tiebreak = np.array([[9, 3, 7, 1, 8, 2, 6, 0, 5, 4]], dtype=np.uint64)
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape, tiebreak=tiebreak), 0.10)
    assert picks.symbol_idx.tolist() == [7]


def test_top_fraction_with_no_candidates_picks_nothing():
    scores = np.full((2, 5), np.nan)
    picks = select_top_fraction(scores, np.ones((2, 5), bool), view(scores.shape), 0.10)
    assert picks.session_idx.size == 0 and picks.symbol_idx.size == 0


def test_pick_codes_and_their_jaccard_match_the_set_versions():
    a = Picks(np.array([0, 0, 2, 5]), np.array([3, 1, 4, 0]))
    b = Picks(np.array([0, 2, 7]), np.array([1, 4, 2]))
    codes_a, codes_b = a.codes(10), b.codes(10)
    assert codes_a.tolist() == [1, 3, 24, 50] and codes_a.dtype == np.int64
    expected = len(a.cells() & b.cells()) / len(a.cells() | b.cells())
    assert jaccard_codes(codes_a, codes_b) == pytest.approx(expected) == pytest.approx(0.4)
    empty = np.zeros(0, dtype=np.int64)
    assert jaccard_codes(empty, empty) == 0.0
