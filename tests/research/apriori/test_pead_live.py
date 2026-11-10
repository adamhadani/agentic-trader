import asyncio
import json
import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming, ReportedPage
from agentic_trader.data.providers import DAILY_BATCH_SYMBOLS
from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import MarketData, build_events
from agentic_trader.research.apriori.pead_live import PEAD_FETCH_CONCURRENCY, event_document, live_events


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry

# S = decision session (Wednesday); D = Nasdaq report date, two sessions earlier; D-1/D+1
# the sessions bracketing it, whose closes feed the reaction and (D+1) the liquidity gate.
SESSION = date(2026, 10, 28)
REPORT_DATE = date(2026, 10, 26)
AS_OF = date(2026, 10, 27)  # D+1

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
D_MINUS_1 = TRADING_DAYS[REPORT_IDX - 1]
assert TRADING_DAYS[-3:] == [REPORT_DATE, AS_OF, SESSION]

EXPECTED_FETCH_START = datetime.combine(TRADING_DAYS[0], time.min, tzinfo=UTC)
EXPECTED_FETCH_END = datetime.combine(AS_OF, time(23, 59), tzinfo=UTC)


class Calendar:
    """Weekday sessions only (09:30-16:00 New York), no holidays."""

    async def get_calendar_range(self, start: date, end: date) -> list[MarketCalendarDay]:
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(date=day, is_trading_day=day.weekday() < 5, is_early_close=False))
            day += timedelta(days=1)
        return days


class Earnings:
    """Serves `page` only for `page.day`; any other requested day gets an empty page.

    A wrong report-date computation (e.g. requesting D+1 or S instead of D) would
    otherwise silently reuse the fixed page rather than surfacing as a failure.
    """

    def __init__(self, page: ReportedPage):
        self.page = page
        self.requested: list[date] = []

    async def reported_rows(self, day: date) -> ReportedPage:
        self.requested.append(day)
        if day == self.page.day:
            return self.page
        return ReportedPage(day, (), 200, None, "ee" * 32)


class Bars:
    """Routes each request to the `adjusted` or `raw` frame table by the requested
    `adjustment`, exactly as a real bar source would -- so fetching the wrong table for
    a purpose (or swapping which purpose asks for which adjustment) changes what the
    caller sees, rather than being silently absorbed by one shared frame set.
    """

    def __init__(
        self,
        adjusted: dict[str, pd.DataFrame],
        raw: dict[str, pd.DataFrame],
        *,
        fail: bool = False,
        message: str = "bars unavailable",
    ):
        self.adjusted = adjusted
        self.raw = raw
        self.fail = fail
        self.message = message
        self.calls: list[tuple] = []

    def fetch_daily_many(self, symbols, start, end, *, adjustment):
        self.calls.append((tuple(symbols), start, end, adjustment))
        if self.fail:
            raise RuntimeError(self.message)
        source = self.adjusted if adjustment == "all" else self.raw
        return {s: source[s] for s in symbols if s in source}


class FlakyBars(Bars):
    """Raises `httpx.ReadTimeout` for the first `failures[adjustment]` calls of each adjustment."""

    def __init__(self, *args, failures: dict[str, int], **kwargs):
        super().__init__(*args, **kwargs)
        self.failures = dict(failures)

    def fetch_daily_many(self, symbols, start, end, *, adjustment):
        if self.failures.get(adjustment, 0) > 0:
            self.failures[adjustment] -= 1
            self.calls.append((tuple(symbols), start, end, adjustment))
            raise httpx.ReadTimeout("read operation timed out")
        return super().fetch_daily_many(symbols, start, end, adjustment=adjustment)


class Sleeps:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


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


def reaction_frame(base: float, multiple: float) -> pd.DataFrame:
    """The *adjusted* series for a symbol whose price jumps by ``multiple`` from D onward.

    Used only to drive the reaction/z computation; its price level is deliberately
    unrelated to the liquidity gate's raw price (see ``liquid_raw_frame``), so mixing the
    two tables up changes both the reaction and the liquidity outcome.
    """
    closes = wiggle(len(TRADING_DAYS), base=base)
    closes[REPORT_IDX:] = closes[REPORT_IDX:] * multiple
    return daily_frame(TRADING_DAYS, closes, volume=2_000_000.0)


def liquid_raw_frame(price: float = 60.0, volume: float = 3_000_000.0) -> pd.DataFrame:
    """A flat *raw* series liquid enough to clear the price/volume liquidity gate."""
    return daily_frame(TRADING_DAYS, np.full(len(TRADING_DAYS), price), volume=volume)


def report(symbol: str, eps: float = 1.10, forecast: float = 1.00, n: int = 5) -> CalendarRow:
    return CalendarRow(symbol, REPORT_DATE, EarningsTiming.UNSPECIFIED, eps, forecast, 10.0, n, "Oct/2026")


def entry_with_window(start: date, end: date):
    window = ENTRY.window.model_copy(update={"decisions": (start, end), "recent_from": end})
    return ENTRY.model_copy(update={"window": window})


SPY_FRAME = daily_frame(TRADING_DAYS, np.full(len(TRADING_DAYS), 400.0), volume=5_000_000.0)
RAW_STATIC_FRAMES = {
    s: daily_frame(TRADING_DAYS, np.full(len(TRADING_DAYS), 20.0), volume=1_000_000.0) for s in STATIC_SYMBOLS
}

# Adjusted (reaction) series: WINR/FLAT use an arbitrary low base price -- irrelevant to
# the liquidity gate, which reads only the raw table below -- so an accidental swap of
# the two tables would be caught by both the reaction outcome and the price/volume gate.
ADJUSTED_FRAMES: dict[str, pd.DataFrame] = {
    "SPY": SPY_FRAME,
    "WINR": reaction_frame(8.0, 1.08),  # +8% reaction: a LONG event
    "LOSR": reaction_frame(50.0, 1.08),  # excluded on raw price before reaction is even read
    "FLAT": reaction_frame(50.0, 1.0),  # no reaction: a control event, no leg
}
# Raw (liquidity) series: WINR/FLAT liquid and well above min_price; LOSR's raw close (5)
# is below the entry's min_price (10), regardless of its unrelated adjusted price.
RAW_FRAMES: dict[str, pd.DataFrame] = {
    "WINR": liquid_raw_frame(60.0),
    "LOSR": liquid_raw_frame(5.0),
    "FLAT": liquid_raw_frame(55.0),
    **RAW_STATIC_FRAMES,
}

BASE_ROWS = (report("WINR"), report("LOSR"), report("FLAT"))
BASE_PAGE = ReportedPage(REPORT_DATE, BASE_ROWS, 200, None, "0" * 64)


def base_kwargs(**overrides):
    kwargs = {
        "entry": ENTRY,
        "session": SESSION,
        "earnings": Earnings(BASE_PAGE),
        "calendar": Calendar(),
        "bars": Bars(ADJUSTED_FRAMES, RAW_FRAMES),
        "static_symbols": STATIC_SYMBOLS,
    }
    kwargs.update(overrides)
    return kwargs


async def test_live_events_equal_the_study_rows_for_that_session():
    earnings = Earnings(BASE_PAGE)
    bars = Bars(ADJUSTED_FRAMES, RAW_FRAMES)

    result = await live_events(**base_kwargs(earnings=earnings, bars=bars))

    # The reference computation: the study's own build_events, over a wide decision
    # window, fed the exact same (correctly labelled) bars/rows live_events used
    # internally.
    market = MarketData(tuple(TRADING_DAYS), ADJUSTED_FRAMES, STATIC_SYMBOLS, liquidity_daily=RAW_FRAMES)
    wide_entry = entry_with_window(date(2026, 8, 3), date(2026, 10, 30))
    frame, _ = build_events(list(BASE_ROWS), market, wide_entry)
    expected = frame[(frame["session"] == SESSION) & (frame["leg"] == "LONG")]
    expected_events = tuple(event_document(row) for row in expected.to_dict("records"))

    assert result.status == "ok"
    assert result.report_date == REPORT_DATE
    assert [event["symbol"] for event in expected_events] == ["WINR"]
    assert result.events == expected_events

    # Pin the date mapping: exactly one Nasdaq page fetched, for D, never D+1 or S.
    assert earnings.requested == [REPORT_DATE]

    # Pin the fetch window and adjustment routing: one "all" call for [benchmark,
    # *reporters], one "raw" call for [*reporters, *static_symbols], both ending at
    # D+1 23:59 UTC -- well before S's own session even starts.
    assert len(bars.calls) == 2
    by_adjustment = {adjustment: set(symbols) for symbols, _, _, adjustment in bars.calls}
    assert by_adjustment == {
        "all": {"SPY", "WINR", "LOSR", "FLAT"},
        "raw": {"WINR", "LOSR", "FLAT", *STATIC_SYMBOLS},
    }
    for _symbols, call_start, call_end, _adjustment in bars.calls:
        assert call_start == EXPECTED_FETCH_START
        assert call_end == EXPECTED_FETCH_END
        assert call_end < datetime.combine(SESSION, time.min, tzinfo=UTC)


async def test_ordering_is_by_reaction_z_then_symbol():
    adjusted = {"SPY": SPY_FRAME, "BIGJ": reaction_frame(50.0, 1.20), "SMLJ": reaction_frame(50.0, 1.08)}
    raw = {"BIGJ": liquid_raw_frame(), "SMLJ": liquid_raw_frame(), **RAW_STATIC_FRAMES}
    page = ReportedPage(REPORT_DATE, (report("BIGJ"), report("SMLJ")), 200, None, "1" * 64)

    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=Bars(adjusted, raw)))

    assert result.status == "ok"
    assert [event["symbol"] for event in result.events] == ["BIGJ", "SMLJ"]
    assert result.events[0]["z"] > result.events[1]["z"]


async def test_counts_carry_build_events_skip_reasons():
    result = await live_events(**base_kwargs())

    assert result.status == "ok"
    assert result.counts["reasons"]["illiquid_price"] == 1


async def test_event_document_maps_a_missing_reported_surprise_to_json_safe_none():
    # Two events, one with `surprise_pct_reported=None`: with >=2 rows pandas widens
    # that column to float64, turning the missing value into NaN -- `_plain` must map
    # it back to `None` rather than to a `json.dumps(..., allow_nan=False)` failure.
    adjusted = {"SPY": SPY_FRAME, "AAAA": reaction_frame(50.0, 1.08), "BBBB": reaction_frame(50.0, 1.08)}
    raw = {"AAAA": liquid_raw_frame(), "BBBB": liquid_raw_frame(), **RAW_STATIC_FRAMES}
    rows = (
        CalendarRow("AAAA", REPORT_DATE, EarningsTiming.UNSPECIFIED, 1.10, 1.00, None, 5, "Oct/2026"),
        report("BBBB"),
    )
    page = ReportedPage(REPORT_DATE, rows, 200, None, "3" * 64)

    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=Bars(adjusted, raw)))

    assert result.status == "ok"
    assert {event["symbol"] for event in result.events} == {"AAAA", "BBBB"}

    payload = json.dumps(result.events, allow_nan=False)
    reloaded = {doc["symbol"]: doc for doc in json.loads(payload)}
    assert reloaded["AAAA"]["surprise_pct_reported"] is None
    assert reloaded["BBBB"]["surprise_pct_reported"] == pytest.approx(10.0)


_D_MINUS_1_TIMESTAMP = pd.Timestamp(datetime.combine(D_MINUS_1, time(5, 0), tzinfo=UTC))
_AS_OF_TIMESTAMP = pd.Timestamp(datetime.combine(AS_OF, time(5, 0), tzinfo=UTC))
_ADJUSTED_NO_BENCHMARK_DMINUS1 = {**ADJUSTED_FRAMES, "SPY": SPY_FRAME.drop(index=_D_MINUS_1_TIMESTAMP)}
_ADJUSTED_NO_BENCHMARK_DPLUS1 = {**ADJUSTED_FRAMES, "SPY": SPY_FRAME.drop(index=_AS_OF_TIMESTAMP)}

_UNAVAILABLE_PAGE = ReportedPage(REPORT_DATE, None, 503, "HTTP 503", None)
_EMPTY_PAGE = ReportedPage(REPORT_DATE, (), 200, None, "2" * 64)
_NOT_A_SESSION = date(2026, 10, 31)  # Saturday


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"earnings": Earnings(_UNAVAILABLE_PAGE)}, "calendar_unavailable"),
        ({"earnings": Earnings(_EMPTY_PAGE)}, "calendar_empty"),
        ({"bars": Bars(_ADJUSTED_NO_BENCHMARK_DMINUS1, RAW_FRAMES)}, "benchmark_bars_unavailable"),
        ({"bars": Bars(_ADJUSTED_NO_BENCHMARK_DPLUS1, RAW_FRAMES)}, "benchmark_bars_unavailable"),
        ({"bars": Bars(ADJUSTED_FRAMES, RAW_FRAMES, fail=True)}, "bars_unavailable"),
        ({"static_symbols": STATIC_SYMBOLS[:15]}, "static_reference_unavailable"),
        # No row ever reaches build_events' own reference gate here (the raw fetch is
        # empty, so every reporter fails earlier on `no_daily_bars`); the independent
        # pre-check must still catch the missing reference rather than failing open.
        ({"bars": Bars(ADJUSTED_FRAMES, {})}, "static_reference_unavailable"),
        ({"session": _NOT_A_SESSION}, "not_a_trading_session"),
    ],
)
async def test_unavailable_reasons(overrides, fragment):
    result = await live_events(**base_kwargs(**overrides), sleep=Sleeps())

    assert result.status == "unavailable"
    assert result.events == ()
    assert fragment in (result.reason or "")


async def test_a_bar_fetch_failure_reason_is_the_type_name_only():
    result = await live_events(
        **base_kwargs(bars=Bars(ADJUSTED_FRAMES, RAW_FRAMES, fail=True, message="secret-host-xyz " * 30)),
        sleep=Sleeps(),
    )
    assert result.reason == "bars_unavailable: RuntimeError (after 1 retry)"
    assert "secret-host" not in result.reason


async def test_bars_retry_after_first_timeout():
    bars = FlakyBars(ADJUSTED_FRAMES, RAW_FRAMES, failures={"all": 1})
    sleep = Sleeps()

    result = await live_events(**base_kwargs(bars=bars), sleep=sleep)

    assert result.status == "ok"
    assert sorted(call[3] for call in bars.calls) == ["all", "all", "raw"]
    assert sleep.delays == [2.0]


async def test_raw_fetch_retry():
    bars = FlakyBars(ADJUSTED_FRAMES, RAW_FRAMES, failures={"raw": 1})
    sleep = Sleeps()

    result = await live_events(**base_kwargs(bars=bars), sleep=sleep)

    assert result.status == "ok"
    assert sorted(call[3] for call in bars.calls) == ["all", "raw", "raw"]
    assert sleep.delays == [2.0]


async def test_bars_unavailable_after_retry():
    bars = FlakyBars(ADJUSTED_FRAMES, RAW_FRAMES, failures={"all": 2})
    sleep = Sleeps()

    result = await live_events(**base_kwargs(bars=bars), sleep=sleep)

    assert result.status == "unavailable"
    assert result.reason == "bars_unavailable: ReadTimeout (after 1 retry)"
    assert [call[3] for call in bars.calls if call[3] == "all"] == ["all", "all"]
    assert sleep.delays == [2.0]


async def test_no_retry_on_success():
    sleep = Sleeps()

    result = await live_events(**base_kwargs(), sleep=sleep)

    assert result.status == "ok"
    assert sleep.delays == []


def _many_reporters(n: int) -> list[str]:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [f"Q{letters[i // 26 // 26 % 26]}{letters[i // 26 % 26]}{letters[i % 26]}" for i in range(n)]


class ChunkRecordingBars:
    """Thread-safe fake: records chunks, tracks peak in-flight, optionally holds calls
    until `gate` callers are in flight, and can fail a chosen chunk.
    """

    def __init__(self, *, gate: int = 0, fail_chunk=None, fail_times: int = 1, message: str = "boom"):
        self.lock = threading.Lock()
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.in_flight = 0
        self.peak = 0
        self.gate = gate
        self.released = threading.Event()
        self.fail_chunk = fail_chunk
        self.fail_remaining = fail_times
        self.message = message

    def fetch_daily_many(self, symbols, start, end, *, adjustment):
        key = (tuple(symbols), adjustment)
        with self.lock:
            self.calls.append(key)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            if self.gate and self.in_flight >= self.gate:
                self.released.set()
        try:
            if self.gate:
                assert self.released.wait(timeout=10), "gate never reached"
            if self.fail_chunk is not None and key == self.fail_chunk:
                with self.lock:
                    failing = self.fail_remaining > 0
                    self.fail_remaining -= 1
                if failing:
                    raise httpx.ReadTimeout(self.message)
            source = ADJUSTED_FRAMES if adjustment == "all" else RAW_FRAMES
            return {s: source[s] for s in symbols if s in source}
        finally:
            with self.lock:
                self.in_flight -= 1


def _big_page(n: int) -> tuple[list[str], ReportedPage]:
    reporters = _many_reporters(n)
    return reporters, ReportedPage(REPORT_DATE, tuple(report(sym) for sym in reporters), 200, None, "3" * 64)


async def test_large_fetch_is_chunked_and_bounded():
    reporters, page = _big_page(450)  # 451 adjusted + 475 raw symbols -> 5 + 5 chunks
    bars = ChunkRecordingBars(gate=PEAD_FETCH_CONCURRENCY)

    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=bars), sleep=Sleeps())

    assert result.status == "ok"
    assert all(len(symbols) <= DAILY_BATCH_SYMBOLS for symbols, _ in bars.calls)
    for adjustment, expected in (
        ("all", {"SPY", *reporters}),
        ("raw", {*reporters, *STATIC_SYMBOLS}),
    ):
        requested = [s for symbols, adj in bars.calls if adj == adjustment for s in symbols]
        assert len(requested) == len(set(requested))
        assert set(requested) == expected
    assert len(bars.calls) == 10
    assert bars.peak == PEAD_FETCH_CONCURRENCY


async def test_failed_chunk_is_retried_alone():
    _, page = _big_page(250)
    probe = ChunkRecordingBars()
    await live_events(**base_kwargs(earnings=Earnings(page), bars=probe), sleep=Sleeps())
    target = next(key for key in probe.calls if key[1] == "raw" and key[0][-1] == STATIC_SYMBOLS[-1])

    bars = ChunkRecordingBars(fail_chunk=target)
    sleep = Sleeps()
    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=bars), sleep=sleep)

    assert result.status == "ok"
    assert bars.calls.count(target) == 2
    assert all(bars.calls.count(key) == 1 for key in set(bars.calls) if key != target)
    assert sleep.delays == [2.0]


async def test_chunk_failing_twice_fails_the_session_with_type_name_only():
    _, page = _big_page(250)
    probe = ChunkRecordingBars()
    await live_events(**base_kwargs(earnings=Earnings(page), bars=probe), sleep=Sleeps())
    target = next(key for key in probe.calls if key[1] == "all")

    bars = ChunkRecordingBars(fail_chunk=target, fail_times=99, message="HTTPSConnectionPool(host='x') read timed out")
    result = await live_events(**base_kwargs(earnings=Earnings(page), bars=bars), sleep=Sleeps())

    assert result.status == "unavailable"
    assert result.events == ()
    assert result.reason == "bars_unavailable: ReadTimeout (after 1 retry)"


async def test_chunked_result_matches_the_existing_fixture_events():
    bars = ChunkRecordingBars()
    result = await live_events(**base_kwargs(bars=bars), sleep=Sleeps())
    reference = await live_events(**base_kwargs(), sleep=Sleeps())

    assert result.status == "ok"
    assert result.events == reference.events
    assert result.counts == reference.counts
    assert len(bars.calls) == 2  # fewer than 100 symbols: one chunk per request


async def test_cancellation_propagates_while_chunks_are_in_flight():
    _, page = _big_page(250)
    block = threading.Event()
    started = threading.Event()

    class BlockedBars:
        def fetch_daily_many(self, symbols, start, end, *, adjustment):
            started.set()
            block.wait(timeout=10)
            return {}

    task = asyncio.ensure_future(
        live_events(**base_kwargs(earnings=Earnings(page), bars=BlockedBars()), sleep=Sleeps())
    )
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    block.set()  # let the worker threads finish so the process exits cleanly
    with pytest.raises(asyncio.CancelledError):
        await task
