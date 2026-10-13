"""Typed news: normalisation of Alpaca rows, the chunked source with page evidence, and selection."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_trader.data.news import (
    AlpacaNewsSource,
    NewsItem,
    NewsPage,
    normalize_news,
    select_items,
)


NOW = datetime(2026, 10, 13, 14, 35, tzinfo=UTC)


def row(item_id, headline, symbols, *, hours_ago=1.0, summary="", source="benzinga", created_at=None):
    return {
        "id": item_id,
        "headline": headline,
        "author": "x",
        "created_at": created_at or (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "updated_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "summary": summary,
        "content": "<p>never fetched</p>",
        "url": "https://example.test/a",
        "images": [],
        "symbols": symbols,
        "source": source,
    }


def test_normalize_news_handles_naive_timestamps_and_symbol_case():
    items = normalize_news(
        [
            row(1, "A", ["aapl", "brk.b"], created_at="2026-10-13T13:00:00"),
            row(2, "B", ["MSFT"], hours_ago=0.5),
            row(1, "A again", ["AAPL"]),
        ]
    )
    assert [i.id for i in items] == [2, 1]  # newest first, duplicate id dropped
    assert items[1].symbols == ("AAPL", "BRK.B")
    assert items[1].created_at.tzinfo is UTC and items[1].created_at.hour == 13
    assert items[1].summary == "" and items[1].url == "https://example.test/a"


class FakeClient:
    """A raw-data news client: records requests, replays canned pages, drives the observer."""

    def __init__(self, pages_by_chunk, *, fail_on=None, token_for=()):
        self.pages_by_chunk, self.fail_on, self.token_for, self.requests = pages_by_chunk, fail_on, token_for, []
        self.observer = None

    @contextmanager
    def observe_responses(self, observer):
        self.observer = observer
        try:
            yield
        finally:
            self.observer = None

    def get_news(self, request):
        self.requests.append(request)
        key = tuple(request.symbols.split(","))
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("provider down")
        rows = self.pages_by_chunk[key]
        token = "more" if key in self.token_for else None
        self.observer(SimpleNamespace(response={"news": rows, "next_page_token": token}))
        return {"news": rows}


def test_source_chunks_symbols_dedupes_items_and_records_page_evidence():
    shared = row(7, "AAPL and MSFT", ["AAPL", "MSFT"])
    client = FakeClient({("AAPL", "MSFT"): [shared, row(1, "A", ["AAPL"])], ("NVDA",): [shared]})
    source = AlpacaNewsSource(client, chunk_size=2, max_items=100, clock=lambda: NOW)
    page = source.fetch(["nvda", "aapl", "MSFT"], NOW - timedelta(hours=48), NOW)
    assert [r.symbols for r in client.requests] == ["AAPL,MSFT", "NVDA"]
    first = client.requests[0]
    assert (first.limit, first.sort, first.include_content) == (100, "desc", False)
    assert sorted(i.id for i in page.items) == [1, 7]
    assert page.symbols == ("AAPL", "MSFT", "NVDA") and page.pages == 2 and len(page.page_sha256) == 2
    assert page.truncated is False and page.requested_at == page.received_at == NOW


def test_source_marks_truncation_from_the_last_observed_page_token():
    client = FakeClient({("AAPL",): [row(1, "A", ["AAPL"])]}, token_for={("AAPL",)})
    assert AlpacaNewsSource(client, max_items=100, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is True
    many = [row(i, "A", ["AAPL"]) for i in range(100)]
    client = FakeClient({("AAPL",): many})  # exactly max_items rows, no token left: complete
    assert AlpacaNewsSource(client, max_items=100, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is False
    client = FakeClient({("AAPL",): many})
    assert AlpacaNewsSource(client, max_items=None, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is False


def test_source_sends_limit_none_when_unbounded():
    client = FakeClient({("AAPL",): []})
    AlpacaNewsSource(client, max_items=None, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW)
    assert client.requests[0].limit is None


def test_source_fails_whole_fetch_on_a_chunk_failure_and_rejects_non_mapping():
    client = FakeClient({("AAPL",): [], ("MSFT",): []}, fail_on="MSFT")
    with pytest.raises(RuntimeError):
        AlpacaNewsSource(client, chunk_size=1, clock=lambda: NOW).fetch(["AAPL", "MSFT"], NOW, NOW)

    class Typed:
        @contextmanager
        def observe_responses(self, observer):
            yield

        def get_news(self, request):
            return SimpleNamespace(data={"news": []})

    with pytest.raises(TypeError):
        AlpacaNewsSource(Typed(), clock=lambda: NOW).fetch(["AAPL"], NOW, NOW)


def test_source_requires_symbols_and_a_positive_chunk():
    with pytest.raises(ValueError):
        AlpacaNewsSource(FakeClient({}), chunk_size=0)
    with pytest.raises(ValueError):
        AlpacaNewsSource(FakeClient({}), clock=lambda: NOW).fetch([" "], NOW, NOW)


def page_of(*rows):
    items = normalize_news(rows)
    return NewsPage(items, NOW, NOW, ("AAPL",), (NOW - timedelta(hours=48), NOW), 1, ("p",), False)


def test_select_items_window_market_wide_and_cap():
    page = page_of(
        row(1, "fresh", ["AAPL"], hours_ago=1),
        row(2, "edge in", ["AAPL"], hours_ago=48),
        row(3, "too old", ["AAPL"], hours_ago=48.01),
        row(4, "list", ["AAPL", "B", "C", "D", "E", "F"], hours_ago=2),
        row(5, "future", ["AAPL"], hours_ago=-0.1),
        row(6, "other", ["MSFT"], hours_ago=1),
        row(7, "second", ["AAPL"], hours_ago=3),
    )
    picked = select_items(page, "aapl", now=NOW, window_hours=48, max_items=2, max_symbols_per_item=5)
    assert [i.id for i in picked.items] == [1, 7]  # newest two of the three in-window company items
    assert picked.n_items == 3 and picked.n_market_wide == 1


def test_select_items_for_an_absent_symbol_is_empty():
    picked = select_items(
        page_of(row(1, "a", ["AAPL"])), "TSLA", now=NOW, window_hours=48, max_items=5, max_symbols_per_item=5
    )
    assert picked == select_items(page_of(), "TSLA", now=NOW, window_hours=48, max_items=5, max_symbols_per_item=5)
    assert picked.items == () and picked.n_items == 0 and picked.n_market_wide == 0


def test_news_item_is_frozen():
    item = normalize_news([row(1, "a", ["AAPL"])])[0]
    with pytest.raises(ValidationError):
        item.headline = "b"  # type: ignore[misc]
    assert isinstance(item, NewsItem)
