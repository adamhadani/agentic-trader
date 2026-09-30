# tests/research/pooled/test_eligibility.py
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import UniverseSpec, _DailySlicer, point_in_time_eligibility
from agentic_trader.screeners.dynamic_universe import median_dollar_volume


UNIVERSE = UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=2)


def days(n: int, start: date = date(2021, 1, 4)) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def daily(sessions: list[date], close: float | list[float], volume: float = 1e6) -> pd.DataFrame:
    closes = np.full(len(sessions), close, dtype=float) if np.isscalar(close) else np.asarray(close, dtype=float)
    index = pd.DatetimeIndex([datetime.combine(d, time(0), tzinfo=ET_TZ).astimezone(UTC) for d in sessions])
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes, "Volume": volume}, index=index
    )


def test_slicer_matches_the_unsliced_live_function():
    sessions = days(120)
    rng = np.random.default_rng(1)
    frame = daily(sessions, list(50 + rng.normal(0, 1, 120)), volume=1e6)
    frame.iloc[[5, 40, 41, 90], frame.columns.get_loc("Close")] = np.nan
    slicer = _DailySlicer(frame)
    for as_of in sessions[18:]:
        assert slicer.median_dollar_volume(as_of) == median_dollar_volume(frame, as_of)


def reference(sessions: list[date]) -> dict[str, pd.DataFrame]:
    """The live gate needs MIN_REFERENCE_NAMES (20) static names: $20 x 1e6 = $2e7 dollar volume each."""
    return {f"R{i:02d}": daily(sessions, 20.0) for i in range(20)}


def test_eligibility_reads_raw_bars_at_as_of():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0), "LOW": daily(sessions, 9.0)}
    # Adjusted history is divided by a later 4:1 split: LOW's adjusted price looks like 2.25 and AAA's 5.0.
    adjusted = {s: daily(sessions, float(f["Close"].iloc[0]) / 4) for s, f in raw.items()}
    result = point_in_time_eligibility(
        sessions,
        (decision, decision),
        ["AAA", "BBB", "LOW"],
        adjusted,
        raw,
        reference(sessions),
        universe=UNIVERSE,
        atr_window=14,
    )
    assert result.sessions == (decision,)
    assert result.eligible[0].tolist() == [True, True, False]  # $10 floor on raw bars, not adjusted
    assert result.reasons["illiquid_price"] == 1


def test_eligibility_never_uses_the_decision_day():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0)}
    later = {s: f.copy() for s, f in raw.items()}
    later["AAA"].iloc[40:, later["AAA"].columns.get_loc("Close")] = 1.0  # collapses ON the decision day
    static = reference(sessions)
    before = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], raw, raw, static, universe=UNIVERSE, atr_window=14
    )
    after = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], later, later, static, universe=UNIVERSE, atr_window=14
    )
    assert before.eligible.tolist() == after.eligible.tolist() == [[True, True]]


def test_sessions_below_the_minimum_eligible_names_are_skipped():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0), "LOW": daily(sessions, 5.0)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "LOW"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert not result.eligible.any()
    assert result.skipped_sessions == {"too_few_eligible": 1}


def test_a_session_without_a_static_reference_is_skipped():
    sessions = days(60)
    decision = sessions[10]  # fewer than 20 completed sessions of history: no reference
    raw = {"AAA": daily(sessions, 20.0), "BBB": daily(sessions, 20.0)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert result.skipped_sessions == {"no_reference": 1}
    assert result.reference == (None,)


def test_atr_and_dollar_volume_are_recorded_for_eligible_cells():
    sessions = days(60)
    decision = sessions[40]
    raw = {"AAA": daily(sessions, 20.0, volume=2e6), "BBB": daily(sessions, 20.0, volume=2e6)}
    result = point_in_time_eligibility(
        sessions, (decision, decision), ["AAA", "BBB"], raw, raw, reference(sessions), universe=UNIVERSE, atr_window=14
    )
    assert result.atr[0, 0] == pytest.approx(0.4)  # high-low = 20*0.02 every day
    assert result.dollar_volume[0, 0] == pytest.approx(4e7)
