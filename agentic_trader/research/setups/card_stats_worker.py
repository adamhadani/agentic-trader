"""The daemon's once-daily card-statistics snapshot (docs/card-evidence.md#card-statistics-snapshot).

Each poll asks whether ``card_policy.stats_time_et`` has passed in New York, whether this is
outside every suggestion-scan window, whether today's key ``card_stats/{et_date}`` is missing
and whether no scan is running. When all hold it labels the journaled candidates of the last
``stats_window_days`` ET dates in a worker thread and appends one idempotent
``card_stats_snapshot`` event. A provider failure writes nothing: the previous snapshot stays
authoritative until it ages out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import compute_card_stats
from agentic_trader.research.setups.outcomes import (
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    FETCH_FAILED_HIT,
    label_journaled,
)
from agentic_trader.research.setups.sources import build_bar_source, scan_ranked_events


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.execution.card_evidence import CardStatsRepository
    from agentic_trader.research.setups.runner import BarSource
    from agentic_trader.storage.db import SignalDatabase

__all__ = ["SCAN_WINDOW_AFTER", "SCAN_WINDOW_BEFORE", "CardStatsUnavailable", "CardStatsWorker", "in_scan_window"]

# The suggestion scans' bursts share the provider: never start labelling inside a slot's window.
SCAN_WINDOW_BEFORE = timedelta(minutes=5)
SCAN_WINDOW_AFTER = timedelta(minutes=20)


class CardStatsUnavailable(RuntimeError):
    """Labelling produced no usable evidence (every bar fetch failed); nothing is written."""


def in_scan_window(now_et: datetime, scan_times_et: Sequence[str]) -> bool:
    """True from five minutes before to twenty minutes after any New York suggestion-scan slot."""
    for slot in scan_times_et:
        due = datetime.combine(now_et.date(), time.fromisoformat(slot), tzinfo=ET_TZ)
        if due - SCAN_WINDOW_BEFORE <= now_et < due + SCAN_WINDOW_AFTER:
            return True
    return False


class CardStatsWorker:
    def __init__(
        self,
        db: SignalDatabase,
        repository: CardStatsRepository,
        config: AppConfig,
        *,
        revision: str,
        bar_source: Callable[[AppConfig], BarSource] = build_bar_source,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        scan_busy: Callable[[], bool] | None = None,
    ):
        self.db, self.repository, self.config = db, repository, config
        self.revision, self.bar_source, self.on_progress = revision, bar_source, on_progress
        # Any scan (swing, intraday, a late suggestion scan, an operator /scan) shares the provider.
        self.scan_busy = scan_busy

    async def run_once(self, now: datetime | None = None) -> str:
        """One poll: ``not_due``, ``scan_window``, ``present``, ``scan_busy`` or ``recorded``; raises on a failed run."""
        now = now or datetime.now(UTC)
        now_et = now.astimezone(ET_TZ)
        policy = self.config.card_policy
        if now_et.time() < time.fromisoformat(policy.stats_time_et):
            return "not_due"
        if in_scan_window(now_et, self.config.scheduler.suggestion_scan_times_et):
            return "scan_window"
        if await self.repository.exists(now_et.date()):
            return "present"
        if self.scan_busy is not None and self.scan_busy():
            return "scan_busy"
        events = await scan_ranked_events(self.db, policy.stats_window_days, now=now)
        if self.on_progress is not None:
            await self.on_progress(f"labelling {len(events)} scan events")
        payload = await asyncio.to_thread(self._snapshot, events, now)
        await self.repository.record(payload)
        return "recorded"

    def _snapshot(self, events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
        """Blocking (provider reads, pacing sleeps, pandas): always runs in a worker thread."""
        frame = label_journaled(
            events,
            self.bar_source(self.config),
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            now=now,
            max_requests_per_minute=self.config.market_data.max_requests_per_minute,
        )
        if len(frame) and bool((frame["hit"] == FETCH_FAILED_HIT).all()):
            reasons = sorted({str(reason) for reason in frame["reason"].dropna()})[:3]
            raise CardStatsUnavailable(f"every bar fetch failed for {len(frame)} candidates: {'; '.join(reasons)}")
        window_end = now.astimezone(ET_TZ).date()
        return compute_card_stats(
            frame,
            window_start=window_end - timedelta(days=self.config.card_policy.stats_window_days - 1),
            window_end=window_end,
            feed=self.config.market_data.alpaca_feed,
            cost_bps=DEFAULT_COST_BPS_PER_SIDE,
            max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
            code_revision=self.revision,
            now=now,
            events_considered=len(events),
        )
