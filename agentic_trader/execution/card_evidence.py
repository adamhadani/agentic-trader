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

from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy import select

from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import CARD_STATS_STREAM, CardStatsSnapshot, card_stats_key
from agentic_trader.research.setups.net_excess import NET_EXCESS_UNIT
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
# A shrunk prior mean this close to the raw replay mean is not worth a "(shrunk from ...)" note.
SHRUNK_FROM_MIN_DIFF = 0.005

CAVEAT = (
    "Not validated alpha. Expected excess is measured on journaled candidates' deterministic brackets; "
    "mature = 21+ sessions old; per-name cost \u2265 5 bp/side."
)


class CardEvidence(BaseModel, frozen=True):
    """What one card displays; stored in provenance and in its notification as ``card_evidence``.

    ``posterior_mean`` is the expected excess R in ``net_excess_r_v1`` (a frozen replay prior shrunk
    toward the live record). A key with any ``n_fetch_failed`` or ``n_unresolved`` candidate is partial:
    shown as measured but never withheld (``card_policy.decide``). ``unit`` is None for evidence that
    predates the unit (a stored v1 notification), which renders as no statistics.
    """

    status: EvidenceStatus
    strategy: str
    direction: str
    timeframe: str | None = None
    unit: str | None = None
    n_mature: int | None = None
    min_mature: int = Field(default=0, ge=0)
    n_fetch_failed: int = Field(default=0, ge=0)
    n_unresolved: int = Field(default=0, ge=0)
    target_rate: float | None = None
    stop_rate: float | None = None
    timeout_rate: float | None = None
    mean_r_cost: float | None = None
    mean_timeout_r: float | None = None
    live_mean: float | None = None
    posterior_mean: float | None = None
    posterior_se: float | None = None
    prior_mean: float | None = None
    prior_replay_mean: float | None = None
    prior_n_rows: int = 0
    prior_source: str | None = None
    prior_window_start: date | None = None
    prior_window_end: date | None = None
    prior_feed: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    feed: str | None = None
    cost_bps_per_side: float | None = None
    snapshot_key: str | None = None
    computed_at: AwareDatetime | None = None
    first_decided_at: AwareDatetime | None = None


def lookup(
    snapshot: CardStatsSnapshot | None,
    strategy: str,
    timeframe: str,
    direction: str,
    *,
    now: datetime,
    max_age: timedelta,
    min_mature: int,
) -> CardEvidence:
    """The evidence a card for ``(strategy, timeframe, direction)`` displays, from the latest snapshot in this scope.

    ``unavailable`` without a snapshot or for one not in ``net_excess_r_v1`` (a v1 snapshot read on
    deploy day); ``stale`` past ``max_age``; ``insufficient`` below ``min_mature`` (or no row: the
    aggregate is never a fallback), still carrying the row's prior and posterior; ``unavailable``
    over the floor without rates or a finite posterior; otherwise ``measured``.
    """
    if snapshot is None or snapshot.unit != NET_EXCESS_UNIT:
        return CardEvidence(
            status="unavailable", strategy=strategy, direction=direction, timeframe=timeframe, min_mature=min_mature
        )
    provenance: dict[str, Any] = {
        "strategy": strategy,
        "direction": direction,
        "timeframe": timeframe,
        "unit": snapshot.unit,
        "min_mature": min_mature,
        "window_start": snapshot.window_start,
        "window_end": snapshot.window_end,
        "feed": snapshot.feed,
        "cost_bps_per_side": snapshot.cost_bps_per_side,
        "snapshot_key": snapshot.snapshot_key,
        "computed_at": snapshot.computed_at,
        "prior_window_start": snapshot.prior_window_start,
        "prior_window_end": snapshot.prior_window_end,
        "prior_feed": snapshot.prior_feed,
    }
    stats = snapshot.stats(strategy, timeframe, direction)
    if stats is not None:
        provenance |= {
            "n_fetch_failed": stats.n_fetch_failed,
            "n_unresolved": stats.n_unresolved,
            "first_decided_at": stats.first_decided_at,
            "live_mean": stats.live_mean,
            "posterior_mean": stats.posterior_mean,
            "posterior_se": stats.posterior_se,
            "prior_mean": stats.prior_mean,
            "prior_replay_mean": stats.prior_replay_mean,
            "prior_n_rows": stats.prior_n_rows,
            "prior_source": stats.prior_source,
        }
    if now - snapshot.computed_at > max_age:
        return CardEvidence(status="stale", **provenance)
    n_mature = stats.n_mature if stats is not None else 0
    if stats is None or n_mature < min_mature:
        return CardEvidence(status="insufficient", n_mature=n_mature, **provenance)
    if None in (stats.posterior_mean, stats.target_rate, stats.stop_rate, stats.timeout_rate):
        # Over the floor but nothing measurable (no finite posterior): never "insufficient".
        return CardEvidence(status="unavailable", n_mature=n_mature, **provenance)
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


def _since(evidence: CardEvidence) -> date | None:
    """The New York date of the key's first journaled decision; the window start when unknown."""
    if evidence.first_decided_at is not None:
        return evidence.first_decided_at.astimezone(ET_TZ).date()
    return evidence.window_start


def _years(start: date, end: date) -> str:
    return f"{start.year}\u2013{end.year % 100:02d}"


def _caveat(evidence: CardEvidence) -> str:
    """The italic caveat: names the replay prior's feed and window and the live feed when the evidence carries them.

    The prior's feed is upper-cased without its provider prefix (the shipped prior records ``alpaca:sip``: ``SIP``).
    """
    if (
        evidence.unit is None
        or evidence.prior_feed is None
        or evidence.prior_window_start is None
        or evidence.prior_window_end is None
    ):
        return CAVEAT
    years = _years(evidence.prior_window_start, evidence.prior_window_end)
    prior_feed = evidence.prior_feed.rsplit(":", 1)[-1].upper()
    return (
        f"Not validated alpha. Replay prior ({prior_feed} {years}) and live record ({evidence.feed}) on journaled "
        "candidates' deterministic brackets; mature = 21+ sessions old; per-name cost \u2265 5 bp/side."
    )


def format_evidence_lines(evidence: CardEvidence, *, html: bool) -> list[str]:
    """The card's evidence block, one line per item; the Telegram (``html``) and terminal cards share it."""

    def bold(text: str) -> str:
        return f"<b>{text}</b>" if html else text

    def text(value: object) -> str:
        return escape(str(value), quote=False) if html else str(value)

    n_mature = evidence.n_mature or 0
    headline = None
    if evidence.unit is not None and evidence.posterior_mean is not None and evidence.posterior_se is not None:
        headline = (
            f"\u2022 {bold('Expected excess')} ({text(evidence.strategy)} {text(evidence.timeframe)}, "
            f"{text(evidence.direction)}): {evidence.posterior_mean:+.2f}R \u00b1 {evidence.posterior_se:.2f} "
            "vs beta-matched SPY after cost"
        )
    if evidence.unit is not None and headline is not None and evidence.status in ("measured", "insufficient"):
        if evidence.prior_source == "skeptical" or evidence.prior_mean is None:
            prior_part = "no replay prior (shrunk toward 0)"
        else:
            shrunk = (
                f" (shrunk from {evidence.prior_replay_mean:+.2f}R)"
                if evidence.prior_replay_mean is not None
                and abs(evidence.prior_replay_mean - evidence.prior_mean) >= SHRUNK_FROM_MIN_DIFF
                else ""
            )
            label = "replay"
            if evidence.prior_window_start is not None and evidence.prior_window_end is not None:
                label += f" {_years(evidence.prior_window_start, evidence.prior_window_end)}"
            prior_part = f"{label}: {evidence.prior_mean:+.2f}R{shrunk} over {evidence.prior_n_rows:,} setups"
        live_part = None
        if evidence.status == "measured" and evidence.live_mean is not None and n_mature >= evidence.min_mature:
            live_part = (
                f"live: {evidence.live_mean:+.2f}R over {n_mature} mature since {_since(evidence)} "
                f"({_pct(evidence.target_rate)} target / {_pct(evidence.stop_rate)} stop / "
                f"{_pct(evidence.timeout_rate)} timeout)"
            )
        elif n_mature < evidence.min_mature:
            live_part = f"live: {n_mature}/{evidence.min_mature} mature, not yet decisive"
        if live_part is not None:
            unreadable = evidence.n_fetch_failed + evidence.n_unresolved
            tail = f" \u00b7 {unreadable} unreadable (never withheld)" if unreadable > 0 else ""
            lines = [headline, f"  {prior_part} \u00b7 {live_part}{tail}"]
        else:
            lines = [f"\u2022 {bold('Measured record:')} no statistics in this scope"]
    elif evidence.status == "insufficient" and n_mature < evidence.min_mature:
        lines = [
            f"\u2022 {bold('Measured record:')} insufficient evidence ({n_mature}/{evidence.min_mature} mature candidates)"
        ]
    elif evidence.status == "stale":
        computed = evidence.computed_at.astimezone(ET_TZ).date().isoformat() if evidence.computed_at else "unknown"
        lines = [f"\u2022 {bold('Measured record:')} statistics stale (last computed {computed})"]
    else:
        lines = [f"\u2022 {bold('Measured record:')} no statistics in this scope"]
    caveat = _caveat(evidence)
    lines.append(f"<i>{escape(caveat, quote=False)}</i>" if html else caveat)
    return lines


def format_pead_base_rate_lines(block: dict[str, Any], *, html: bool) -> list[str]:
    """The PEAD card's measured base rate (``pead_base_rate_block``): evidence only, never a gate."""

    def bold(text: str) -> str:
        return f"<b>{text}</b>" if html else text

    unavailable = [f"\u2022 {bold('PEAD base rate:')} unavailable"]
    if block.get("status") != "measured":
        return unavailable
    try:
        window_start = date.fromisoformat(block["window_start"])
        window_end = date.fromisoformat(block["window_end"])
        recent_from = date.fromisoformat(block["recent_from"])
        years = _years(window_start, window_end)
        recent_years = _years(recent_from, window_end)
        caveat = (
            "Not validated alpha. PEAD v2 events replayed on SIP bars in the native card unit; "
            "EPS and consensus as served today, not point-in-time."
        )
        return [
            (
                f"\u2022 {bold('Expected excess')} (PEAD v2, LONG; {recent_years} shrunk toward 0): "
                f"{block['expected_mean']:+.2f}R \u00b1 {block['expected_se']:.2f} "
                f"(se of the {recent_years} mean) vs beta-matched SPY after cost"
            ),
            (
                f"  replay {years}: {block['overall_mean']:+.2f}R over {block['n_leg']:,} events \u00b7 "
                f"{recent_years}: {block['recent_mean']:+.2f}R over {block['n_recent']:,} \u00b7 "
                f"beat vs all reporters {recent_years}: {block['recent_p2_mean']:+.2f}R"
            ),
            f"<i>{caveat}</i>" if html else caveat,
        ]
    except KeyError, ValueError, TypeError:
        return unavailable


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
