# tests/research/pooled/test_cube.py
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Eligibility, UniverseSpec
from agentic_trader.research.pooled.cube import (
    BracketSpec,
    CoverageSpec,
    CubeSpec,
    LabelCube,
    build_cube,
    check_coverage,
    load_cube,
    save_cube,
    tiebreak_key,
)


SPEC = CubeSpec(
    feed="alpaca:sip",
    bars_from=date(2021, 1, 1),
    decisions=(date(2021, 3, 1), date(2021, 3, 31)),
    bars_through=date(2021, 5, 1),
    bracket=BracketSpec(
        decision_time_et="10:35", stop_atr_multiple=2.0, atr_window=14, target_r=3.0, max_hold_sessions=3
    ),
    universe=UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=1),
    decision_cost_bps=5.0,
)


def weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def hourly(days: list[date], path: list[float]) -> pd.DataFrame:
    """Seven regular-session hourly bars (09:00..15:00 NY) per day; ``path`` gives one price per bar."""
    index, prices = [], []
    k = 0
    for day in days:
        for h in range(9, 16):
            index.append(datetime.combine(day, time(h), tzinfo=ET_TZ).astimezone(UTC))
            prices.append(path[min(k, len(path) - 1)])
            k += 1
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p, "Volume": 1.0}, index=pd.DatetimeIndex(index))


def eligibility(sessions, symbols, eligible, atr=1.0) -> Eligibility:
    grid = np.asarray(eligible, dtype=bool)
    return Eligibility(
        sessions=tuple(sessions),
        symbols=tuple(symbols),
        eligible=grid,
        atr=np.where(grid, atr, np.nan),
        dollar_volume=np.where(grid, 1e7, np.nan),
        reference=tuple(1e6 for _ in sessions),
        reference_names=tuple(30 for _ in sessions),
        skipped_sessions={},
        reasons={},
    )


def test_cells_are_labelled_with_the_long_bracket_from_the_decision_price():
    days = weekdays(date(2021, 3, 1), 10)
    # 09:00 bar closes at 100 (the decision price); entry is the 11:00 bar's open (100);
    # the 12:00 bar reaches the 106 target (100 + 3 * 2 * ATR 1).
    bars = {"AAA": hourly(days, [100.0, 100.0, 100.0, 106.0])}
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), bars, SPEC, cohort_sha256="c" * 64)
    view = cube.window(days[0], days[0])
    assert view.labelled.tolist() == [[True]]
    assert view.r_gross[0, 0] == pytest.approx(3.0)
    assert view.r_cost[0, 0] == pytest.approx(3.0 - 2 * 5 / 1e4 * 100 / 2)
    assert view.hit[0, 0] == 1  # target


def test_missing_hourly_bars_are_counted_not_labelled():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), {}, SPEC, cohort_sha256="c" * 64)
    view = cube.window(days[0], days[0])
    assert not view.labelled.any()
    assert view.holding[0, 0] == SPEC.bracket.max_hold_sessions
    assert cube.coverage["unlabelled_by_year"]["no_hourly_bars"] == {"2021": 1}


def test_the_cube_exposes_labels_only_through_windows():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:2], ["AAA"], [[True], [True]]), {}, SPEC, cohort_sha256="c" * 64)
    for name in ("r_cost", "r_gross", "labelled", "hit", "holding"):
        assert not hasattr(cube, name)
    view = cube.window(days[1], days[1])
    assert view.sessions == (days[1],) and view.offset == 1
    with pytest.raises(ValueError):
        cube.window(date(2020, 1, 1), date(2020, 1, 31))  # no session inside


def test_tiebreak_is_a_hash_not_the_alphabet():
    day = date(2021, 3, 1)
    keys = {s: tiebreak_key(s, day) for s in ("AAA", "BBB", "CCC", "DDD")}
    assert sorted(keys, key=lambda s: keys[s]) != ["AAA", "BBB", "CCC", "DDD"]
    assert tiebreak_key("AAA", day) != tiebreak_key("AAA", day + timedelta(days=1))


def test_save_and_load_round_trip_verifies_the_hash(tmp_path):
    days = weekdays(date(2021, 3, 1), 10)
    bars = {"AAA": hourly(days, [100.0, 100.0, 100.0, 106.0])}
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), bars, SPEC, cohort_sha256="c" * 64)
    path = tmp_path / "cube.npz"
    save_cube(cube, path)
    loaded = load_cube(path)
    assert loaded.sha256 == cube.sha256
    assert loaded.window(days[0], days[0]).r_gross[0, 0] == pytest.approx(3.0)
    # Tampering with the stored arrays is detected.
    data = dict(np.load(path, allow_pickle=False))
    data["r_gross"] = data["r_gross"] + 1
    np.savez_compressed(path, **data)
    with pytest.raises(ValueError, match="hash"):
        load_cube(path)


def test_with_shift_moves_only_the_given_labelled_cells():
    days = weekdays(date(2021, 3, 1), 10)
    bars = {"AAA": hourly(days, [100.0] * 80), "BBB": hourly(days, [100.0] * 80)}
    cube = build_cube(
        eligibility(days[:2], ["AAA", "BBB"], [[True, True], [True, True]]), bars, SPEC, cohort_sha256="c" * 64
    )
    shifted = cube.with_shift(np.array([1]), np.array([0]), 0.5)
    before, after = cube.window(days[0], days[1]), shifted.window(days[0], days[1])
    assert after.r_cost[1, 0] == pytest.approx(before.r_cost[1, 0] + 0.5)
    assert np.array_equal(np.delete(after.r_cost.ravel(), 2), np.delete(before.r_cost.ravel(), 2))


def test_coverage_fails_closed():
    days = weekdays(date(2021, 3, 1), 5)
    cube = build_cube(eligibility(days[:1], ["AAA"], [[True]]), {}, SPEC, cohort_sha256="c" * 64)
    with pytest.raises(ValueError, match="hourly"):
        check_coverage(cube, CoverageSpec(max_no_reference_fraction=0.02, max_unlabelled_fraction_per_year=0.05))


def test_spec_identity_is_stable_and_sensitive():
    assert SPEC.identity == CubeSpec.model_validate(SPEC.model_dump()).identity
    changed = SPEC.model_copy(update={"decision_cost_bps": 0.0})
    assert changed.identity != SPEC.identity


def test_views_are_read_only_and_the_callers_arrays_stay_writeable():
    days = weekdays(date(2021, 3, 1), 5)
    grid = np.ones((2, 1), dtype=bool)
    arrays = {
        "eligible": grid.copy(),
        "labelled": grid.copy(),
        "r_gross": np.zeros((2, 1)),
        "r_cost": np.zeros((2, 1)),
        "holding": np.ones((2, 1), dtype=np.int16),
        "hit": np.zeros((2, 1), dtype=np.int8),
        "tiebreak": np.zeros((2, 1), dtype=np.uint64),
        "dollar_volume": np.ones((2, 1)),
    }
    cube = LabelCube(
        spec_identity="s", cohort_sha256="c", sessions=tuple(days[:2]), symbols=("AAA",), arrays=arrays, coverage={}
    )
    view = cube.window(days[0], days[1])
    with pytest.raises(ValueError):
        view.r_cost[0, 0] = 9.0
    with pytest.raises(ValueError):
        view.sub(0, 1).r_cost[0, 0] = 9.0
    assert arrays["r_cost"].flags.writeable
    shifted = cube.with_shift(np.array([0]), np.array([0]), 0.5)
    with pytest.raises(ValueError):
        shifted.window(days[0], days[0]).r_cost[0, 0] = 9.0
    assert shifted.window(days[0], days[0]).r_cost[0, 0] == pytest.approx(0.5)
