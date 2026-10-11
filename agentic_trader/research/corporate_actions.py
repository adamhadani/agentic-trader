"""Cached corporate-action acquisition for research (docs/corporate-actions.md#research-acquisition).

One JSON file per request under ``cache_dir/corporate-actions/<sha256 of the request>.json`` holding the
normalised actions, the receipt and the content digest; a reuse verifies the digest and refuses a corrupt
file. Research only: no database, registry, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionsSource
from agentic_trader.storage.workflow import encode


__all__ = ["fetch_corporate_actions_cached"]


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


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
    request = {"source": "alpaca", "symbols": ordered, "start": start.isoformat(), "end": end.isoformat()}
    request_sha = _sha256(request)
    path = Path(cache_dir) / "corporate-actions" / f"{request_sha}.json"
    if path.exists():
        document = json.loads(await asyncio.to_thread(path.read_text))
        if _sha256(document["actions"]) != document["content_sha256"]:
            raise ValueError(
                f"Corporate-action cache {path.name} does not match its recorded SHA-256; refusing a corrupt cache"
            )
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
