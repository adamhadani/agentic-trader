from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.beta import BETA_MIN_OBSERVATIONS, BETA_WINDOW_SESSIONS, trailing_beta


def _daily(closes, *, end="2026-02-27"):
    index = pd.bdate_range(end=end, periods=len(closes)) + pd.Timedelta(hours=20)
    return pd.DataFrame({"Close": list(closes)}, index=pd.DatetimeIndex(index, tz="UTC"))


def _paths(n, factor=0.5, seed=7):
    rng = np.random.default_rng(seed)
    market_ret = rng.normal(0.0, 0.01, n - 1)
    market = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + market_ret]))
    symbol = 50.0 * np.cumprod(np.concatenate([[1.0], 1.0 + factor * market_ret]))
    return symbol, market


def test_constants():
    assert (BETA_WINDOW_SESSIONS, BETA_MIN_OBSERVATIONS) == (120, 100)


def test_half_beta_series():
    symbol, market = _paths(150)
    beta, reason = trailing_beta(_daily(symbol), _daily(market), date(2026, 3, 2))
    assert reason is None
    assert beta == pytest.approx(0.5, abs=1e-9)


def test_window_ends_before_decision_session():
    symbol, market = _paths(150)
    # A same-day bar (2026-03-02) with a wild symbol return must not enter the window.
    symbol_df = pd.concat([_daily(symbol), _daily([symbol[-1] * 3.0], end="2026-03-02")])
    market_df = pd.concat([_daily(market), _daily([market[-1] * 0.5], end="2026-03-02")])
    beta, _ = trailing_beta(symbol_df, market_df, date(2026, 3, 2))
    assert beta == pytest.approx(0.5, abs=1e-9)
    later, _ = trailing_beta(symbol_df, market_df, date(2026, 3, 3))
    assert later is not None and abs(later - 0.5) > 1e-3


def test_window_uses_only_the_last_120_returns():
    symbol, market = _paths(200)
    # Early history has a different relationship; it must fall outside the window.
    symbol = symbol.copy()
    symbol[:60] = symbol[:60] * np.linspace(1.0, 2.0, 60)
    beta, _ = trailing_beta(_daily(symbol), _daily(market), date(2026, 3, 2))
    assert beta == pytest.approx(0.5, abs=1e-9)


def test_short_history_none():
    symbol, market = _paths(60)
    assert trailing_beta(_daily(symbol), _daily(market), date(2026, 3, 2)) == (None, "short_history")


def test_no_daily_bars():
    _, market = _paths(150)
    assert trailing_beta(pd.DataFrame(), _daily(market), date(2026, 3, 2)) == (None, "no_daily_bars")
    assert trailing_beta(_daily(market), None, date(2026, 3, 2)) == (None, "no_daily_bars")
    assert trailing_beta(_daily(market), _daily(market), date(2020, 1, 1)) == (None, "no_daily_bars")


def test_zero_market_variance_none():
    symbol, _ = _paths(150)
    assert trailing_beta(_daily(symbol), _daily([100.0] * 150), date(2026, 3, 2)) == (None, "short_history")


def test_alignment_by_date_with_missing_day():
    symbol, market = _paths(150)
    symbol_df = _daily(symbol).drop(_daily(symbol).index[100])
    beta, reason = trailing_beta(symbol_df, _daily(market), date(2026, 3, 2))
    # The two returns spanning the gap are dropped, not mixed across horizons.
    assert reason is None
    assert beta == pytest.approx(0.5, abs=1e-9)
