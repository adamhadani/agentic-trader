# tests/research/pooled/test_screen_executor.py
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled import screen as screen_module
from agentic_trader.research.pooled.cohort import Cohort
from agentic_trader.research.pooled.screen import LoadedScreenRule, execute_screen, load_screen_rule


REPO = Path(__file__).resolve().parents[3]
RULE = load_screen_rule(REPO / "config/research/pooled/screen-v2.json").rule


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


class FakeFetch:
    """Batched raw daily bars; the first ``fail`` calls raise."""

    def __init__(self, fail=0):
        self.calls = []
        self.fail = fail

    def __call__(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("provider down")
        days, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        index = pd.DatetimeIndex([pd.Timestamp(x).tz_localize(ET_TZ) for x in days]).tz_convert("UTC")
        return {
            symbol: pd.DataFrame({"Close": 20.0 + ord(symbol[0]) - 64, "Volume": 1e6}, index=index)
            for symbol in symbols
        }


async def _no_pace():
    return None


def member(symbol, name="Example Corp - Common Stock", source="nasdaqlisted"):
    return {
        "symbol": symbol,
        "classification": "listed_non_etf_equity_candidate",
        "directory": {"Security Name": name, "source": source},
    }


def setup(tmp_path, symbols=("AAA", "BBB", "CCC")):
    members = [member(s) for s in symbols] + [member("XTERW", "Karman Line Acquisition Corp. - Warrant")]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"snapshot_id": RULE.snapshot.snapshot_id, "members": members}))
    rule = RULE.model_copy(
        update={
            "window_sessions": 5,
            "min_sessions_with_bars": 4,
            "top": 2,
            "snapshot": RULE.snapshot.model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}),
        }
    )
    return LoadedScreenRule(rule=rule, sha256="e" * 64, path=Path("screen-v2.json")), path


def run(tmp_path, loaded, snapshot, fetch, out="out", progress=None):
    return asyncio.run(
        execute_screen(
            loaded,
            snapshot,
            tmp_path / out,
            rule_path="config/research/pooled/screen-v2.json",
            config_symbols=["AAPL", "MSFT"],
            calendar=FakeCalendar(),
            fetch=fetch,
            cache_dir=tmp_path / "cache",
            pace=_no_pace,
            environment={"python": "x"},
            progress=progress,
        )
    )


def test_screen_writes_manifest_first_then_the_table_and_a_loadable_cohort(tmp_path):
    loaded, snapshot = setup(tmp_path)
    seen = {}

    class Watching(FakeFetch):
        def __call__(self, symbols, start, end):
            seen.setdefault("manifest", (tmp_path / "out" / "manifest.json").exists())
            return super().__call__(symbols, start, end)

    fetch = Watching()
    result = run(tmp_path, loaded, snapshot, fetch)
    assert result["status"] == "completed", result
    assert seen == {"manifest": True}
    # Every request ends on the rule's window_end: nothing after 2023-12-29 is read.
    assert {call[2].date() for call in fetch.calls} == {date(2023, 12, 29)}
    assert {call[1].date() for call in fetch.calls} == {date(2023, 12, 25)}
    cohort_bytes = (tmp_path / "out" / "cohort.json").read_bytes()
    cohort = Cohort.model_validate_json(cohort_bytes)
    assert result["cohort_sha256"] == hashlib.sha256(cohort_bytes).hexdigest()
    assert cohort.symbols == ("AAPL", "BBB", "CCC", "MSFT")
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert screened.identity == hashlib.sha256((tmp_path / "out" / "screen.csv").read_bytes()).hexdigest()
    assert screened.identity == result["screen_sha256"]
    assert (result["kept"], result["excluded"], result["selected"]) == (3, 1, 2)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["check"] == "screen" and manifest["authorizes_promotion"] is False
    assert result["authorizes_promotion"] is False


def test_a_mismatched_snapshot_fails_before_any_fetch(tmp_path):
    loaded, snapshot = setup(tmp_path)
    snapshot.write_text(snapshot.read_text() + " ")
    fetch = FakeFetch()
    result = run(tmp_path, loaded, snapshot, fetch)
    assert result["status"] == "failed" and "sha256" in result["error"]
    assert fetch.calls == []


def test_chunks_are_cached_and_a_rerun_fetches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 2)
    loaded, snapshot = setup(tmp_path)
    first = run(tmp_path, loaded, snapshot, FakeFetch(), out="one")
    fetch = FakeFetch()
    second = run(tmp_path, loaded, snapshot, fetch, out="two")
    assert fetch.calls == []
    assert first["cohort_sha256"] == second["cohort_sha256"]


def test_five_consecutive_failures_stop_acquisition(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 1)
    loaded, snapshot = setup(tmp_path, symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"))
    fetch = FakeFetch(fail=99)
    messages: list[str] = []
    result = run(tmp_path, loaded, snapshot, fetch, progress=messages.append)
    assert result["status"] == "failed"
    assert "provider unavailable: 5 consecutive requests failed" in result["error"]
    assert len(fetch.calls) == 5
    assert any("chunk 1/7 failed: RuntimeError: provider down" in m for m in messages)


def test_a_failed_chunk_fails_the_screen_after_every_chunk_and_a_rerun_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 1)
    loaded, snapshot = setup(tmp_path)
    fetch = FakeFetch(fail=1)
    result = run(tmp_path, loaded, snapshot, fetch, out="one")
    assert result["status"] == "failed" and "rerun to resume from the cache" in result["error"]
    assert len(fetch.calls) == 3  # every chunk was attempted
    again = FakeFetch()
    assert run(tmp_path, loaded, snapshot, again, out="two")["status"] == "completed"
    assert [call[0] for call in again.calls] == [("AAA",)]  # only the failed chunk


def test_refuses_an_existing_output_directory(tmp_path):
    loaded, snapshot = setup(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        run(tmp_path, loaded, snapshot, FakeFetch())
