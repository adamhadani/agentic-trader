"""Observed session boundaries own aggregation; missing minutes are not invented."""

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import SessionCoverageError, SessionSchedule, build_session_bars


@pytest.fixture
def early_close(schedule_for):
    return schedule_for(("2024-11-27", "16:00"), ("2024-11-29", "13:00"))


@pytest.mark.parametrize(("timeframe", "counts"), [("15m", (26, 14)), ("1h", (7, 4)), ("4h", (2, 1)), ("1d", (1, 1))])
def test_observed_holiday_and_early_close_clip_signal_bars(early_close, timeframe, counts, minute_bars):
    bars = minute_bars(early_close)
    result = build_session_bars(bars, early_close, timeframe, as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    assert len(result.execution) == 600
    assert len(result.signals) == sum(counts)
    assert result.closed_at[-1] == pd.Timestamp("2024-11-29 18:00", tz="UTC")
    assert (
        result.signals.iloc[-1].volume == (210 if timeframe in ("4h", "1d") else 30 if timeframe == "1h" else 15) * 10
    )
    assert result.signals.attrs["bar_layout"] == "rth_open_v1"
    assert result.coverage["missing_minutes"] == 0


def test_dst_uses_each_observed_local_session_not_fixed_utc_hours(schedule_for, minute_bars):
    schedule = schedule_for(("2024-03-08", "16:00"), ("2024-03-11", "16:00"))
    result = build_session_bars(minute_bars(schedule), schedule, "4h", as_of=pd.Timestamp("2024-03-12", tz="UTC"))
    assert result.signals.index.tolist() == [
        pd.Timestamp(s, tz="UTC")
        for s in ("2024-03-08 14:30", "2024-03-08 18:30", "2024-03-11 13:30", "2024-03-11 17:30")
    ]


def test_extended_hours_prices_never_enter_regular_session_bars(early_close, minute_bars):
    bars = minute_bars(early_close)
    extra = bars.iloc[:2].copy()
    extra.index = pd.DatetimeIndex(["2024-11-27 14:29Z", "2024-11-29 18:00Z"])
    extra.loc[:, ["open", "high", "low", "close"]] = 10000
    combined = pd.concat([bars, extra]).sort_index()
    result = build_session_bars(combined, early_close, "1h", as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    assert result.signals.high.max() == 101
    assert result.coverage["excluded_minutes"] == 2


@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "nonfinite", "misaligned", "adjusted", "naive", "unknown_timestamp"]
)
def test_unobserved_or_ambiguous_execution_clock_fails_closed(early_close, defect, minute_bars):
    bars = minute_bars(early_close)
    if defect == "missing":
        bars = bars.drop(bars.index[60])
    elif defect == "duplicate":
        bars = pd.concat([bars, bars.iloc[[60]]]).sort_index()
    elif defect == "nonfinite":
        bars.iloc[60, 0] = np.nan
    elif defect == "misaligned":
        bars.index += pd.Timedelta(seconds=1)
    elif defect == "adjusted":
        bars.attrs["adjustment"] = "split"
    elif defect == "unknown_timestamp":
        extra = bars.iloc[[0]].copy()
        extra.index = pd.DatetimeIndex([pd.NaT], tz="UTC")
        bars = pd.concat([bars, extra])
    else:
        bars.index = bars.index.tz_localize(None)
    with pytest.raises(ValueError):
        build_session_bars(bars, early_close, "15m", as_of=pd.Timestamp("2024-11-30", tz="UTC"))


def test_coverage_error_retains_missing_evidence(early_close, minute_bars):
    bars = minute_bars(early_close)
    with pytest.raises(SessionCoverageError) as error:
        build_session_bars(bars.iloc[1:], early_close, "1h", as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    assert error.value.coverage["missing_minutes"] == 1
    assert error.value.coverage["expected_minutes"] == 600


def test_closed_bar_availability_and_future_perturbation(early_close, minute_bars):
    bars = minute_bars(early_close)
    as_of = pd.Timestamp("2024-11-27 15:30", tz="UTC")
    first = build_session_bars(bars, early_close, "1h", as_of=as_of)
    assert len(first.execution) == 60 and len(first.signals) == 1
    assert first.closed_at[0] == as_of
    before = build_session_bars(bars, early_close, "1h", as_of=as_of - pd.Timedelta(seconds=1))
    assert len(before.execution) == 59 and before.signals.empty
    bars.loc[bars.index >= as_of, :] = np.nan
    after = build_session_bars(bars, early_close, "1h", as_of=as_of)
    pd.testing.assert_frame_equal(first.execution, after.execution)
    pd.testing.assert_frame_equal(first.signals, after.signals)
    assert first.coverage == after.coverage


def test_schedule_round_trip_is_exact_and_missing_calendar_is_not_a_weekday_fallback(early_close):
    assert SessionSchedule.from_document(early_close.document()) == early_close
    with pytest.raises(ValueError, match="session"):
        SessionSchedule(early_close.start, early_close.end, (), source="alpaca")
