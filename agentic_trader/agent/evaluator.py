from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import litellm
from pydantic import BaseModel, Field

from agentic_trader.agent.calendar import BaseEconomicCalendar, ForexFactoryCalendar
from agentic_trader.agent.earnings import (
    EarningsCalendarProtocol,
    EarningsLookup,
    earnings_blackout_reason,
    earnings_note,
)
from agentic_trader.agent.position_sizing import (
    calculate_dynamic_sizing,
)
from agentic_trader.agent.prompts import USER_EVALUATION_TEMPLATE, build_system_prompt
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    CALLBACK_LANGSMITH,
    AssetClass,
    Direction,
)
from agentic_trader.market.session import MarketSessionProtocol
from agentic_trader.research.alpha.strategy import bracket_prices, entry_limit, execution_policy_from_dict
from agentic_trader.risk import (
    Book,
    EntryIntent,
    RiskLimits,
    RiskRule,
    book_gates,
    entry_session_open,
    macro_lockout,
    per_trade_risk,
    per_trade_risk_budget,
    regime_breakout,
    required_reward_risk,
    reward_risk,
)
from agentic_trader.screeners.base import ScreenerCandidate


if TYPE_CHECKING:
    from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)

# Admission's text for a request it cannot express as an ``EntryIntent``. The scan refuses
# a card whose deterministic bracket, quantity or configured multiplier (or direction)
# fails ``EntryIntent`` validation with the same reason.
_INVALID_BRACKET = "Quantity and bracket prices must be finite and positive."
# Float noise tolerated, in ticks, when a target is rounded away from the entry to a tick.
_TICK_EPSILON = 1e-6


@dataclass(frozen=True)
class DeterministicLevels:
    stop_loss: float
    take_profit: float
    stop_distance: float
    target_distance: float
    risk_dollars: float
    reward_dollars: float
    notional_value: float
    quantity: float
    sizing_tiers: list[dict[str, Any]] = field(default_factory=list)
    gating_reasons: list[str] = field(default_factory=list)


def _explicit_true(value: Any) -> bool:
    """Only an explicit true approves: ``bool("false")`` would record a veto as approval."""
    return value is True or (isinstance(value, str) and value.lower() == "true")


class LLMTradeEvaluation(BaseModel):
    approved: bool
    rejection_reason: str | None = None
    # The ``agentic_trader.risk.RiskRule`` value of the shared rule that refused the card
    # (``rejection_reason`` is then that rule's text); None for an approval, an LLM veto, a
    # sizing block and the evaluator-only statistical-correlation check.
    rejection_rule: str | None = None
    contract: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    stop_distance_points: float
    target_distance_points: float
    risk_reward_ratio: float = Field(ge=2.0)
    risk_dollars: float
    reward_dollars: float
    notional_value: float
    effective_leverage: float
    macro_clearance: bool
    thesis_summary: str
    quantity: float = 1.0
    asset_class: AssetClass = AssetClass.FUTURES
    sizing_tiers: list[dict[str, Any]] | None = None
    gating_reasons: list[str] | None = None
    session_type: str = "RTH"
    earnings_note: str | None = None
    # Every parsed LLM answer: approved/rejection_reason, the bracket as it would be
    # applied, and whether the verdict decided the card (native) or is commentary (catalog).
    llm_verdict: dict[str, Any] | None = None


class RiskEvaluator:
    def __init__(
        self,
        config: AppConfig,
        calendar: BaseEconomicCalendar | None = None,
        regime_detector: RegimeDetector | None = None,
        data_fetcher: MarketDataFetcher | None = None,
        session_provider: MarketSessionProtocol | None = None,
        earnings_calendar: EarningsCalendarProtocol | None = None,
    ):
        self.config = config
        self.calendar: BaseEconomicCalendar = calendar or ForexFactoryCalendar()
        self.regime_detector: RegimeDetector = regime_detector or RegimeDetector(config=config.regime)
        self.data_fetcher = data_fetcher
        self.session_provider = session_provider
        # None by default: never construct a network client implicitly (tests
        # block network I/O); the gate/note are simply skipped when unset.
        self.earnings_calendar = earnings_calendar
        litellm.drop_params = True

        # Wire up LangSmith tracing if credentials exist in environment
        if os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"):
            if CALLBACK_LANGSMITH not in litellm.success_callback:
                litellm.success_callback.append(CALLBACK_LANGSMITH)
            if CALLBACK_LANGSMITH not in litellm.failure_callback:
                litellm.failure_callback.append(CALLBACK_LANGSMITH)
            logger.info(
                "LangSmith tracing auto-enabled for LiteLLM evaluator",
                extra={"callback": CALLBACK_LANGSMITH, "model": self.config.llm_model},
            )

    def calculate_levels_deterministic(
        self,
        candidate: ScreenerCandidate,
        current_open_notional: float = 0.0,
        current_drawdown_pct: float = 0.0,
        macro_risk_multiplier: float = 1.0,
        current_equity: float | None = None,
        min_reward_risk: float | None = None,
    ) -> DeterministicLevels:
        """
        Calculate structural stop loss, profit target, and dynamic position sizing deterministically.
        Returns explicit prices, distances, exposure, quantity and sizing tiers.

        The target sits at least ``min_reward_risk`` times the actual stop distance from the
        entry, rounded away from the entry to the next tick (a target already on a tick
        stays), so the bracket's two-decimal reward/risk on its actual prices always meets
        the ratio: the scan passes
        ``agentic_trader.risk.required_reward_risk`` (the configured minimum or the regime's,
        whichever is higher); research replay and the backtest pass nothing and get the
        configured ``risk.min_risk_reward_ratio``. A versioned alpha policy's bracket
        replaces both levels.
        """
        ratio = min_reward_risk if min_reward_risk is not None else self.config.risk.min_risk_reward_ratio
        contract_info = self.config.contracts.get(candidate.contract)
        asset_class = (
            getattr(candidate, "asset_class", None)
            or (contract_info.asset_class if contract_info else None)
            or (AssetClass.FUTURES if candidate.contract.startswith("/") else AssetClass.EQUITY)
        )

        if asset_class == AssetClass.EQUITY:
            multiplier = contract_info.multiplier if contract_info else 1.0
            tick_size = contract_info.tick_size if contract_info else 0.01
        else:
            multiplier = contract_info.multiplier if contract_info else 5.0
            tick_size = contract_info.tick_size if contract_info else 0.25

        entry = (
            entry_limit(candidate.current_price, execution_policy_from_dict(candidate.alpha_policy))
            if candidate.alpha_policy
            else candidate.current_price
        )
        min_stop_distance = self.config.risk.min_stop_atr_multiple * candidate.atr_14

        if str(candidate.direction).upper() in ("LONG", str(Direction.LONG)):
            # Anchor behind recent swing low, but enforce >= 1.5 * ATR
            structural_stop = candidate.recent_swing_low - (2 * tick_size)
            stop_distance = max(entry - structural_stop, min_stop_distance)
            # Round to nearest tick
            stop_loss = round(round((entry - stop_distance) / tick_size) * tick_size, 2)
            stop_distance = round(entry - stop_loss, 2)

            # Round the target up to the next tick: never short of ``ratio`` times the actual
            # stop distance (``entry - stop_loss``, not its two-decimal rounding: a sub-penny entry).
            take_profit = round(
                math.ceil((entry + (entry - stop_loss) * ratio) / tick_size - _TICK_EPSILON) * tick_size, 2
            )
            target_distance = round(take_profit - entry, 2)

        else:  # SHORT
            structural_stop = candidate.recent_swing_high + (2 * tick_size)
            stop_distance = max(structural_stop - entry, min_stop_distance)
            stop_loss = round(round((entry + stop_distance) / tick_size) * tick_size, 2)
            stop_distance = round(stop_loss - entry, 2)

            # Round the target down to the next tick: never short of ``ratio`` times the actual
            # stop distance (``stop_loss - entry``, not its two-decimal rounding: a sub-penny entry).
            take_profit = round(
                math.floor((entry - (stop_loss - entry) * ratio) / tick_size + _TICK_EPSILON) * tick_size, 2
            )
            target_distance = round(entry - take_profit, 2)

        if candidate.alpha_policy is not None:
            policy = execution_policy_from_dict(candidate.alpha_policy)
            if abs(policy.tick_size - tick_size) > 1e-9:
                raise ValueError("Alpha execution tick differs from instrument policy; revalidate this version")
            stop_loss, take_profit = bracket_prices(
                entry,
                1 if candidate.direction == Direction.LONG else -1,
                candidate.atr_14,
                candidate.recent_swing_low,
                candidate.recent_swing_high,
                policy,
            )
            stop_distance, target_distance = abs(entry - stop_loss), abs(take_profit - entry)

        # Both supported modes use stop-distance sizing and the shared hard caps.
        risk_dollars_cap = self.config.alpha_pipeline.probe_risk_dollars if candidate.probe else None
        sizing_result = calculate_dynamic_sizing(
            entry=entry,
            stop_distance=stop_distance,
            target_distance=target_distance,
            multiplier=multiplier,
            asset_class=asset_class,
            config=self.config,
            candidate=candidate,
            current_open_notional=current_open_notional,
            current_drawdown_pct=current_drawdown_pct,
            macro_risk_multiplier=macro_risk_multiplier,
            current_equity=current_equity,
            risk_dollars_cap=risk_dollars_cap,
        )
        quantity = sizing_result.default_tier.quantity
        risk_dollars = sizing_result.default_tier.risk_dollars
        reward_dollars = sizing_result.default_tier.reward_dollars
        notional_value = sizing_result.default_tier.notional_dollars
        sizing_tiers = [t.model_dump() for t in sizing_result.tiers]
        gating_reasons = sizing_result.gating_reasons

        return DeterministicLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_distance=stop_distance,
            target_distance=target_distance,
            risk_dollars=risk_dollars,
            reward_dollars=reward_dollars,
            notional_value=notional_value,
            quantity=quantity,
            sizing_tiers=sizing_tiers,
            gating_reasons=gating_reasons,
        )

    async def evaluate_candidate(
        self,
        candidate: ScreenerCandidate,
        current_open_notional: float = 0.0,
        use_llm: bool = True,
        active_positions: list[dict[str, Any]] | None = None,
        current_drawdown_pct: float = 0.0,
        current_equity: float | None = None,
    ) -> LLMTradeEvaluation:
        """Size the candidate, run the deterministic gates, then (optionally) the LLM.

        Gate order, first refusal wins: sizing block; the shared book rules
        (``agentic_trader.risk.book_gates``) on ``active_positions`` -- open positions plus the
        cards this scan already sent; the statistical return-correlation check; macro lockout;
        earnings blackout; regime breakout suppression; session. A refusal by a shared rule
        carries that rule's text and ``rejection_rule``. ``current_open_notional`` bounds the
        sizing notional ceiling; the book's own notional is what the caps judge.
        """
        # One clock for the deterministic lockout gate and the prompt's macro block,
        # so the LLM never sees a verdict that differs from the gate's.
        evaluated_at = datetime.now(UTC)
        # Fetch current volatility and macro regime
        regime = await self.regime_detector.get_regime()
        limits = RiskLimits.from_config(self.config)
        # One reward/risk floor (agentic_trader.risk.required_reward_risk) for the deterministic
        # target and the LLM clamp: the configured minimum or the regime's, whichever is higher.
        required_rr = required_reward_risk(limits, regime.min_rr_threshold)

        # Compute deterministic baseline levels and position sizing incorporating macro stress scaling
        levels = self.calculate_levels_deterministic(
            candidate,
            current_open_notional=current_open_notional,
            current_drawdown_pct=current_drawdown_pct,
            macro_risk_multiplier=regime.risk_multiplier,
            current_equity=current_equity,
            min_reward_risk=required_rr,
        )
        stop_loss = levels.stop_loss
        take_profit = levels.take_profit
        stop_distance = levels.stop_distance
        target_distance = levels.target_distance
        risk_dollars = levels.risk_dollars
        reward_dollars = levels.reward_dollars
        notional_value = levels.notional_value
        quantity = levels.quantity
        sizing_tiers = levels.sizing_tiers
        gating_reasons = levels.gating_reasons

        entry = (
            entry_limit(candidate.current_price, execution_policy_from_dict(candidate.alpha_policy))
            if candidate.alpha_policy
            else candidate.current_price
        )
        contract_info = self.config.contracts.get(candidate.contract)
        asset_class = (
            getattr(candidate, "asset_class", None)
            or (contract_info.asset_class if contract_info else None)
            or (AssetClass.FUTURES if candidate.contract.startswith("/") else AssetClass.EQUITY)
        )
        multiplier = contract_info.multiplier if contract_info else (1.0 if asset_class == AssetClass.EQUITY else 5.0)
        tick_size = contract_info.tick_size if contract_info else (0.01 if asset_class == AssetClass.EQUITY else 0.25)
        effective_leverage = round(notional_value / self.config.portfolio.cash, 2)
        projected_notional = current_open_notional + notional_value

        def _rejected(reason: str, rule: RiskRule | None = None, *, thesis: str, **fields: Any) -> LLMTradeEvaluation:
            """The one rejection shape: a flat bracket at the entry (stop = target = entry), zero
            risk and reward, the sized notional and quantity. ``fields`` override any of these."""
            shape: dict[str, Any] = {
                "approved": False,
                "rejection_reason": reason,
                "rejection_rule": None if rule is None else rule.value,
                "contract": candidate.contract,
                "direction": candidate.direction,
                "entry_price": entry,
                "stop_loss": entry,
                "take_profit": entry,
                "stop_distance_points": 0.0,
                "target_distance_points": 0.0,
                "risk_reward_ratio": 2.0,
                "risk_dollars": 0.0,
                "reward_dollars": 0.0,
                "notional_value": notional_value,
                "effective_leverage": effective_leverage,
                "macro_clearance": True,
                "thesis_summary": thesis,
                "quantity": quantity,
                "asset_class": asset_class,
            }
            return LLMTradeEvaluation(**{**shape, **fields})

        if quantity <= 0:
            return _rejected(
                "Sizing blocked: " + "; ".join(gating_reasons),
                thesis="Rejected by deterministic sizing gates.",
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance_points=stop_distance,
                target_distance_points=target_distance,
                risk_reward_ratio=target_distance / stop_distance if stop_distance > 0 else 0,
                notional_value=0,
                effective_leverage=0,
                quantity=0,
                sizing_tiers=sizing_tiers,
                gating_reasons=gating_reasons,
            )

        # 1. Shared book rules (agentic_trader.risk.book_gates), in their fixed order: exposure_known,
        #    drawdown_halt, aggregate_stop_risk, concurrent_positions, portfolio_notional,
        #    asset_class_notional, correlation_group. The scan's book is ``active_positions``: open
        #    positions plus the cards this scan already sent; the intent is the deterministic bracket.
        positions_list = active_positions or []
        try:
            intent = EntryIntent(
                symbol=candidate.contract,
                direction=str(candidate.direction),
                asset_class=str(asset_class),
                quantity=quantity,
                entry=entry,
                stop=stop_loss,
                target=take_profit,
                multiplier=multiplier,
                strategy=str(candidate.strategy),
            )
        except ValueError:
            return _rejected(
                _INVALID_BRACKET,
                thesis="Rejected: the deterministic bracket, quantity or configured multiplier is not finite and positive.",
            )
        budget = per_trade_risk_budget(
            limits, equity=current_equity, drawdown_pct=current_drawdown_pct, macro_multiplier=regime.risk_multiplier
        )
        if gates := book_gates(intent, Book.from_signal_rows(positions_list, reservations=False), budget, limits):
            return _rejected(gates[0].reason, gates[0].rule, thesis=f"Rejected by risk manager: {gates[0].reason}")

        # 1b. Statistical return correlation (if enabled): evaluator-only, needs the data fetcher;
        #     not a shared rule, so its refusal carries no ``rejection_rule``.
        enable_dyn_corr = getattr(self.config.portfolio, "enable_dynamic_correlation", False)
        max_corr_thresh = getattr(self.config.portfolio, "max_correlation_threshold", 0.85)
        if enable_dyn_corr and self.data_fetcher and positions_list:
            cand_info = self.config.contracts.get(candidate.contract)
            cand_ticker = cand_info.ticker if cand_info else candidate.contract
            for pos in positions_list:
                pos_dir = str(pos.get("direction", "")).upper()
                if pos_dir != str(candidate.direction).upper():
                    continue
                pos_contract = str(pos.get("contract") or pos.get("symbol") or "")
                pos_info = self.config.contracts.get(pos_contract)
                pos_ticker = pos_info.ticker if pos_info else pos_contract
                corr = await asyncio.to_thread(self.data_fetcher.calculate_correlation, cand_ticker, pos_ticker)
                if corr is not None and corr >= max_corr_thresh:
                    return _rejected(
                        f"Statistical correlation limit exceeded: {candidate.contract} has high return correlation "
                        f"({corr:.2f} >= {max_corr_thresh}) with active position {pos_contract} in the same direction ({candidate.direction}).",
                        thesis=f"Rejected by risk manager: High return correlation with {pos_contract}.",
                    )

        # 2. Macro lockout (agentic_trader.risk.macro_lockout): the calendar finds the tier-1 event
        #    whose window contains ``evaluated_at``; its timestamp is aware, and the rule's reason
        #    prints the event's UTC clock time.
        in_lockout, lock_event = await self.calendar.is_in_lockout_window(
            pre_minutes=limits.lockout_pre_minutes,
            post_minutes=limits.lockout_post_minutes,
            now=evaluated_at,
        )
        if (
            in_lockout
            and lock_event
            and (rejection := macro_lockout(lock_event.title, lock_event.timestamp, evaluated_at, limits))
        ):
            return _rejected(
                rejection.reason,
                rejection.rule,
                thesis=f"Rejected: macro lockout active for '{lock_event.title}'.",
                macro_clearance=False,
            )

        # 2b. Earnings blackout (agentic_trader.agent.earnings.earnings_blackout_reason over
        # agentic_trader.risk.earnings_days_out; equities only; None calendar or a 0-day config
        # skips the gate and leaves no note).
        earnings_note_value: str | None = None
        if (
            asset_class == AssetClass.EQUITY
            and limits.earnings_blackout_days > 0
            and self.earnings_calendar is not None
        ):
            blackout_days = limits.earnings_blackout_days
            try:
                earnings_lookup = await self.earnings_calendar.next_earnings(
                    candidate.contract, now=evaluated_at, horizon_days=blackout_days
                )
            except Exception as e:
                logger.warning(
                    "Earnings calendar lookup failed (%s); failing open (no blackout, unverified note).",
                    e,
                    extra={"contract": candidate.contract, "error": str(e)},
                )
                earnings_lookup = EarningsLookup(event=None, verified=False, horizon_end=evaluated_at.date())

            blackout_reason = earnings_blackout_reason(earnings_lookup, candidate.contract, evaluated_at, blackout_days)
            if blackout_reason:
                return _rejected(
                    f"Earnings Blackout: {blackout_reason}",
                    RiskRule.EARNINGS_BLACKOUT,
                    thesis=f"Rejected: earnings blackout for {candidate.contract}.",
                )
            earnings_note_value = earnings_note(earnings_lookup, evaluated_at, blackout_days)

        # 3. Regime breakout suppression (agentic_trader.risk.regime_breakout); the regime detail
        #    stays in the thesis.
        if rejection := regime_breakout(candidate.strategy, regime.breakout_allowed):
            macro_detail = ""
            if regime.macro_report and not regime.macro_report.stress.squeeze_breakout_allowed:
                macro_detail = f" & Macro Stress ({regime.macro_report.stress.level.value})"
            return _rejected(
                rejection.reason,
                rejection.rule,
                thesis=(
                    f"Rejected: breakout suppressed due to {regime.vix_regime.value} volatility regime (VIX {regime.vix:.1f}{macro_detail})."
                ),
                earnings_note=earnings_note_value,
            )

        # 4. Session (agentic_trader.risk.entry_session_open): a closed session refuses; an open
        #    session outside regular hours refuses only when ``session.enforce_rth`` is set.
        current_session_type = "RTH"
        if self.session_provider:
            session_info = await self.session_provider.get_session_info(candidate.contract)
            current_session_type = str(session_info.session_type.value)
            if rejection := entry_session_open(
                session_info.is_open, session_info.is_rth, limits.enforce_rth, detail=str(session_info.details)
            ):
                return _rejected(
                    rejection.reason,
                    rejection.rule,
                    thesis=f"Rejected: market session is {current_session_type}.",
                    session_type=current_session_type,
                    earnings_note=earnings_note_value,
                )

        macro_summary = await self.calendar.get_macro_summary_for_prompt(
            now=evaluated_at,
            pre_minutes=limits.lockout_pre_minutes,
            post_minutes=limits.lockout_post_minutes,
        )
        regime_summary = self.regime_detector.get_prompt_context(regime)

        # If LLM evaluation is disabled or no LLM keys provided, return deterministic evaluation
        has_api_key = bool(self.config.openai_api_key or self.config.anthropic_api_key or self.config.gemini_api_key)

        if not use_llm or not has_api_key:
            return LLMTradeEvaluation(
                approved=True,
                rejection_reason=None,
                contract=candidate.contract,
                direction=candidate.direction,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance_points=stop_distance,
                target_distance_points=target_distance,
                risk_reward_ratio=round(target_distance / stop_distance, 2) if stop_distance > 0 else 2.0,
                risk_dollars=risk_dollars,
                reward_dollars=reward_dollars,
                notional_value=notional_value,
                effective_leverage=effective_leverage,
                macro_clearance=True,
                thesis_summary=(
                    f"Quantitative trigger verified: {candidate.trigger_detail} "
                    f"Macro cleared (Regime: {regime.vix_regime.value}, VIX {regime.vix:.1f})."
                ),
                quantity=quantity,
                asset_class=asset_class,
                earnings_note=earnings_note_value,
            )

        # 3. Call LLM for final reasoning & thesis synthesis
        prompt = USER_EVALUATION_TEMPLATE.format(
            timeframe=candidate.timeframe,
            min_stop_atr_multiple=self.config.risk.min_stop_atr_multiple,
            max_notional_exposure=self.config.portfolio.max_notional_exposure,
            min_risk_reward_ratio=required_rr,
            contract=candidate.contract,
            multiplier=multiplier,
            tick_size=tick_size,
            strategy=candidate.strategy,
            direction=candidate.direction,
            current_price=candidate.current_price,
            ema_20=candidate.ema_20,
            ema_50=candidate.ema_50,
            ema_200=candidate.ema_200,
            rsi_14=candidate.rsi_14,
            atr_14=candidate.atr_14,
            min_stop_distance=self.config.risk.min_stop_atr_multiple * candidate.atr_14,
            recent_swing_low=candidate.recent_swing_low,
            recent_swing_high=candidate.recent_swing_high,
            trigger_detail=candidate.trigger_detail,
            current_open_notional=current_open_notional,
            contract_notional=notional_value,
            projected_notional=projected_notional,
            regime_summary=regime_summary,
            macro_summary=macro_summary,
        )

        try:
            # Configure litellm call
            model_name = self.config.llm_model
            response = await litellm.acompletion(
                api_key=self.config.llm_api_key,
                model=model_name,
                messages=[
                    {"role": "system", "content": build_system_prompt(self.config)},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw_text = response.choices[0].message.content.strip()
            # Clean possible markdown wrapping
            cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
            data = json.loads(cleaned)

            # Enforce hard invariants over LLM values
            data["contract"] = candidate.contract
            data["direction"] = candidate.direction
            data["entry_price"] = entry
            data["notional_value"] = notional_value
            data["effective_leverage"] = effective_leverage
            data["quantity"] = quantity
            data["asset_class"] = asset_class

            # Invalidation guarantee: stop distance >= 1.5 * ATR
            llm_stop = float(data.get("stop_loss", stop_loss))
            llm_stop_dist = abs(entry - llm_stop)
            if llm_stop_dist < (self.config.risk.min_stop_atr_multiple * candidate.atr_14):
                # Clamp to minimum safe distance
                llm_stop = stop_loss
                llm_stop_dist = stop_distance

            # A target below required_rr reverts to the deterministic target (built at required_rr)
            llm_target = float(data.get("take_profit", take_profit))
            llm_target_dist = abs(llm_target - entry)
            rr = round(llm_target_dist / llm_stop_dist, 2) if llm_stop_dist > 0 else 2.0
            if rr < required_rr:
                llm_target = take_profit
                llm_target_dist = target_distance
                # Against the final stop, which may be the LLM's tighter one.
                rr = round(target_distance / llm_stop_dist, 2) if llm_stop_dist > 0 else 0.0

            if candidate.alpha_policy is not None:
                llm_stop, llm_target = stop_loss, take_profit
                llm_stop_dist, llm_target_dist = stop_distance, target_distance
                rr = target_distance / stop_distance

            # Final-bracket check, so the card as sent passes its own tap gate under this regime:
            # a bracket other than the deterministic one that is not a valid ``EntryIntent``,
            # fails ``reward_risk`` at required_rr or exceeds ``per_trade_risk`` for this card's
            # budget (observed equity and drawdown, never above the tap gate's configured-cash
            # budget) -- e.g. a wider stop at the deterministic quantity -- reverts stop and target
            # together to the deterministic bracket (built at required_rr, sized within the budget).
            # A paper probe's bracket also reverts when its risk exceeds
            # ``alpha_pipeline.probe_risk_dollars``: the probe cap may only ever reduce risk.
            if (llm_stop, llm_target) != (stop_loss, take_profit):
                try:
                    final = EntryIntent(
                        symbol=candidate.contract,
                        direction=str(candidate.direction),
                        asset_class=str(asset_class),
                        quantity=quantity,
                        entry=entry,
                        stop=llm_stop,
                        target=llm_target,
                        multiplier=multiplier,
                    )
                    final_passes = (
                        reward_risk(final, required_rr) is None
                        and per_trade_risk(final, budget) is None
                        and not (candidate.probe and final.risk_dollars > self.config.alpha_pipeline.probe_risk_dollars)
                    )
                except ValueError:
                    final_passes = False
                if not final_passes:
                    llm_stop, llm_target = stop_loss, take_profit
                    llm_stop_dist, llm_target_dist = stop_distance, target_distance
                    rr = round(target_distance / stop_distance, 2)
            data["stop_loss"] = llm_stop
            data["take_profit"] = llm_target
            data["stop_distance_points"] = round(llm_stop_dist, 2)
            data["target_distance_points"] = round(llm_target_dist, 2)
            data["risk_reward_ratio"] = rr
            data["risk_dollars"] = round(llm_stop_dist * multiplier * quantity, 2)
            data["reward_dollars"] = round(llm_target_dist * multiplier * quantity, 2)
            data["sizing_tiers"] = sizing_tiers
            data["gating_reasons"] = gating_reasons
            data["session_type"] = current_session_type

            data.pop("llm_verdict", None)  # only ever set below, never taken from the LLM
            data.pop("rejection_rule", None)  # a shared rule's id; the LLM never supplies one
            applied = candidate.catalog_event is None
            data["llm_verdict"] = {
                "approved": _explicit_true(data.get("approved")),
                "rejection_reason": data.get("rejection_reason"),
                "stop_loss": llm_stop,
                "take_profit": llm_target,
                "applied": applied,
            }
            if not applied:
                # A catalog probe tests the unfiltered study rule: the verdict is recorded,
                # its text shown as commentary, and it never vetoes (deterministic gates already ran).
                data["approved"], data["rejection_reason"] = True, None
                data["thesis_summary"] = f"LLM commentary (not a gate): {data.get('thesis_summary') or ''}".strip()

            # Never ask the LLM for the earnings note (prompt/schema stay untouched);
            # attach it deterministically after the LLM result is parsed.
            evaluation = LLMTradeEvaluation(**data).model_copy(update={"earnings_note": earnings_note_value})
            if applied and evaluation.llm_verdict is not None:
                # Record exactly the decision taken, after pydantic's own bool parsing.
                evaluation.llm_verdict["approved"] = evaluation.approved
            return evaluation

        except Exception as e:
            logger.warning(
                "LLM evaluation failed (%s), falling back to deterministic risk engine.",
                e,
                extra={"contract": candidate.contract, "strategy": candidate.strategy, "error": str(e)},
            )
            return LLMTradeEvaluation(
                approved=True,
                rejection_reason=None,
                contract=candidate.contract,
                direction=candidate.direction,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                stop_distance_points=stop_distance,
                target_distance_points=target_distance,
                risk_reward_ratio=round(target_distance / stop_distance, 2),
                risk_dollars=risk_dollars,
                reward_dollars=reward_dollars,
                notional_value=notional_value,
                effective_leverage=effective_leverage,
                macro_clearance=True,
                thesis_summary=f"Automated thesis: {candidate.trigger_detail} (LLM fallback used: {e})",
                quantity=quantity,
                asset_class=asset_class,
                sizing_tiers=sizing_tiers,
                gating_reasons=gating_reasons,
                session_type=current_session_type,
                earnings_note=earnings_note_value,
            )
