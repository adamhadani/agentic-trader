from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import litellm
from pydantic import BaseModel, Field

from agentic_trader.agent.calendar import BaseEconomicCalendar, ForexFactoryCalendar
from agentic_trader.agent.position_sizing import (
    calculate_dynamic_sizing,
)
from agentic_trader.agent.prompts import USER_EVALUATION_TEMPLATE, build_system_prompt
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.config import DEFAULT_CORRELATION_GROUPS, AppConfig
from agentic_trader.constants import (
    CALLBACK_LANGSMITH,
    AssetClass,
    Direction,
    StrategyType,
)
from agentic_trader.market.session import MarketSessionProtocol
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, bracket_prices, entry_limit
from agentic_trader.screeners.base import ScreenerCandidate


if TYPE_CHECKING:
    from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


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
    sizing_tiers: list[dict[str, Any]] | None = None
    gating_reasons: list[str] | None = None
    session_type: str = "RTH"


class RiskEvaluator:
    def __init__(
        self,
        config: AppConfig,
        calendar: BaseEconomicCalendar | None = None,
        regime_detector: RegimeDetector | None = None,
        data_fetcher: MarketDataFetcher | None = None,
        session_provider: MarketSessionProtocol | None = None,
    ):
        self.config = config
        self.calendar: BaseEconomicCalendar = calendar or ForexFactoryCalendar()
        self.regime_detector: RegimeDetector = regime_detector or RegimeDetector(config=config.regime)
        self.data_fetcher = data_fetcher
        self.session_provider = session_provider
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
    ) -> DeterministicLevels:
        """
        Calculate structural stop loss, 2:1 profit target, and dynamic position sizing deterministically.
        Returns explicit prices, distances, exposure, quantity and sizing tiers.
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

        entry = (
            entry_limit(candidate.current_price, AlphaExecutionPolicy(**candidate.alpha_policy))
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

        if candidate.alpha_policy is not None:
            policy = AlphaExecutionPolicy(**candidate.alpha_policy)
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

        # Position sizing: dynamic calculation supporting static, volatility-targeted, and fractional Kelly modes
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
    ) -> LLMTradeEvaluation:
        # Fetch current volatility and macro regime
        regime = await self.regime_detector.get_regime()

        # Compute deterministic baseline levels and position sizing incorporating macro stress scaling
        levels = self.calculate_levels_deterministic(
            candidate,
            current_open_notional=current_open_notional,
            current_drawdown_pct=current_drawdown_pct,
            macro_risk_multiplier=regime.risk_multiplier,
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
            entry_limit(candidate.current_price, AlphaExecutionPolicy(**candidate.alpha_policy))
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

        # 1. Check the configured portfolio exposure limit
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

        # 1b. Check Asset Class Allocation Cap
        positions_list = active_positions or []
        current_ac_notional = 0.0
        for p in positions_list:
            p_ac = str(p.get("asset_class") or "").upper()
            if p_ac == str(asset_class).upper():
                current_ac_notional += float(p.get("notional_value") or 0.0)

        projected_ac_notional = current_ac_notional + notional_value
        ac_limit: float | None = None
        if asset_class == AssetClass.FUTURES:
            ac_limit = getattr(self.config.portfolio, "max_futures_exposure", None)
        elif asset_class == AssetClass.EQUITY:
            ac_limit = getattr(self.config.portfolio, "max_equity_exposure", None)
        elif asset_class == AssetClass.CRYPTO:
            ac_limit = getattr(self.config.portfolio, "max_crypto_exposure", None)

        if ac_limit is not None and projected_ac_notional > ac_limit:
            return LLMTradeEvaluation(
                approved=False,
                rejection_reason=(
                    f"Asset class limit exceeded: Adding {candidate.contract} (${notional_value:,.2f} {asset_class}) "
                    f"would bring {asset_class} exposure to ${projected_ac_notional:,.2f} "
                    f"(cap is ${ac_limit:,.2f})."
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
                thesis_summary=f"Rejected by risk manager: {asset_class} allocation budget reached.",
                quantity=quantity,
                asset_class=asset_class,
            )

        # 1c. Check Correlation Group Filtering
        corr_groups = getattr(self.config.portfolio, "correlation_groups", None) or DEFAULT_CORRELATION_GROUPS
        cand_syms = {candidate.contract.upper(), candidate.contract.strip("/").upper()}
        max_corr_positions = getattr(self.config.portfolio, "max_correlated_positions", 1)

        for group_name, members in corr_groups.items():
            norm_members = {m.strip("/").upper() for m in members} | {m.upper() for m in members}
            if cand_syms & norm_members:
                matching_active: list[dict[str, Any]] = []
                for pos in positions_list:
                    pos_contract = str(pos.get("contract") or pos.get("symbol") or "")
                    pos_syms = {pos_contract.upper(), pos_contract.strip("/").upper()}
                    if pos_syms & norm_members:
                        pos_dir = str(pos.get("direction", "")).upper()
                        cand_dir = str(candidate.direction).upper()
                        if pos_dir == cand_dir:
                            matching_active.append(pos)

                if len(matching_active) >= max_corr_positions:
                    active_syms = ", ".join(str(p.get("contract") or p.get("symbol")) for p in matching_active)
                    return LLMTradeEvaluation(
                        approved=False,
                        rejection_reason=(
                            f"Correlation limit exceeded: Group '{group_name}' already has {len(matching_active)} "
                            f"active {candidate.direction} position(s) ({active_syms}) "
                            f"(max allowed: {max_corr_positions})."
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
                        thesis_summary=f"Rejected by risk manager: Correlation group '{group_name}' cap reached.",
                        quantity=quantity,
                        asset_class=asset_class,
                    )

        # 1d. Check Statistical Return Correlation (if enabled)
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
                    return LLMTradeEvaluation(
                        approved=False,
                        rejection_reason=(
                            f"Statistical correlation limit exceeded: {candidate.contract} has high return correlation "
                            f"({corr:.2f} >= {max_corr_thresh}) with active position {pos_contract} in the same direction ({candidate.direction})."
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
                        thesis_summary=f"Rejected by risk manager: High return correlation with {pos_contract}.",
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
        if candidate.strategy == StrategyType.SQUEEZE_BREAKOUT and not regime.breakout_allowed:
            macro_detail = ""
            if regime.macro_report and not regime.macro_report.stress.squeeze_breakout_allowed:
                macro_detail = f" & Macro Stress ({regime.macro_report.stress.level.value})"
            return LLMTradeEvaluation(
                approved=False,
                rejection_reason=(
                    f"Volatility Regime Filter: Squeeze breakouts suppressed during {regime.vix_regime.value} "
                    f"regime (VIX: {regime.vix:.1f}{macro_detail})."
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
                    f"Rejected: breakout suppressed due to {regime.vix_regime.value} volatility regime (VIX {regime.vix:.1f}{macro_detail})."
                ),
                quantity=quantity,
                asset_class=asset_class,
            )

        # 4. Check Market Session & Regular Trading Hours (RTH)
        current_session_type = "RTH"
        if self.session_provider:
            session_info = await self.session_provider.get_session_info(candidate.contract)
            current_session_type = str(session_info.session_type.value)
            if not session_info.is_open:
                return LLMTradeEvaluation(
                    approved=False,
                    rejection_reason=f"Market Session Filter: Market is {session_info.session_type.value} ({session_info.details}).",
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
                    thesis_summary=f"Rejected: Market is currently {session_info.session_type.value}.",
                    quantity=quantity,
                    asset_class=asset_class,
                    session_type=current_session_type,
                )
            if getattr(self.config.session, "enforce_rth", True) and not session_info.is_rth:
                return LLMTradeEvaluation(
                    approved=False,
                    rejection_reason=f"RTH Session Filter: Session is {session_info.session_type.value} (Outside Regular Trading Hours).",
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
                    thesis_summary=f"Rejected: Trading restricted to RTH; currently {session_info.session_type.value}.",
                    quantity=quantity,
                    asset_class=asset_class,
                    session_type=current_session_type,
                )

        macro_summary = await self.calendar.get_macro_summary_for_prompt()
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
            )

        # 3. Call LLM for final reasoning & thesis synthesis
        prompt = USER_EVALUATION_TEMPLATE.format(
            timeframe=candidate.timeframe,
            min_stop_atr_multiple=self.config.risk.min_stop_atr_multiple,
            max_notional_exposure=self.config.portfolio.max_notional_exposure,
            min_risk_reward_ratio=max(regime.min_rr_threshold, self.config.risk.min_risk_reward_ratio),
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

            # R:R guarantee >= min_rr
            min_required_rr = max(regime.min_rr_threshold, self.config.risk.min_risk_reward_ratio)
            llm_target = float(data.get("take_profit", take_profit))
            llm_target_dist = abs(llm_target - entry)
            rr = round(llm_target_dist / llm_stop_dist, 2) if llm_stop_dist > 0 else 2.0
            if rr < min_required_rr:
                llm_target = take_profit
                llm_target_dist = target_distance
                rr = round(target_distance / stop_distance, 2)

            if candidate.alpha_policy is not None:
                llm_stop, llm_target = stop_loss, take_profit
                llm_stop_dist, llm_target_dist = stop_distance, target_distance
                rr = target_distance / stop_distance
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
                sizing_tiers=sizing_tiers,
                gating_reasons=gating_reasons,
                session_type=current_session_type,
            )
