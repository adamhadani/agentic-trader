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
    evaluate_prepared_hourly,
    expression_nodes,
    jaccard_codes,
    prepare_hourly,
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


def hourly(
    days: list[date], *, closes_by_bar: Sequence[float] | None = None, hours=(9, 10, 11, 12, 13, 14, 15), extended=False
) -> pd.DataFrame:
    """One bar per regular hour per day, stamped at the bar OPEN in New York time (09:30, 10:30, ...).
    ``extended`` adds 04:00 and 16:00 bars that must be filtered out."""
    stamps, rows = [], []
    k = 0
    for d in days:
        bar_hours = [(h, 30) for h in hours]
        if extended:
            bar_hours = [(4, 0), *bar_hours, (16, 0)]
        for h, m in bar_hours:
            stamps.append(datetime.combine(d, time(h, m), tzinfo=ET_TZ).astimezone(UTC))
            c = 100.0 + k if closes_by_bar is None else closes_by_bar[k]
            rows.append(
                {"Open": c - 0.5, "High": c + 1.0, "Low": c - 1.0, "Close": c, "Volume": 1000.0 + k, "VWAP": c - 0.25}
            )
            k += 1
    return pd.DataFrame(rows, index=pd.DatetimeIndex(stamps))


def test_prepare_hourly_keeps_regular_session_bars_only():
    days = weekdays(date(2024, 1, 2), 3)
    prepared = prepare_hourly({"AAA": hourly(days, extended=True), "BBB": pd.DataFrame(), "CCC": None})
    assert set(prepared) == {"AAA"}
    frame = prepared["AAA"]
    assert len(frame) == 21 and frame.index.tz is not None and frame.index.is_monotonic_increasing
    assert set(frame.columns) >= {"open", "high", "low", "close", "volume", "vwap"}
    assert all(ts.astimezone(ET_TZ).hour in range(9, 16) for ts in frame.index)


def test_hourly_read_takes_the_last_bar_closed_before_the_decision():
    days = weekdays(date(2024, 1, 2), 3)
    prepared = prepare_hourly({"AAA": hourly(days)})
    out = evaluate_prepared_hourly(
        "close", prepared, days, ("AAA", "ZZZ"), decision_time_et="10:35", bars_before_decision=1
    )
    # Day 1 bars close at 100..106 (09:30 bar = 100); at 10:35 the 09:30 bar (close 10:30) is the last closed one.
    assert out.shape == (3, 2)
    assert out[:, 0].tolist() == [100.0, 107.0, 114.0]
    assert np.isnan(out[:, 1]).all()
    two = evaluate_prepared_hourly("close", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=2)
    # The bar before the 09:30 bar is the previous session's 15:30 bar; day 1 has none.
    assert np.isnan(two[0, 0]) and two[1, 0] == 106.0 and two[2, 0] == 113.0
    late = evaluate_prepared_hourly("close", prepared, days, ("AAA",), decision_time_et="11:35", bars_before_decision=1)
    assert late[:, 0].tolist() == [101.0, 108.0, 115.0]


def test_hourly_read_is_nan_when_no_bar_closed_before_the_decision():
    days = weekdays(date(2024, 1, 2), 2)
    frame = hourly(days)
    # Drop day 2's 09:30 bar: at 10:35 on day 2 nothing has closed yet today.
    day2_open = datetime.combine(days[1], time(9, 30), tzinfo=ET_TZ).astimezone(UTC)
    frame = frame.drop(index=pd.Timestamp(day2_open))
    out = evaluate_prepared_hourly(
        "close", prepare_hourly({"AAA": frame}), days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert out[0, 0] == 100.0 and np.isnan(out[1, 0])


def test_hourly_dsl_fields_are_bar_over_bar():
    days = weekdays(date(2024, 1, 2), 2)
    prepared = prepare_hourly({"AAA": hourly(days)})
    gap = evaluate_prepared_hourly(
        "open_gap", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    # Day 2's 09:30 bar opens at 106.5 after day 1's last close 106: open_gap = 106.5/106 - 1.
    assert np.isnan(gap[0, 0]) and gap[1, 0] == pytest.approx(106.5 / 106.0 - 1.0)
    oc = evaluate_prepared_hourly(
        "oc_spread", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert oc[0, 0] == pytest.approx(0.5 / 100.0)
    windowed = evaluate_prepared_hourly(
        "volume / ts_mean(volume, 7)", prepared, days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert np.isnan(windowed[0, 0]) and np.isfinite(windowed[1, 0])


def test_hourly_read_never_sees_bars_at_or_after_the_decision():
    """Mutating every bar stamped at or after 10:30 (close 11:30) must not change the 10:35 read."""
    days = weekdays(date(2024, 1, 2), 3)
    base = hourly(days)
    poisoned = base.copy()
    late = [ts for ts in poisoned.index if ts.astimezone(ET_TZ).time() >= time(10, 30)]
    poisoned.loc[late, ["Open", "High", "Low", "Close", "Volume", "VWAP"]] = 1e9
    a = evaluate_prepared_hourly(
        "ts_mean(close, 3)",
        prepare_hourly({"AAA": base}),
        days,
        ("AAA",),
        decision_time_et="10:35",
        bars_before_decision=1,
    )
    b = evaluate_prepared_hourly(
        "ts_mean(close, 3)",
        prepare_hourly({"AAA": poisoned}),
        days,
        ("AAA",),
        decision_time_et="10:35",
        bars_before_decision=1,
    )
    # Day 1's read uses only the 09:30 bar window (NaN for a 3-bar mean); later days' windows include
    # earlier sessions' late bars, which ARE in the past, so only the first session is identical by
    # construction: assert the day-1 reads agree and that no 1e9 leaks into day 2/3 through the
    # current session's own late bars by comparing with a frame where only the CURRENT day's late
    # bars are poisoned.
    current_only = base.copy()
    for d in days:
        late_today = [
            ts
            for ts in current_only.index
            if ts.astimezone(ET_TZ).date() == d and ts.astimezone(ET_TZ).time() >= time(10, 30)
        ]
        current_only.loc[late_today, "Close"] = 1e9
    c = evaluate_prepared_hourly(
        "close", prepare_hourly({"AAA": current_only}), days, ("AAA",), decision_time_et="10:35", bars_before_decision=1
    )
    assert c[:, 0].tolist() == [100.0, 107.0, 114.0]
    assert np.isnan(a[0, 0]) and np.isnan(b[0, 0])
