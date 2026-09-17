from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, SessionSchedule, TradingSession
from agentic_trader.research.alpha.forecasts import ForecastContract
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis, PanelStudyPlan
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


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


@pytest.fixture
def panel_study_input():
    clock = pd.date_range("2022-01-03", periods=180, freq="B", tz="America/New_York")
    frames = {}
    rng = np.random.default_rng(715)
    for symbol in ("AAA", "BBB", "CCC", "DDD", "SPY"):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(clock))))
        opening = close * np.exp(rng.normal(0, 0.002, len(clock)))
        frame = pd.DataFrame(
            {
                "open": opening,
                "high": np.maximum(opening, close) * 1.01,
                "low": np.minimum(opening, close) * 0.99,
                "close": close,
                "volume": 1000.0,
            },
            index=clock,
        )
        frame.attrs.update(feed="alpaca:sip", adjustment="raw", timeframe="1d")
        frames[symbol] = frame
    plan = PanelStudyPlan(
        campaign_id="fixture",
        symbols=("AAA", "BBB", "CCC", "DDD"),
        benchmark="SPY",
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(
            PanelFold("first", clock[70].date(), clock[119].date()),
            PanelFold("second", clock[120].date(), clock[-1].date()),
        ),
        hypotheses=(
            PanelHypothesis("momentum", "roc(close,20)"),
            PanelHypothesis("residual", "roc(close,20)", beta_window=20),
        ),
        target=ForecastTarget("1d", 5, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        top_k=1,
        ic=ICPolicy(min_assets=4, min_observations=10, hac_lags=5, observations_per_year=252),
    )
    return frames, clock, plan


@pytest.fixture
def forecast_contract():
    return ForecastContract(ForecastTarget("1h", 1), "alpaca:sip", "raw", FIXED_BAR_LAYOUT, "USD")
