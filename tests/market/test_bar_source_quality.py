"""Strict research must not mistake discarded source rows for known absence."""

import pandas as pd
import pytest

from agentic_trader.market.quality import BarSourceQuality
from agentic_trader.research.alpha.panel import align_daily_panel


@pytest.fixture
def quality_frame():
    clock = pd.date_range("2024-06-03", periods=2, tz="America/New_York")
    frame = pd.DataFrame({"open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}, index=clock)
    frame.attrs.update(
        feed="alpaca:iex", timeframe="1d", adjustment="raw", source_quality=BarSourceQuality(2, 2, 2).document()
    )
    return frame, clock


@pytest.mark.parametrize("raw_rows", [None, 2])
def test_observed_and_unobserved_raw_counts_remain_distinct(quality_frame, raw_rows):
    frame, clock = quality_frame
    quality = BarSourceQuality(raw_rows, 2, 2)
    frame.attrs["source_quality"] = quality.document()
    assert BarSourceQuality.from_document(quality.document()) == quality
    assert align_daily_panel({"SPY": frame}, clock, feed="alpaca:iex").complete
    assert quality.document()["sdk_omitted_rows"] == (None if raw_rows is None else 0)


@pytest.mark.parametrize(
    "quality",
    [BarSourceQuality(3, 2, 2), BarSourceQuality(3, 3, 2), BarSourceQuality(None, 3, 2), BarSourceQuality(3, 3, 3)],
)
def test_daily_panel_rejects_loss_and_mismatched_frame_counts(quality_frame, quality):
    frame, clock = quality_frame
    frame.attrs["source_quality"] = quality.document()
    with pytest.raises(ValueError, match="(discarded|frame rows)"):
        align_daily_panel({"SPY": frame}, clock, feed="alpaca:iex")


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_rows", -1),
        ("raw_rows", 1),
        ("raw_rows", True),
        ("raw_rows", 2.0),
        ("parsed_rows", None),
        ("normalized_rows", 3),
        ("sdk_omitted_rows", 1),
        ("sdk_omitted_rows", False),
        ("normalization_dropped_rows", 0.0),
        ("version", "other"),
        ("unexpected", 0),
    ],
)
def test_tampered_quality_metadata_fails_closed(quality_frame, field, value):
    frame, clock = quality_frame
    frame.attrs["source_quality"][field] = value
    with pytest.raises(ValueError):
        align_daily_panel({"SPY": frame}, clock, feed="alpaca:iex")


@pytest.mark.parametrize("document", [None, [], {}, {"version": "bar_source_quality_v1"}])
def test_missing_or_nonmapping_summary_is_not_lossless(document):
    with pytest.raises((TypeError, ValueError)):
        BarSourceQuality.from_document(document)


def test_aggregation_does_not_invent_unknown_raw_counts():
    quality = BarSourceQuality.combine([BarSourceQuality(2, 2, 1), BarSourceQuality(None, 3, 3)])
    assert quality == BarSourceQuality(None, 5, 4)
    with pytest.raises(ValueError, match="discarded"):
        quality.require_lossless(frame_rows=4)


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_unknown_counts_cannot_erase_known_sdk_omissions(reverse):
    qualities = [BarSourceQuality(3, 2, 2), BarSourceQuality(None, 3, 3)]
    if reverse:
        qualities.reverse()
    with pytest.raises(ValueError, match="known SDK omissions"):
        BarSourceQuality.combine(qualities)
