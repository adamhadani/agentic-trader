from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import SessionSchedule, TradingSession


def _schedule_for(*days):
    sessions = tuple(
        TradingSession(
            date.fromisoformat(day),
            pd.Timestamp(f"{day} 09:30", tz="America/New_York"),
            pd.Timestamp(f"{day} {close}", tz="America/New_York"),
        )
        for day, close in days
    )
    return SessionSchedule(sessions[0].date, sessions[-1].date, sessions, source="fixture")


def _minute_bars(schedule):
    index = pd.DatetimeIndex(
        np.concatenate(
            [pd.date_range(s.open, s.close, freq="min", inclusive="left").tz_convert("UTC") for s in schedule.sessions]
        )
    )
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}, index=index)
    frame.attrs.update(timeframe="1m", feed="synthetic", adjustment="raw")
    return frame


@pytest.fixture
def schedule_for():
    return _schedule_for


@pytest.fixture
def minute_bars():
    return _minute_bars


@pytest.fixture
def forecast_market():
    rng = np.random.default_rng(518)
    gap = rng.normal(0, 0.01, 600)
    # The preceding observable open gap predicts the next close return.
    returns = np.r_[0, 0.8 * gap[:-1]] + rng.normal(0, 0.001, 600)
    close = 100 * np.exp(returns.cumsum())
    opening = np.r_[100, close[:-1]] * (1 + gap)
    frame = pd.DataFrame(
        {
            "open": opening,
            "high": np.maximum(opening, close) * 1.01,
            "low": np.minimum(opening, close) * 0.99,
            "close": close,
            "volume": rng.integers(1000, 10000, 600),
        },
        index=pd.date_range("2020-01-01", periods=600, tz="UTC"),
    )
    frame.attrs.update(timeframe="1d", feed="synthetic", adjustment="raw")
    return frame
