"""Insider watch cards: the frozen ``insider`` v1 cluster rule, applied live at the 10:35 suggestion scan.

Spec: docs/superpowers/specs/2026-10-23-insider-watch-cards-design.md, section 3. The v1 leg failed P1 and
passed P2, so the rule is shown to the operator as an unvalidated, tap-only card (``kind == "watch"``): no
registry, probe, shadow or promotion credit, never auto-entered. Item 1.4 (auto-entry) must exclude every
card whose source ``kind`` is ``watch``; the card's ``decision_provenance["card_kind"]`` says so too.

One decision per New York session (``CatalogCardSource``):

1. The trigger calendar is the study's: every session from the cube spec's ``bars_from`` (2016-01-01)
   through D, so the 63-session cooldown chain is the frozen rule's, however far back it reaches. The
   pinned quarterly archive (digest-checked, about 16 s) is read once per process in a worker thread
   (``LiveWatchHistory``) and the journaled Form 4 captures are merged onto it per New York date. A cold
   load that outlasts the scan's preparation deadline makes that decision ``unavailable`` (``timeout``)
   without cancelling the load. A history that does not reach the previous calendar day
   (``WatchHistory.current_for``) makes the session ``unavailable``: never a decision on a partial history.
2. Apply the study's plausibility filter (a trade above 10 x the 20-session median raw dollar volume is
   dropped) on SIP daily bars over the last ``PLAUSIBILITY_SESSIONS`` sessions, then run
   ``build_insider_events`` narrowed to D = today, as ``pead_live`` narrows PEAD. Name changes of the
   event symbols (Alpaca corporate actions, read live; digest and request time journaled) are the identity
   breaks; the rule is re-run with them.
3. Eligibility as of D-1 on SIP daily bars: the cube's own ``point_in_time_eligibility`` ($10 raw close,
   $5M 20-session median raw dollar volume, ATR14 on adjusted bars), per event symbol. A symbol whose
   captured documents failed to parse inside the cluster + cooldown span is not carded.
4. Display order only: buyers (desc), opportunistic dollars over dollar volume (desc), symbol.

The card is policy-locked (``alpha_policy`` = the cube bracket, ``session_close`` validity), its LLM pass
is commentary, it carries news context like a native card, and it is exempt from ``card_policy``, book
sizing and the broad-index rule. Its risk is capped at ``insider_watch.max_risk_per_trade_usd``.

Residual differences from the study, both in the direction of keeping a trade:

- Trades filed before the plausibility window have no dollar volume and are kept, which is the study's own
  rule for a missing dollar volume; the study had bars back to 2016 and could drop an implausible old
  trade. Such a trade only matters through the routine rule or an old link of the cooldown chain.
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
from typing import TYPE_CHECKING, Any, Literal

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
    WATCH_DECISION_TIME_ET,
    WatchContract,
    load_watch_contract,
)
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.cohort import point_in_time_eligibility
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.insider_fields import dollar_volume_series, plausible_transactions
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
    "PLAUSIBILITY_SESSIONS",
    "InsiderWatchSource",
    "LiveWatchHistory",
    "WatchPreparation",
    "build_insider_watch_source",
    "cluster_members",
    "rank_events",
]

logger = logging.getLogger(__name__)

# Sessions of raw SIP daily bars read for the plausibility filter: the routine rule's look-back (the same
# month a year earlier, at most 13 months, about 275 sessions), the cluster window plus the cooldown (84)
# and the 20-session dollar-volume warm-up. Older trades keep a missing dollar volume (kept, as in the study).
PLAUSIBILITY_SESSIONS = 380
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


class LiveWatchHistory:
    """The daemon's watch history: the pinned archive once per process, the captures merged per call.

    ``archive()`` starts the blocking archive read in a worker thread on first use (or on ``warm()``) and
    awaits it shielded, so a caller's timeout never cancels the read: the next decision finds it done. A
    failed read is retried by the next call.
    """

    def __init__(
        self,
        store: InsiderCaptureStore | None,
        *,
        load_archive: Callable[[], tuple[Any, Any]],
        merge: Callable[..., WatchHistory] = merge_watch_history,
    ):
        self.store = store
        self.load_archive = load_archive
        self.merge = merge
        self._task: asyncio.Task[tuple[Any, Any]] | None = None

    def warm(self) -> None:
        """Start the archive read now (inside a running event loop); a no-op once started."""
        self._start()

    def _start(self) -> asyncio.Task[tuple[Any, Any]]:
        task = self._task
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = asyncio.get_running_loop().create_task(asyncio.to_thread(self.load_archive))
            self._task = task
        return task

    async def archive(self) -> tuple[Any, Any]:
        return await asyncio.shield(self._start())

    async def __call__(self, trading: Sequence[date]) -> WatchHistory:
        cohort, archive = await self.archive()
        assert self.store is not None
        captures = await self.store.captures()
        return await asyncio.to_thread(self.merge, archive, cohort, tuple(trading), {}, (), captures)


@dataclass(frozen=True)
class _SessionHistory:
    """The history of one decision date with the plausibility filter applied (cached per New York date)."""

    session: date
    trading: tuple[date, ...]
    history: WatchHistory
    transactions: Mapping[str, tuple[InsiderTransaction, ...]]
    raw: Mapping[str, pd.DataFrame]
    implausible: int


HistoryLoader = Callable[[Sequence[date]], Awaitable[WatchHistory]]


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


def _plausible(
    history: WatchHistory, trading: tuple[date, ...], raw: Mapping[str, pd.DataFrame], max_trade_rel: float
) -> tuple[dict[str, tuple[InsiderTransaction, ...]], int]:
    """The study's plausibility filter on the symbols with bars (the others keep every trade, as in the study)."""
    ordinals = np.array([day.toordinal() for day in trading], dtype=np.int64)
    out: dict[str, tuple[InsiderTransaction, ...]] = {}
    dropped = 0
    for symbol, rows in history.transactions.items():
        if symbol not in raw or not rows:
            out[symbol] = tuple(rows)
            continue
        kept, removed = plausible_transactions(
            rows, ordinals, dollar_volume_series(raw[symbol], trading), max_trade_rel
        )
        out[symbol] = tuple(kept)
        dropped += removed
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

    def __init__(
        self,
        config: AppConfig,
        contract: WatchContract | None,
        *,
        history: HistoryLoader | None = None,
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

    def warm(self) -> None:
        """Start the once-per-process archive read early (daemon start), so the 10:35 decision finds it done."""
        warm = getattr(self.history, "warm", None)
        if self.config.insider_watch.enabled and callable(warm):
            warm()

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

    async def _trading_days(self, session: date) -> tuple[date, ...]:
        """Every session from the study's first (the cube spec's ``bars_from``) through ``session``."""
        assert self.contract is not None
        days = await self.calendar.get_calendar_range(self.contract.spec.bars_from, session)
        return tuple(sorted(day.date for day in days if day.is_trading_day))

    async def _daily(
        self, symbols: Sequence[str], trading: tuple[date, ...], adjustment: str, *, sessions: int
    ) -> dict:
        """Daily bars of ``symbols`` over the last ``sessions`` sessions through D-1."""
        if not symbols:
            return {}
        start = datetime.combine(trading[max(0, len(trading) - 1 - sessions)], time.min, tzinfo=UTC)
        end = datetime.combine(trading[-2], time(23, 59), tzinfo=UTC)  # through D-1: no bar of D is read
        return await asyncio.to_thread(self.bars.fetch_daily_many, list(symbols), start, end, adjustment=adjustment)

    async def _session_history(self, session: date, trading: tuple[date, ...]) -> _SessionHistory | str:
        """The plausible history of ``session`` (cached per date once current), or why it is unavailable."""
        cached = self._cache
        if cached is not None and cached.session == session and cached.trading == trading:
            return cached
        assert self.history is not None and self.contract is not None  # applies() checked both
        history = await self.history(trading)
        if history.provenance.get("entry_sha256") != self.contract.entry_sha256:
            raise ValueError("the watch history was seeded under another insider entry")
        if not history.current_for(session):
            return f"capture_not_current: latest filing date {history.latest_filing_date.isoformat()}"
        # Only purchases feed the rule, and only those inside the bar window can be measured.
        first = trading[max(0, len(trading) - 1 - PLAUSIBILITY_SESSIONS)]
        buyers = sorted(
            symbol
            for symbol, rows in history.transactions.items()
            if any(t.code == "P" and t.filing_date >= first for t in rows)
        )
        raw = await self._daily(buyers, trading, "raw", sessions=PLAUSIBILITY_SESSIONS)
        max_trade_rel = float(history.provenance["max_trade_rel"])  # the protocol's own threshold
        transactions, implausible = _plausible(history, trading, raw, max_trade_rel)
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
        trading = await self._trading_days(session)
        if len(trading) < 2 or trading[-1] != session:
            return self.unavailable(session, "not_a_trading_session")
        seeded = await self._session_history(session, trading)
        if isinstance(seeded, str):
            return self.unavailable(session, seeded)
        history = seeded.history
        frame, counts = await asyncio.to_thread(self._events, seeded, {})
        breaks: dict[str, date] = {}
        actions_read: dict[str, Any] | None = None
        if not frame.empty:
            symbols = sorted(set(frame["symbol"]))
            start = min(frame["filing_date"])
            assert self.actions is not None  # _built() checked it
            page = await asyncio.to_thread(self.actions.fetch, symbols, start, session)
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
                frame, counts = await asyncio.to_thread(self._events, seeded, breaks)
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
        # ATR14 through D-1 plus its previous close, with margin for holidays.
        adjusted = await self._daily(event_symbols, trading, "all", sessions=40)
        eligible: list[dict] = []
        excluded: list[dict] = []
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
            reason, atr, volume = _eligibility(contract, trading, symbol, adjusted, seeded.raw)
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

    def load_archive() -> tuple[WatchCohort, WatchArchive]:
        cohort = load_watch_cohort(repo_root, config.insider_watch.archive_dir)
        return cohort, load_watch_archive(config.insider_watch.archive_dir, cohort, first_day)

    history = LiveWatchHistory(store, load_archive=load_archive)
    return InsiderWatchSource(config, contract, history=history, calendar=calendar, bars=bars, actions=actions)
