# agentic_trader/data/corporate_actions_worker.py
"""The daemon's once-daily corporate-action capture (docs/corporate-actions.md#daemon-worker).

Each poll asks whether this is outside every suggestion-scan window and whether today's New York
date has been checked (in memory, or by the latest journaled check). When neither holds it fetches
the desk's symbols over ``[today - lookback_days, today + lookahead_days]`` on ``process_date`` in a
worker thread and records the capture. A failed fetch records nothing; the next poll retries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import in_scan_window
from agentic_trader.storage.corporate_actions import build_capture


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.data.corporate_actions import CorporateActionsSource
    from agentic_trader.storage.corporate_actions import CorporateActionsStore

__all__ = ["CorporateActionsWorker"]


class CorporateActionsWorker:
    def __init__(
        self,
        source: CorporateActionsSource,
        store: CorporateActionsStore,
        config: AppConfig,
        *,
        symbols: Callable[[], Awaitable[Sequence[str]]],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.source, self.store, self.config = source, store, config
        self.symbols, self.on_progress = symbols, on_progress
        self._checked: date | None = None

    async def run_once(self, now: datetime | None = None) -> str:
        """One poll: ``scan_window``, ``present``, ``captured`` or ``unchanged``; raises on a failed run."""
        now = now or datetime.now(UTC)
        now_et = now.astimezone(ET_TZ)
        today = now_et.date()
        if in_scan_window(now_et, self.config.scheduler.suggestion_scan_times_et):
            return "scan_window"
        if self._checked == today:
            return "present"
        check = await self.store.latest_check()
        if check is not None and check.get("as_of") == today.isoformat():
            self._checked = today
            return "present"
        policy = self.config.corporate_actions
        symbols = sorted({s.strip().upper() for s in await self.symbols() if s and s.strip()})
        if len(symbols) > policy.max_symbols:
            raise ValueError(f"Corporate-action symbols {len(symbols)} exceed max_symbols {policy.max_symbols}")
        if self.on_progress is not None:
            await self.on_progress(f"capturing {len(symbols)} symbols")
        start = today - timedelta(days=policy.lookback_days)
        end = today + timedelta(days=policy.lookahead_days)
        page = await asyncio.to_thread(self.source.fetch, symbols, start, end)
        changed = await self.store.record(build_capture(page, as_of=today, symbols=symbols))
        self._checked = today
        return "captured" if changed else "unchanged"
