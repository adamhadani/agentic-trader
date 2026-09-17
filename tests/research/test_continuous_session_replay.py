"""Acquisition partitions must never partition the trading state machine."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.replay import ReplayPlan


@pytest.fixture
def continuous_plan():
    return AlphaDefinition(
        "continuous",
        "Continuous",
        "returns",
        semantics_version=3,
        clock=SessionClockPolicy(),
        timeframe="15m",
        eligible_symbols=("SPY",),
        data_feed="alpaca:sip",
    )


def test_one_year_plan_is_bounded_without_changing_short_plan_identity(continuous_plan):
    plan = ReplayPlan("SPY", date(2024, 1, 1), date(2024, 12, 31), continuous_plan)
    assert plan.document()["end"] == "2024-12-31"
    with pytest.raises(ValueError):
        ReplayPlan("SPY", date(2024, 1, 1), date(2025, 1, 1), continuous_plan)


@pytest.fixture
def minute_source():
    index = pd.date_range("2024-10-15 00:00Z", "2024-11-30 00:00Z", freq="min", inclusive="left")
    frame = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000.0}, index=index)
    frame.attrs.update(feed="alpaca:sip", adjustment="raw", timeframe="1m")
    provider = Mock()

    def fetch(symbol, timeframe, *, start, end):
        return frame.loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()

    provider.fetch_bars.side_effect = fetch
    return SimpleNamespace(frame=frame, provider=provider, source=AlpacaSessionSource(provider, Mock()))


def test_half_open_chunks_match_one_continuous_snapshot_without_boundary_duplication(minute_source):
    c = minute_source
    result = c.source.minutes("SPY", c.frame.index[0], c.frame.index[-1] + pd.Timedelta(minutes=1), "alpaca:sip")
    pd.testing.assert_frame_equal(result, c.frame, check_freq=False)
    calls = c.provider.fetch_bars.call_args_list
    assert len(calls) == 2
    assert all(
        pd.Timestamp(call.kwargs["end"]) - pd.Timestamp(call.kwargs["start"]) < pd.Timedelta(days=31) for call in calls
    )
    assert pd.Timestamp(calls[0].kwargs["end"]) + pd.Timedelta(microseconds=1) == pd.Timestamp(calls[1].kwargs["start"])
    assert len(result.attrs["acquisition"]) == 2
    assert sum(r["rows"] for r in result.attrs["acquisition"]) == len(result)
    assert all(r["requested_at"] <= r["received_at"] for r in result.attrs["acquisition"])


@pytest.mark.parametrize("fault", ["duplicate", "out_of_range", "wrong_feed", "failure"])
def test_chunk_failure_is_retained_and_never_retried(minute_source, fault):
    c = minute_source
    original = c.provider.fetch_bars.side_effect

    def broken(symbol, timeframe, *, start, end):
        if c.provider.fetch_bars.call_count == 2:
            if fault == "failure":
                raise TimeoutError("fixture timeout")
            data = original(symbol, timeframe, start=start, end=end)
            if fault == "duplicate":
                data = pd.concat([data, data.iloc[[0]]])
            elif fault == "out_of_range":
                data = pd.concat([c.frame.iloc[[0]], data])
            else:
                data.attrs["feed"] = "alpaca:iex"
            return data
        return original(symbol, timeframe, start=start, end=end)

    c.provider.fetch_bars.side_effect = broken
    with pytest.raises(ValueError) as caught:
        c.source.minutes("SPY", c.frame.index[0], c.frame.index[-1] + pd.Timedelta(minutes=1), "alpaca:sip")
    assert len(caught.value.receipts) == 2
    assert caught.value.receipts[-1]["error_type"]
    assert c.provider.fetch_bars.call_count == 2
