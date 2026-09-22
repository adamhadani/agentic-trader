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
