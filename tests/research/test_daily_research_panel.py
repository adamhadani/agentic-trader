"""Calendar alignment and relative features must preserve causal observations."""

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.panel import align_daily_panel, beta_adjust_scores


@pytest.fixture
def daily_panel_source():
    clock = pd.date_range("2022-01-03", periods=90, freq="B", tz="America/New_York")
    rng = np.random.default_rng(9)
    frames = {}
    for symbol in ("AAA", "BBB", "SPY"):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(clock))))
        frame = pd.DataFrame(
            {"open": close * 0.999, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000.0},
            index=clock.tz_convert("UTC"),
        )
        frame.attrs.update(feed="alpaca:sip", adjustment="raw", timeframe="1d")
        frames[symbol] = frame
    return frames, clock


def test_panel_preserves_expected_calendar_without_fill_or_intersection(daily_panel_source):
    frames, clock = daily_panel_source
    frames["AAA"] = frames["AAA"].drop(frames["AAA"].index[5])
    panel = align_daily_panel(frames, clock, feed="alpaca:sip")
    assert panel.close.index.equals(clock)
    assert panel.close.shape == (90, 3)
    assert pd.isna(panel.close.iloc[5]["AAA"])
    assert panel.coverage["AAA"]["missing_dates"] == [clock[5].date().isoformat()]
    assert not panel.complete


@pytest.mark.parametrize("fault", ["duplicate", "timezone", "wrong_time", "feed", "nonfinite", "ohlc", "extra_date"])
def test_invalid_source_is_not_silently_normalized(daily_panel_source, fault):
    frames, clock = daily_panel_source
    frame = frames["AAA"]
    if fault == "duplicate":
        frames["AAA"] = pd.concat([frame, frame.iloc[[0]]])
    elif fault == "timezone":
        frame.index = frame.index.tz_localize(None)
    elif fault == "wrong_time":
        frame.index += pd.Timedelta(hours=1)
    elif fault == "feed":
        frame.attrs["feed"] = "alpaca:iex"
    elif fault == "nonfinite":
        frame.iloc[1, 0] = np.nan
    elif fault == "ohlc":
        frame.iloc[1, 1] = 0.1
    else:
        frames["AAA"] = pd.concat(
            [frame, frame.iloc[[-1]].set_axis(pd.DatetimeIndex([clock[-1] + pd.Timedelta(days=3)]))]
        )
    with pytest.raises(ValueError):
        align_daily_panel(frames, clock, feed="alpaca:sip")


def test_beta_adjustment_uses_only_observed_prefix():
    ix = pd.date_range("2020-01-01", periods=100, tz="UTC")
    market = pd.Series(np.sin(np.arange(100)) * 0.01, index=ix)
    returns = pd.DataFrame({"A": 2 * market, "B": -market}, index=ix)
    market_score = pd.Series(0.05, index=ix)
    scores = pd.DataFrame({"A": 0.1, "B": -0.05}, index=ix)
    adjusted = beta_adjust_scores(scores, returns, market_score, market, window=20)
    assert np.nanmax(np.abs(adjusted.to_numpy())) < 1e-12
    prefix = adjusted.iloc[:70].copy()
    returns.iloc[70:] *= 50
    market.iloc[70:] *= -20
    scores.iloc[70:] *= 2
    pd.testing.assert_frame_equal(
        beta_adjust_scores(scores, returns, market_score, market, window=20).iloc[:70], prefix
    )
