"""Typed news items from Alpaca's news API, a chunked bounded source, and per-symbol selection.

Research and the scan share one normalisation. Article bodies are never requested
(``include_content=False``). Research only on the acquisition side; the scan reads one page per scan.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from alpaca.data.requests import NewsRequest
from pydantic import BaseModel, Field, field_validator

from agentic_trader.data.pacing import RequestPacer


__all__ = [
    "SDK_PAGE_CAP",
    "AlpacaNewsSource",
    "NewsItem",
    "NewsPage",
    "NewsSelection",
    "NewsSource",
    "normalize_news",
    "select_items",
]

# alpaca-py's ``_get_marketdata`` follows at most 50 pages of 50 items when ``limit`` is None.
SDK_PAGE_CAP = 2500


class NewsItem(BaseModel):
    """One provider item: headline-level only, symbols as tagged (upper case)."""

    model_config = {"frozen": True, "extra": "ignore"}

    id: int
    headline: str
    summary: str = ""
    source: str
    url: str | None = None
    created_at: datetime
    symbols: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("created_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @field_validator("symbols", mode="before")
    @classmethod
    def upper(cls, value: Any) -> tuple[str, ...]:
        return tuple(str(s).strip().upper() for s in (value or ()) if str(s).strip())

    @field_validator("summary", mode="before")
    @classmethod
    def text(cls, value: Any) -> str:
        return "" if value is None else str(value)


def normalize_news(rows: Iterable[Mapping[str, Any]]) -> tuple[NewsItem, ...]:
    """Typed items, de-duplicated by id (first wins), newest first."""
    seen: dict[int, NewsItem] = {}
    for raw in rows:
        item = NewsItem.model_validate(dict(raw))
        seen.setdefault(item.id, item)
    return tuple(sorted(seen.values(), key=lambda item: (item.created_at, item.id), reverse=True))


@dataclass(frozen=True)
class NewsPage:
    items: tuple[NewsItem, ...]
    requested_at: datetime
    received_at: datetime
    symbols: tuple[str, ...]
    window: tuple[datetime, datetime]
    pages: int
    page_sha256: tuple[str, ...]
    truncated: bool


class NewsSource(Protocol):
    def fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage: ...


def _page_digest(response: Any) -> str:
    return hashlib.sha256(json.dumps(response, sort_keys=True, default=str).encode()).hexdigest()


class AlpacaNewsSource:
    """Blocking source over a raw-data ``BoundedNewsClient``; callers offload it to a thread.

    Symbols are requested in chunks as the comma-joined string the SDK expects. ``max_items`` is the
    SDK ``limit``, a cap on the whole paginated result of one request; ``None`` follows every page
    up to the SDK's own 50-page cap. A chunk is ``truncated`` when the provider still had a page
    token or the chunk filled its cap. A failed chunk fails the whole fetch.
    """

    def __init__(
        self,
        client: Any,
        *,
        pacer: RequestPacer | None = None,
        chunk_size: int = 50,
        max_items: int | None = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be positive or None")
        self.client, self.pacer, self.chunk_size, self.max_items, self.clock = (
            client,
            pacer,
            chunk_size,
            max_items,
            clock,
        )

    def fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage:
        ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not ordered:
            raise ValueError("At least one symbol is required")
        requested_at = self.clock()
        digests: list[str] = []
        rows: list[Mapping[str, Any]] = []
        truncated = False
        cap = self.max_items if self.max_items is not None else SDK_PAGE_CAP

        def observe(page: Any) -> None:
            digests.append(_page_digest(page.response))

        for index in range(0, len(ordered), self.chunk_size):
            chunk = ordered[index : index + self.chunk_size]
            if self.pacer is not None:
                self.pacer.acquire()
            request = NewsRequest(
                symbols=",".join(chunk),
                start=start,
                end=end,
                sort="desc",
                include_content=False,
                limit=self.max_items,
            )
            with self.client.observe_responses(observe):
                data = self.client.get_news(request)
            if not isinstance(data, Mapping):
                raise TypeError("News response is not a mapping; construct the client with raw_data=True")
            chunk_rows = list(data.get("news") or [])
            rows.extend(chunk_rows)
            if data.get("next_page_token") or len(chunk_rows) >= cap:
                truncated = True
        return NewsPage(
            items=normalize_news(rows),
            requested_at=requested_at,
            received_at=self.clock(),
            symbols=tuple(ordered),
            window=(start, end),
            pages=len(digests),
            page_sha256=tuple(digests),
            truncated=truncated,
        )


@dataclass(frozen=True)
class NewsSelection:
    items: tuple[NewsItem, ...]
    n_items: int
    n_market_wide: int


def select_items(
    page: NewsPage,
    symbol: str,
    *,
    now: datetime,
    window_hours: int,
    max_items: int,
    max_symbols_per_item: int,
) -> NewsSelection:
    """The symbol's company-specific items inside the window, newest first, capped.

    Items tagged with more than ``max_symbols_per_item`` symbols are market-wide lists: excluded and
    counted. Items dated after ``now`` are outside the window.
    """
    wanted = symbol.strip().upper()
    start = now - timedelta(hours=window_hours)
    company: list[NewsItem] = []
    market_wide = 0
    for item in page.items:
        if wanted not in item.symbols or not start <= item.created_at <= now:
            continue
        if len(item.symbols) > max_symbols_per_item:
            market_wide += 1
            continue
        company.append(item)
    company.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return NewsSelection(items=tuple(company[:max_items]), n_items=len(company), n_market_wide=market_wide)
