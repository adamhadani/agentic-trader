# tests/research/pooled/test_runner.py
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, LoadedCohort, UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CubeSpec
from agentic_trader.research.pooled.runner import build_cube_inputs


@dataclass
class Day:
    date: date
    is_trading_day: bool


class FakeCalendar:
    async def get_calendar_range(self, start, end):
        out, d = [], start
        while d <= end:
            out.append(Day(d, d.weekday() < 5))
            d += timedelta(days=1)
        return out


class FakeBars:
    def __init__(self):
        self.calls = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment="all", **kwargs):
        self.calls.append((symbol, timeframe, adjustment))
        days, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        if timeframe == "1h":
            index = [datetime.combine(x, time(h), tzinfo=ET_TZ).astimezone(UTC) for x in days for h in range(9, 16)]
        else:
            index = [datetime.combine(x, time(0), tzinfo=ET_TZ).astimezone(UTC) for x in days]
        n = len(index)
        price = np.full(n, 50.0)
        return pd.DataFrame(
            {"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price, "Volume": 1e6},
            index=pd.DatetimeIndex(index),
        )


SPEC = CubeSpec(
    feed="alpaca:sip",
    bars_from=date(2021, 1, 1),
    decisions=(date(2021, 3, 1), date(2021, 3, 5)),
    bars_through=date(2021, 5, 1),
    bracket=BracketSpec(
        decision_time_et="10:35", stop_atr_multiple=2.0, atr_window=14, target_r=3.0, max_hold_sessions=5
    ),
    universe=UniverseSpec(min_price=10.0, static_percentile=0.25, min_eligible_names=1),
    decision_cost_bps=5.0,
)


def cohort() -> LoadedCohort:
    c = Cohort(
        id="pooled-cohort",
        version=1,
        survivorship="test",
        sources=(CohortSource(kind="config_groups", description="t", identity="x", symbols=("AAA", "BBB")),),
        excluded={},
        symbols=("AAA", "BBB"),
    )
    return LoadedCohort(cohort=c, sha256="c" * 64, path=Path("cohort.json"))


async def _no_pace():
    return None


def test_build_fetches_once_and_reuses_the_cached_cube(tmp_path):
    bars = FakeBars()
    static = [f"R{i:02d}" for i in range(20)]  # the live gate needs 20 reference names

    def build():
        return asyncio.run(
            build_cube_inputs(
                cohort(),
                SPEC,
                bars=bars,
                calendar=FakeCalendar(),
                cache_dir=tmp_path,
                static_symbols=static,
                pace=_no_pace,
            )
        )

    first = build()
    assert first.cube.window(date(2021, 3, 1), date(2021, 3, 5)).eligible.all()
    calls = len(bars.calls)
    second = build()
    assert second.cube.sha256 == first.cube.sha256
    assert len(bars.calls) == calls  # bars and the cube came from the cache
    assert {adj for _, tf, adj in bars.calls if tf == "1d"} == {"all", "raw"}
