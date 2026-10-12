"""Cached corporate-action acquisition for research (docs/corporate-actions.md#research-acquisition).

One JSON file per request under ``cache_dir/corporate-actions/<sha256 of the request>.json`` holding the
normalised actions, the receipt and the content digest; a reuse verifies the digest and refuses a corrupt
file. Research only: no database, registry, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agentic_trader.config import AppConfig
from agentic_trader.data.corporate_actions import AlpacaCorporateActionsSource, CorporateAction, CorporateActionsSource
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.storage.workflow import encode
from agentic_trader.transport.alpaca import BoundedCorporateActionsClient


__all__ = ["build_corporate_actions_source", "cohort_symbols", "fetch_corporate_actions_cached"]


def cohort_symbols(path: Path) -> list[str]:
    """Symbols of a frozen cohort file: a ``sectors`` mapping (symbols per sector) or a ``symbols`` list."""
    document = json.loads(path.read_text())
    if "sectors" in document:
        return [symbol for members in document["sectors"].values() for symbol in members]
    if "symbols" in document:
        return list(document["symbols"])
    raise ValueError("Cohort file needs a 'sectors' mapping or a 'symbols' list")


def build_corporate_actions_source(config: AppConfig) -> CorporateActionsSource:
    """The research acquisition's Alpaca source (bounded client, raw rows, the configured pacer)."""
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise ValueError("Alpaca data credentials are required")
    client = BoundedCorporateActionsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    return AlpacaCorporateActionsSource(
        client,
        pacer=RequestPacer(config.corporate_actions.max_requests_per_minute),
        chunk_size=config.corporate_actions.chunk_size,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    """Create the directory and file owner-only and publish the file atomically (no truncated cache file)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    temporary.replace(path)


def _load_cached(path: Path) -> dict[str, Any]:
    """The cache document, or a ValueError naming the file when it is unreadable or incomplete."""
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Corporate-action cache {path.name} is unreadable: {type(exc).__name__}") from None
    missing = [key for key in ("actions", "content_sha256", "requested_at", "received_at") if key not in document]
    if missing:
        raise ValueError(f"Corporate-action cache {path.name} is missing {', '.join(missing)}")
    if _sha256(document["actions"]) != document["content_sha256"]:
        raise ValueError(
            f"Corporate-action cache {path.name} does not match its recorded SHA-256; refusing a corrupt cache"
        )
    return document


async def fetch_corporate_actions_cached(
    source: CorporateActionsSource,
    symbols: Sequence[str],
    start: date,
    end: date,
    cache_dir: Path,
    *,
    pace: Callable[[], Awaitable[None]],
) -> tuple[tuple[CorporateAction, ...], dict[str, Any]]:
    ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not ordered:
        raise ValueError("At least one symbol is required")
    if end < start:
        raise ValueError("The window end precedes its start")
    # ``format`` 2: every chunk is followed to its last page (a format-1 file may be truncated at 1,000
    # actions per chunk), so a file written before that fix hashes differently and is never reused.
    request = {
        "source": "alpaca",
        "format": 2,
        "symbols": ordered,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    request_sha = _sha256(request)
    path = Path(cache_dir) / "corporate-actions" / f"{request_sha}.json"
    if path.exists():
        document = await asyncio.to_thread(_load_cached, path)
        actions = tuple(CorporateAction.model_validate(row) for row in document["actions"])
        cached = True
    else:
        await pace()
        page = await asyncio.to_thread(source.fetch, ordered, start, end)
        actions = page.actions
        rows = [action.model_dump(mode="json") for action in actions]
        document = {
            "request": request,
            "request_sha256": request_sha,
            "actions": rows,
            "content_sha256": _sha256(rows),
            "requested_at": page.requested_at.isoformat(),
            "received_at": page.received_at.isoformat(),
            "pages": page.pages,
            "page_sha256": list(page.page_sha256),
        }
        await asyncio.to_thread(_write_private, path, json.dumps(document, indent=2, sort_keys=True))
        cached = False
    counts = Counter(action.kind.value for action in actions)
    manifest = {
        "request_sha256": request_sha,
        "content_sha256": document["content_sha256"],
        "symbols": ordered,
        "window": [start.isoformat(), end.isoformat()],
        "count": len(actions),
        "counts_by_kind": dict(sorted(counts.items())),
        "cache_file": str(path),
        "cached": cached,
        "requested_at": document["requested_at"],
        "received_at": document["received_at"],
    }
    return actions, manifest
