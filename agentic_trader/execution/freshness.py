"""Pure card-freshness assessment: re-judge a Telegram card at tap time.

No I/O. Callers (the copilot tap handler) fetch the current price, session state and
gate outcome, and pass them in; this module only decides the outcome and reason text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

from agentic_trader.config import CardFreshnessConfig
from agentic_trader.constants import Direction
from agentic_trader.market.session import ET_TZ


# One bound for every tap-time read (price, session, regime/macro gates, earnings): a
# serialized Telegram handler must never wait on a slow provider indefinitely.
TAP_CHECK_TIMEOUT_SECONDS = 15.0
# How long a background re-evaluation waits for a running scan before reporting "busy".
REEVALUATE_SCAN_WAIT_SECONDS = 120.0


def _aware_et(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Card freshness assessment requires timezone-aware datetimes")
    return value.astimezone(ET_TZ)


def parse_valid_until(raw_valid_until: str | None) -> datetime | None:
    """Parse a ``decision_provenance["valid_until"]`` value shared by the tap path and the sweep.

    Returns ``None`` when the value is absent, unparseable, or offset-naive; every caller
    then falls back to the legacy New-York-date rule via :func:`card_session_over`.
    """
    try:
        value = datetime.fromisoformat(raw_valid_until) if raw_valid_until else None
    except TypeError, ValueError:
        return None
    if value is not None and value.utcoffset() is None:
        return None  # unreadable: fall back to the legacy New York date rule
    return value


def card_session_over(issued_at: datetime, valid_until: datetime | None, now: datetime) -> bool:
    """True once a card's issuing session has ended -- the one expiry rule tap and sweep share.

    A legacy card (no usable ``valid_until``) expires when the New York calendar date has
    moved on from the date it was issued on. All three datetimes must be timezone-aware.
    """
    issued_et, now_et = _aware_et(issued_at), _aware_et(now)
    until_et = _aware_et(valid_until) if valid_until is not None else None
    return now_et >= until_et if until_et is not None else issued_et.date() != now_et.date()


def valid_until_from_provenance(provenance: object) -> datetime | None:
    """Extract and parse ``decision_provenance["valid_until"]``, shared by the tap path and the sweep.

    The one place that knows the provenance key/shape, so a future rename or nesting change
    cannot make the tap and the sweep disagree about which cards are legacy. Non-dict
    ``provenance`` (missing, unreadable, or of an unexpected type) and every failure mode
    :func:`parse_valid_until` handles both fall back to ``None`` -- the legacy New York date
    rule via :func:`card_session_over`.
    """
    raw_valid_until = provenance.get("valid_until") if isinstance(provenance, dict) else None
    return parse_valid_until(raw_valid_until)


def card_is_stale(*, issued_at: datetime, decision_provenance: dict[str, Any] | None, now: datetime) -> bool:
    """The sweep's staleness rule for an untapped ``PENDING`` card.

    Identical to the tap-time rule: extract and parse ``decision_provenance["valid_until"]``
    via the shared :func:`valid_until_from_provenance`, then apply :func:`card_session_over`.
    """
    return card_session_over(issued_at, valid_until_from_provenance(decision_provenance), now)


@dataclass(frozen=True)
class ExecutionReply:
    """The operator-facing result of a card tap (Telegram button or CLI ``execute``)."""

    ok: bool
    text: str  # HTML, as rendered by Telegram
    offer_reevaluate: bool = False
    # True only for refusals that changed nothing and may succeed on a later tap: a card
    # left PENDING (price or tap-time checks unavailable, including timeouts), or a
    # re-evaluation that took no claim (session closed/unavailable, daemon shutting down).
    # Telegram restores the tapped button for these.
    retryable: bool = False


class CardOutcome(StrEnum):
    EXECUTE = "execute"
    REPRICE = "reprice"
    MISSED = "missed"
    EXPIRED = "expired"
    # Journal-only: the tap-time reads (price, session, gates) failed or timed out, so no
    # assessment was possible and the card stayed PENDING. ``assess_card`` never returns it.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CardAssessment:
    outcome: CardOutcome
    reason: str  # operator-facing, plain text
    r_consumed: float | None
    age_seconds: float
    price: float | None


def assess_card(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    issued_at: datetime,
    valid_until: datetime | None,
    now: datetime,
    price: float,
    session_is_rth: bool,
    gate_reason: str | None,
    min_reward_risk: float,
    policy: CardFreshnessConfig,
) -> CardAssessment:
    issued_et, now_et = _aware_et(issued_at), _aware_et(now)
    age_seconds = (now_et - issued_et).total_seconds()

    # Legacy cards without `valid_until` expire when the New York date changes; the sweep
    # applies this exact rule to untapped cards via the same shared helper.
    if card_session_over(issued_at, valid_until, now) or not session_is_rth:
        return CardAssessment(CardOutcome.EXPIRED, "Card expired: its session has ended.", None, age_seconds, None)

    sign = 1 if direction == Direction.LONG else -1
    risk = abs(entry - stop)
    if risk <= 0:
        return CardAssessment(
            CardOutcome.MISSED, "Missed: the card's entry and stop coincide.", None, age_seconds, price
        )
    r_consumed = sign * (price - entry) / risk

    if (direction == Direction.LONG and price <= stop) or (direction == Direction.SHORT and price >= stop):
        reason = f"Missed: price {price:.2f} is at or through the stop at {stop:.2f}."
        return CardAssessment(CardOutcome.MISSED, reason, r_consumed, age_seconds, price)

    if (direction == Direction.LONG and price >= target) or (direction == Direction.SHORT and price <= target):
        reason = f"Missed: price {price:.2f} is at or through the target at {target:.2f}."
        return CardAssessment(CardOutcome.MISSED, reason, r_consumed, age_seconds, price)

    remaining_risk = sign * (price - stop)
    if remaining_risk < policy.reprice_min_risk_fraction * risk:
        return CardAssessment(CardOutcome.MISSED, "Too close to the stop.", r_consumed, age_seconds, price)

    reward_remaining = sign * (target - price)
    actual_reward_risk = reward_remaining / remaining_risk
    if actual_reward_risk < min_reward_risk:
        reason = f"reward:risk at {price:.2f} is {actual_reward_risk:.1f} < {min_reward_risk:.1f}"
        return CardAssessment(CardOutcome.MISSED, reason, r_consumed, age_seconds, price)

    if gate_reason is not None:
        return CardAssessment(CardOutcome.MISSED, gate_reason, r_consumed, age_seconds, price)

    if age_seconds <= policy.fresh_seconds and abs(r_consumed) <= policy.fresh_max_r:
        reason = f"Fresh: {age_seconds:.0f}s old, {r_consumed:+.2f}R since the card."
        return CardAssessment(CardOutcome.EXECUTE, reason, r_consumed, age_seconds, price)

    reason = f"Re-priced at {price:.2f}: {r_consumed:+.2f}R since the card."
    return CardAssessment(CardOutcome.REPRICE, reason, r_consumed, age_seconds, price)


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest multiple of ``tick_size`` (exact decimal arithmetic)."""
    if not tick_size > 0:
        raise ValueError("tick_size must be positive")
    tick = Decimal(str(tick_size))
    return float((Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_HALF_EVEN) * tick)


def reprice_quantity(
    *,
    original_quantity: float,
    entry: float,
    stop: float,
    new_entry: float,
    multiplier: float,
    whole_units: bool,
) -> float:
    """Resize a replacement card to keep the original risk dollars.

    ``multiplier`` (the contract's dollar-per-point value) is applied symmetrically to
    the original and replacement risk/notional, so it cancels out of both ratios below;
    it is kept explicit for clarity and in case a future caller needs asymmetric units.
    """
    new_risk_per_unit = abs(new_entry - stop) * multiplier
    if new_risk_per_unit <= 0:
        return 0.0

    original_risk_dollars = original_quantity * abs(entry - stop) * multiplier
    quantity = original_risk_dollars / new_risk_per_unit

    new_notional_per_unit = new_entry * multiplier
    if new_notional_per_unit > 0:
        original_notional = original_quantity * entry * multiplier
        quantity = min(quantity, original_notional / new_notional_per_unit)

    quantity = max(quantity, 0.0)
    if whole_units:
        quantity = float(int(quantity))
        if quantity < 1:
            return 0.0
    return quantity
