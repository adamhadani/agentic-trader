"""The copilot's tap-time paths are thin adapters over ``agentic_trader.risk``.

``_regime_gate`` is ``regime_breakout``, ``reward_risk`` at ``required_reward_risk`` and
``per_trade_risk`` against ``per_trade_risk_budget`` (configured cash, regime multiplier);
``_capped_replacement_quantity`` sizes against the same budget; every entry-side session
decision (tap, re-evaluate, ``/scan``, card validity) is ``entry_session_open``; the macro
lockout text is ``macro_lockout``'s everywhere.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.agent.calendar import MacroEvent
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import AssetClass, SignalStatus, StrategyType
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.market.session import MarketSessionInfo, MarketSessionType
from agentic_trader.risk import RiskLimits, per_trade_risk_budget
from tests.agent.test_card_freshness_tap import (  # noqa: F401  (tap_desk is a fixture)
    finish_reevaluations,
    record_card,
    tap_desk,
    tap_events,
)


CHECKS_UNAVAILABLE = ExecutionReply(False, "⚠️ Checks unavailable; try again shortly.", retryable=True)


@pytest.fixture
def desk(app_config):
    """A copilot holding only ``config``: the regime gate and replacement sizing read nothing else."""
    copilot = object.__new__(TradingCopilot)
    copilot.config = app_config
    return copilot


@pytest.fixture
def request_factory():
    def build(*, quantity=10.0, entry_price=100.0, stop_loss=95.0, take_profit=110.0, direction="LONG"):
        return OrderRequest(
            symbol="CRM",
            asset_class=AssetClass.EQUITY,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    return build


@pytest.fixture
def signal_factory():
    def build(*, strategy=StrategyType.TREND_PULLBACK):
        return {"strategy": strategy}

    return build


def extended_hours(*, next_close=None):
    now = datetime.now(UTC)
    return MarketSessionInfo(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        is_open=True,
        is_rth=False,
        session_type=MarketSessionType.ETH,
        current_time=now,
        next_open=datetime(2026, 9, 24, 13, 30, tzinfo=UTC),
        next_close=next_close,
    )


# --- _regime_gate --------------------------------------------------------------------------


def test_regime_gate_uses_the_shared_budget_and_ratio(desk, request_factory, signal_factory):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.2, risk_multiplier=0.5)
    request = request_factory(quantity=10.0, entry_price=100.0, stop_loss=99.0, take_profit=102.0)  # rr 2.0
    assert (
        desk._regime_gate(request, signal_factory(strategy="TREND_PULLBACK"), regime)
        == "Reward/risk 2.00 is below the required 2.20."
    )
    limits = RiskLimits.from_config(desk.config)
    budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=0.5)
    too_big = request_factory(quantity=budget.dollars / 1.0 + 1, entry_price=100.0, stop_loss=99.0, take_profit=102.2)
    assert desk._regime_gate(too_big, signal_factory(strategy="TREND_PULLBACK"), regime).startswith(
        "Order exceeds the drawdown- and macro-adjusted"
    )
    ok = request_factory(quantity=10.0, entry_price=100.0, stop_loss=99.0, take_profit=102.2)
    assert desk._regime_gate(ok, signal_factory(strategy="TREND_PULLBACK"), regime) is None


def test_regime_gate_with_nan_multiplier_raises_value_error_for_the_tap_to_report(
    desk, request_factory, signal_factory
):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=float("nan"))
    with pytest.raises(ValueError):
        desk._regime_gate(request_factory(), signal_factory(), regime)


def test_regime_gate_suppresses_breakouts_with_the_rule_text(desk, request_factory, signal_factory):
    regime = SimpleNamespace(breakout_allowed=False, min_rr_threshold=2.0, risk_multiplier=1.0)
    assert (
        desk._regime_gate(request_factory(), signal_factory(strategy=StrategyType.SQUEEZE_BREAKOUT), regime)
        == "Volatility/macro policy suppresses breakout entries."
    )
    assert desk._regime_gate(request_factory(), signal_factory(), regime) is None


@pytest.mark.parametrize(
    ("multiplier", "budget_dollars"),
    [
        (1.0, 1000.0),  # 100,000 x 1%: the configured per-trade cap
        (1.5, 1000.0),  # a regime multiplier scales risk down, never up
        (0.05, 100.0),  # ... and never below the 10% floor
        (0.0, 100.0),
    ],
)
def test_regime_gate_budget_clamps_the_regime_multiplier(
    desk, request_factory, signal_factory, multiplier, budget_dollars
):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=multiplier)
    at_budget = request_factory(quantity=budget_dollars, entry_price=100.0, stop_loss=99.0, take_profit=102.0)
    over = request_factory(quantity=budget_dollars + 1, entry_price=100.0, stop_loss=99.0, take_profit=102.0)
    assert desk._regime_gate(at_budget, signal_factory(), regime) is None
    assert desk._regime_gate(over, signal_factory(), regime) is not None


def test_regime_gate_at_the_configured_cap_names_the_configured_cap(desk, request_factory, signal_factory):
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0)
    over = request_factory(quantity=1001.0, entry_price=100.0, stop_loss=99.0, take_profit=102.0)
    assert desk._regime_gate(over, signal_factory(), regime) == "Order exceeds the configured per-trade risk cap."


def test_regime_gate_requires_the_configured_minimum_when_the_regime_threshold_is_lower(
    desk, request_factory, signal_factory
):
    desk.config.risk.min_risk_reward_ratio = 2.5
    regime = SimpleNamespace(breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0)
    request = request_factory(entry_price=100.0, stop_loss=95.0, take_profit=110.0)  # rr 2.0
    assert desk._regime_gate(request, signal_factory(), regime) == "Reward/risk 2.00 is below the required 2.50."


# --- _capped_replacement_quantity -------------------------------------------------------------


def test_replacement_quantity_uses_the_budget(desk):
    regime = SimpleNamespace(risk_multiplier=0.5)
    limits = RiskLimits.from_config(desk.config)
    budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=0.5)
    capped = desk._capped_replacement_quantity(
        10_000.0, price=100.0, stop=99.0, multiplier=1.0, asset_class="EQUITY", regime=regime
    )
    assert capped == float(
        int(
            min(
                budget.dollars / 1.0,
                desk.config.sizing.max_shares_per_trade,
                desk.config.sizing.max_trade_notional_cap / 100.0,
            )
        )
    )


@pytest.mark.parametrize(
    ("multiplier", "expected"),
    [
        (0.5, 100.0),  # budget 500 / unit risk 5
        (0.05, 20.0),  # the 10% floor: budget 100 / 5
        (2.0, 200.0),  # never scaled up: budget 1,000 / 5
    ],
)
def test_replacement_quantity_is_bounded_by_the_clamped_budget(desk, multiplier, expected):
    capped = desk._capped_replacement_quantity(
        10_000.0,
        price=100.0,
        stop=95.0,
        multiplier=1.0,
        asset_class="EQUITY",
        regime=SimpleNamespace(risk_multiplier=multiplier),
    )
    assert capped == expected


def test_replacement_quantity_with_a_nan_multiplier_raises(desk):
    with pytest.raises(ValueError):
        desk._capped_replacement_quantity(
            10.0,
            price=100.0,
            stop=95.0,
            multiplier=1.0,
            asset_class="EQUITY",
            regime=SimpleNamespace(risk_multiplier=float("nan")),
        )


# --- The tap ----------------------------------------------------------------------------------


async def test_a_nan_regime_multiplier_at_tap_is_a_retryable_checks_failure(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db)
    tap_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="calm", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=float("nan")
    )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == CHECKS_UNAVAILABLE
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    tap_desk.entry_service.authorize.assert_not_awaited()
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "unavailable" and "ValueError" in event["payload"]["reason"]


async def test_tap_refuses_a_card_below_the_regime_ratio_with_the_rule_text(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db, target=110.0)  # rr 2.0
    tap_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="elevated", breakout_allowed=True, min_rr_threshold=2.2, risk_multiplier=1.0
    )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == ExecutionReply(False, "⌛ Reward/risk 2.00 is below the required 2.20.", offer_reevaluate=True)
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_tap_refuses_a_size_beyond_the_regime_scaled_budget(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db, quantity=120.0)  # risk 600 > 100,000 x 1% x 0.5
    tap_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="stressed", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=0.5
    )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.text == (
        "⌛ Order exceeds the drawdown- and macro-adjusted per-trade risk cap; request a fresh scan for smaller sizing."
    )
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_one_macro_lockout_text_at_tap_and_at_admission(tap_desk, temp_db):  # noqa: F811
    # An aware, non-UTC event time: the rule prints its UTC clock.
    event_at = datetime.now(UTC).replace(second=0, microsecond=0).astimezone(ZoneInfo("America/New_York"))
    tap_desk.calendar.is_in_lockout_window.return_value = (
        True,
        MacroEvent(title="CPI", country="USD", impact="High", timestamp=event_at),
    )
    text = f"Macro event lockout: CPI at {event_at.astimezone(UTC):%H:%M} UTC."
    sid = await record_card(temp_db)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == ExecutionReply(False, f"⌛ {text}", offer_reevaluate=True)
    tap_desk.entry_service.authorize.assert_not_awaited()
    signal = {"strategy": StrategyType.TREND_PULLBACK}
    request = OrderRequest(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=10.0,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=120.0,
    )
    assert await tap_desk._entry_macro_check(request, signal) == text
    # One clock: the calendar is asked about the same instant the rule judges.
    assert all(
        isinstance(call.kwargs["now"], datetime) and call.kwargs["now"].utcoffset() is not None
        for call in tap_desk.calendar.is_in_lockout_window.await_args_list
    )


async def test_the_rule_not_the_caller_judges_the_lockout_window(tap_desk):  # noqa: F811
    # ``macro_lockout`` re-judges the window at the instant the calendar was asked about:
    # an event a day past is no lockout, whatever the calendar's flag says.
    tap_desk.calendar.is_in_lockout_window.return_value = (
        True,
        MacroEvent(title="CPI", country="USD", impact="High", timestamp=datetime.now(UTC) - timedelta(days=1)),
    )
    assert await tap_desk._macro_lockout_reason() is None


@pytest.mark.parametrize("enforce_rth", [True, False])
async def test_tap_in_extended_hours_follows_enforce_rth(tap_desk, temp_db, app_config, enforce_rth):  # noqa: F811
    app_config.session.enforce_rth = enforce_rth
    tap_desk.session_provider.get_session_info.return_value = extended_hours()
    sid = await record_card(temp_db)

    reply = await tap_desk.execute_signal_by_id(sid)

    [event] = await tap_events(temp_db, sid)
    if enforce_rth:
        assert event["payload"]["outcome"] == "expired"
        assert reply.offer_reevaluate is True and "Next regular open" in reply.text
        assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
        tap_desk.entry_service.authorize.assert_not_awaited()
    else:
        assert event["payload"]["outcome"] == "execute"
        tap_desk.entry_service.authorize.assert_awaited_once()


@pytest.mark.parametrize("enforce_rth", [True, False])
async def test_reevaluate_in_extended_hours_follows_enforce_rth(tap_desk, temp_db, app_config, enforce_rth):  # noqa: F811
    app_config.session.enforce_rth = enforce_rth
    tap_desk.session_provider.get_session_info.return_value = extended_hours()
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)

    reply = await tap_desk.reevaluate_signal(sid)
    await finish_reevaluations(tap_desk)

    if enforce_rth:
        assert reply == ExecutionReply(
            False, "Market closed; Next regular open: 2026-09-24 13:30 UTC (09:30 NY).", retryable=True
        )
        tap_desk.run_scan.assert_not_awaited()
    else:
        assert reply.ok is True
        tap_desk.run_scan.assert_awaited_once()


@pytest.mark.parametrize("enforce_rth", [True, False])
async def test_symbol_scan_in_extended_hours_follows_enforce_rth(tap_desk, app_config, enforce_rth):  # noqa: F811
    app_config.session.enforce_rth = enforce_rth
    tap_desk.session_provider.get_session_info.return_value = extended_hours()
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await tap_desk.request_symbol_scan("SPY")
    await finish_reevaluations(tap_desk)

    if enforce_rth:
        assert reply == ExecutionReply(False, "Market closed; Next regular open: 2026-09-24 13:30 UTC (09:30 NY).")
        tap_desk.run_scan.assert_not_awaited()
    else:
        assert reply == ExecutionReply(True, "🔍 Scanning SPY… a card or a result message will follow.")
        tap_desk.run_scan.assert_awaited_once()


@pytest.mark.parametrize("enforce_rth", [True, False])
async def test_card_validity_in_extended_hours_follows_enforce_rth(tap_desk, app_config, enforce_rth):  # noqa: F811
    app_config.session.enforce_rth = enforce_rth
    close = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    tap_desk.session_provider.get_session_info.return_value = extended_hours(next_close=close)

    valid_until = await tap_desk._card_valid_until("SPY")

    assert valid_until == (None if enforce_rth else close.isoformat())


async def test_card_validity_needs_an_aware_close_and_real_booleans(tap_desk, app_config):  # noqa: F811
    app_config.session.enforce_rth = False
    naive_close = datetime(2026, 9, 24, 20, 0)  # noqa: DTZ001 - intentionally naive, asserting it is ignored
    tap_desk.session_provider.get_session_info.return_value = extended_hours(next_close=naive_close)
    assert await tap_desk._card_valid_until("SPY") is None
    tap_desk.session_provider.get_session_info.return_value = SimpleNamespace(
        is_open="yes", is_rth="yes", next_close=datetime.now(UTC) + timedelta(hours=1)
    )
    assert await tap_desk._card_valid_until("SPY") is None
