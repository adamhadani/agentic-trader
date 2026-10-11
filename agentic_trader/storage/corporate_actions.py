"""Corporate-action captures on the event store: one capture per content change, one check per run."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from agentic_trader.data.corporate_actions import CorporateActionsLookup, CorporateActionsPage
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.workflow import WorkflowStore, encode


__all__ = ["CAPTURE_STREAM", "CHECK_STREAM", "CorporateActionsStore", "build_capture"]

CAPTURE_STREAM = "corporate-actions/capture"
CHECK_STREAM = "corporate-actions/check"


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def build_capture(page: CorporateActionsPage, *, as_of: date, symbols: Sequence[str]) -> dict[str, Any]:
    """The journal payload for one fetch; ``content_sha256`` covers the sorted actions only."""
    actions = [action.model_dump(mode="json") for action in page.actions]
    counts = Counter(action.kind.value for action in page.actions)
    return {
        "captured_at": page.received_at.isoformat(),
        "requested_at": page.requested_at.isoformat(),
        "as_of": as_of.isoformat(),
        "symbols_count": len(symbols),
        "symbols_sha256": _sha256(sorted({s.upper() for s in symbols})),
        "window": [page.window[0].isoformat(), page.window[1].isoformat()],
        "actions": actions,
        "counts_by_kind": dict(sorted(counts.items())),
        "content_sha256": _sha256(actions),
        "pages": page.pages,
        "page_sha256": list(page.page_sha256),
    }


class CorporateActionsStore:
    def __init__(self, store: WorkflowStore, *, max_actions: int = 5000):
        if max_actions < 1:
            raise ValueError("max_actions must be positive")
        self.store, self.max_actions = store, max_actions

    async def _latest(self, stream: str) -> dict[str, Any] | None:
        rows = await self.store.events(stream, limit=1)
        return rows[0]["payload"] if rows else None

    async def latest_capture(self) -> dict[str, Any] | None:
        return await self._latest(CAPTURE_STREAM)

    async def latest_check(self) -> dict[str, Any] | None:
        return await self._latest(CHECK_STREAM)

    async def record(self, capture: dict[str, Any]) -> bool:
        """Append the check always and the capture only when its content changed; True when it did."""
        if len(capture["actions"]) > self.max_actions:
            raise ValueError(f"Corporate-action capture exceeds {self.max_actions} actions")
        latest = await self.latest_capture()
        changed = latest is None or latest["content_sha256"] != capture["content_sha256"]
        check = {
            "checked_at": capture["captured_at"],
            "as_of": capture["as_of"],
            "content_sha256": capture["content_sha256"],
            "symbols_count": capture["symbols_count"],
            "pages": capture["pages"],
            "changed": changed,
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="corporate-actions")
            if changed:
                await self.store.append(
                    session,
                    stream=CAPTURE_STREAM,
                    kind=EventKind.CORPORATE_ACTIONS_CAPTURED,
                    payload=capture,
                    key=f"corporate-actions/capture/{capture['as_of']}/{capture['content_sha256']}/{capture['captured_at']}",
                )
            await self.store.append(
                session, stream=CHECK_STREAM, kind=EventKind.CORPORATE_ACTIONS_CHECKED, payload=check
            )
        return changed

    async def lookup(self, *, now: datetime, max_age_seconds: int) -> CorporateActionsLookup:
        return CorporateActionsLookup.build(
            await self.latest_capture(), await self.latest_check(), now=now, max_age_seconds=max_age_seconds
        )
