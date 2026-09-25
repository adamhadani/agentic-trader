from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import MarketData, build_events, decision_at, surprise_pct


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry


def sessions(start: str, count: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, periods=count)]


def daily_frame(days, closes, volume=1_000_000.0, spread=1.0):
    # 05:00 UTC is the New York date's midnight or 01:00 in both EST and EDT (as Alpaca stamps daily bars).
    index = pd.DatetimeIndex([datetime.combine(d, time(5, 0), tzinfo=UTC) for d in days])
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + spread / 2,
            "Low": closes - spread / 2,
            "Close": closes,
            "Volume": np.full(len(closes), volume),
        },
        index=index,
    )


def report(symbol, day, eps=1.10, forecast=1.00, n=5):
    return CalendarRow(symbol, day, EarningsTiming.UNSPECIFIED, eps, forecast, 10.0, n, "Mar/2021")


def wiggle(n, base=50.0, step=0.2):
    # Alternating returns give a finite, known volatility.
    return base + np.array([step if i % 2 else 0.0 for i in range(n)])


def market_with(event_closes_after, *, days=None, static=25, static_volume=1_000_000.0, volume=2_000_000.0):
    """60 sessions; the event symbol XYZ jumps on the report day index 40."""
    days = days or sessions("2021-03-01", 60)
    base = wiggle(len(days))
    xyz = base.copy()
    xyz[40:] = xyz[40:] * event_closes_after
    daily = {"XYZ": daily_frame(days, xyz, volume=volume), "SPY": daily_frame(days, wiggle(len(days), 400.0))}
    for i in range(static):
        daily[f"S{i:02d}"] = daily_frame(days, wiggle(len(days)), volume=static_volume)
    return MarketData(tuple(days), daily, tuple(f"S{i:02d}" for i in range(static))), days


def entry_with_window(start, end):
    window = ENTRY.window.model_copy(update={"decisions": (start, end), "recent_from": end})
    return ENTRY.model_copy(update={"window": window})


def test_surprise_requires_forecast_estimates_and_size():
    assert surprise_pct(report("A", date(2021, 4, 1), 1.10, 1.00), ENTRY.event) == pytest.approx(10.0)
    assert surprise_pct(report("A", date(2021, 4, 1), -0.90, -1.00), ENTRY.event) == pytest.approx(10.0)
    assert surprise_pct(report("A", date(2021, 4, 1), 0.10, 0.04), ENTRY.event) is None  # |forecast| < 0.05
    assert surprise_pct(report("A", date(2021, 4, 1), 1.0, 1.0, n=None), ENTRY.event) is None
    assert surprise_pct(report("A", date(2021, 4, 1), None, 1.0), ENTRY.event) is None


def test_decision_at_is_new_york_clock_in_utc():
    assert decision_at(date(2021, 4, 5), time(10, 35)) == datetime(2021, 4, 5, 14, 35, tzinfo=UTC)
    assert decision_at(date(2021, 1, 5), time(10, 35)) == datetime(2021, 1, 5, 15, 35, tzinfo=UTC)


def test_positive_surprise_and_jump_is_a_long_leg_event():
    market, days = market_with(1.10)
    entry = entry_with_window(days[0], days[-1])
    events, counts = build_events([report("XYZ", days[40])], market, entry)
    [event] = events.to_dict("records")
    assert event["leg"] == "LONG" and event["surprise_long"] and event["reaction_long"]
    assert event["session"] == days[42] and event["report_date"] == days[40]
    assert event["decision_at"] == decision_at(days[42], time(10, 35))
    assert event["z"] > 1.0 and event["surprise_pct"] == pytest.approx(10.0)
    assert counts["events"] == 1


def test_reaction_uses_adjacent_sessions_across_a_holiday_and_no_report_day_volatility():
    days = sessions("2021-03-01", 60)
    days.pop(41)  # D+1 is a later calendar day after a "holiday"
    days.append(days[-1] + timedelta(days=1))
    market, _ = market_with(1.0, days=days)
    xyz = market.daily["XYZ"].copy()
    xyz.loc[xyz.index[40], "Close"] *= 5.0  # a huge move ON the report day must not enter sigma
    daily = dict(market.daily, XYZ=xyz)
    market = MarketData(market.trading_days, daily, market.static_symbols)
    events, _ = build_events([report("XYZ", days[40])], market, entry_with_window(days[0], days[-1]))
    [event] = events.to_dict("records")
    closes = xyz["Close"].to_numpy()
    sigma = np.std(np.diff(np.log(closes[19:40])), ddof=1)
    spy = market.daily["SPY"]["Close"].to_numpy()
    expected = ((closes[41] / closes[39] - 1) - (spy[41] / spy[39] - 1)) / (sigma * np.sqrt(2))
    assert event["z"] == pytest.approx(expected)


def test_illiquid_and_cheap_names_are_counted_not_emitted():
    market, days = market_with(1.10, volume=1.0)
    entry = entry_with_window(days[0], days[-1])
    events, counts = build_events([report("XYZ", days[40])], market, entry)
    assert events.empty and counts["reasons"] == {"illiquid_volume": 1}
    cheap, _ = market_with(0.1)  # close falls to ~5
    events, counts = build_events([report("XYZ", days[40])], cheap, entry)
    assert events.empty and counts["reasons"] == {"illiquid_price": 1}


def test_skip_reasons():
    market, days = market_with(1.10)
    entry = entry_with_window(days[30], days[50])
    rows = [
        report("BRK/B", days[40]),
        report("NOPE", days[40]),
        report("XYZ", date(2021, 3, 6)),  # a Saturday
        report("XYZ", days[58]),  # D+2 beyond the calendar
        report("XYZ", days[5]),  # too early for sigma
        report("XYZ", days[52]),  # decision after the window
    ]
    events, counts = build_events(rows, market, entry)
    assert events.empty
    assert counts["reasons"] == {
        "unsupported_symbol": 1,
        "no_daily_bars": 1,
        "non_session_date": 1,
        "outside_calendar": 2,
        "outside_window": 1,
    }
    assert counts["reasons_by_year"]["non_session_date"] == {2021: 1}


def test_negative_surprise_and_drop_is_a_short_leg_event_and_mixed_signals_are_control_only():
    market, days = market_with(0.9)
    entry = entry_with_window(days[0], days[-1])
    events, _ = build_events([report("XYZ", days[40], eps=0.80, forecast=1.00)], market, entry)
    assert events.iloc[0]["leg"] == "SHORT"
    events, _ = build_events([report("XYZ", days[40], eps=1.20, forecast=1.00)], market, entry)
    assert events.iloc[0]["leg"] is None and events.iloc[0]["reaction_short"] and events.iloc[0]["surprise_long"]
