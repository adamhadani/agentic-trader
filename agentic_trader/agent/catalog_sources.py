"""Catalog card sources: cards an event calendar selects, outside the native screener ranking.

A source decides once per session, at the scheduled suggestion scan whose New York time is its
decision time. It prepares that session's events (minus names an open position or a live alpha
owns), offers one candidate per event after the whole native ranking, spends its own derived
per-session card budget (today's signals carrying its ``strategy_id``; the native budget excludes
every catalog source's) and stops carding while ``max_open_positions`` of its positions are open.
Every decision, sent or not, is journaled as one ``journal_kind`` event on ``scan/<date>``.

``TradingCopilot.run_scan`` loops over ``TradingCopilot.catalog_sources``. ``PeadDriftSource``
(the PEAD catalog paper probe, ``kind="probe"``) is the first implementation; ``InsiderWatchSource``
(``agent/insider_watch.py``, ``kind="watch"``) is an unvalidated, operator-judged card with no probe
credit that is never auto-entered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.screeners.earnings_drift import (
    PEAD_DECISION_TIME_ET,
    PEAD_STRATEGY_ID,
    DriftPreparation,
    EarningsDriftService,
    drift_card_facts,
)


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentic_trader.config import AppConfig
    from agentic_trader.screeners.base import ScreenerCandidate
    from agentic_trader.storage.workflow import WorkflowStore


__all__ = [
    "DRIFT_POSITION_CAP_REACHED",
    "CatalogCardSource",
    "CatalogDecision",
    "CatalogPreparation",
    "CatalogRun",
    "PeadDriftSource",
]

# A PEAD event's outcome when apriori.max_open_drift_positions PEAD positions are already open.
DRIFT_POSITION_CAP_REACHED = "drift position cap reached"


@runtime_checkable
class CatalogPreparation(Protocol):
    """What the scan reads of any source's session preparation."""

    @property
    def status(self) -> str: ...  # "ok", or why no card can come from it ("unavailable", "skipped", ...)

    @property
    def session(self) -> date: ...  # the New York session decided

    @property
    def reason(self) -> str | None: ...

    @property
    def events(self) -> tuple[dict, ...]: ...  # each with a "symbol"; carded in this order

    @property
    def elapsed_seconds(self) -> float | None: ...


@dataclass(frozen=True)
class CatalogDecision[P: CatalogPreparation]:
    """One journaled decision: the preparation and every considered event's card outcome by symbol."""

    scan_id: str
    preparation: P
    outcomes: Mapping[str, str]
    # Open positions carrying the source's strategy id; None for a decision no scan reached.
    open_positions: int | None = None


@runtime_checkable
class CatalogCardSource[P: CatalogPreparation](Protocol):
    """A per-session event source whose cards bypass ``setup_quality`` ranking and the native budget."""

    source_id: str  # also the prefix of its log events ("pead" -> "pead_prepare_timeout")
    kind: Literal["probe", "watch"]
    strategy_id: str  # its cards' strategy; budgets and the open-position cap count it
    provenance_key: str  # decision_provenance key holding the card's event document
    journal_kind: EventKind
    summary_key: str  # the scan summary's per-source block ("<key>", "<key>_journal_error")
    # The card payload keyword carrying card_facts: a declared send_signal_alert keyword (the outbox calls
    # it with **arguments), PEAD's historical "drift" or the generic "card_facts".
    notification_key: Literal["drift", "card_facts"]
    # Whether its cards carry the scan's news block like native cards (a watch card) or none (a probe,
    # which tests the unfiltered study rule). Every catalog card is exempt from card_policy, book sizing
    # and the broad-index rule; the scan keys these on the rank's source, never on the candidate.
    news_context: bool
    display_name: str  # log wording
    card_label: str  # log wording: "<display_name> <card_label> preparation ..."
    budget_spent_reason: str  # the event outcome once max_cards_per_session is spent
    position_cap_reason: str  # every event's outcome at max_open_positions

    def max_cards_per_session(self, config: AppConfig) -> int: ...

    def max_open_positions(self, config: AppConfig) -> int: ...

    def max_lateness_seconds(self, config: AppConfig) -> float: ...

    def applies(self, scheduled_time_et: str | None) -> bool:
        """Whether this scheduled slot is the source's decision time and the source can prepare."""
        ...

    def skipped(
        self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date, reason: str
    ) -> P | None:
        """The ``skipped`` decision of a scan that never reached it, or None when it would not have decided."""
        ...

    def unbuilt(self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date) -> P | None:
        """The ``unavailable`` decision when the source failed to build but would have decided now."""
        ...

    def unavailable(self, session: date, reason: str) -> P: ...

    async def prepare(self, *, now: datetime, snapshot: Any, owned: Mapping[str, str]) -> P: ...

    def candidate(self, event: dict, data: Any, preparation: P) -> ScreenerCandidate | str:
        """The event's locked candidate, or the reason there is none (journaled as its outcome)."""
        ...

    def card_facts(self, candidate: ScreenerCandidate, preparation: P) -> dict | None:
        """The card payload's facts under ``notification_key``; None sends none."""
        ...

    async def journal(self, workflows: WorkflowStore, session: AsyncSession, decision: CatalogDecision[P]) -> None:
        """Append the decision (and any notice) inside the caller's locked transaction."""
        ...


@dataclass
class CatalogRun:
    """One source's state within one scan."""

    source: CatalogCardSource[Any]
    open_positions: int  # open positions carrying the source's strategy id
    # At the cap the session's events are still prepared and journaled (the counterfactual set),
    # but none is carded.
    capped: bool
    preparation: Any = None  # the source's CatalogPreparation, or None when it does not decide this scan
    ranked: list[tuple[Any, Any, Any]] = field(default_factory=list)  # admitted (candidate, evaluation, risk)
    outcomes: dict[str, str] = field(default_factory=dict)  # event outcomes by symbol
    remaining: int = 0  # cards this source may still send this session
    start: int = 0  # index of its first candidate in the scan's combined ranking

    @classmethod
    def begin(
        cls, source: CatalogCardSource[Any], positions: Sequence[Mapping[str, Any]], config: AppConfig
    ) -> CatalogRun:
        open_positions = sum(1 for p in positions if p.get("strategy") == source.strategy_id)
        return cls(source, open_positions, open_positions >= source.max_open_positions(config))


class PeadDriftSource:
    """The PEAD long leg's drift cards (catalog paper probe), over ``EarningsDriftService``.

    ``service`` is None when the frozen entry could not be loaded; ``service_error`` then names
    why, and a decision scan with a live catalog probe journals ``service unavailable`` instead
    of going silently dead.
    """

    source_id = "pead"
    kind: Literal["probe", "watch"] = "probe"
    strategy_id = PEAD_STRATEGY_ID
    provenance_key = "pead_event"
    journal_kind = EventKind.PEAD_DECISION
    summary_key = "drift"
    notification_key: Literal["drift", "card_facts"] = "drift"
    news_context = False
    display_name = "PEAD"
    card_label = "drift"
    budget_spent_reason = "drift budget spent"
    position_cap_reason = DRIFT_POSITION_CAP_REACHED

    def __init__(self, service: EarningsDriftService | None = None, *, service_error: str | None = None):
        self.service = service
        self.service_error = service_error

    def max_cards_per_session(self, config: AppConfig) -> int:
        return config.apriori.max_drift_cards_per_session

    def max_open_positions(self, config: AppConfig) -> int:
        return config.apriori.max_open_drift_positions

    def max_lateness_seconds(self, config: AppConfig) -> float:
        return config.apriori.max_drift_lateness_seconds

    def applies(self, scheduled_time_et: str | None) -> bool:
        return self.service is not None and self.service.applies(scheduled_time_et)

    def _unbuilt_at(self, config: AppConfig, scheduled_time_et: str | None) -> bool:
        """An unbuilt service at the frozen entry's decision time (the entry itself could not say)."""
        return (
            self.service is None
            and config.apriori.enabled
            and self.service_error is not None
            and scheduled_time_et == PEAD_DECISION_TIME_ET
        )

    def skipped(
        self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date, reason: str
    ) -> DriftPreparation | None:
        """A scan would otherwise have run drift when its scheduled time is the entry's decision
        time and a live catalog probe is in the snapshot."""
        service = self.service
        if service is not None:
            version = service.live_version(snapshot) if service.applies(scheduled_time_et) else None
        elif self._unbuilt_at(config, scheduled_time_et):
            probes = getattr(snapshot, "catalog_probes", ())
            version = probes[0] if probes else None
        else:
            version = None
        if version is None:
            return None
        return DriftPreparation(
            "skipped",
            session,
            version_id=version.get("version_id"),
            policy=version.get("execution"),
            reason=reason,
        )

    def unbuilt(
        self, config: AppConfig, snapshot: Any, scheduled_time_et: str | None, *, session: date
    ) -> DriftPreparation | None:
        if not (self._unbuilt_at(config, scheduled_time_et) and bool(getattr(snapshot, "catalog_probes", ()))):
            return None
        return DriftPreparation("unavailable", session, reason=f"service unavailable: {self.service_error}")

    def unavailable(self, session: date, reason: str) -> DriftPreparation:
        return DriftPreparation("unavailable", session, reason=reason)

    async def prepare(self, *, now: datetime, snapshot: Any, owned: Mapping[str, str]) -> DriftPreparation:
        if self.service is None:
            return self.unavailable(now.astimezone(ET_TZ).date(), "drift source unavailable")
        return await self.service.prepare(now=now, snapshot=snapshot, owned=owned)

    def candidate(self, event: dict, data: Any, preparation: DriftPreparation) -> ScreenerCandidate | str:
        if self.service is None:  # never reached: only a prepared ("ok") session offers events
            return "drift source unavailable"
        return self.service.candidate(event, data, preparation)

    def card_facts(self, candidate: ScreenerCandidate, preparation: DriftPreparation) -> dict | None:
        if self.service is None or candidate.catalog_event is None:
            return None
        return drift_card_facts(candidate.catalog_event, preparation, self.service.holding_sessions)

    async def journal(
        self, workflows: WorkflowStore, session: AsyncSession, decision: CatalogDecision[DriftPreparation]
    ) -> None:
        """One ``pead_decision`` event, plus the session's one unavailable notice.

        ``events`` holds every event the study would have traded: the considered ones with
        their card outcome, then the owner-skipped ones as ``skipped: <reason>``.
        """
        prep = decision.preparation
        session_date = prep.session.isoformat()
        payload = {
            "scan_id": decision.scan_id,
            "session": session_date,
            "status": prep.status,
            "reason": prep.reason,
            "version_id": prep.version_id,
            "report_date": prep.report_date,
            "page_sha256": prep.page_sha256,
            "counts": prep.counts,
            "skipped": prep.skipped,
            "time_exit_at": prep.time_exit_at,
            "elapsed_seconds": prep.elapsed_seconds,
            "open_drift_positions": decision.open_positions,
            "events": [
                *(
                    {**event, "outcome": decision.outcomes.get(event["symbol"], "not considered")}
                    for event in prep.events
                ),
                *(
                    {**event, "outcome": f"skipped: {prep.skipped.get(event['symbol'], 'owned')}"}
                    for event in prep.skipped_events
                ),
            ],
        }
        await workflows.append(
            session,
            stream=f"scan/{session_date}",
            kind=self.journal_kind,
            payload=payload,
            key=f"pead_decision/{decision.scan_id}",
        )
        if prep.status == "unavailable":
            # Debounced per New York session by the outbox dedup key.
            await workflows.add_notification(
                session,
                f"pead-unavailable/{session_date}",
                NotificationKind.MESSAGE,
                {
                    "text": f"🧪 PEAD probe: no drift card this session — {prep.reason} "
                    f"(report date {prep.report_date or 'unknown'}). Native cards are unaffected.",
                    "formatted": False,
                },
            )
