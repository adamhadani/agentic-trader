"""Cached news acquisition for research (docs/news-context.md#research-acquisition).

One gzip JSON file per request under ``cache_dir/news/<sha256 of the request>.json.gz`` holding the
normalised items (headline, summary, source, url, created_at, symbols — never bodies), the receipt and
the content digest; a reuse verifies the digest and refuses a corrupt file. Requests go per symbol
chunk per calendar month with each request bounded to RESEARCH_MAX_ITEMS; a truncated window is split in half down to one day.
Research only: no database, registry, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from agentic_trader.config import AppConfig
from agentic_trader.data.news import AlpacaNewsSource, NewsItem, NewsSource
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.storage.workflow import encode
from agentic_trader.transport.alpaca import BoundedNewsClient


__all__ = ["build_news_source", "fetch_news_cached", "month_windows"]

FORMAT = 1
RESEARCH_MAX_ITEMS = 5000


def build_news_source(config: AppConfig) -> AlpacaNewsSource:
    """The research source: bounded raw client, bounded requests (split on truncation), the configured pacer."""
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise ValueError("Alpaca data credentials are required")
    client = BoundedNewsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    return AlpacaNewsSource(
        client,
        pacer=RequestPacer(config.news.max_requests_per_minute),
        chunk_size=config.news.chunk_size,
        max_items=RESEARCH_MAX_ITEMS,
    )


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive calendar-month windows covering ``[start, end]``, the first and last clipped."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        windows.append((cursor, min(next_month - timedelta(days=1), end)))
        cursor = next_month
    return windows


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    temporary.replace(path)


def _load_cached(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise ValueError(f"News cache {path.name} is unreadable: {type(exc).__name__}") from None
    missing = [key for key in ("items", "content_sha256", "requested_at", "received_at") if key not in document]
    if missing:
        raise ValueError(f"News cache {path.name} is missing {', '.join(missing)}")
    if _sha256(document["items"]) != document["content_sha256"]:
        raise ValueError(f"News cache {path.name} does not match its recorded SHA-256; refusing a corrupt cache")
    return document


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min, UTC), datetime.combine(end, time.max, UTC)


async def _fetch_window(
    source: NewsSource,
    chunk: list[str],
    start: date,
    end: date,
    pace: Callable[[], Awaitable[None]],
    stats: Counter[str],
) -> list[NewsItem]:
    await pace()
    stats["requests"] += 1
    page = await asyncio.to_thread(source.fetch, chunk, *_bounds(start, end))
    if not page.truncated:
        return list(page.items)
    if start == end:
        raise ValueError(
            f"News window {start} for {len(chunk)} symbols exceeds the provider page cap; use fewer symbols"
        )
    stats["splits"] += 1
    middle = start + (end - start) // 2
    first = await _fetch_window(source, chunk, start, middle, pace, stats)
    second = await _fetch_window(source, chunk, middle + timedelta(days=1), end, pace, stats)
    return first + second


async def fetch_news_cached(
    source: NewsSource,
    symbols: Sequence[str],
    start: date,
    end: date,
    cache_dir: Path,
    *,
    pace: Callable[[], Awaitable[None]],
    chunk_symbols: int = 20,
) -> tuple[tuple[NewsItem, ...], dict[str, Any]]:
    ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not ordered:
        raise ValueError("At least one symbol is required")
    if end < start:
        raise ValueError("The window end precedes its start")
    if chunk_symbols < 1:
        raise ValueError("chunk_symbols must be positive")
    request = {
        "source": "alpaca-news",
        "format": FORMAT,
        "symbols": ordered,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    request_sha = _sha256(request)
    path = Path(cache_dir) / "news" / f"{request_sha}.json.gz"
    stats: Counter[str] = Counter()
    if path.exists():
        document = await asyncio.to_thread(_load_cached, path)
        cached = True
    else:
        requested_at = datetime.now(UTC)
        collected: dict[int, NewsItem] = {}
        for index in range(0, len(ordered), chunk_symbols):
            chunk = ordered[index : index + chunk_symbols]
            for window_start, window_end in month_windows(start, end):
                for item in await _fetch_window(source, chunk, window_start, window_end, pace, stats):
                    collected.setdefault(item.id, item)
        items_sorted = sorted(collected.values(), key=lambda item: (item.created_at, item.id))
        rows = [item.model_dump(mode="json") for item in items_sorted]
        document = {
            "request": request,
            "request_sha256": request_sha,
            "items": rows,
            "content_sha256": _sha256(rows),
            "requested_at": requested_at.isoformat(),
            "received_at": datetime.now(UTC).isoformat(),
            "requests": stats["requests"],
            "splits": stats["splits"],
        }
        payload = gzip.compress(json.dumps(document, sort_keys=True).encode())
        await asyncio.to_thread(_write_private, path, payload)
        cached = False
    items = tuple(NewsItem.model_validate(item_row) for item_row in document["items"])
    by_symbol: Counter[str] = Counter()
    by_month: Counter[str] = Counter()
    for item in items:
        by_month[item.created_at.strftime("%Y-%m")] += 1
        for symbol in item.symbols:
            if symbol in ordered:
                by_symbol[symbol] += 1
    manifest = {
        "request_sha256": request_sha,
        "content_sha256": document["content_sha256"],
        "symbols": ordered,
        "window": [start.isoformat(), end.isoformat()],
        "count": len(items),
        "counts_by_symbol": dict(sorted(by_symbol.items())),
        "counts_by_month": dict(sorted(by_month.items())),
        "requests": int(document.get("requests", 0)),
        "splits": int(document.get("splits", 0)),
        "cache_file": str(path),
        "cached": cached,
        "requested_at": document["requested_at"],
        "received_at": document["received_at"],
    }
    return items, manifest
