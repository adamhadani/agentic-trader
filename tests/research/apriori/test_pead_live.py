from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming, ReportedPage
from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import MarketData, build_events
from agentic_trader.research.apriori.pead_live import event_document, live_events


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry

# S = decision session (Wednesday); D = Nasdaq report date, two sessions earlier; D+1 the
# session in between, whose close feeds both the reaction and the liquidity gate.
SESSION = date(2026, 10, 28)
REPORT_DATE = date(2026, 10, 26)
AS_OF = date(2026, 10, 27)

STATIC_SYMBOLS = tuple(f"S{i:02d}" for i in range(25))


def _weekday_range(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


# The exact trading-day list `live_events` derives from the fake calendar below, for the
# lookback window ending at SESSION -- computed independently here so the fixtures and
# the parity check share one unambiguous day list.
TRADING_DAYS = _weekday_range(SESSION - timedelta(days=120), SESSION)
REPORT_IDX = TRADING_DAYS.index(REPORT_DATE)
assert TRADING_DAYS[-3:] == [REPORT_DATE, AS_OF, SESSION]


class Calendar:
    """Weekday sessions only (09:30-16:00 New York), no holidays."""

    async def get_calendar_range(self, start: date, end: date) -> list[MarketCalendarDay]:
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(date=day, is_trading_day=day.weekday() < 5, is_early_close=False))
            day += timedelta(days=1)
        return days


class Earnings:
    """A fake `reported_rows` that always answers with one fixed page, whatever the day."""

    def __init__(self, page: ReportedPage):
        self.page = page
        self.requested: list[date] = []

    async def reported_rows(self, day: date) -> ReportedPage:
        self.requested.append(day)
        return self.page


class Bars:
    def __init__(self, frames: dict[str, pd.DataFrame], *, fail: bool = False):
        self.frames = frames
        self.fail = fail
        self.calls: list[tuple] = []

    def fetch_daily_many(self, symbols, start, end, *, adjustment):
        self.calls.append((tuple(symbols), start, end, adjustment))
        if self.fail:
            raise RuntimeError("bars unavailable")
        return {s: self.frames[s] for s in symbols if s in self.frames}


def daily_frame(days: list[date], closes, volume: float = 1_000_000.0, spread: float = 1.0) -> pd.DataFrame:
    # 05:00 UTC is the New York date's midnight or 1am (EDT/EST), as Alpaca stamps daily bars.
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


def wiggle(n: int, base: float = 50.0, step: float = 0.2) -> np.ndarray:
    # Alternating returns give a finite, known volatility -- same trick as test_pead_events.py.
    return base + np.array([step if i % 2 else 0.0 for i in range(n)])


def event_frame(base: float, multiple: float, volume: float = 2_000_000.0) -> pd.DataFrame:
    """A liquid symbol whose price jumps by ``multiple`` from the report date D onward."""
    closes = wiggle(len(TRADING_DAYS), base=base)
    closes[REPORT_IDX:] = closes[REPORT_IDX:] * multiple
    return daily_frame(TRADING_DAYS, closes, volume=volume)


def report(symbol: str, eps: float = 1.10, forecast: float = 1.00, n: int = 5) -> CalendarRow:
    return CalendarRow(symbol, REPORT_DATE, EarningsTiming.UNSPECIFIED, eps, forecast, 10.0, n, "Oct/2026")


def entry_with_window(start: date, end: date):
    window = ENTRY.window.model_copy(update={"decisions": (start, end), "recent_from": end})
    return ENTRY.model_copy(update={"window": window})


SPY_FRAME = daily_frame(TRADING_DAYS, np.full(len(TRADING_DAYS), 400.0), volume=5_000_000.0)
STATIC_FRAMES = {
    s: daily_frame(TRADING_DAYS, np.full(len(TRADING_DAYS), 20.0), volume=1_000_000.0) for s in STATIC_SYMBOLS
}
WINR_FRAME = event_frame(50.0, 1.08)  # +8% reaction, price 50: a LONG event
LOSR_FRAME = event_frame(5.0, 1.08)  # +8% reaction, price 5: fails min_price
FLAT_FRAME = event_frame(50.0, 1.0)  # no reaction: a control event, no leg

FRAMES: dict[str, pd.DataFrame] = {
    "SPY": SPY_FRAME,
    "WINR": WINR_FRAME,
    "LOSR": LOSR_FRAME,
    "FLAT": FLAT_FRAME,
    **STATIC_FRAMES,
}

BASE_ROWS = (report("WINR"), report("LOSR"), report("FLAT"))
BASE_PAGE = ReportedPage(REPORT_DATE, BASE_ROWS, 200, None, "0" * 64)


def base_kwargs(**overrides):
    kwargs = {
        "entry": ENTRY,
        "session": SESSION,
        "earnings": Earnings(BASE_PAGE),
        "calendar": Calendar(),
        "bars": Bars(FRAMES),
        "static_symbols": STATIC_SYMBOLS,
    }
    kwargs.update(overrides)
    return kwargs


async def test_live_events_equal_the_study_rows_for_that_session():
    result = await live_events(**base_kwargs())

    # The reference computation: the study's own build_events, over a wide decision
    # window, fed the exact same bars/rows live_events used internally.
    market = MarketData(tuple(TRADING_DAYS), FRAMES, STATIC_SYMBOLS, liquidity_daily=FRAMES)
    wide_entry = entry_with_window(date(2026, 8, 3), date(2026, 10, 30))
    frame, _ = build_events(list(BASE_ROWS), market, wide_entry)
    expected = frame[(frame["session"] == SESSION) & (frame["leg"] == "LONG")]
    expected_events = tuple(event_document(row) for row in expected.to_dict("records"))

    assert result.status == "ok"
    assert [event["symbol"] for event in expected_events] == ["WINR"]
    assert result.events == expected_events


async def test_ordering_is_by_reaction_z_then_symbol():
    frames = {
        "SPY": SPY_FRAME,
        **STATIC_FRAMES,
        "BIGJ": event_frame(50.0, 1.20),
        "SMLJ": event_frame(50.0, 1.08),
    }
    page = ReportedPage(REPORT_DATE, (report("BIGJ"), report("SMLJ")), 200, None, "1" * 64)

    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=Bars(frames)))

    assert result.status == "ok"
    assert [event["symbol"] for event in result.events] == ["BIGJ", "SMLJ"]
    assert result.events[0]["z"] > result.events[1]["z"]


async def test_counts_carry_build_events_skip_reasons():
    result = await live_events(**base_kwargs())

    assert result.status == "ok"
    assert result.counts["reasons"]["illiquid_price"] == 1


_AS_OF_TIMESTAMP = pd.Timestamp(datetime.combine(AS_OF, time(5, 0), tzinfo=UTC))
_FRAMES_NO_BENCHMARK_DPLUS1 = {**FRAMES, "SPY": SPY_FRAME.drop(index=_AS_OF_TIMESTAMP)}

_UNAVAILABLE_PAGE = ReportedPage(REPORT_DATE, None, 503, "HTTP 503", None)
_EMPTY_PAGE = ReportedPage(REPORT_DATE, (), 200, None, "2" * 64)
_NOT_A_SESSION = date(2026, 10, 31)  # Saturday


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"earnings": Earnings(_UNAVAILABLE_PAGE)}, "calendar_unavailable"),
        ({"earnings": Earnings(_EMPTY_PAGE)}, "calendar_empty"),
        ({"bars": Bars(_FRAMES_NO_BENCHMARK_DPLUS1)}, "benchmark_bars_unavailable"),
        ({"bars": Bars(FRAMES, fail=True)}, "bars_unavailable"),
        ({"static_symbols": STATIC_SYMBOLS[:15]}, "static_reference_unavailable"),
        ({"session": _NOT_A_SESSION}, "not_a_trading_session"),
    ],
)
async def test_unavailable_reasons(overrides, fragment):
    result = await live_events(**base_kwargs(**overrides))

    assert result.status == "unavailable"
    assert result.events == ()
    assert fragment in (result.reason or "")
