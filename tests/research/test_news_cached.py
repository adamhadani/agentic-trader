"""Cached news acquisition: month windows, splitting on truncation, de-duplication, digest-checked reuse."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime

import pytest

from agentic_trader.data.news import NewsPage, normalize_news
from agentic_trader.research.news import fetch_news_cached, month_windows
from tests.data.test_news import row


async def _noop():
    return None


class FakeSource:
    """Replays items per (symbols, start date, end date); a window listed in `truncate` reports truncation."""

    def __init__(self, items_by_symbol, *, truncate=()):
        self.items_by_symbol, self.truncate, self.calls = items_by_symbol, set(truncate), []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start.date(), end.date()))
        rows = [
            r
            for s in symbols
            for r in self.items_by_symbol.get(s, [])
            if start <= datetime.fromisoformat(r["created_at"]) <= end
        ]
        truncated = (start.date(), end.date()) in self.truncate
        return NewsPage(normalize_news(rows), start, end, tuple(symbols), (start, end), 1, ("p",), truncated)


def at(day, item_id, symbol):
    return row(item_id, f"h{item_id}", [symbol], created_at=f"2024-03-{day:02d}T12:00:00Z")


def test_month_windows_are_inclusive_and_clipped():
    assert month_windows(date(2024, 1, 15), date(2024, 3, 10)) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]
    assert month_windows(date(2024, 3, 5), date(2024, 3, 5)) == [(date(2024, 3, 5), date(2024, 3, 5))]


async def test_fetch_requests_chunk_by_month_dedupes_and_caches(tmp_path):
    shared = row(9, "both", ["AAPL", "MSFT"], created_at="2024-03-02T12:00:00Z")
    source = FakeSource(
        {"AAPL": [at(1, 1, "AAPL"), shared], "MSFT": [shared, at(3, 2, "MSFT")], "NVDA": [at(4, 3, "NVDA")]}
    )
    items, manifest = await fetch_news_cached(
        source, ["nvda", "AAPL", "MSFT"], date(2024, 3, 1), date(2024, 3, 31), tmp_path, pace=_noop, chunk_symbols=2
    )
    assert sorted(i.id for i in items) == [1, 2, 3, 9]
    assert source.calls == [
        (("AAPL", "MSFT"), date(2024, 3, 1), date(2024, 3, 31)),
        (("NVDA",), date(2024, 3, 1), date(2024, 3, 31)),
    ]
    assert manifest["count"] == 4 and manifest["symbols"] == ["AAPL", "MSFT", "NVDA"] and manifest["cached"] is False
    assert manifest["requests"] == 2 and manifest["splits"] == 0
    assert manifest["counts_by_symbol"] == {"AAPL": 2, "MSFT": 2, "NVDA": 1} and manifest["counts_by_month"] == {
        "2024-03": 4
    }
    path = tmp_path / "news" / f"{manifest['request_sha256']}.json.gz"
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    again, manifest2 = await fetch_news_cached(
        source, ["AAPL", "MSFT", "NVDA"], date(2024, 3, 1), date(2024, 3, 31), tmp_path, pace=_noop, chunk_symbols=2
    )
    assert again == items and manifest2["cached"] is True and len(source.calls) == 2


async def test_truncated_window_is_split_down_to_a_day(tmp_path):
    source = FakeSource(
        {"AAPL": [at(d, d, "AAPL") for d in range(1, 11)]},
        truncate={(date(2024, 3, 1), date(2024, 3, 10)), (date(2024, 3, 1), date(2024, 3, 5))},
    )
    items, manifest = await fetch_news_cached(
        source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 10), tmp_path, pace=_noop
    )
    assert len(items) == 10 and manifest["splits"] == 2 and manifest["requests"] == 5
    assert source.calls[0][1:] == (date(2024, 3, 1), date(2024, 3, 10))
    assert (date(2024, 3, 1), date(2024, 3, 3)) in [c[1:] for c in source.calls]


async def test_one_day_window_that_still_truncates_fails(tmp_path):
    source = FakeSource({"AAPL": [at(1, 1, "AAPL")]}, truncate={(date(2024, 3, 1), date(2024, 3, 1))})
    with pytest.raises(ValueError, match="page cap"):
        await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)


async def test_corrupt_cache_is_refused(tmp_path):
    source = FakeSource({"AAPL": [at(1, 1, "AAPL")]})
    _, manifest = await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)
    path = tmp_path / "news" / f"{manifest['request_sha256']}.json.gz"
    document = json.loads(gzip.decompress(path.read_bytes()))
    document["items"][0]["headline"] = "tampered"
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    with pytest.raises(ValueError, match="SHA-256"):
        await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)


async def test_rejects_empty_symbols_and_inverted_windows(tmp_path):
    with pytest.raises(ValueError):
        await fetch_news_cached(FakeSource({}), [" "], date(2024, 3, 1), date(2024, 3, 2), tmp_path, pace=_noop)
    with pytest.raises(ValueError):
        await fetch_news_cached(FakeSource({}), ["AAPL"], date(2024, 3, 2), date(2024, 3, 1), tmp_path, pace=_noop)


async def test_garbage_cache_is_unreadable(tmp_path):
    source = FakeSource({"AAPL": [at(1, 1, "AAPL")]})
    _, manifest = await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)
    (tmp_path / "news" / f"{manifest['request_sha256']}.json.gz").write_bytes(b"not gzip at all")
    with pytest.raises(ValueError, match="unreadable"):
        await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)
