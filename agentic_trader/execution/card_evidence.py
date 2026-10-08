"""What a native suggestion card states about its own measured record (docs/card-evidence.md).

``lookup``, ``parse_card_evidence`` and ``format_evidence_lines`` are pure; ``CardStatsRepository``
reads and writes the ``card_stats`` journal stream in this scope. Evidence only: nothing here
ranks, sizes or gates a card (the operator's switch is ``agentic_trader.execution.card_policy``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import CARD_STATS_STREAM, CardStatsSnapshot, card_stats_key
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    from agentic_trader.storage.workflow import WorkflowStore

logger = logging.getLogger(__name__)

__all__ = [
    "CAVEAT",
    "CardEvidence",
    "CardStatsRepository",
    "EvidenceStatus",
    "format_evidence_lines",
    "lookup",
    "parse_card_evidence",
]

EvidenceStatus = Literal["measured", "insufficient", "stale", "unavailable"]
CAVEAT = "Not validated alpha. Record measured on journaled candidates' deterministic brackets at the next hourly open"


class CardEvidence(BaseModel, frozen=True):
    """What one card displays; stored in provenance and in its notification as ``card_evidence``."""

    status: EvidenceStatus
    strategy: str
    direction: str
    n_mature: int | None = None
    min_mature: int = Field(default=0, ge=0)
    target_rate: float | None = None
    stop_rate: float | None = None
    timeout_rate: float | None = None
    mean_r_cost: float | None = None
    mean_timeout_r: float | None = None
    window_start: date | None = None
    window_end: date | None = None
    feed: str | None = None
    cost_bps_per_side: float | None = None
    snapshot_key: str | None = None
    computed_at: datetime | None = None

    def implied_ev(self, rr: float) -> float | None:
        """``target_rate·rr − stop_rate + timeout_rate·mean_timeout_r`` (no timeouts' R counts as 0); None unless measured."""
        if self.status != "measured" or self.target_rate is None or self.stop_rate is None or self.timeout_rate is None:
            return None
        return self.target_rate * rr - self.stop_rate + self.timeout_rate * (self.mean_timeout_r or 0.0)


def lookup(
    snapshot: CardStatsSnapshot | None,
    strategy: str,
    direction: str,
    *,
    now: datetime,
    max_age: timedelta,
    min_mature: int,
) -> CardEvidence:
    """The evidence a card for ``(strategy, direction)`` displays, from the latest snapshot in this scope."""
    if snapshot is None:
        return CardEvidence(status="unavailable", strategy=strategy, direction=direction, min_mature=min_mature)
    provenance: dict[str, Any] = {
        "strategy": strategy,
        "direction": direction,
        "min_mature": min_mature,
        "window_start": snapshot.window_start,
        "window_end": snapshot.window_end,
        "feed": snapshot.feed,
        "cost_bps_per_side": snapshot.cost_bps_per_side,
        "snapshot_key": snapshot.snapshot_key,
        "computed_at": snapshot.computed_at,
    }
    if now - snapshot.computed_at > max_age:
        return CardEvidence(status="stale", **provenance)
    stats = snapshot.stats(strategy, direction)
    n_mature = stats.n_mature if stats is not None else 0
    if stats is None or n_mature < min_mature or stats.mean_r_cost is None:
        return CardEvidence(status="insufficient", n_mature=n_mature, **provenance)
    return CardEvidence(
        status="measured",
        n_mature=n_mature,
        target_rate=stats.target_rate,
        stop_rate=stats.stop_rate,
        timeout_rate=stats.timeout_rate,
        mean_r_cost=stats.mean_r_cost,
        mean_timeout_r=stats.mean_timeout_r,
        **provenance,
    )


def parse_card_evidence(raw: Any, *, strategy: str, direction: str) -> CardEvidence | None:
    """A notification's ``card_evidence``: None when absent; ``unavailable`` when it does not validate.

    A card recorded before the evidence block existed renders without one; a malformed payload
    must never fail a delivery, so it renders as "no statistics".
    """
    if raw is None:
        return None
    try:
        return CardEvidence.model_validate(raw)
    except ValidationError:
        logger.warning(
            "Card evidence does not validate; rendered as unavailable", extra={"event": "card_evidence_invalid"}
        )
        return CardEvidence(status="unavailable", strategy=strategy, direction=direction)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def format_evidence_lines(evidence: CardEvidence, rr: float, *, html: bool) -> list[str]:
    """The card's evidence block, one line per item; the Telegram (``html``) and terminal cards share it."""

    def bold(text: str) -> str:
        return f"<b>{text}</b>" if html else text

    def text(value: object) -> str:
        return escape(str(value), quote=False) if html else str(value)

    ev = evidence.implied_ev(rr)
    if evidence.status == "measured" and ev is not None and evidence.mean_r_cost is not None:
        lines = [
            (
                f"• {bold('Measured record')} ({text(evidence.strategy)}, {text(evidence.direction)}): "
                f"{evidence.n_mature} mature cards since {evidence.window_start}: {_pct(evidence.target_rate)} target / "
                f"{_pct(evidence.stop_rate)} stop / {_pct(evidence.timeout_rate)} timeout, "
                f"mean {evidence.mean_r_cost:+.2f}R after cost"
            ),
            f"• {bold(f'Implied EV at {rr:.1f}:1:')} {ev:+.2f}R",
        ]
    elif evidence.status in ("measured", "insufficient"):
        lines = [
            (
                f"• {bold('Measured record:')} insufficient evidence "
                f"({evidence.n_mature or 0}/{evidence.min_mature} mature cards)"
            )
        ]
    elif evidence.status == "stale":
        computed = evidence.computed_at.astimezone(ET_TZ).date().isoformat() if evidence.computed_at else "unknown"
        lines = [f"• {bold('Measured record:')} statistics stale (last computed {computed})"]
    else:
        lines = [f"• {bold('Measured record:')} no statistics in this scope"]
    source = (
        f" ({evidence.feed}, {evidence.cost_bps_per_side:g} bp/side)."
        if evidence.feed is not None and evidence.cost_bps_per_side is not None
        else "."
    )
    caveat = CAVEAT + source
    lines.append(f"<i>{escape(caveat, quote=False)}</i>" if html else caveat)
    return lines


class CardStatsRepository:
    """The ``card_stats`` journal stream in this scope: one idempotent snapshot per New York date."""

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    async def latest(self) -> CardStatsSnapshot | None:
        """The newest snapshot, or None when there is none or it does not validate (never raises on a payload)."""
        rows = await self.workflows.events(stream=CARD_STATS_STREAM, limit=1)
        if not rows:
            return None
        try:
            return CardStatsSnapshot.model_validate(rows[0]["payload"])
        except ValidationError:
            logger.warning(
                "Card statistics event %s does not validate; treated as no statistics",
                rows[0]["id"],
                extra={"event": "card_stats_invalid", "event_id": rows[0]["id"]},
            )
            return None

    async def exists(self, et_date: date) -> bool:
        key = card_stats_key(et_date)
        async with self.workflows.db.session_factory() as session:
            found = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope, DomainEventRecord.event_key == key
                )
            )
        return found is not None

    async def record(self, payload: dict[str, Any]) -> None:
        """Append one snapshot under its date key, in its own locked transaction; a repeat is a no-op."""
        snapshot = CardStatsSnapshot.model_validate(payload)
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session)
            await self.workflows.append(
                session,
                stream=CARD_STATS_STREAM,
                kind=EventKind.CARD_STATS_SNAPSHOT,
                payload=payload,
                key=snapshot.snapshot_key,
            )
