import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, CorporateActionsPage
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.research.apriori.catalog import load_exdiv_entry
from agentic_trader.research.apriori.exdiv_runner import build_exdiv_inputs


@pytest.fixture(autouse=True)
def _no_bar_retry_delays(monkeypatch):
    monkeypatch.setattr("agentic_trader.research.setups.runner._RETRY_DELAYS", (0.0, 0.0))


ENTRY = load_exdiv_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/exdiv-v1.json").entry


def tiny_entry():
    window = ENTRY.window.model_copy(
        update={
            "decisions": (date(2021, 3, 1), date(2021, 6, 30)),
            "bars_through": date(2021, 9, 30),
            "recent_from": date(2021, 5, 1),
        }
    )
    return ENTRY.model_copy(update={"window": window, "actions_window": (date(2020, 10, 1), date(2021, 10, 31))})


class Calendar:
    async def get_calendar_range(self, start, end):
        days, day = [], start
        while day <= end:
            days.append(MarketCalendarDay(date=day, is_trading_day=day.weekday() < 5, is_early_close=False))
            day += timedelta(days=1)
        return days


class Bars:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, adjustment))
        if symbol in self.missing:
            raise RuntimeError("unknown symbol")
        if timeframe == "1d":
            days = pd.bdate_range(start.date(), end.date())
            index = pd.DatetimeIndex([datetime.combine(d.date(), time(5), tzinfo=UTC) for d in days])
        else:
            index = pd.DatetimeIndex(
                [
                    datetime.combine(d.date(), time(h), tzinfo=ET_TZ).astimezone(UTC)
                    for d in pd.bdate_range(start.date(), end.date())
                    for h in range(9, 16)
                ]
            )
        n = len(index)
        close = 50.0 + np.arange(n) % 2 * 0.2
        return pd.DataFrame(
            {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": np.full(n, 1e6)},
            index=index,
        )


class Actions:
    def __init__(self, actions):
        self.actions = tuple(actions)
        self.calls = []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        now = datetime.now(UTC)
        return CorporateActionsPage(
            actions=self.actions,
            requested_at=now,
            received_at=now,
            symbols=len(symbols),
            window=(start, end),
            pages=1,
            page_sha256=("a" * 64,),
        )


def _dividend(symbol, ex):
    return CorporateAction(
        id=f"{symbol}-{ex}",
        kind=CorporateActionKind.CASH_DIVIDEND,
        symbol=symbol,
        effective_on=ex,
        process_date=ex + timedelta(days=30),
        rate=0.5,
    )


async def _pace():
    return None


# static_reference needs MIN_REFERENCE_NAMES (20) static names with bars, else every event is "no_reference"
STATIC = [f"S{i:02d}" for i in range(20)]


def _cohort(tmp_path):
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps({"sectors": {"energy": ["XYZ", "ABC"]}}))
    return path


async def test_builds_inputs_fetches_actions_once_and_records_bar_failures(tmp_path):
    entry = tiny_entry()
    bars = Bars(missing={"ABC"})
    actions = Actions(
        [_dividend("XYZ", date(2021, 1, 20)), _dividend("XYZ", date(2021, 4, 21)), _dividend("ABC", date(2021, 4, 21))]
    )
    cohort = _cohort(tmp_path)
    inputs = await build_exdiv_inputs(
        entry,
        bars=bars,
        calendar=Calendar(),
        actions_source=actions,
        cache_dir=tmp_path / "cache",
        static_symbols=STATIC,
        pace=_pace,
        cohort_path=cohort,
    )
    assert actions.calls == [(("ABC", "XYZ"), date(2020, 10, 1), date(2021, 10, 31))]
    assert inputs.actions_manifest["count"] == 3 and inputs.cohort["symbols"] == 2
    assert inputs.cohort["sha256"] == hashlib.sha256(cohort.read_bytes()).hexdigest()
    assert "ABC" in inputs.bar_failures and "XYZ" in inputs.hourly and "ABC" not in inputs.hourly
    assert set(inputs.market.static_symbols) == set(STATIC) and inputs.static_requested == tuple(STATIC)
    assert {c for c in bars.calls if c[0] == "XYZ"} == {
        ("XYZ", "1d", "all"),
        ("XYZ", "1d", "raw"),
        ("XYZ", "1h", "all"),
    }
    assert ("S00", "1d", "raw") in bars.calls and ("S00", "1d", "all") not in bars.calls  # static: raw reference only


async def test_rerun_reuses_actions_and_bars(tmp_path):
    entry = tiny_entry()
    cohort = _cohort(tmp_path)
    first_bars, actions = Bars(), Actions([_dividend("XYZ", date(2021, 1, 20)), _dividend("XYZ", date(2021, 4, 21))])
    kwargs = {
        "calendar": Calendar(),
        "actions_source": actions,
        "cache_dir": tmp_path / "cache",
        "static_symbols": STATIC,
        "pace": _pace,
        "cohort_path": cohort,
    }
    await build_exdiv_inputs(entry, bars=first_bars, **kwargs)
    second_bars = Bars()
    inputs = await build_exdiv_inputs(entry, bars=second_bars, **kwargs)
    assert len(actions.calls) == 1 and inputs.actions_manifest["cached"] is True
    assert second_bars.calls == []


async def test_a_missing_cohort_file_raises_before_any_provider_call(tmp_path):
    actions, bars = Actions([]), Bars()
    with pytest.raises(FileNotFoundError):
        await build_exdiv_inputs(
            tiny_entry(),
            bars=bars,
            calendar=Calendar(),
            actions_source=actions,
            cache_dir=tmp_path / "cache",
            static_symbols=[],
            pace=_pace,
            cohort_path=tmp_path / "missing.json",
        )
    assert actions.calls == [] and bars.calls == []
