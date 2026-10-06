"""The evaluator's gates are the shared risk rules (spec decisions 1, 3, 4, 5, 7)."""

import dataclasses
import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.agent.calendar import MacroEvent
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import AssetClass, OrderSide
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.market.session import MarketSessionInfo, MarketSessionType
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, bracket_prices, entry_limit
from agentic_trader.risk import (
    EntryIntent,
    RiskLimits,
    RiskRule,
    meets_min_reward_risk,
    per_trade_risk,
    per_trade_risk_budget,
    required_reward_risk,
)
from tests.agent.test_evaluator import (
    create_candidate as make_candidate,
    create_equity_candidate,
    evaluator_factory,  # noqa: F401  (fixture)
)


def book_row(symbol, direction="LONG", notional=1000.0, risk=50.0, asset_class="EQUITY"):
    return {
        "contract": symbol,
        "symbol": symbol,
        "direction": direction,
        "asset_class": asset_class,
        "notional_value": notional,
        "risk_dollars": risk,
    }


def set_regime_min_rr(evaluator, min_rr: float) -> None:
    """The factory's detector is an ``AsyncMock`` returning one ``RegimeSnapshot``; raise its R:R floor."""
    snapshot = evaluator.regime_detector.get_regime.return_value
    evaluator.regime_detector.get_regime.return_value = dataclasses.replace(snapshot, min_rr_threshold=min_rr)


def set_session(evaluator, *, is_open: bool, is_rth: bool) -> None:
    provider = AsyncMock()
    provider.get_session_info = AsyncMock(
        return_value=MarketSessionInfo(
            symbol="/MES",
            asset_class=AssetClass.FUTURES,
            is_open=is_open,
            is_rth=is_rth,
            session_type=MarketSessionType.RTH if is_rth else MarketSessionType.ETH,
            current_time=datetime.now(UTC),
            details="fixture session",
        )
    )
    evaluator.session_provider = provider


def llm_completion(monkeypatch, **answer) -> AsyncMock:
    content = json.dumps({"approved": True, "macro_clearance": True, "thesis_summary": "fixture", **answer})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", mock)
    return mock


async def test_full_book_is_rejected_at_scan_time(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.max_concurrent_positions = 2
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A"), book_row("B")]
    )
    assert result.approved is False and result.rejection_rule == RiskRule.CONCURRENT_POSITIONS
    assert result.rejection_reason == "Maximum concurrent positions (2) reached."


async def test_a_symbol_the_book_holds_is_rejected_at_scan_time(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    held = {**book_row("MES", asset_class="FUTURES"), "status": "EXECUTED"}  # root spelling of the card's /MES
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False, active_positions=[held])
    assert result.approved is False and result.rejection_rule == RiskRule.SAME_SYMBOL
    assert result.rejection_reason == (
        "Symbol already has a position or entry reservation; adding/netting requires a separate reviewed plan."
    )
    other = {**book_row("/MNQ", asset_class="FUTURES"), "status": "EXECUTED"}
    assert (await evaluator.evaluate_candidate(make_candidate(), use_llm=False, active_positions=[other])).approved


async def test_exhausted_stop_risk_budget_is_rejected_at_scan_time(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.cash = 100_000.0
    evaluator.config.portfolio.max_stop_risk_pct = 0.02
    budget = 100_000.0 * 0.02
    sized = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert sized.approved is True and 0 < sized.risk_dollars < budget
    # Remaining budget is half the candidate's sized risk: the candidate alone would fit an empty book.
    planted = budget - sized.risk_dollars / 2
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A", risk=planted)]
    )
    assert result.approved is False and result.rejection_rule == RiskRule.AGGREGATE_STOP_RISK
    assert result.rejection_reason == "Order would breach the aggregate planned stop-risk budget."
    # With one dollar more than the candidate's risk left, the same book admits it.
    room = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A", risk=budget - sized.risk_dollars - 1.0)]
    )
    assert room.approved is True and room.rejection_rule is None


async def test_opposite_direction_in_group_is_allowed_and_empty_groups_disable(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.portfolio.correlation_groups = {"g": [make_candidate().contract, "OTHER"]}
    evaluator.config.portfolio.max_correlated_positions = 1
    same = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "LONG")]
    )
    assert same.rejection_rule == RiskRule.CORRELATION_GROUP
    assert same.rejection_reason == "Correlation group 'g' already has 1 LONG position(s) (OTHER); max 1."
    opposite = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "SHORT")]
    )
    assert opposite.approved is True
    evaluator.config.portfolio.correlation_groups = {}
    disabled = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("OTHER", "LONG")]
    )
    assert disabled.approved is True  # no fallback to DEFAULT_CORRELATION_GROUPS


async def test_unknown_exposure_fails_closed(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    result = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("A", notional=None)]
    )
    assert result.approved is False and result.rejection_rule == RiskRule.EXPOSURE_UNKNOWN
    assert result.rejection_reason == "Existing exposure is unknown or invalid; reconcile it before new risk."


async def test_portfolio_and_asset_class_caps_use_the_scan_book(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    # /MES: one contract at 5800 x 5 = $29,000 notional; the book's futures notional is what the cap sees.
    evaluator.config.portfolio.max_futures_exposure = 40_000.0
    futures = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("/MGC", notional=20_000.0, asset_class="FUTURES")]
    )
    assert futures.rejection_rule == RiskRule.ASSET_CLASS_NOTIONAL
    assert futures.rejection_reason == "FUTURES notional would reach $49,000, above the $40,000 ceiling."
    evaluator.config.portfolio.max_futures_exposure = 100_000.0
    total = await evaluator.evaluate_candidate(
        make_candidate(), use_llm=False, active_positions=[book_row("/MGC", notional=35_000.0, asset_class="FUTURES")]
    )
    assert total.rejection_rule == RiskRule.PORTFOLIO_NOTIONAL
    assert total.rejection_reason == "Portfolio notional would reach $64,000, above the $60,000 ceiling."


async def test_target_is_built_at_the_regime_adjusted_ratio(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    set_regime_min_rr(evaluator, 2.2)
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert result.approved is True
    assert round(result.target_distance_points / result.stop_distance_points, 2) >= 2.2


async def test_a_short_target_is_built_at_the_regime_adjusted_ratio(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    set_regime_min_rr(evaluator, 2.2)
    result = await evaluator.evaluate_candidate(make_candidate(direction="SHORT"), use_llm=False)
    assert result.approved is True and result.take_profit < result.entry_price < result.stop_loss
    reward, risk = result.entry_price - result.take_profit, result.stop_loss - result.entry_price
    assert meets_min_reward_risk(reward, risk, 2.2) and result.risk_reward_ratio >= 2.2


def _alpha_policy_candidate():
    """A /MES candidate on a versioned 2.0 reward/risk bracket, with its entry and policy bracket."""
    policy = AlphaExecutionPolicy(tick_size=0.25)
    candidate = make_candidate().model_copy(
        update={"alpha_policy": policy.to_dict(), "alpha_version": "frozen-version"}
    )
    entry = entry_limit(candidate.current_price, policy)
    bracket = bracket_prices(
        entry, 1, candidate.atr_14, candidate.recent_swing_low, candidate.recent_swing_high, policy
    )
    return candidate, entry, bracket


async def test_an_alpha_policy_card_keeps_its_policy_bracket_at_the_required_ratio(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    set_regime_min_rr(evaluator, 2.0)
    candidate, entry, policy_bracket = _alpha_policy_candidate()
    deterministic = await evaluator.evaluate_candidate(candidate, use_llm=False)
    assert deterministic.approved is True
    assert (deterministic.stop_loss, deterministic.take_profit) == policy_bracket
    # An LLM bracket never rebuilds a versioned alpha's protection.
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    llm_completion(monkeypatch, stop_loss=entry - 60.0, take_profit=entry + 150.0)
    result = await evaluator.evaluate_candidate(candidate, use_llm=True)
    assert (result.stop_loss, result.take_profit) == policy_bracket


async def test_an_alpha_policy_bracket_below_the_regime_ratio_is_rejected_at_scan_time(
    evaluator_factory,  # noqa: F811
):
    evaluator = evaluator_factory()
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    set_regime_min_rr(evaluator, 2.2)
    candidate, entry, (stop, target) = _alpha_policy_candidate()
    result = await evaluator.evaluate_candidate(candidate, use_llm=False)
    assert result.approved is False and result.rejection_rule == RiskRule.REWARD_RISK
    # The tap-time regime gate would refuse the same card with the same text.
    order = OrderRequest(
        symbol=candidate.contract,
        asset_class=AssetClass.FUTURES,
        direction="LONG",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
    )
    regime = evaluator.regime_detector.get_regime.return_value
    tap_reason = TradingCopilot._regime_gate(
        SimpleNamespace(config=evaluator.config), order, {"strategy": candidate.strategy}, regime
    )
    assert result.rejection_reason == tap_reason == "Reward/risk 2.00 is below the required 2.20."


async def test_llm_target_below_the_regime_ratio_reverts_to_the_regime_adjusted_target(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    evaluator.config.risk.min_risk_reward_ratio = 2.0
    set_regime_min_rr(evaluator, 2.2)
    deterministic = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    # The LLM keeps the deterministic stop but proposes a 2.1 R:R target, below the regime's 2.2.
    stop_distance = deterministic.entry_price - deterministic.stop_loss
    llm_completion(
        monkeypatch, stop_loss=deterministic.stop_loss, take_profit=deterministic.entry_price + 2.1 * stop_distance
    )
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=True)
    assert result.approved is True
    assert result.take_profit == deterministic.take_profit
    assert result.risk_reward_ratio >= 2.2


async def test_extended_hours_allowed_when_rth_not_enforced(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    set_session(evaluator, is_open=True, is_rth=False)
    evaluator.config.session.enforce_rth = True
    rejected = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert rejected.rejection_rule == RiskRule.SESSION_NOT_RTH
    assert rejected.rejection_reason == "Outside regular trading hours." and rejected.session_type == "ETH"
    evaluator.config.session.enforce_rth = False
    assert (await evaluator.evaluate_candidate(make_candidate(), use_llm=False)).approved is True


async def test_closed_session_reason_carries_the_provider_detail(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    set_session(evaluator, is_open=False, is_rth=False)
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert result.rejection_rule == RiskRule.SESSION_CLOSED
    assert result.rejection_reason == "Market session closed: fixture session."


async def test_macro_lockout_reason_is_the_rule_text_on_the_utc_clock(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    # An aware, non-UTC event time: the reason prints its UTC clock.
    event_at = datetime.now(UTC).replace(second=0, microsecond=0).astimezone(ZoneInfo("America/New_York"))
    event = MacroEvent(title="CPI", country="USD", impact="High", timestamp=event_at)
    evaluator.calendar.is_in_lockout_window.return_value = (True, event)
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert result.approved is False and result.rejection_rule == RiskRule.MACRO_LOCKOUT
    assert result.rejection_reason == f"Macro event lockout: CPI at {event_at.astimezone(UTC):%H:%M} UTC."
    assert result.macro_clearance is False


async def test_llm_veto_and_approval_carry_no_rule(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    llm_completion(monkeypatch, approved=False, rejection_reason="thesis too thin")
    vetoed = await evaluator.evaluate_candidate(make_candidate(), use_llm=True)
    assert vetoed.approved is False and vetoed.rejection_rule is None
    assert (await evaluator.evaluate_candidate(make_candidate(), use_llm=False)).rejection_rule is None


async def test_a_rejection_rule_in_the_llm_answer_is_ignored(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    llm_completion(
        monkeypatch, approved=False, rejection_reason="thesis too thin", rejection_rule=RiskRule.MACRO_LOCKOUT.value
    )
    vetoed = await evaluator.evaluate_candidate(make_candidate(), use_llm=True)
    assert vetoed.approved is False and vetoed.rejection_reason == "thesis too thin"
    assert vetoed.rejection_rule is None  # only a shared rule's refusal carries one
    llm_completion(monkeypatch, rejection_rule=RiskRule.CONCURRENT_POSITIONS.value)
    approved = await evaluator.evaluate_candidate(make_candidate(), use_llm=True)
    assert approved.approved is True and approved.rejection_rule is None


async def test_a_bracket_with_a_non_positive_price_is_refused_with_the_admission_text(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    # A $2 share with a $2 ATR: the 1.5 x ATR minimum stop lands at -$1.
    result = await evaluator.evaluate_candidate(
        create_equity_candidate(price=2.0, atr=2.0, swing_low=1.5, swing_high=2.5), use_llm=False
    )
    assert result.approved is False and result.rejection_rule is None
    assert result.rejection_reason == "Quantity and bracket prices must be finite and positive."
    assert result.thesis_summary == (
        "Rejected: the deterministic bracket, quantity or configured multiplier is not finite and positive."
    )


async def test_a_non_finite_configured_multiplier_is_refused_with_the_admission_text(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.contracts["/MES"].multiplier = float("nan")
    result = await evaluator.evaluate_candidate(make_candidate(), use_llm=False)
    assert result.approved is False and result.rejection_rule is None
    assert result.rejection_reason == "Quantity and bracket prices must be finite and positive."
    assert result.thesis_summary == (
        "Rejected: the deterministic bracket, quantity or configured multiplier is not finite and positive."
    )


# --- R7: the deterministic target rounds up to the next tick ---------------------------------


# Unconfigured instruments: an equity trades in 0.01 ticks, a futures contract in 0.25 ticks.
TICKS = ((AssetClass.EQUITY, "ZZEQ", 0.01), (AssetClass.FUTURES, "/ZZFUT", 0.25))


def _levels(evaluator, *, asset_class, contract, direction, entry, tick, atr, ratio):
    """Deterministic levels whose stop is exactly the configured ATR minimum (the structural stop sits at the entry)."""
    candidate = make_candidate(
        contract=contract,
        direction=direction,
        price=entry,
        atr=atr,
        swing_low=entry + 2 * tick,
        swing_high=entry - 2 * tick,
    ).model_copy(update={"asset_class": asset_class})
    return evaluator.calculate_levels_deterministic(candidate, min_reward_risk=ratio)


def test_deterministic_target_meets_the_required_ratio_at_every_tick(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    failures = []
    for asset_class, contract, tick in TICKS:
        for atr_multiple in (1.5, 2.0, 3.1):
            evaluator.config.risk.min_stop_atr_multiple = atr_multiple
            for ratio in (2.0, 2.2, 2.5):
                for step in range(math.ceil((500 - 5) / 0.37)):
                    entry = round(5 + step * 0.37, 2)
                    for direction in ("LONG", "SHORT"):
                        levels = _levels(
                            evaluator,
                            asset_class=asset_class,
                            contract=contract,
                            direction=direction,
                            entry=entry,
                            tick=tick,
                            atr=round(0.02 * entry, 2),
                            ratio=ratio,
                        )
                        rr = round(levels.target_distance / levels.stop_distance, 2)
                        if not (
                            rr >= ratio and meets_min_reward_risk(levels.target_distance, levels.stop_distance, ratio)
                        ):
                            failures.append((contract, direction, entry, atr_multiple, ratio, rr))
    assert failures == []


def test_an_on_tick_target_at_ratio_two_is_the_nearest_tick_result(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    for asset_class, contract, tick in TICKS:
        for atr_multiple in (1.5, 2.0, 3.1):
            evaluator.config.risk.min_stop_atr_multiple = atr_multiple
            for entry in (5.0, 17.25, 99.75, 250.5, 499.0):
                for direction, sign in (("LONG", 1), ("SHORT", -1)):
                    levels = _levels(
                        evaluator,
                        asset_class=asset_class,
                        contract=contract,
                        direction=direction,
                        entry=entry,
                        tick=tick,
                        atr=1.37,
                        ratio=2.0,
                    )
                    previous = round(round((entry + sign * round(levels.stop_distance * 2.0, 2)) / tick) * tick, 2)
                    assert levels.take_profit == previous, (contract, direction, entry, atr_multiple)


def test_sub_penny_entries_meet_the_ratio_on_actual_prices(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    # The Task 4 example: entry 20.004, swing low 19.25 -> stop 19.23 (risk 0.774), regime ratio 2.2.
    example = evaluator.calculate_levels_deterministic(
        create_equity_candidate(price=20.004, atr=0.5, swing_low=19.25), min_reward_risk=2.2
    )
    assert example.stop_loss == 19.23
    assert meets_min_reward_risk(example.take_profit - 20.004, 20.004 - example.stop_loss, 2.2)
    failures = []
    for asset_class, contract, tick in TICKS:
        for atr_multiple in (1.5, 2.0, 3.1):
            evaluator.config.risk.min_stop_atr_multiple = atr_multiple
            for ratio in (2.0, 2.2, 2.5):
                for entry in (5.0049, 7.3651, 20.004, 33.3333, 99.9951, 101.0049, 250.1234, 499.9999):
                    for direction, sign in (("LONG", 1), ("SHORT", -1)):
                        levels = _levels(
                            evaluator,
                            asset_class=asset_class,
                            contract=contract,
                            direction=direction,
                            entry=entry,
                            tick=tick,
                            atr=round(0.02 * entry, 2),
                            ratio=ratio,
                        )
                        reward = sign * (levels.take_profit - entry)
                        risk = sign * (entry - levels.stop_loss)
                        if not meets_min_reward_risk(reward, risk, ratio):
                            failures.append((contract, direction, entry, atr_multiple, ratio, reward / risk))
    assert failures == []


# --- R10: the card as sent passes its own tap gate ------------------------------------------


async def _deterministic_and_llm(evaluator, monkeypatch, candidate, **answer):
    """The deterministic card, then the LLM card for ``answer`` (stop/target as functions of it)."""
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    deterministic = await evaluator.evaluate_candidate(candidate, use_llm=False)
    assert deterministic.approved is True
    llm_completion(monkeypatch, **{key: value(deterministic) for key, value in answer.items()})
    return deterministic, await evaluator.evaluate_candidate(candidate, use_llm=True)


def _wider_than_the_budget(card):
    """A LONG stop whose risk at the card's quantity exceeds the 100,000 x 1% per-trade budget."""
    return card.entry_price - (1000.0 / (5.0 * card.quantity) + 10.0)


def _assert_deterministic_bracket(result, deterministic):
    assert (result.stop_loss, result.take_profit) == (deterministic.stop_loss, deterministic.take_profit)
    assert result.stop_distance_points == deterministic.stop_distance_points
    assert result.target_distance_points == deterministic.target_distance_points
    assert result.risk_reward_ratio == deterministic.risk_reward_ratio
    assert result.risk_dollars == deterministic.risk_dollars
    assert result.reward_dollars == deterministic.reward_dollars


@pytest.mark.parametrize("approved", [True, False])
async def test_llm_stop_widened_beyond_the_budget_restores_the_deterministic_bracket(
    evaluator_factory,  # noqa: F811
    monkeypatch,
    approved,
):
    evaluator = evaluator_factory()
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        make_candidate(),
        approved=lambda card: approved,
        stop_loss=_wider_than_the_budget,
        take_profit=lambda card: card.take_profit,
    )
    assert result.approved is approved
    _assert_deterministic_bracket(result, deterministic)


async def test_llm_stop_widened_and_target_lowered_restores_the_deterministic_bracket(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        make_candidate(),
        stop_loss=_wider_than_the_budget,
        take_profit=lambda card: card.take_profit - 10.0,
    )
    assert result.approved is True
    _assert_deterministic_bracket(result, deterministic)


async def test_llm_bracket_meeting_the_ratio_but_not_the_budget_restores_the_deterministic_bracket(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()

    def target_at_three_r(card):
        return card.entry_price + 3.0 * (card.entry_price - _wider_than_the_budget(card))

    deterministic, result = await _deterministic_and_llm(
        evaluator, monkeypatch, make_candidate(), stop_loss=_wider_than_the_budget, take_profit=target_at_three_r
    )
    _assert_deterministic_bracket(result, deterministic)


async def test_a_valid_tighter_llm_bracket_is_kept(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    # A structural stop 60.5 points away (wider than the 30-point ATR minimum) leaves room to tighten.
    candidate = make_candidate(price=5800.0, atr=20.0, swing_low=5740.0)
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        candidate,
        stop_loss=lambda card: 5765.0,  # 35 points: above the minimum, tighter than 60.5
        take_profit=lambda card: 5875.0,  # 75 points: rr 2.14, tighter than the deterministic target
    )
    assert deterministic.stop_loss == 5739.5 and deterministic.take_profit > 5875.0
    assert (result.stop_loss, result.take_profit) == (5765.0, 5875.0)
    assert result.risk_reward_ratio == 2.14
    assert result.risk_dollars == round(35.0 * 5.0 * result.quantity, 2)
    assert result.approved is True
    # A tightened stop whose target is too close keeps the stop; the target reverts to the
    # deterministic one, and reward/risk, risk and reward dollars all describe that bracket.
    llm_completion(monkeypatch, stop_loss=5765.0, take_profit=5850.0)  # rr 50/35 = 1.43
    reverted = await evaluator.evaluate_candidate(candidate, use_llm=True)
    assert (reverted.stop_loss, reverted.take_profit) == (5765.0, deterministic.take_profit)
    assert reverted.risk_reward_ratio == round(
        reverted.target_distance_points / reverted.stop_distance_points, 2
    ) and reverted.risk_dollars == round(35.0 * 5.0 * reverted.quantity, 2)


async def test_a_non_finite_llm_stop_restores_the_deterministic_bracket(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        make_candidate(),
        approved=lambda card: False,
        rejection_reason=lambda card: "thin thesis",
        stop_loss=lambda card: "NaN",
    )
    # The LLM's verdict stands; only its unusable bracket is replaced.
    assert result.approved is False and result.rejection_reason == "thin thesis"
    _assert_deterministic_bracket(result, deterministic)


async def test_a_non_numeric_llm_stop_falls_back_to_the_deterministic_card(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        make_candidate(),
        approved=lambda card: False,
        rejection_reason=lambda card: "thin thesis",
        stop_loss=lambda card: "abc",
    )
    # An unparseable answer is no LLM verdict: the deterministic fallback approves the card.
    assert result.approved is True and result.llm_verdict is None
    assert "LLM fallback used" in result.thesis_summary
    _assert_deterministic_bracket(result, deterministic)


def _twice_the_stop(card):
    return card.entry_price - 2 * (card.entry_price - card.stop_loss)


def _target_at_two_and_a_half_r_of_twice_the_stop(card):
    return card.entry_price + 2.5 * (card.entry_price - _twice_the_stop(card))


async def test_an_llm_stop_widened_on_a_probe_is_kept_on_tiers_resized_within_the_probe_cap(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    cap = evaluator.config.alpha_pipeline.probe_risk_dollars
    probe = create_equity_candidate(price=100.0, atr=1.0, swing_low=99.0).model_copy(update={"probe": True})
    answer = {"stop_loss": _twice_the_stop, "take_profit": _target_at_two_and_a_half_r_of_twice_the_stop}
    deterministic, result = await _deterministic_and_llm(evaluator, monkeypatch, probe, **answer)
    # The card's per-trade budget, as the evaluator derives it (no observed equity or drawdown).
    regime = evaluator.regime_detector.get_regime.return_value
    budget = per_trade_risk_budget(
        RiskLimits.from_config(evaluator.config),
        equity=None,
        drawdown_pct=0.0,
        macro_multiplier=regime.risk_multiplier,
    )
    # The doubled stop's risk at the deterministic quantity exceeds the probe cap but not the per-trade budget.
    assert deterministic.risk_dollars <= cap < 2 * deterministic.risk_dollars < budget.dollars
    # The LLM's bracket stands on tiers re-sized at its stop: the probe cap still bounds every tier.
    assert (result.stop_loss, result.take_profit) == (
        _twice_the_stop(deterministic),
        _target_at_two_and_a_half_r_of_twice_the_stop(deterministic),
    )
    largest = max(result.sizing_tiers, key=lambda tier: tier["quantity"])
    assert largest["risk_dollars"] <= cap and result.quantity < deterministic.quantity
    # The same answer on a native card (no probe cap) stays within the budget and is kept.
    native, kept = await _deterministic_and_llm(
        evaluator, monkeypatch, probe.model_copy(update={"probe": False}), **answer
    )
    assert (kept.stop_loss, kept.take_profit) == (
        _twice_the_stop(native),
        _target_at_two_and_a_half_r_of_twice_the_stop(native),
    )


async def test_an_llm_widened_probe_stop_with_no_permissible_resized_size_restores_the_deterministic_card(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    # One share at the deterministic 1.50 stop fits a $2 cap; at the doubled 3.00 stop none does.
    evaluator.config.alpha_pipeline.probe_risk_dollars = 2.0
    probe = create_equity_candidate(price=100.0, atr=1.0, swing_low=99.0).model_copy(update={"probe": True})
    levels = _deterministic_levels(evaluator, probe)
    assert levels.quantity == 1 and levels.stop_loss == 98.5
    deterministic, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        probe,
        stop_loss=_twice_the_stop,
        take_profit=_target_at_two_and_a_half_r_of_twice_the_stop,
    )
    _assert_deterministic_bracket(result, deterministic)
    assert result.quantity == levels.quantity and result.sizing_tiers == levels.sizing_tiers


# --- A: the offered tiers match the card as sent -----------------------------------------------


def _deterministic_levels(evaluator, candidate):
    """The scan's deterministic levels for ``candidate`` under the fixture regime, on an empty book."""
    regime = evaluator.regime_detector.get_regime.return_value
    return evaluator.calculate_levels_deterministic(
        candidate,
        macro_risk_multiplier=regime.risk_multiplier,
        min_reward_risk=required_reward_risk(RiskLimits.from_config(evaluator.config), regime.min_rr_threshold),
    )


def _widened(card):
    """A LONG stop 1.4 times the card's stop distance from the entry, on a cent."""
    return round(card.entry_price - 1.4 * (card.entry_price - card.stop_loss), 2)


def _target_at_two_and_a_half_r(card):
    return round(card.entry_price + 2.5 * (card.entry_price - _widened(card)), 2)


def _order(card, quantity):
    return OrderRequest(
        symbol=card.contract,
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        side=OrderSide.BUY,
        quantity=quantity,
        entry_price=card.entry_price,
        stop_loss=card.stop_loss,
        take_profit=card.take_profit,
    )


async def test_an_llm_widened_stop_resizes_every_tier_at_the_final_stop(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    candidate = create_equity_candidate()  # AAPL at 150, deterministic stop 145.98 (4.02)
    levels = _deterministic_levels(evaluator, candidate)
    deterministic, result = await _deterministic_and_llm(
        evaluator, monkeypatch, candidate, stop_loss=_widened, take_profit=_target_at_two_and_a_half_r
    )
    assert (result.stop_loss, result.take_profit) == (
        _widened(deterministic),
        _target_at_two_and_a_half_r(deterministic),
    )
    distance = result.entry_price - result.stop_loss
    tiers = result.sizing_tiers
    assert tiers and all(
        tier["risk_dollars"] == pytest.approx(tier["quantity"] * distance, abs=0.005) for tier in tiers
    )
    # Every tier shrinks; the card's own quantity, risk and notional are the resized default tier's.
    assert [t["quantity"] for t in tiers] != [t["quantity"] for t in levels.sizing_tiers]
    assert max(t["quantity"] for t in tiers) < max(t["quantity"] for t in levels.sizing_tiers)
    [default] = [tier for tier in tiers if tier["is_default"]]
    assert (result.quantity, result.risk_dollars, result.notional_value) == (
        default["quantity"],
        default["risk_dollars"],
        default["notional_dollars"],
    )
    # The max tier passes its own tap gate and admission on an empty book; per_trade_risk holds by construction.
    largest = max(tiers, key=lambda tier: tier["quantity"])
    regime = evaluator.regime_detector.get_regime.return_value
    order = _order(result, largest["quantity"])
    gate = TradingCopilot._regime_gate(
        SimpleNamespace(config=evaluator.config), order, {"strategy": candidate.strategy}, regime
    )
    assert gate is None and reservation_rejection(order, [], evaluator.config) is None
    budget = per_trade_risk_budget(
        RiskLimits.from_config(evaluator.config), equity=None, drawdown_pct=0.0, macro_multiplier=regime.risk_multiplier
    )
    intent = EntryIntent("AAPL", "LONG", "EQUITY", largest["quantity"], result.entry_price, result.stop_loss, 200.0)
    assert per_trade_risk(intent, budget) is None


async def test_a_resized_max_tier_beyond_the_aggregate_budget_restores_the_deterministic_card(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    evaluator.config.portfolio.max_stop_risk_pct = 0.02  # 100,000 x 2% = 2,000 of aggregate planned stop risk
    candidate = create_equity_candidate()
    levels = _deterministic_levels(evaluator, candidate)
    # Room for twice the deterministic default tier's risk: the book gates admit the card, but not a
    # resized max tier near the 1,000 per-trade budget.
    book = [{**book_row("XOM", risk=2_000.0 - 2 * levels.risk_dollars), "status": "EXECUTED"}]
    deterministic = await evaluator.evaluate_candidate(candidate, use_llm=False, active_positions=book)
    assert deterministic.approved is True
    llm_completion(
        monkeypatch, stop_loss=_widened(deterministic), take_profit=_target_at_two_and_a_half_r(deterministic)
    )
    result = await evaluator.evaluate_candidate(candidate, use_llm=True, active_positions=book)
    _assert_deterministic_bracket(result, deterministic)
    assert result.quantity == levels.quantity and result.sizing_tiers == levels.sizing_tiers
    # On an empty book the same answer is kept on resized tiers.
    kept = await evaluator.evaluate_candidate(candidate, use_llm=True)
    assert kept.stop_loss == _widened(deterministic) and kept.sizing_tiers != levels.sizing_tiers


async def test_an_unchanged_llm_bracket_keeps_the_deterministic_tiers(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    candidate = create_equity_candidate()
    levels = _deterministic_levels(evaluator, candidate)
    _, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        candidate,
        stop_loss=lambda card: card.stop_loss,
        take_profit=lambda card: card.take_profit,
    )
    assert (result.stop_loss, result.take_profit) == (levels.stop_loss, levels.take_profit)
    assert result.quantity == levels.quantity and result.sizing_tiers == levels.sizing_tiers


async def test_a_tighter_llm_stop_keeps_the_deterministic_tiers(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    candidate = create_equity_candidate(swing_low=140.0)  # structural stop 139.98: 10.02 below the entry
    levels = _deterministic_levels(evaluator, candidate)
    _, result = await _deterministic_and_llm(
        evaluator,
        monkeypatch,
        candidate,
        stop_loss=lambda card: 146.0,  # 4.00: above the 3.00 ATR minimum, tighter than 10.02
        take_profit=lambda card: 160.0,  # 2.5 R
    )
    assert (result.stop_loss, result.take_profit) == (146.0, 160.0)
    # Sizes never grow because of an LLM edit: the tiers (and their overstated risk) are the deterministic ones.
    assert result.quantity == levels.quantity and result.sizing_tiers == levels.sizing_tiers
    assert all(tier["risk_dollars"] >= tier["quantity"] * 4.0 for tier in result.sizing_tiers)
