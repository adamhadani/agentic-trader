"""One pure function per entry-risk rule; every layer calls these and nothing else.

Inputs are the typed objects from this package (``EntryIntent``, ``Book``, ``RiskBudget``,
``RiskLimits``). A rule returns a ``Rejection`` or ``None`` and never performs I/O: the
calendar, session provider, regime detector and database stay with the callers, which pass
in what they observed. Layers differ only in the book they pass (the scan: open positions
plus this scan's cards; admission: open positions plus reservations).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from agentic_trader.constants import AssetClass, Direction, StrategyType
from agentic_trader.risk.book import Book
from agentic_trader.risk.capital import RiskBudget
from agentic_trader.risk.limits import RiskLimits, normalize_symbol


__all__ = [
    "EntryIntent",
    "Rejection",
    "RiskRule",
    "admission_gates",
    "aggregate_stop_risk",
    "asset_class_notional",
    "book_gates",
    "concurrent_positions",
    "correlation_group",
    "drawdown_halt",
    "earnings_days_out",
    "entry_session_open",
    "exposure_known",
    "in_lockout_window",
    "macro_lockout",
    "meets_min_reward_risk",
    "per_trade_notional",
    "per_trade_risk",
    "portfolio_notional",
    "quantity_cap",
    "regime_breakout",
    "required_reward_risk",
    "reward_risk",
    "same_symbol",
]


class RiskRule(StrEnum):
    """Stable identifier of the rule behind a rejection, for journals and tests."""

    EXPOSURE_UNKNOWN = "exposure_unknown"
    DRAWDOWN_HALT = "drawdown_halt"
    PER_TRADE_RISK = "per_trade_risk"
    PER_TRADE_NOTIONAL = "per_trade_notional"
    QUANTITY_CAP = "quantity_cap"
    REWARD_RISK = "reward_risk"
    AGGREGATE_STOP_RISK = "aggregate_stop_risk"
    CONCURRENT_POSITIONS = "concurrent_positions"
    SAME_SYMBOL = "same_symbol"
    PORTFOLIO_NOTIONAL = "portfolio_notional"
    ASSET_CLASS_NOTIONAL = "asset_class_notional"
    CORRELATION_GROUP = "correlation_group"
    MACRO_LOCKOUT = "macro_lockout"
    EARNINGS_BLACKOUT = "earnings_blackout"
    SESSION_CLOSED = "session_closed"
    SESSION_NOT_RTH = "session_not_rth"
    REGIME_BREAKOUT = "regime_breakout"


@dataclass(frozen=True)
class Rejection:
    """A rule's refusal: the rule id plus the operator-facing reason (``str(rejection)``)."""

    rule: RiskRule
    reason: str

    def __str__(self) -> str:
        return self.reason


def _positive(name: str, value: float) -> float:
    try:
        number = float(value)
    except TypeError, ValueError:
        raise ValueError(f"{name} must be finite and positive") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


@dataclass(frozen=True)
class EntryIntent:
    """What is about to be risked, validated once on construction.

    Direction and asset class are upper-cased; direction must be LONG or SHORT. Quantity,
    bracket prices, multiplier and a known current price must be finite and positive numbers
    (``ValueError`` otherwise, including for ``None`` or a non-numeric value), so the
    constructor is the single validation boundary. An inverted bracket is a valid intent:
    ``reward_risk`` rejects it. Notional is priced at ``max(entry, current_price)`` when a
    current price is known.
    """

    symbol: str
    direction: str
    asset_class: str
    quantity: float
    entry: float
    stop: float
    target: float
    multiplier: float = 1.0
    current_price: float | None = None
    strategy: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", str(self.direction).upper())
        object.__setattr__(self, "asset_class", str(self.asset_class).upper())
        if self.direction not in (Direction.LONG, Direction.SHORT):
            raise ValueError("direction must be LONG or SHORT")
        for name in ("quantity", "entry", "stop", "target", "multiplier"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.current_price is not None:
            object.__setattr__(self, "current_price", _positive("current_price", self.current_price))

    @property
    def key(self) -> str:
        return normalize_symbol(self.symbol)

    @property
    def sign(self) -> float:
        return 1.0 if self.direction == Direction.LONG else -1.0

    @property
    def risk_distance(self) -> float:
        return (self.entry - self.stop) * self.sign

    @property
    def reward_distance(self) -> float:
        return (self.target - self.entry) * self.sign

    @property
    def risk_dollars(self) -> float:
        return max(self.risk_distance, 0.0) * self.quantity * self.multiplier

    @property
    def exposure_price(self) -> float:
        return max(self.entry, self.current_price) if self.current_price is not None else self.entry

    @property
    def notional(self) -> float:
        return self.exposure_price * self.quantity * self.multiplier


# --- Rules -----------------------------------------------------------------------------


def exposure_known(book: Book) -> Rejection | None:
    """Rule ``exposure_unknown``: every position in ``book`` must have a known, finite, non-negative notional and
    planned risk. Runs first at the scan (``book_gates``) and at admission (``admission_gates``), so no numeric
    rule ever judges a book whose exposure is unknown.
    """
    if book.invalid:
        return Rejection(
            RiskRule.EXPOSURE_UNKNOWN, "Existing exposure is unknown or invalid; reconcile it before new risk."
        )
    return None


def drawdown_halt(budget: RiskBudget) -> Rejection | None:
    """Rule ``drawdown_halt``: refuses new entries when the account drawdown in ``budget`` has driven the sizing
    drawdown factor to zero. Called by the scan (``book_gates``) and admission (``admission_gates``).
    """
    if budget.drawdown_factor == 0:
        return Rejection(
            RiskRule.DRAWDOWN_HALT,
            f"Account drawdown {budget.drawdown_pct:.1%} reaches the configured sizing halt; new entries blocked.",
        )
    return None


def per_trade_risk(intent: EntryIntent, budget: RiskBudget) -> Rejection | None:
    """Rule ``per_trade_risk``: the intent's stop risk in dollars must not exceed ``budget.dollars`` (the one
    per-trade budget, already reduced by drawdown and macro factors). Called by admission and by the tap-time
    regime gate.
    """
    if intent.risk_dollars > budget.dollars:
        if budget.drawdown_factor < 1 or budget.macro_factor < 1:
            reason = (
                "Order exceeds the drawdown- and macro-adjusted per-trade risk cap; "
                "request a fresh scan for smaller sizing."
            )
        else:
            reason = "Order exceeds the configured per-trade risk cap."
        return Rejection(RiskRule.PER_TRADE_RISK, reason)
    return None


def per_trade_notional(intent: EntryIntent, limits: RiskLimits) -> Rejection | None:
    """Rule ``per_trade_notional``: the intent's notional, priced at ``max(entry, current_price)``, must not exceed
    ``limits.max_trade_notional_cap``. Called by admission.
    """
    if intent.notional > limits.max_trade_notional_cap:
        return Rejection(RiskRule.PER_TRADE_NOTIONAL, "Order exceeds the configured per-trade notional cap.")
    return None


def quantity_cap(intent: EntryIntent, limits: RiskLimits) -> Rejection | None:
    """Rule ``quantity_cap``: futures intents are capped at ``limits.max_contracts_per_trade``, every other class at
    ``limits.max_shares_per_trade``. Called by admission.
    """
    cap = limits.max_contracts_per_trade if intent.asset_class == AssetClass.FUTURES else limits.max_shares_per_trade
    if intent.quantity > cap:
        return Rejection(RiskRule.QUANTITY_CAP, "Order exceeds the configured quantity cap.")
    return None


def meets_min_reward_risk(reward: float, risk: float, minimum: float) -> bool:
    """Whether a bracket's reward:risk, rounded to two decimals, clears ``minimum``.

    The single comparison shared by every place that re-checks a bracket's reward:risk
    against the configured/regime minimum after the scan already approved it: the
    tap-time current-price check (``execution.freshness.assess_card``) and the
    ``reward_risk`` rule (tap-time regime gate and admission). Two-decimal rounding
    matches the evaluator's own approval rounding (``round(target_dist / stop_dist, 2)``),
    so a card the scan approved at exactly "2.0" is never refused later for float noise
    (e.g. ``21.12 / 10.56 == 1.9999999999999973``, which rounds to ``2.0``).

    ``risk <= 0`` is always False -- a degenerate or inverted bracket never "meets" a
    minimum, regardless of ``reward``.
    """
    if risk <= 0:
        return False
    return round(reward / risk, 2) >= minimum


def required_reward_risk(limits: RiskLimits, regime_min_rr: float | None) -> float:
    """The minimum reward/risk every layer applies: ``max(limits.min_risk_reward_ratio, regime_min_rr)``, or the
    configured minimum alone when the regime threshold is unknown or non-finite. The evaluator builds its
    deterministic target at this ratio; the tap-time regime gate and admission check against it.
    """
    if regime_min_rr is None or not math.isfinite(float(regime_min_rr)):
        return limits.min_risk_reward_ratio
    return max(limits.min_risk_reward_ratio, float(regime_min_rr))


def reward_risk(intent: EntryIntent, required: float) -> Rejection | None:
    """Rule ``reward_risk``: the intent's bracket must meet ``required`` (from ``required_reward_risk``) under the
    two-decimal comparison of ``meets_min_reward_risk``; a degenerate or inverted bracket always fails. Called by
    admission and by the tap-time regime gate.
    """
    if meets_min_reward_risk(intent.reward_distance, intent.risk_distance, required):
        return None
    rr = round(intent.reward_distance / intent.risk_distance, 2) if intent.risk_distance > 0 else 0.0
    return Rejection(RiskRule.REWARD_RISK, f"Reward/risk {rr:.2f} is below the required {required:.2f}.")


def aggregate_stop_risk(intent: EntryIntent, book: Book, budget: RiskBudget, limits: RiskLimits) -> Rejection | None:
    """Rule ``aggregate_stop_risk``: the book's planned stop risk plus the intent's must stay within
    ``budget.capital x limits.max_stop_risk_pct x budget.drawdown_factor``. Called by the scan (``book_gates``) and
    admission (``admission_gates``).
    """
    if book.planned_risk() + intent.risk_dollars > budget.capital * limits.max_stop_risk_pct * budget.drawdown_factor:
        return Rejection(RiskRule.AGGREGATE_STOP_RISK, "Order would breach the aggregate planned stop-risk budget.")
    return None


def concurrent_positions(book: Book, limits: RiskLimits) -> Rejection | None:
    """Rule ``concurrent_positions``: the book (positions and reservations alike) must hold fewer than
    ``limits.max_concurrent_positions`` entries. Called by the scan (``book_gates``) and admission.
    """
    if book.count >= limits.max_concurrent_positions:
        return Rejection(
            RiskRule.CONCURRENT_POSITIONS, f"Maximum concurrent positions ({limits.max_concurrent_positions}) reached."
        )
    return None


def same_symbol(intent: EntryIntent, book: Book) -> Rejection | None:
    """Rule ``same_symbol``: the book must not already hold the intent's normalised symbol as a position or
    reservation. Called by admission.
    """
    if book.holds(intent.symbol):
        return Rejection(
            RiskRule.SAME_SYMBOL,
            "Symbol already has a position or entry reservation; adding/netting requires a separate reviewed plan.",
        )
    return None


def portfolio_notional(intent: EntryIntent, book: Book, limits: RiskLimits) -> Rejection | None:
    """Rule ``portfolio_notional``: the book's notional plus the intent's must not exceed
    ``limits.max_notional_exposure``. Called by the scan (``book_gates``) and admission.
    """
    total, cap = book.notional() + intent.notional, limits.max_notional_exposure
    if total > cap:
        return Rejection(
            RiskRule.PORTFOLIO_NOTIONAL, f"Portfolio notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling."
        )
    return None


def asset_class_notional(intent: EntryIntent, book: Book, limits: RiskLimits) -> Rejection | None:
    """Rule ``asset_class_notional``: the book's notional in the intent's asset class plus the intent's must not
    exceed that class's cap in ``limits.asset_class_caps``; a class without a cap (FX, or a cap configured as
    ``None``) is uncapped. Called by the scan (``book_gates``) and admission.
    """
    cap = limits.asset_class_caps.get(intent.asset_class)
    if cap is None:
        return None
    total = book.notional_for(intent.asset_class) + intent.notional
    if total > cap:
        return Rejection(
            RiskRule.ASSET_CLASS_NOTIONAL,
            f"{intent.asset_class} notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling.",
        )
    return None


def correlation_group(intent: EntryIntent, book: Book, limits: RiskLimits) -> Rejection | None:
    """Rule ``correlation_group``: for each configured group containing the intent's normalised symbol, the book
    must hold fewer than ``limits.max_correlated_positions`` same-direction positions in that group. An empty
    ``limits.correlation_groups`` disables the rule. Called by the scan (``book_gates``) and admission.
    """
    for group, keys in limits.correlation_groups.items():
        if intent.key not in keys:
            continue
        held = book.same_direction_in(keys, intent.direction)
        if len(held) >= limits.max_correlated_positions:
            symbols = ", ".join(p.symbol for p in held)
            return Rejection(
                RiskRule.CORRELATION_GROUP,
                f"Correlation group '{group}' already has {len(held)} {intent.direction} position(s) ({symbols}); "
                f"max {limits.max_correlated_positions}.",
            )
    return None


def in_lockout_window(now: datetime, event_at: datetime, pre_minutes: int, post_minutes: int) -> bool:
    """Whether ``now`` lies in ``[event_at - pre_minutes, event_at + post_minutes]``, inclusive at both ends. The one
    lockout-window predicate: ``macro_lockout`` uses it, and so do the economic calendar and the scan-start check.
    """
    return event_at - timedelta(minutes=pre_minutes) <= now <= event_at + timedelta(minutes=post_minutes)


def macro_lockout(
    event_title: str | None, event_at: datetime | None, now: datetime, limits: RiskLimits
) -> Rejection | None:
    """Rule ``macro_lockout``: refuses an entry while ``now`` is inside the lockout window
    (``limits.lockout_pre_minutes`` / ``limits.lockout_post_minutes``) around the tier-1 event the caller found;
    no event means no lockout. The reason prints the event's UTC clock time, and an untitled event as
    "scheduled release". Called by the scan (evaluator) and by entry admission on both broker branches.
    """
    if event_at is None or not in_lockout_window(
        now, event_at, limits.lockout_pre_minutes, limits.lockout_post_minutes
    ):
        return None
    title = event_title or "scheduled release"
    return Rejection(
        RiskRule.MACRO_LOCKOUT, f"Macro event lockout: {title} at {event_at.astimezone(UTC).strftime('%H:%M')} UTC."
    )


def earnings_days_out(event_date: date | None, today: date, blackout_days: int) -> int | None:
    """Days until the next earnings date when it falls within ``[0, blackout_days]`` of ``today``, else ``None``
    (also ``None`` when there is no event or ``blackout_days <= 0``). The one blackout predicate behind the
    ``earnings_blackout`` reason the scan (evaluator) and the tap format themselves.
    """
    if blackout_days <= 0 or event_date is None:
        return None
    days = (event_date - today).days
    return days if 0 <= days <= blackout_days else None


def entry_session_open(is_open: bool, is_rth: bool, enforce_rth: bool, detail: str = "") -> Rejection | None:
    """Rules ``session_closed`` / ``session_not_rth``: a closed session refuses every entry; an open session outside
    regular hours refuses only when ``enforce_rth`` is set. ``detail`` is appended to the closed-session reason.
    Called by the scan (evaluator), the tap, re-evaluate, ``/scan`` and card validity.
    """
    if not is_open:
        return Rejection(RiskRule.SESSION_CLOSED, f"Market session closed{(': ' + detail) if detail else ''}.")
    if enforce_rth and not is_rth:
        return Rejection(RiskRule.SESSION_NOT_RTH, "Outside regular trading hours.")
    return None


def regime_breakout(strategy: str | None, breakout_allowed: bool) -> Rejection | None:
    """Rule ``regime_breakout``: a squeeze-breakout entry is refused when the volatility/macro regime disallows
    breakouts; every other strategy passes. Called by the scan (evaluator) and the tap-time regime gate.
    """
    if str(strategy).upper() == StrategyType.SQUEEZE_BREAKOUT and not breakout_allowed:
        return Rejection(RiskRule.REGIME_BREAKOUT, "Volatility/macro policy suppresses breakout entries.")
    return None


# --- Composites: the rule order is fixed here once --------------------------------------


def book_gates(intent: EntryIntent, book: Book, budget: RiskBudget, limits: RiskLimits) -> tuple[Rejection, ...]:
    """Every shared book-rule rejection, in order: ``exposure_known``, ``drawdown_halt``, ``aggregate_stop_risk``,
    ``concurrent_positions``, ``portfolio_notional``, ``asset_class_notional``, ``correlation_group``. A book with
    unknown exposure yields the ``exposure_known`` rejection alone: no numeric rule judges it. The scan
    (evaluator) passes its book of open positions plus this scan's cards and reports the first rejection.
    """
    if unknown := exposure_known(book):
        return (unknown,)
    results = (
        drawdown_halt(budget),
        aggregate_stop_risk(intent, book, budget, limits),
        concurrent_positions(book, limits),
        portfolio_notional(intent, book, limits),
        asset_class_notional(intent, book, limits),
        correlation_group(intent, book, limits),
    )
    return tuple(r for r in results if r is not None)


def admission_gates(
    intent: EntryIntent, book: Book, budget: RiskBudget, limits: RiskLimits, *, regime_min_rr: float | None = None
) -> Rejection | None:
    """The first rejection, in order: ``exposure_known``, ``drawdown_halt``, ``reward_risk`` (against
    ``required_reward_risk(limits, regime_min_rr)``), ``per_trade_risk``, ``per_trade_notional``, ``quantity_cap``,
    ``aggregate_stop_risk``, ``concurrent_positions``, ``same_symbol``, ``portfolio_notional``,
    ``asset_class_notional``, ``correlation_group``. Admission passes its book of open positions plus reservations.
    """
    results = (
        exposure_known(book),
        drawdown_halt(budget),
        reward_risk(intent, required_reward_risk(limits, regime_min_rr)),
        per_trade_risk(intent, budget),
        per_trade_notional(intent, limits),
        quantity_cap(intent, limits),
        aggregate_stop_risk(intent, book, budget, limits),
        concurrent_positions(book, limits),
        same_symbol(intent, book),
        portfolio_notional(intent, book, limits),
        asset_class_notional(intent, book, limits),
        correlation_group(intent, book, limits),
    )
    return next((r for r in results if r is not None), None)
