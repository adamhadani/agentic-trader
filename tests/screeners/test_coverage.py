from types import SimpleNamespace

import pandas as pd

from agentic_trader.screeners.coverage import active_hourly_bars, coverage_exclusions


def hourly(days, bars_per_day=7, volume=1000):
    idx = pd.date_range("2026-08-03 09:30", periods=days * 24, freq="h", tz="America/New_York")
    idx = idx[idx.indexer_between_time("09:30", "15:30")][: days * bars_per_day]
    assert len(idx) == days * bars_per_day
    return pd.DataFrame({"Close": 100.0, "Volume": volume}, index=idx)


def test_active_bars_counts_positive_volume_in_the_last_sessions_only():
    frame = hourly(12)
    frame.iloc[:7, frame.columns.get_loc("Volume")] = 0  # first session silent, but it is outside the last 10
    assert active_hourly_bars(frame, sessions=10) == 10 * 7
    frame.iloc[-3:, frame.columns.get_loc("Volume")] = 0
    assert active_hourly_bars(frame, sessions=10) == 10 * 7 - 3


def test_thin_names_are_excluded_relative_to_the_reference():
    datasets = {
        "SPY": SimpleNamespace(hourly=hourly(12)),
        "GOOD": SimpleNamespace(hourly=hourly(12)),
        "THIN": SimpleNamespace(hourly=hourly(12, volume=0)),
        "/MES": SimpleNamespace(hourly=hourly(12, volume=0)),
    }
    excluded, note = coverage_exclusions(
        datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"SPY", "GOOD", "THIN"}
    )
    assert excluded == {"THIN"} and note is None  # futures are never gated


def test_missing_reference_skips_the_gate_with_a_note():
    datasets = {"GOOD": SimpleNamespace(hourly=hourly(12))}
    excluded, note = coverage_exclusions(datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"GOOD"})
    assert excluded == set() and "SPY" in note


def test_failed_reference_frame_skips_the_gate():
    datasets = {"SPY": ConnectionError("boom"), "GOOD": SimpleNamespace(hourly=hourly(12))}
    excluded, note = coverage_exclusions(
        datasets, reference="SPY", sessions=10, min_ratio=0.8, equities={"GOOD", "SPY"}
    )
    assert excluded == set() and note


def utc_hourly_buckets(days, ny_hours):
    """Top-of-hour UTC bars as the Alpaca 1h feed labels them, at the given New York hours."""
    sessions = pd.bdate_range("2026-09-08", periods=days, tz="America/New_York")
    idx = pd.DatetimeIndex([s + pd.Timedelta(hours=h) for s in sessions for h in ny_hours]).tz_convert("UTC")
    return pd.DataFrame({"Close": 100.0, "Volume": 1000}, index=idx)


def test_extended_hours_reference_bars_do_not_exclude_regular_session_names():
    # SPY prints the 08:00 pre-market and 16:00 post-market buckets; most names do not.
    # Counting them made 70/88 = 0.795 < 0.8 and excluded XLI, VTI and ~100 others.
    regular = list(range(9, 16))
    spy = utc_hourly_buckets(10, [8, *regular, 16])
    xli = utc_hourly_buckets(10, regular)
    assert active_hourly_bars(spy, sessions=10) == active_hourly_bars(xli, sessions=10) == 70
    excluded, note = coverage_exclusions(
        {"SPY": SimpleNamespace(hourly=spy), "XLI": SimpleNamespace(hourly=xli)},
        reference="SPY",
        sessions=10,
        min_ratio=0.8,
        equities={"SPY", "XLI"},
    )
    assert excluded == set() and note is None


def test_morning_partial_session_counts_the_same_buckets_for_both():
    # 10:35 New York: SPY has 08:00, 09:00 and 10:00 buckets today; XLI has 09:00 and 10:00.
    spy = pd.concat(
        [utc_hourly_buckets(9, [8, *range(9, 16), 16]), utc_hourly_buckets(1, [8, 9, 10]).shift(9, freq="B")]
    )
    xli = pd.concat([utc_hourly_buckets(9, list(range(9, 16))), utc_hourly_buckets(1, [9, 10]).shift(9, freq="B")])
    assert active_hourly_bars(spy, sessions=10) == active_hourly_bars(xli, sessions=10) == 9 * 7 + 2
