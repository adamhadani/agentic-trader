"""The evaluator's gates are the shared risk rules (spec decisions 1, 3, 4, 5, 7)."""

import dataclasses
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from agentic_trader.agent.calendar import MacroEvent
from agentic_trader.constants import AssetClass
from agentic_trader.market.session import MarketSessionInfo, MarketSessionType
from agentic_trader.risk import RiskRule
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


async def test_a_bracket_with_a_non_positive_price_is_refused_with_the_admission_text(evaluator_factory):  # noqa: F811
    evaluator = evaluator_factory()
    # A $2 share with a $2 ATR: the 1.5 x ATR minimum stop lands at -$1.
    result = await evaluator.evaluate_candidate(
        create_equity_candidate(price=2.0, atr=2.0, swing_low=1.5, swing_high=2.5), use_llm=False
    )
    assert result.approved is False and result.rejection_rule is None
    assert result.rejection_reason == "Quantity and bracket prices must be finite and positive."
