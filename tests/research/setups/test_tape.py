from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.tape import (
    MEAN_SESSIONS,
    RET_SESSIONS,
    TAPE_BUCKETS,
    TAPE_HISTORY_DAYS,
    TapeState,
    tape_buckets,
    tape_state,
)


def _daily(closes: list[float], *, end: str = "2026-10-13", tz: str | None = "UTC") -> pd.DataFrame:
    """One bar per weekday ending on ``end`` (a session date), stamped 20:00 UTC = 16:00 New York."""
    index = pd.bdate_range(end=end, periods=len(closes), freq="B") + pd.Timedelta(hours=20)
    index = pd.DatetimeIndex(index, tz=tz) if tz else pd.DatetimeIndex(index)
    return pd.DataFrame({"Close": closes}, index=index)


def test_constants_are_the_spec_values():
    assert (TAPE_HISTORY_DAYS, RET_SESSIONS, MEAN_SESSIONS) == (400, 20, 200)
    assert TAPE_BUCKETS == ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")


def test_tape_state_computes_the_20_session_return_and_the_200_session_mean():
    closes = [100.0] * 199 + [110.0] * 21  # 220 sessions; last 21 at 110 → ret_20 == 0; mean(last 200) < 110
    state = tape_state(_daily(closes), date(2026, 10, 14))
    assert state.sessions == 220
    assert state.ret_20 == pytest.approx(0.0)
    assert state.above_200 is True


def test_tape_state_return_uses_exactly_21_closes():
    closes = [100.0] * 20 + [105.0]  # 21 closes: ret_20 == 105 / 100 - 1
    state = tape_state(_daily(closes), date(2026, 10, 14))
    assert state.ret_20 == pytest.approx(0.05)
    assert state.above_200 is None  # fewer than 200 sessions
    assert state.sessions == 21


def test_tape_state_short_history_is_none():
    state = tape_state(_daily([100.0] * 20), date(2026, 10, 14))
    assert state == TapeState(ret_20=None, above_200=None, sessions=20)


def test_tape_state_excludes_the_decision_session():
    """A bar stamped on the decision's own New York date is never used."""
    closes = [100.0] * 20 + [105.0, 999.0]  # the 999 bar is on 2026-10-13, the decision's session
    state = tape_state(_daily(closes), date(2026, 10, 13))
    assert state.sessions == 21
    assert state.ret_20 == pytest.approx(0.05)


def test_tape_state_refuses_a_naive_index():
    """A naive index cannot be placed on the New York calendar without guessing."""
    closes = [100.0] * 20 + [105.0]
    assert tape_state(_daily(closes, tz=None), date(2026, 10, 14)) == TapeState(None, None, 0)


def test_tape_state_reads_alpacas_midnight_new_york_daily_stamp():
    """Alpaca stamps a daily bar 04:00Z (midnight New York, EDT) on its session date."""
    index = pd.DatetimeIndex(pd.bdate_range(end="2026-10-14", periods=22, freq="B") + pd.Timedelta(hours=4), tz="UTC")
    closes = [100.0] * 20 + [105.0, 999.0]  # the 999 bar is the decision date itself
    state = tape_state(pd.DataFrame({"Close": closes}, index=index), date(2026, 10, 14))
    assert state.sessions == 21
    assert state.ret_20 == pytest.approx(0.05)


def test_tape_state_uses_new_york_dates_for_sessions():
    """A bar stamped 03:00 UTC on the 14th belongs to the New York session of the 13th."""
    frame = _daily([100.0] * 21, end="2026-10-13")
    late = pd.DataFrame({"Close": [200.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-14T03:00:00Z")]))
    state = tape_state(pd.concat([frame, late]), date(2026, 10, 14))
    assert state.sessions == 22
    assert state.ret_20 == pytest.approx(200.0 / 100.0 - 1.0)


def test_tape_state_empty_none_and_nan_frames():
    assert tape_state(None, date(2026, 10, 14)) == TapeState(None, None, 0)
    assert tape_state(pd.DataFrame(), date(2026, 10, 14)) == TapeState(None, None, 0)
    assert tape_state(_daily([math.nan] * 30), date(2026, 10, 14)) == TapeState(None, None, 0)
    mixed = _daily([100.0] * 20 + [math.nan] + [105.0])
    assert tape_state(mixed, date(2026, 10, 14)) == TapeState(ret_20=pytest.approx(0.05), above_200=None, sessions=21)


def test_tape_state_without_a_close_column_is_empty():
    frame = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-01T20:00:00Z")]))
    assert tape_state(frame, date(2026, 10, 14)) == TapeState(None, None, 0)


def test_tape_state_zero_base_close_gives_no_return():
    closes = [0.0] + [100.0] * 20
    assert tape_state(_daily(closes), date(2026, 10, 14)).ret_20 is None


def test_tape_state_unsorted_index_is_sorted_first():
    frame = _daily([100.0] * 20 + [105.0]).iloc[::-1]
    assert tape_state(frame, date(2026, 10, 14)).ret_20 == pytest.approx(0.05)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (TapeState(0.02, True, 250), ("ret20_up", "above_200")),
        (TapeState(-0.01, False, 250), ("ret20_down", "below_200")),
        (TapeState(0.0, True, 250), ("ret20_down", "above_200")),
        (TapeState(0.03, None, 30), ("ret20_up",)),
        (TapeState(None, False, 250), ("below_200",)),
        (TapeState(None, None, 0), ("unknown",)),
    ],
)
def test_tape_buckets(state, expected):
    assert tape_buckets(state) == expected
    assert set(expected) <= set(TAPE_BUCKETS)


def test_tape_buckets_never_mix_unknown_with_a_measure():
    assert "unknown" not in tape_buckets(TapeState(np.float64(0.1), None, 21))
