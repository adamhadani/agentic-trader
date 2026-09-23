"""Pure card-freshness assessment: re-judge a Telegram card at tap time.

No I/O. Callers (the copilot tap handler) fetch the current price, session state and
gate outcome, and pass them in; this module only decides the outcome and reason text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

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


@dataclass(frozen=True)
class ExecutionReply:
    """The operator-facing result of a card tap (Telegram button or CLI ``execute``)."""

    ok: bool
    text: str  # HTML, as rendered by Telegram
    offer_reevaluate: bool = False
    # True only for refusals that left the card PENDING and may succeed on a later tap
    # (price or tap-time checks unavailable, including timeouts).
    retryable: bool = False


class CardOutcome(StrEnum):
    EXECUTE = "execute"
    REPRICE = "reprice"
    MISSED = "missed"
    EXPIRED = "expired"


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
    until_et = _aware_et(valid_until) if valid_until is not None else None
    age_seconds = (now_et - issued_et).total_seconds()

    # Legacy cards without `valid_until` expire when the New York date changes.
    session_over = now_et >= until_et if until_et is not None else issued_et.date() != now_et.date()
    if session_over or not session_is_rth:
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
