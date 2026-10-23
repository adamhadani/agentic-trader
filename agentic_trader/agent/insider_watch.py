"""Insider watch cards: the frozen ``insider`` v1 cluster rule, applied live at the 10:35 suggestion scan.

Spec: docs/superpowers/specs/2026-10-23-insider-watch-cards-design.md, section 3. The v1 leg failed P1 and
passed P2, so the rule is shown to the operator as an unvalidated, tap-only card (``kind == "watch"``): no
registry, probe, shadow or promotion credit, never auto-entered. Item 1.4 (auto-entry) must exclude every
card whose source ``kind`` is ``watch``; the card's ``decision_provenance["card_kind"]`` says so too.

One decision per New York session (``CatalogCardSource``):

1. The trigger calendar is the study's: every session from the cube spec's ``bars_from`` (2016-01-01)
   through D, so the 63-session cooldown chain is the frozen rule's, however far back it reaches. The
   prefix through the spec's ``bars_through`` is read once per process, year by year, accepted only when
   every day comes from the Alpaca API (``calendar_source`` otherwise: a fallback calendar misses one-off
   closures) and its session count and digest are the study's recorded ones (``calendar_mismatch``
   otherwise); only the days after it are read per New York date.
2. The pinned quarterly archive (digest-checked) is read once per process in a worker thread
   (``LiveWatchHistory``), together with the study's plausibility verdict on every archived purchase: the
   10 x 20-session median raw dollar volume rule on the pooled cube's own raw-bar cache (the exact bars the
   study used, digest-checked as the pooled runner checks them; ``plausibility_cache_missing`` without it).
   The journaled Form 4 captures are merged onto it per New York date, and only symbols with a captured
   purchase since the archive's end get live SIP raw bars (cached per process, extended per date) for
   their own plausibility check. A cold load that outlasts the scan's preparation deadline makes that
   decision ``unavailable`` (``timeout``) without cancelling the load. A history that does not reach the
   previous calendar day (``WatchHistory.current_for``) makes the session ``unavailable``: never a
   decision on a partial history. Then ``build_insider_events`` runs narrowed to D = today, as
   ``pead_live`` narrows PEAD. Name changes of the event symbols (Alpaca corporate actions, read live;
   digest and request time journaled) are the identity breaks; the rule is re-run with them. Each phase's
   wall time is journaled (``counts["elapsed_seconds"]``).
3. Eligibility as of D-1 on SIP daily bars: the cube's own ``point_in_time_eligibility`` ($10 raw close,
   $5M 20-session median raw dollar volume, ATR14 on adjusted bars), per event symbol. A symbol whose
   captured documents failed to parse inside the cluster + cooldown span is not carded.
4. Display order only: buyers (desc), opportunistic dollars over dollar volume (desc), symbol.

The card is policy-locked (``alpha_policy`` = the cube bracket, ``session_close`` validity), its LLM pass
is commentary, it carries news context like a native card, and it is exempt from ``card_policy``, book
sizing and the broad-index rule. Its risk is capped at ``insider_watch.max_risk_per_trade_usd``.

Residual differences from the study, both in the direction of keeping a trade:

- Archived trades filed after the cube's ``bars_through`` (2026-09-01) and on or before the archive's end
  (2026-09-30) have no dollar volume in the study's bars and are kept, which is the study's own rule for a
  missing dollar volume (the study never saw them).
- A filing dated on a day without a session (Good Friday, when EDGAR is open and NYSE closed) rolls
  forward to D itself when D is the next session. D has no daily bar at 10:35, so that trade has no dollar
  volume and is kept; the study read D's bar.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, Protocol

import numpy as np
import pandas as pd

from agentic_trader.data.insider_capture import (
    InsiderCaptureStore,
    WatchArchive,
    WatchCohort,
    WatchHistory,
    default_capture_directory,
    load_watch_archive,
    load_watch_cohort,
    merge_watch_history,
)
from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.insider_events import build_insider_events, opportunistic_flags
from agentic_trader.research.apriori.pead_live import event_document
from agentic_trader.research.apriori.watch_contract import (
    INSIDER_WATCH_STRATEGY_ID,
    WATCH_CALENDAR_SESSIONS,
    WATCH_CALENDAR_SHA256,
    WATCH_DECISION_TIME_ET,
    WatchContract,
    calendar_sha256,
    load_watch_contract,
)
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.cohort import point_in_time_eligibility
from agentic_trader.research.pooled.cube import load_cube
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.insider_fields import dollar_volume_series, plausible_transactions
from agentic_trader.research.pooled.runner import _verify_bar_files
from agentic_trader.research.setups.runner import _fetch_cached
from agentic_trader.screeners.earnings_drift import catalog_candidate


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentic_trader.agent.catalog_sources import CatalogDecision
    from agentic_trader.config import AppConfig
    from agentic_trader.data.corporate_actions import CorporateActionsSource
    from agentic_trader.screeners.base import ScreenerCandidate
    from agentic_trader.storage.workflow import WorkflowStore


__all__ = [
    "INSIDER_WATCH_POSITION_CAP_REACHED",
    "InsiderWatchSource",
    "LiveWatchHistory",
    "PlausibilityCacheMissing",
    "WatchArchiveState",
    "WatchHistorySource",
    "WatchPreparation",
    "WatchUnavailable",
    "archive_implausible",
    "build_insider_watch_source",
    "cluster_members",
    "rank_events",
    "read_study_raw_bars",
]

logger = logging.getLogger(__name__)

# Sessions of live raw bars read before the first live-captured filing date: the 20-session dollar-volume
# median needs 20 finite sessions before it, with margin for missing bars.
_LIVE_DV_WARMUP_SESSIONS = 30
_ALPACA_CALENDAR_SOURCE = "alpaca_api"
INSIDER_WATCH_POSITION_CAP_REACHED = "insider watch position cap reached"
# Calendar days looked ahead for the 20-session time exit shown on the card.
_EXIT_CALENDAR_DAYS = 45
_DOLLAR_TOLERANCE = 0.01


@dataclass(frozen=True)
class WatchPreparation:
    """One session's insider watch decision (a ``CatalogPreparation``).

    ``events`` are the eligible, complete, unowned events in display order; ``skipped_events`` the
    eligible ones an open position or a live alpha owns (``skipped`` names why); ``excluded_events``
    every other event, each with its ``reason`` (an eligibility reason or incomplete filings).
    """

    status: str  # "ok", "unavailable" or "skipped"
    session: date
    reason: str | None = None
    events: tuple[dict, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    skipped_events: tuple[dict, ...] = ()
    excluded_events: tuple[dict, ...] = ()
    counts: dict = field(default_factory=dict)
    entry_sha256: str | None = None
    policy: dict | None = None
    latest_filing_date: str | None = None
    history: dict = field(default_factory=dict)
    incomplete: dict[str, list[str]] = field(default_factory=dict)
    calendar: dict = field(default_factory=dict)  # the trigger calendar's first session and session count
    time_exit_at: str | None = None
    elapsed_seconds: float | None = None


class WatchUnavailable(Exception):
    """The decision cannot be made on the frozen rule's inputs; ``reason`` is journaled as the decision's."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PlausibilityCacheMissing(WatchUnavailable):
    """The pooled cube's raw-bar cache (the study's own bars) is not on this machine."""

    def __init__(self, where: Path | str):
        super().__init__("plausibility_cache_missing")
        self.where = str(where)


@dataclass(frozen=True)
class WatchArchiveState:
    """Once per process: the archive, its cohort, and the archived trades the study's bars call implausible."""

    implausible: frozenset[InsiderTransaction]
    provenance: Mapping[str, Any]
    cohort: Any = None
    archive: Any = None


def archive_implausible(
    by_symbol: Mapping[str, Sequence[InsiderTransaction]],
    prefix: Sequence[date],
    raw: Mapping[str, pd.DataFrame],
    max_trade_rel: float,
) -> frozenset[InsiderTransaction]:
    """The archived trades the study's plausibility filter dropped: ``plausible_transactions`` on the study's
    calendar (``prefix``) and raw bars, exactly as ``insider_runner`` applies it. Trades filed after the
    calendar have no dollar volume there and are never dropped here."""
    ordinals = np.array([day.toordinal() for day in prefix], dtype=np.int64)
    days = tuple(prefix)
    dropped: set[InsiderTransaction] = set()
    for symbol, rows in by_symbol.items():
        frame = raw.get(symbol)
        inside = [t for t in rows if t.filing_date <= days[-1]]
        if frame is None or frame.empty or not inside:
            continue
        kept, removed = plausible_transactions(inside, ordinals, dollar_volume_series(frame, days), max_trade_rel)
        if removed:
            survivors = set(kept)
            dropped.update(t for t in inside if t not in survivors)
    return frozenset(dropped)


class _CacheOnly:
    """The pooled runner's bar source for a ``require_cached`` read: a cache miss is a missing cache."""

    def fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, adjustment: str
    ) -> pd.DataFrame:
        raise PlausibilityCacheMissing(f"{symbol}_{timeframe}/{adjustment}")


async def _no_pace() -> None:
    return None


async def read_study_raw_bars(
    cache_dir: Path, *, cohort_sha256: str, contract: WatchContract, symbols: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """The raw daily bars the insider v1 study read, from the pooled cube's cache (no provider access).

    The cube must be the one the frozen entry pins (``cube_sha256``) for this cohort and spec, and every bar
    file it was built from must be unchanged (the pooled runner's own ``_verify_bar_files``); each symbol is
    read through the runner's cache reader. Anything missing raises ``PlausibilityCacheMissing``; a
    mismatch raises ``ValueError``.
    """
    spec = contract.spec
    cube_path = cache_dir / f"cube-{cohort_sha256[:16]}-{spec.identity[:16]}.npz"
    if not cube_path.exists():
        raise PlausibilityCacheMissing(cube_path)
    cube = await asyncio.to_thread(load_cube, cube_path)
    if cube.spec_identity != spec.identity or cube.cohort_sha256 != cohort_sha256:
        raise ValueError(f"cached cube {cube_path.name} does not match the watch contract's cohort and spec")
    if await asyncio.to_thread(lambda: cube.sha256) != contract.entry.cube_sha256:
        raise ValueError(f"cached cube {cube_path.name} is not the cube the insider entry pins")
    bar_dir, raw_dir = cache_dir / "bars", cache_dir / "bars_raw"
    await asyncio.to_thread(_verify_bar_files, cube.coverage, bar_dir, raw_dir)
    start = datetime.combine(spec.bars_from, time.min, tzinfo=UTC)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = await _fetch_cached(symbol, "1d", _CacheOnly(), raw_dir, start, end, "raw", _no_pace)
        if not frame.empty:
            out[symbol] = frame
    return out


class LiveWatchHistory:
    """The daemon's watch history: the archive and its plausibility verdict once per process, the captures
    merged per call.

    ``archive(prefix)`` starts the read in the background on first use (or on ``warm``) and awaits it
    shielded, so a caller's timeout never cancels it: the next decision finds it done. A failed read is
    logged by type name and retried by the next call. ``prefix`` is the verified study calendar.
    """

    def __init__(
        self,
        store: InsiderCaptureStore | None,
        *,
        load_archive: Callable[[], tuple[Any, Any]],
        merge: Callable[..., WatchHistory] = merge_watch_history,
        read_raw: Callable[[Any, Sequence[str]], Awaitable[Mapping[str, pd.DataFrame]]] | None = None,
    ):
        self.store = store
        self.load_archive = load_archive
        self.merge = merge
        self.read_raw = read_raw
        self._prefix: tuple[date, ...] = ()
        self._task: asyncio.Task[WatchArchiveState] | None = None

    def warm(self, prefix: Sequence[date] = ()) -> None:
        """Start the read now (inside a running event loop); a no-op once started."""
        self._start(prefix)

    def _start(self, prefix: Sequence[date]) -> asyncio.Task[WatchArchiveState]:
        task = self._task
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            if prefix:
                self._prefix = tuple(prefix)
            task = asyncio.get_running_loop().create_task(self._load(self._prefix))
            task.add_done_callback(_log_failure)
            self._task = task
        return task

    async def _load(self, prefix: tuple[date, ...]) -> WatchArchiveState:
        cohort, archive = await asyncio.to_thread(self.load_archive)
        if self.read_raw is None or not prefix:
            return WatchArchiveState(frozenset(), {"plausibility": "not_checked"}, cohort, archive)
        symbols = sorted(
            symbol
            for symbol, rows in archive.by_symbol.items()
            if any(t.code == "P" and prefix[0] <= t.filing_date <= prefix[-1] for t in rows)
        )
        raw = await self.read_raw(cohort, symbols)
        dropped = await asyncio.to_thread(
            archive_implausible, archive.by_symbol, prefix, raw, cohort.filings.max_trade_rel
        )
        provenance = {"plausibility": "study_bars", "symbols_checked": len(symbols), "implausible": len(dropped)}
        return WatchArchiveState(dropped, provenance, cohort, archive)

    async def archive(self, prefix: Sequence[date] = ()) -> WatchArchiveState:
        return await asyncio.shield(self._start(prefix))

    async def __call__(self, trading: Sequence[date]) -> WatchHistory:
        state = await self.archive(self._prefix)
        assert self.store is not None
        captures = await self.store.captures()
        return await asyncio.to_thread(self.merge, state.archive, state.cohort, tuple(trading), {}, (), captures)


def _log_failure(task: asyncio.Task[Any]) -> None:
    """Retrieve a failed background read's exception and log its type name only (paths and SDK text stay out)."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning(
            "Insider watch archive read failed; the next decision retries",
            extra={"event": "insider_watch_archive_failed", "error": type(error).__name__},
        )


@dataclass(frozen=True)
class _SessionHistory:
    """The history of one decision date with the plausibility filter applied (cached per New York date)."""

    session: date
    trading: tuple[date, ...]
    history: WatchHistory
    transactions: Mapping[str, tuple[InsiderTransaction, ...]]
    raw: Mapping[str, pd.DataFrame]  # live raw bars (symbols with a captured purchase since the archive)
    implausible: int  # live trades dropped; the archive's drops are in the process state's provenance


class WatchHistorySource(Protocol):
    """Where the decision's history comes from (``LiveWatchHistory`` in the daemon)."""

    async def archive(self, prefix: Sequence[date] = ()) -> WatchArchiveState: ...

    async def __call__(self, trading: Sequence[date]) -> WatchHistory: ...


def cluster_members(
    rows: Sequence[InsiderTransaction], trading: Sequence[date], contract: WatchContract, filing: date
) -> list[InsiderTransaction]:
    """The opportunistic purchases inside the cluster window ending at ``filing``, as ``build_insider_events``
    counts them: on the calendar, routine ones excluded, filed on or before ``filing``."""
    purchases = sorted((t for t in rows if t.code == "P"), key=lambda t: t.filing_date)
    ordinals = np.array([day.toordinal() for day in trading], dtype=np.int64)
    first = int(ordinals[0])
    opportunistic = [
        t
        for t, flag in zip(purchases, opportunistic_flags(purchases), strict=True)
        if flag and t.filing_date.toordinal() >= first
    ]
    position = int(np.searchsorted(ordinals, filing.toordinal(), side="left"))
    window = contract.entry.event.cluster_window_sessions
    return [
        t
        for t in opportunistic
        if position - (window - 1) <= int(np.searchsorted(ordinals, t.filing_date.toordinal(), side="left")) <= position
        and t.filing_date <= filing
    ]


def rank_events(events: Sequence[dict]) -> list[dict]:
    """Display order (not validated): buyers desc, opportunistic dollars over dollar volume desc, symbol."""
    return sorted(events, key=lambda event: (-event["buyers"], -event["opp_rel"], event["symbol"]))


def _apply_plausibility(
    history: WatchHistory,
    trading: tuple[date, ...],
    archive_dropped: frozenset[InsiderTransaction],
    raw: Mapping[str, pd.DataFrame],
    max_trade_rel: float,
) -> tuple[dict[str, tuple[InsiderTransaction, ...]], int]:
    """Drop the archived trades the study's bars call implausible, then check the live-captured trades (filed
    after the archive) of every symbol with live bars on those bars; the others keep every trade."""
    ordinals = np.array([day.toordinal() for day in trading], dtype=np.int64)
    out: dict[str, tuple[InsiderTransaction, ...]] = {}
    dropped = 0
    for symbol, rows in history.transactions.items():
        archived = [t for t in rows if t.filing_date <= history.archive_through and t not in archive_dropped]
        live = [t for t in rows if t.filing_date > history.archive_through]
        if symbol in raw and live:
            live, removed = plausible_transactions(
                live, ordinals, dollar_volume_series(raw[symbol], trading), max_trade_rel
            )
            dropped += removed
        out[symbol] = (*archived, *live)
    return out, dropped


def _eligibility(
    contract: WatchContract,
    trading: tuple[date, ...],
    symbol: str,
    adjusted: Mapping[str, pd.DataFrame],
    raw: Mapping[str, pd.DataFrame],
) -> tuple[str | None, float | None, float | None]:
    """``(reason, atr, dollar_volume)`` at D (as of D-1): the cube's own rule, applied to one symbol."""
    universe = contract.spec.universe.model_copy(update={"min_eligible_names": 1})
    result = point_in_time_eligibility(
        trading,
        (trading[-1], trading[-1]),
        [symbol],
        {symbol: adjusted[symbol]} if symbol in adjusted else {},
        {symbol: raw[symbol]} if symbol in raw else {},
        {},
        universe=universe,
        atr_window=contract.spec.bracket.atr_window,
    )
    if result.eligible.size and bool(result.eligible[0, 0]):
        return None, float(result.atr[0, 0]), float(result.dollar_volume[0, 0])
    reason = next(iter(result.reasons), None) or next(iter(result.skipped_sessions), None) or "ineligible"
    return reason, None, None


class InsiderWatchSource:
    """The insider watch card source (``kind="watch"``) on the ``CatalogCardSource`` seam.

    ``contract`` is None when the frozen entry or its protocol could not be loaded; ``service_error``
    then names why, and the decision scan journals ``service unavailable`` instead of going silent.
    """

    source_id = "insider_watch"
    kind: Literal["probe", "watch"] = "watch"
    strategy_id = INSIDER_WATCH_STRATEGY_ID
    provenance_key = "insider_watch"
    journal_kind = EventKind.INSIDER_WATCH_DECISION
    summary_key = "insider_watch"
    notification_key: Literal["drift", "card_facts"] = "card_facts"
    display_name = "Insider watch"
    card_label = "watch"
    budget_spent_reason = "insider watch budget spent"
    position_cap_reason = INSIDER_WATCH_POSITION_CAP_REACHED
    news_context = True
    # The study's recorded calendar (watch_contract); a test may substitute its own.
    calendar_sessions = WATCH_CALENDAR_SESSIONS
    calendar_sha256 = WATCH_CALENDAR_SHA256

    def __init__(
        self,
        config: AppConfig,
        contract: WatchContract | None,
        *,
        history: WatchHistorySource | None = None,
        calendar: Any = None,
        bars: Any = None,
        actions: CorporateActionsSource | None = None,
        service_error: str | None = None,
    ):
        self.config = config
        self.contract = contract
        self.history = history
        self.calendar = calendar
        self.bars = bars
        self.actions = actions
        self.service_error = service_error
        self._cache: _SessionHistory | None = None
        self._prefix: tuple[date, ...] | None = None  # the verified study calendar, once per process
        self._tail: tuple[date, tuple[date, ...]] | None = None  # (session, sessions after the study's)
        self._live_raw: dict[str, pd.DataFrame] = {}  # live raw bars per symbol, extended per date
        self._live_through: dict[str, date] = {}
        self._warm_task: asyncio.Task[Any] | None = None

    def warm(self) -> None:
        """Start the once-per-process calendar and archive reads now (daemon start, inside the event loop),
        so the 10:35 decision finds them done. A failure is logged by type name; the decision retries."""
        if not (self.config.insider_watch.enabled and self._built()):
            return
        task = asyncio.get_running_loop().create_task(self._warm())
        task.add_done_callback(_log_failure)
        self._warm_task = task

    async def _warm(self) -> None:
        prefix = await self._study_calendar()
        assert self.history is not None
        await self.history.archive(prefix)

    # --- limits and gating ------------------------------------------------------------------------

    def max_cards_per_session(self, config: AppConfig) -> int:
        return config.insider_watch.max_cards_per_session

    def max_open_positions(self, config: AppConfig) -> int:
        return config.insider_watch.max_open_positions

    def max_lateness_seconds(self, config: AppConfig) -> float:
        # The catalog lateness bound every 10:35 decision shares: the study priced at the decision time.
        return config.apriori.max_drift_lateness_seconds

    @property
    def decision_time_et(self) -> str:
        return self.contract.decision_time_et if self.contract is not None else WATCH_DECISION_TIME_ET

    def _built(self) -> bool:
        return all(part is not None for part in (self.contract, self.history, self.calendar, self.bars, self.actions))

    def applies(self, scheduled_time_et: str | None) -> bool:
        return self.config.insider_watch.enabled and self._built() and scheduled_time_et == self.decision_time_et

    def _contract_fields(self) -> dict[str, Any]:
        if self.contract is None:
            return {}
        return {"entry_sha256": self.contract.entry_sha256, "policy": self.contract.policy}

    def skipped(
        self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date, reason: str
    ) -> WatchPreparation | None:
        """A gated or late scan at the decision time skips the watch decision (no registry is involved)."""
        if not config.insider_watch.enabled or scheduled_time_et != self.decision_time_et:
            return None
        return WatchPreparation("skipped", session, reason=reason, **self._contract_fields())

    def unbuilt(
        self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date
    ) -> WatchPreparation | None:
        if self._built() or not config.insider_watch.enabled or scheduled_time_et != self.decision_time_et:
            return None
        return WatchPreparation(
            "unavailable",
            session,
            reason=f"service unavailable: {self.service_error or 'not configured'}",
            **self._contract_fields(),
        )

    def unavailable(self, session: date, reason: str) -> WatchPreparation:
        return WatchPreparation("unavailable", session, reason=reason, **self._contract_fields())

    # --- preparation ------------------------------------------------------------------------------

    @staticmethod
    def _sessions(days: Sequence[Any]) -> tuple[date, ...]:
        if any(day.source != _ALPACA_CALENDAR_SOURCE for day in days):
            raise WatchUnavailable("calendar_source")  # a fallback calendar misses one-off closures
        return tuple(sorted(day.date for day in days if day.is_trading_day))

    async def _study_calendar(self) -> tuple[date, ...]:
        """The study's sessions, ``bars_from``..``bars_through``, read year by year once per process and
        accepted only as the recorded calendar (``calendar_mismatch`` otherwise)."""
        if self._prefix is not None:
            return self._prefix
        assert self.contract is not None
        spec = self.contract.spec
        days: list[Any] = []
        for year in range(spec.bars_from.year, spec.bars_through.year + 1):
            start, end = max(date(year, 1, 1), spec.bars_from), min(date(year, 12, 31), spec.bars_through)
            days.extend(await self.calendar.get_calendar_range(start, end))
        sessions = self._sessions(days)
        if len(sessions) != self.calendar_sessions or calendar_sha256(sessions) != self.calendar_sha256:
            raise WatchUnavailable("calendar_mismatch")
        self._prefix = sessions
        return sessions

    async def _trading_days(self, session: date) -> tuple[date, ...]:
        """Every session from the study's first (the cube spec's ``bars_from``) through ``session``."""
        assert self.contract is not None
        prefix = await self._study_calendar()
        if self._tail is None or self._tail[0] != session:
            after = self.contract.spec.bars_through + timedelta(days=1)
            days = await self.calendar.get_calendar_range(after, session) if after <= session else []
            self._tail = (session, self._sessions(days))
        return prefix + self._tail[1]

    async def _daily(self, symbols: Sequence[str], start: date, end: date, adjustment: str) -> dict:
        if not symbols or start > end:
            return {}
        return await asyncio.to_thread(
            self.bars.fetch_daily_many,
            list(symbols),
            datetime.combine(start, time.min, tzinfo=UTC),
            datetime.combine(end, time(23, 59), tzinfo=UTC),
            adjustment=adjustment,
        )

    async def _live_bars(
        self, symbols: Sequence[str], trading: tuple[date, ...], archive_through: date
    ) -> dict[str, pd.DataFrame]:
        """Raw SIP bars through D-1 for the symbols with a live-captured purchase, cached per process and
        extended per date: a new symbol from shortly before the archive's end, a known one from its last bar."""
        end = trading[-2]  # D-1: no bar of D is read
        first_live = int(np.searchsorted(np.array([d.toordinal() for d in trading]), archive_through.toordinal() + 1))
        fresh_start = trading[max(0, first_live - _LIVE_DV_WARMUP_SESSIONS)]
        groups: dict[date, list[str]] = {}
        for symbol in symbols:
            through = self._live_through.get(symbol)
            start = fresh_start if through is None else through + timedelta(days=1)
            if start <= end:
                groups.setdefault(start, []).append(symbol)
        for start, members in sorted(groups.items()):
            fetched = await self._daily(members, start, end, "raw")
            for symbol in members:
                frame = fetched.get(symbol)
                if frame is not None and not frame.empty:
                    known = self._live_raw.get(symbol)
                    joined = frame if known is None else pd.concat([known, frame])
                    self._live_raw[symbol] = joined[~joined.index.duplicated(keep="last")].sort_index()
                self._live_through[symbol] = end
        return {symbol: self._live_raw[symbol] for symbol in symbols if symbol in self._live_raw}

    async def _session_history(
        self, session: date, trading: tuple[date, ...], phases: dict[str, float]
    ) -> _SessionHistory | str:
        """The plausible history of ``session`` (cached per date once current), or why it is unavailable."""
        cached = self._cache
        if cached is not None and cached.session == session and cached.trading == trading:
            return cached
        assert self.history is not None and self.contract is not None  # applies() checked both
        started = monotonic()
        state = await self.history.archive(trading[: len(self._prefix or ())])
        history = await self.history(trading)
        phases["archive"] += monotonic() - started
        if history.provenance.get("entry_sha256") != self.contract.entry_sha256:
            raise ValueError("the watch history was seeded under another insider entry")
        if not history.current_for(session):
            return f"capture_not_current: latest filing date {history.latest_filing_date.isoformat()}"
        started = monotonic()
        live_buyers = sorted(
            symbol
            for symbol, rows in history.transactions.items()
            if any(t.code == "P" and t.filing_date > history.archive_through for t in rows)
        )
        raw = await self._live_bars(live_buyers, trading, history.archive_through)
        phases["bars"] += monotonic() - started
        max_trade_rel = float(history.provenance["max_trade_rel"])  # the protocol's own threshold
        transactions, implausible = _apply_plausibility(history, trading, state.implausible, raw, max_trade_rel)
        result = _SessionHistory(session, trading, history, transactions, raw, implausible)
        self._cache = result
        return result

    async def _time_exit(self, now: datetime, policy: dict) -> str | None:
        try:
            lifetime = SessionLifetimePolicy(**policy["lifetime"])
            start = now.astimezone(ET_TZ).date()
            days = await self.calendar.get_calendar_range(start, start + timedelta(days=_EXIT_CALENDAR_DAYS))
            return lifetime.holding_deadline(now, [day for day in days if day.is_trading_day]).isoformat()
        except Exception:
            # The card shows the frozen hold length regardless; the lifetime service owns the real deadline.
            logger.warning(
                "Insider watch time exit unavailable", extra={"event": "insider_watch_time_exit_unavailable"}
            )
            return None

    def _events(self, seeded: _SessionHistory, breaks: Mapping[str, date]) -> tuple[pd.DataFrame, dict[str, Any]]:
        assert self.contract is not None
        entry = self.contract.entry
        session = seeded.session
        window = entry.window.model_copy(update={"decisions": (session, session)})
        return build_insider_events(
            seeded.transactions,
            seeded.trading,
            breaks,
            entry.model_copy(update={"window": window}),
            decision_time=self.contract.spec.bracket.decision_clock(),
        )

    async def prepare(self, *, now: datetime, snapshot: Any, owned: Mapping[str, str]) -> WatchPreparation:
        session = now.astimezone(ET_TZ).date()
        if not self._built():
            return self.unavailable(session, "insider watch source unavailable")
        contract = self.contract
        assert contract is not None
        phases = dict.fromkeys(("archive", "calendar", "bars", "rule"), 0.0)
        try:
            started = monotonic()
            trading = await self._trading_days(session)
            phases["calendar"] += monotonic() - started
            if len(trading) < 2 or trading[-1] != session:
                return self.unavailable(session, "not_a_trading_session")
            seeded = await self._session_history(session, trading, phases)
        except WatchUnavailable as exc:
            return self.unavailable(session, exc.reason)
        if isinstance(seeded, str):
            return self.unavailable(session, seeded)
        history = seeded.history
        started = monotonic()
        frame, counts = await asyncio.to_thread(self._events, seeded, {})
        phases["rule"] += monotonic() - started
        breaks: dict[str, date] = {}
        actions_read: dict[str, Any] | None = None
        if not frame.empty:
            symbols = sorted(set(frame["symbol"]))
            start = min(frame["filing_date"])
            assert self.actions is not None  # _built() checked it
            started = monotonic()
            page = await asyncio.to_thread(self.actions.fetch, symbols, start, session)
            phases["bars"] += monotonic() - started
            actions_read = {
                "requested_at": page.requested_at.isoformat(),
                "received_at": page.received_at.isoformat(),
                "window": [start.isoformat(), session.isoformat()],
                "symbols": page.symbols,
                "pages": page.pages,
                "page_sha256": list(page.page_sha256),
            }
            breaks = identity_breaks(page.actions, symbols)
            if breaks:
                started = monotonic()
                frame, counts = await asyncio.to_thread(self._events, seeded, breaks)
                phases["rule"] += monotonic() - started
        counts = {
            **counts,
            "implausible_size": seeded.implausible,
            "identity_breaks": {s: d.isoformat() for s, d in sorted(breaks.items())},
            "corporate_actions": actions_read,
        }

        # A document that failed to parse inside the cluster window plus the cooldown could hide a buyer
        # or a suppressing trigger: that symbol is not carded.
        span = contract.entry.event.cluster_window_sessions + contract.entry.event.cooldown_sessions
        span_start = trading[max(0, len(trading) - 1 - span)]
        incomplete = {
            symbol: [day.isoformat() for day in dates if day >= span_start]
            for symbol, dates in sorted(history.incomplete.items())
        }
        incomplete = {symbol: dates for symbol, dates in incomplete.items() if dates}

        rows = frame.to_dict("records")
        event_symbols = sorted({row["symbol"] for row in rows})
        # ATR14 through D-1 plus its previous close, with margin for holidays (raw for the price and
        # dollar-volume floors: the live bars of an event symbol, which has a live-captured purchase).
        started = monotonic()
        adjusted = await self._daily(event_symbols, trading[-41], trading[-2], "all")
        missing_raw = [symbol for symbol in event_symbols if symbol not in seeded.raw]
        raw_floor = {**seeded.raw, **await self._daily(missing_raw, trading[-41], trading[-2], "raw")}
        phases["bars"] += monotonic() - started
        eligible: list[dict] = []
        excluded: list[dict] = []
        started = monotonic()
        for row in rows:
            symbol = row["symbol"]
            members = cluster_members(seeded.transactions[symbol], trading, contract, row["filing_date"])
            owners = sorted({t.owner_cik for t in members})
            if len(owners) != row["buyers"] or not math.isclose(
                sum(t.dollars for t in members), row["opp_dollars"], abs_tol=_DOLLAR_TOLERANCE
            ):
                raise ValueError(f"cluster members of {row['event_id']} disagree with the event rule")
            document = {
                **event_document(row),
                "entry_sha256": contract.entry_sha256,
                "issuer_cik": history.issuer_ciks.get(symbol),
                "owners": owners,
                "filing_dates": sorted({t.filing_date.isoformat() for t in members}),
            }
            reason, atr, volume = _eligibility(contract, trading, symbol, adjusted, raw_floor)
            if reason is None and symbol in incomplete:
                reason = f"incomplete filings: {', '.join(incomplete[symbol])}"
            elif reason is not None:
                reason = f"ineligible: {reason}"
            if reason is not None:
                excluded.append({**document, "reason": reason})
                continue
            assert atr is not None and volume is not None
            eligible.append(
                {**document, "atr": atr, "dollar_volume": volume, "opp_rel": float(row["opp_dollars"]) / volume}
            )

        phases["rule"] += monotonic() - started
        counts["elapsed_seconds"] = {phase: round(seconds, 3) for phase, seconds in phases.items()}

        events, skipped, skipped_events = [], {}, []
        for event in rank_events(eligible):
            reason = owned.get(event["symbol"])
            if reason:
                skipped[event["symbol"]] = reason
                skipped_events.append(event)
            else:
                events.append(event)
        return WatchPreparation(
            "ok",
            session,
            events=tuple(events),
            skipped=skipped,
            skipped_events=tuple(skipped_events),
            excluded_events=tuple(sorted(excluded, key=lambda e: e["symbol"])),
            counts=counts,
            latest_filing_date=history.latest_filing_date.isoformat(),
            history={key: history.provenance.get(key) for key in sorted(history.provenance)},
            incomplete=incomplete,
            calendar={"first_session": trading[0].isoformat(), "sessions": len(trading)},
            time_exit_at=await self._time_exit(now, contract.policy),
            **self._contract_fields(),
        )

    # --- cards ------------------------------------------------------------------------------------

    def candidate(self, event: dict, data: Any, preparation: WatchPreparation) -> ScreenerCandidate | str:
        exit_text = (
            f"time exit {preparation.time_exit_at[:10]} 15:45 NY"
            if preparation.time_exit_at
            else "20-session time exit"
        )
        return catalog_candidate(
            event,
            data,
            preparation.session,
            strategy=self.strategy_id,
            trigger_detail=lambda: (
                f"Insider watch (unvalidated): {event['buyers']} officer/director buyers, "
                f"${event['opp_dollars']:,.0f} opportunistic purchases filed {', '.join(event['filing_dates'])}; "
                f"20-session hold, {exit_text}."
            ),
            alpha_version=None,
            alpha_policy=preparation.policy,
            probe=False,
        )

    def card_facts(self, candidate: ScreenerCandidate, preparation: WatchPreparation) -> dict | None:
        event = candidate.catalog_event
        if event is None or self.contract is None:
            return None
        return {
            "kind": "insider_watch",
            "buyers": event["buyers"],
            "issuer_cik": event.get("issuer_cik"),
            "filing_dates": event["filing_dates"],
            "opp_dollars": event["opp_dollars"],
            "holding_sessions": self.contract.holding_sessions,
            "time_exit_at": preparation.time_exit_at,
            "risk_cap": self.config.insider_watch.max_risk_per_trade_usd,
        }

    async def journal(
        self, workflows: WorkflowStore, session: AsyncSession, decision: CatalogDecision[WatchPreparation]
    ) -> None:
        """One ``insider_watch_decision`` event (every event, sent or not, with its outcome) and, for an
        unavailable session, its one notice."""
        prep = decision.preparation
        session_date = prep.session.isoformat()
        payload = {
            "scan_id": decision.scan_id,
            "session": session_date,
            "status": "completed" if prep.status == "ok" else prep.status,
            "reason": prep.reason,
            "entry_sha256": prep.entry_sha256,
            "policy": prep.policy,
            "latest_filing_date": prep.latest_filing_date,
            "history": prep.history,
            "counts": prep.counts,
            "incomplete": prep.incomplete,
            "calendar": prep.calendar,
            "skipped": prep.skipped,
            "time_exit_at": prep.time_exit_at,
            "elapsed_seconds": prep.elapsed_seconds,
            "open_watch_positions": decision.open_positions,
            "events": [
                *(
                    {**event, "outcome": decision.outcomes.get(event["symbol"], "not considered")}
                    for event in prep.events
                ),
                *(
                    {**event, "outcome": f"skipped: {prep.skipped.get(event['symbol'], 'owned')}"}
                    for event in prep.skipped_events
                ),
                *({**event, "outcome": event["reason"]} for event in prep.excluded_events),
            ],
        }
        await workflows.append(
            session,
            stream=f"scan/{session_date}",
            kind=self.journal_kind,
            payload=payload,
            key=f"insider_watch_decision/{decision.scan_id}",
        )
        if prep.status == "unavailable":
            # Debounced per New York session by the outbox dedup key.
            await workflows.add_notification(
                session,
                f"insider-watch-unavailable/{session_date}",
                NotificationKind.MESSAGE,
                {
                    "text": f"👀 Insider watch: no watch card this session — {prep.reason}. "
                    "Native and PEAD cards are unaffected.",
                    "formatted": False,
                },
            )


def build_insider_watch_source(
    config: AppConfig,
    workflows: WorkflowStore,
    *,
    repo_root: Path,
    calendar: Any,
    clients: Callable[[], tuple[Any, CorporateActionsSource]],
    capture_directory: Path | None = None,
) -> InsiderWatchSource:
    """The live source: the frozen contract and the provider clients now (``clients`` returns the SIP daily
    bars and the corporate-actions source; no request is made), the cohort (archive ticker map) on the
    first decision.

    Never raises: a contract or a client that cannot be built leaves a source whose decision scan journals
    ``service unavailable: <type>: <message>``.
    """
    contract: WatchContract | None = None
    try:
        contract = load_watch_contract(repo_root)
        bars, actions = clients()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Insider watch source unavailable: %s", error, extra={"event": "insider_watch_unavailable", "error": error}
        )
        return InsiderWatchSource(config, contract, service_error=error)
    store = InsiderCaptureStore(workflows, capture_directory or default_capture_directory())
    first_day = contract.spec.bars_from
    pooled_cache_dir = config.insider_watch.pooled_cache_dir

    def load_archive() -> tuple[WatchCohort, WatchArchive]:
        cohort = load_watch_cohort(repo_root, config.insider_watch.archive_dir)
        return cohort, load_watch_archive(config.insider_watch.archive_dir, cohort, first_day)

    async def read_raw(cohort: WatchCohort, symbols: Sequence[str]) -> Mapping[str, pd.DataFrame]:
        return await read_study_raw_bars(
            pooled_cache_dir, cohort_sha256=cohort.cohort_sha256, contract=contract, symbols=symbols
        )

    history = LiveWatchHistory(store, load_archive=load_archive, read_raw=read_raw)
    return InsiderWatchSource(config, contract, history=history, calendar=calendar, bars=bars, actions=actions)
