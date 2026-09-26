import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_runner import build_pead_inputs


@pytest.fixture(autouse=True)
def _no_bar_retry_delays(monkeypatch):
    monkeypatch.setattr("agentic_trader.research.setups.runner._RETRY_DELAYS", (0.0, 0.0))


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json").entry


def tiny_entry():
    window = ENTRY.window.model_copy(
        update={
            "decisions": (date(2021, 3, 15), date(2021, 4, 30)),
            "bars_through": date(2021, 6, 30),
            "recent_from": date(2021, 4, 1),
        }
    )
    return ENTRY.model_copy(update={"window": window})


class Calendar:
    async def get_calendar_range(self, start, end):
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(date=day, is_trading_day=day.weekday() < 5, is_early_close=False))
            day += timedelta(days=1)
        return days


class Bars:
    def __init__(self, missing=(), missing_raw=()):
        self.calls = []
        self.missing = set(missing)
        self.missing_raw = set(missing_raw)

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        if symbol in self.missing or (adjustment == "raw" and symbol in self.missing_raw):
            raise RuntimeError("unknown symbol")
        if timeframe == "1d":
            days = pd.bdate_range(start.date(), end.date())
            index = pd.DatetimeIndex([datetime.combine(d.date(), time(5), tzinfo=UTC) for d in days])
        else:
            stamps = [
                datetime.combine(d.date(), time(h), tzinfo=ET_TZ).astimezone(UTC)
                for d in pd.bdate_range(start.date(), end.date())
                for h in range(9, 16)
            ]
            index = pd.DatetimeIndex(stamps)
        n = len(index)
        close = 50.0 + np.arange(n) % 2 * 0.2
        return pd.DataFrame(
            {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": np.full(n, 1e6)},
            index=index,
        )


def calendar_transport(symbols_by_day):
    def handler(request):
        day = request.url.params["date"]
        rows = [
            {"symbol": s, "eps": "$1.10", "epsForecast": "$1.00", "noOfEsts": "4"} for s in symbols_by_day.get(day, [])
        ]
        payload = {"data": {"rows": rows}, "status": {"rCode": 200}}
        return httpx.Response(200, content=json.dumps(payload).encode())

    return httpx.MockTransport(handler)


async def _no_sleep(_s):
    return None


async def _pace():
    return None


async def test_builds_inputs_and_records_bar_failures(tmp_path):
    bars = Bars(missing={"GONE"})
    inputs = await build_pead_inputs(
        tiny_entry(),
        bars=bars,
        calendar=Calendar(),
        cache_dir=tmp_path,
        static_symbols=[f"S{i:02d}" for i in range(25)],
        pace=_pace,
        calendar_transport=calendar_transport({"2021-04-05": ["XYZ", "GONE", "BRK/B"]}),
        calendar_sleep=_no_sleep,
    )
    assert inputs.acquisition.failed_dates == ()
    assert {row.symbol for row in inputs.rows} == {"XYZ", "GONE", "BRK/B"}
    assert "SPY" in inputs.market.daily and "XYZ" in inputs.market.daily
    assert inputs.bar_failures["GONE"].startswith("daily:")
    assert not any(call[0] == "BRK/B" for call in bars.calls)  # unsupported symbols are never fetched
    daily_calls = [c for c in bars.calls if c[1] == "1d" and c[0] == "XYZ"]
    # One chunk each: adjusted daily bars (reaction, sigma, ATR) and raw ones (liquidity gate).
    assert sorted(c[4] for c in daily_calls) == ["all", "raw"]
    assert all(call[4] == "all" for call in bars.calls if call[1] == "1h")
    assert not any(c[0] == "SPY" and c[4] == "raw" for c in bars.calls)  # the benchmark needs no raw bars
    assert set(inputs.market.liquidity_daily) == set(inputs.market.daily) - {"SPY"}
    assert inputs.static_requested == tuple(f"S{i:02d}" for i in range(25))
    assert (tmp_path / "bars").is_dir() and (tmp_path / "bars_raw").is_dir()
    assert {p.name for p in (tmp_path / "bars_raw").iterdir() if p.is_dir()} >= {"XYZ_1d", "S00_1d"}


async def test_raw_bar_failures_are_recorded_and_exclude_static_names(tmp_path):
    bars = Bars(missing_raw={"S03"})
    inputs = await build_pead_inputs(
        tiny_entry(),
        bars=bars,
        calendar=Calendar(),
        cache_dir=tmp_path,
        static_symbols=[f"S{i:02d}" for i in range(25)],
        pace=_pace,
        calendar_transport=calendar_transport({}),
        calendar_sleep=_no_sleep,
    )
    assert inputs.bar_failures["S03"].startswith("daily_raw: ")
    assert "S03" in inputs.market.daily and "S03" not in inputs.market.liquidity_daily
    assert "S03" not in inputs.market.static_symbols and len(inputs.market.static_symbols) == 24


async def test_rerun_reuses_pages_and_bars(tmp_path):
    kwargs = {
        "calendar": Calendar(),
        "cache_dir": tmp_path,
        "static_symbols": [f"S{i:02d}" for i in range(25)],
        "pace": _pace,
        "calendar_sleep": _no_sleep,
    }
    first = Bars()
    await build_pead_inputs(
        tiny_entry(), bars=first, calendar_transport=calendar_transport({"2021-04-05": ["XYZ"]}), **kwargs
    )

    def refuse(request):
        raise AssertionError("calendar page re-fetched")

    second = Bars()
    inputs = await build_pead_inputs(
        tiny_entry(), bars=second, calendar_transport=httpx.MockTransport(refuse), **kwargs
    )
    assert second.calls == [] and inputs.acquisition.fetched_dates == 0
