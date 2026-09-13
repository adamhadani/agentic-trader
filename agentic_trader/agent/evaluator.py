import json
import logging
import os
import re

import litellm
from pydantic import BaseModel, Field

from agentic_trader.agent.calendar import BaseEconomicCalendar, EconomicCalendar
from agentic_trader.agent.prompts import SYSTEM_PROMPT, USER_EVALUATION_TEMPLATE
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    CALLBACK_LANGSMITH,
    AssetClass,
    Direction,
    StrategyType,
)
from agentic_trader.screeners.strategies import ScreenerCandidate


logger = logging.getLogger(__name__)


class LLMTradeEvaluation(BaseModel):
    approved: bool
    rejection_reason: str | None = None
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


class RiskEvaluator:
    def __init__(
        self,
        config: AppConfig,
        calendar: BaseEconomicCalendar | None = None,
        regime_detector: RegimeDetector | None = None,
    ):
        self.config = config
        self.calendar: BaseEconomicCalendar = calendar or EconomicCalendar(finnhub_api_key=config.finnhub_api_key)
        self.regime_detector: RegimeDetector = regime_detector or RegimeDetector(config=config.regime)

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
        self, candidate: ScreenerCandidate
    ) -> tuple[float, float, float, float, float, float, float, float]:
        """
        Calculate structural stop loss, 2:1 profit target, and dynamic position sizing deterministically.
        Returns:
            (stop_loss, take_profit, stop_pts, target_pts, risk_dollars, reward_dollars, notional_value, quantity)
        """
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

        entry = candidate.current_price
        min_stop_distance = self.config.risk.min_stop_atr_multiple * candidate.atr_14

        if str(candidate.direction).upper() in ("LONG", str(Direction.LONG)):
            # Anchor behind recent swing low, but enforce >= 1.5 * ATR
            structural_stop = candidate.recent_swing_low - (2 * tick_size)
            stop_distance = max(entry - structural_stop, min_stop_distance)
            # Round to nearest tick
            stop_loss = round(round((entry - stop_distance) / tick_size) * tick_size, 2)
            stop_distance = round(entry - stop_loss, 2)

            target_distance = round(stop_distance * self.config.risk.min_risk_reward_ratio, 2)
            take_profit = round(round((entry + target_distance) / tick_size) * tick_size, 2)
            target_distance = round(take_profit - entry, 2)

        else:  # SHORT
            structural_stop = candidate.recent_swing_high + (2 * tick_size)
            stop_distance = max(structural_stop - entry, min_stop_distance)
            stop_loss = round(round((entry + stop_distance) / tick_size) * tick_size, 2)
            stop_distance = round(stop_loss - entry, 2)

            target_distance = round(stop_distance * self.config.risk.min_risk_reward_ratio, 2)
            take_profit = round(round((entry - target_distance) / tick_size) * tick_size, 2)
            target_distance = round(entry - take_profit, 2)

        # Dynamic position sizing: fixed dollar risk for equities, 1 contract for futures
        if asset_class == AssetClass.EQUITY:
            target_risk = (
                contract_info.target_risk_dollars
                if contract_info and contract_info.target_risk_dollars
                else self.config.portfolio.default_equity_risk_dollars
            )
            per_share_risk = max(stop_distance * multiplier, 0.01)
            quantity = max(1.0, float(int(target_risk / per_share_risk)))
        else:
            quantity = 1.0

        risk_dollars = round(stop_distance * multiplier * quantity, 2)
        reward_dollars = round(target_distance * multiplier * quantity, 2)
        notional_value = round(entry * multiplier * quantity, 2)

        return (
            stop_loss,
            take_profit,
            stop_distance,
            target_distance,
            risk_dollars,
            reward_dollars,
            notional_value,
            quantity,
        )

    async def evaluate_candidate(
        self,
        candidate: ScreenerCandidate,
        current_open_notional: float = 0.0,
        use_llm: bool = True,
    ) -> LLMTradeEvaluation:
        # Compute deterministic baseline levels and position sizing first
        (
            stop_loss,
            take_profit,
            stop_distance,
            target_distance,
            risk_dollars,
            reward_dollars,
            notional_value,
            quantity,
        ) = self.calculate_levels_deterministic(candidate)

        entry = candidate.current_price
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

        # 1. Check Portfolio Exposure Limit ($60,000 max)
        if projected_notional > self.config.portfolio.max_notional_exposure:
            return LLMTradeEvaluation(
                approved=False,
                rejection_reason=f"Exposure limit exceeded: Adding {candidate.contract} (${notional_value:,.2f}) would bring total exposure to ${projected_notional:,.2f} (cap is ${self.config.portfolio.max_notional_exposure:,.2f}).",
                contract=candidate.contract,
                direction=candidate.direction,
                entry_price=entry,
                stop_loss=entry,
                take_profit=entry,
                stop_distance_points=0.0,
                target_distance_points=0.0,
                risk_reward_ratio=2.0,
                risk_dollars=0.0,
                reward_dollars=0.0,
                notional_value=notional_value,
                effective_leverage=effective_leverage,
                macro_clearance=True,
                thesis_summary="Rejected by risk manager: portfolio notional ceiling reached.",
                quantity=quantity,
                asset_class=asset_class,
            )

        # 2. Check Macro Lockout Window
        in_lockout, lock_event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout and lock_event:
            return LLMTradeEvaluation(
                approved=False,
                rejection_reason=f"Macro Event Lockout: Tier-1 release '{lock_event.title}' at {lock_event.timestamp.strftime('%H:%M UTC')}.",
                contract=candidate.contract,
                direction=candidate.direction,
                entry_price=entry,
                stop_loss=entry,
                take_profit=entry,
                stop_distance_points=0.0,
                target_distance_points=0.0,
                risk_reward_ratio=2.0,
                risk_dollars=0.0,
                reward_dollars=0.0,
                notional_value=notional_value,
                effective_leverage=effective_leverage,
                macro_clearance=False,
                thesis_summary=f"Rejected: macro lockout active for '{lock_event.title}'.",
                quantity=quantity,
                asset_class=asset_class,
            )

        # 3. Check Volatility Regime & Adaptive Strategy Suppression
        regime = await self.regime_detector.get_regime()
        if candidate.strategy == StrategyType.SQUEEZE_BREAKOUT and not regime.breakout_allowed:
            return LLMTradeEvaluation(
                approved=False,
                rejection_reason=(
                    f"Volatility Regime Filter: Squeeze breakouts suppressed during {regime.vix_regime.value} "
                    f"regime (VIX: {regime.vix:.1f} > {self.config.regime.vix_extreme_threshold:.1f})."
                ),
                contract=candidate.contract,
                direction=candidate.direction,
                entry_price=entry,
                stop_loss=entry,
                take_profit=entry,
                stop_distance_points=0.0,
                target_distance_points=0.0,
                risk_reward_ratio=2.0,
                risk_dollars=0.0,
                reward_dollars=0.0,
                notional_value=notional_value,
                effective_leverage=effective_leverage,
                macro_clearance=True,
                thesis_summary=(
                    f"Rejected: breakout suppressed due to {regime.vix_regime.value} volatility regime (VIX {regime.vix:.1f})."
                ),
                quantity=quantity,
                asset_class=asset_class,
            )

        macro_summary = await self.calendar.get_macro_summary_for_prompt()
        regime_summary = self.regime_detector.get_prompt_context(regime)

        # If LLM evaluation is disabled or no LLM keys provided, return deterministic evaluation
        has_api_key = bool(
            self.config.openai_api_key
            or self.config.anthropic_api_key
            or self.config.gemini_api_key
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )

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
            )

        # 3. Call LLM for final reasoning & thesis synthesis
        prompt = USER_EVALUATION_TEMPLATE.format(
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
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
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

            # R:R guarantee >= min_rr
            min_required_rr = max(regime.min_rr_threshold, self.config.risk.min_risk_reward_ratio)
            llm_target = float(data.get("take_profit", take_profit))
            llm_target_dist = abs(llm_target - entry)
            rr = round(llm_target_dist / llm_stop_dist, 2) if llm_stop_dist > 0 else 2.0
            if rr < min_required_rr:
                llm_target = take_profit
                llm_target_dist = target_distance
                rr = round(target_distance / stop_distance, 2)

            data["stop_loss"] = llm_stop
            data["take_profit"] = llm_target
            data["stop_distance_points"] = round(llm_stop_dist, 2)
            data["target_distance_points"] = round(llm_target_dist, 2)
            data["risk_reward_ratio"] = max(min_required_rr, rr)
            data["risk_dollars"] = round(llm_stop_dist * multiplier * quantity, 2)
            data["reward_dollars"] = round(llm_target_dist * multiplier * quantity, 2)

            return LLMTradeEvaluation(**data)

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
            )
