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
    """When B misses a session, both symbols' next return spans the gap (two sessions of A's closes):
    the frame never pairs A's one-session return with B's two-session return."""
    a = frame(11)
    full_b = frame(12)
    gap_ts = full_b.index[100]
    b = full_b.drop(index=gap_ts)
    returns, dropped = daily_returns(
        {"AAA": a, "BBB": b}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60
    )
    assert dropped == ()
    gap = pd.Timestamp(gap_ts.date())
    nxt = pd.Timestamp(full_b.index[101].date())
    prev = pd.Timestamp(full_b.index[99].date())
    assert gap not in returns.index
    a_closes = a["Close"].copy()
    a_closes.index = pd.DatetimeIndex([pd.Timestamp(d) for d in a.index.date])
    assert returns.loc[nxt, "AAA"] == pytest.approx(a_closes[nxt] / a_closes[prev] - 1)
    b_closes = full_b["Close"].copy()
    b_closes.index = pd.DatetimeIndex([pd.Timestamp(d) for d in full_b.index.date])
    assert returns.loc[nxt, "BBB"] == pytest.approx(b_closes[nxt] / b_closes[prev] - 1)


def test_non_positive_or_nan_closes_are_dropped_not_turned_into_infinite_returns():
    a = frame(18)
    a.loc[a.index[50], "Close"] = 0.0
    a.loc[a.index[60], "Close"] = float("nan")
    returns, dropped = daily_returns({"AAA": a}, as_of=date(2027, 1, 1), lookback_sessions=500, min_observations=60)
    assert dropped == ()
    assert np.isfinite(returns["AAA"]).all()
    covariance, _ = shrunk_covariance(returns)
    assert np.isfinite(covariance.to_numpy()).all()


def test_last_return_is_the_last_completed_session_before_as_of():
    a = frame(19)
    as_of = a.index[-3].date()
    returns, _ = daily_returns({"AAA": a}, as_of=as_of, lookback_sessions=500, min_observations=10)
    assert returns.index[-1] == pd.Timestamp(a.index[-4].date())


@pytest.mark.parametrize("bad", [{"lookback_sessions": 0}, {"min_observations": 0}])
def test_non_positive_window_arguments_raise(bad):
    kwargs = {"as_of": date(2027, 1, 1), "lookback_sessions": 50, "min_observations": 10, **bad}
    with pytest.raises(ValueError):
        daily_returns({"AAA": frame(20)}, **kwargs)


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
