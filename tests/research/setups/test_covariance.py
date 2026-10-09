# tests/research/setups/test_covariance.py
from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.covariance import daily_returns, is_raw_frame, shrunk_covariance


def frame(seed: int, sessions: int = 200, start: str = "2026-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start=start, periods=sessions)
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, sessions)))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Volume": 1e6}, index=index)


def test_returns_use_completed_sessions_only_and_align_on_dates():
    a, b = frame(1), frame(2, sessions=180)  # b starts the same day, ends earlier
    as_of = a.index[-1].date()
    returns, dropped = daily_returns({"AAA": a, "BBB": b}, as_of=as_of, lookback_sessions=500, min_observations=60)
    assert dropped == ()
    assert list(returns.columns) == ["AAA", "BBB"]
    assert len(returns) == 179  # intersection of 180 closes -> 179 returns
    expected = a["Close"].pct_change().dropna().loc[returns.index]
    assert np.allclose(returns["AAA"].to_numpy(), expected.to_numpy())


def test_bars_on_or_after_as_of_are_excluded():
    a = frame(3)
    as_of = a.index[-5].date()  # pretend the last 4 sessions are not completed
    returns, _ = daily_returns({"AAA": a}, as_of=as_of, lookback_sessions=500, min_observations=10)
    assert returns.index.max().date() < as_of


def test_lookback_window_limits_rows():
    returns, _ = daily_returns({"AAA": frame(4)}, as_of=date(2027, 1, 1), lookback_sessions=50, min_observations=10)
    assert len(returns) == 50


def test_symbols_below_min_observations_are_dropped_and_reported():
    short = frame(5, sessions=30)
    returns, dropped = daily_returns(
        {"AAA": frame(6), "BBB": short}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60
    )
    assert dropped == ("BBB",)
    assert list(returns.columns) == ["AAA"]


def test_adjusted_frame_is_not_raw():
    adjusted = frame(7)
    adjusted.attrs["adjustment"] = "yfinance_auto_adjust"
    raw = frame(8)
    raw.attrs["adjustment"] = "raw"
    assert not is_raw_frame(adjusted)
    assert is_raw_frame(raw)
    assert is_raw_frame(frame(9))  # no attr -> raw by default


def test_shrunk_covariance_is_symmetric_psd_with_shrinkage_in_unit_interval():
    returns, _ = daily_returns(
        {s: frame(i) for i, s in enumerate(("AAA", "BBB", "CCC"))},
        as_of=date(2027, 1, 1),
        lookback_sessions=500,
        min_observations=60,
    )
    covariance, shrinkage = shrunk_covariance(returns)
    matrix = covariance.to_numpy()
    assert np.allclose(matrix, matrix.T)
    assert np.linalg.eigvalsh(matrix).min() >= -1e-12
    assert 0.0 <= shrinkage <= 1.0
    assert list(covariance.index) == ["AAA", "BBB", "CCC"]


def test_single_symbol_covariance_is_its_variance():
    returns, _ = daily_returns({"AAA": frame(10)}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60)
    covariance, shrinkage = shrunk_covariance(returns)
    assert covariance.shape == (1, 1)
    assert np.isclose(covariance.iloc[0, 0], returns["AAA"].var(ddof=0))
    assert shrinkage == 0.0


def test_empty_returns_raise():
    with pytest.raises(ValueError):
        shrunk_covariance(pd.DataFrame())


def test_missing_session_never_yields_a_two_session_return():
    a = frame(11)
    b = frame(12).drop(index=frame(12).index[100])  # one session missing in B
    returns, dropped = daily_returns(
        {"AAA": a, "BBB": b}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60
    )
    assert dropped == ()
    # Returns are one-session for both: the day after the gap is absent for both symbols.
    gap = pd.Timestamp(frame(12).index[100].date())
    assert gap not in returns.index
    expected_a = a["Close"].pct_change().dropna()
    expected_a.index = pd.DatetimeIndex([pd.Timestamp(d) for d in expected_a.index.date])
    common = returns.index.intersection(expected_a.index).difference([gap + pd.offsets.BDay(1)])
    assert np.allclose(returns.loc[common, "AAA"].to_numpy(), expected_a.loc[common].to_numpy())


def test_sparse_symbol_is_dropped_instead_of_emptying_the_book():
    """Three names with enough history each but a tiny overlap for one of them."""
    a, b = frame(13), frame(14)
    c = frame(15, start="2026-09-20", sessions=80)  # long enough alone, barely overlaps
    returns, dropped = daily_returns(
        {"AAA": a, "BBB": b, "CCC": c}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=100
    )
    assert dropped == ("CCC",)
    assert list(returns.columns) == ["AAA", "BBB"]
    assert len(returns) >= 100


def test_tz_aware_and_naive_indexes_align_on_session_dates():
    aware = frame(16)
    aware.index = aware.index.tz_localize("UTC") + pd.Timedelta(hours=4)  # Alpaca-style 04:00 UTC
    naive = frame(17)
    returns, dropped = daily_returns(
        {"AAA": aware, "BBB": naive}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60
    )
    assert dropped == ()
    assert len(returns) == 199
    assert returns.index.tz is None
