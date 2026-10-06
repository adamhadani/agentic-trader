"""Shared capital and aggregate commitments, before any broker mutation."""

import pytest

from agentic_trader.agent.position_sizing import calculate_dynamic_sizing
from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import ContractConfig, PortfolioConfig
from agentic_trader.constants import AssetClass
from agentic_trader.execution.admission import reservation_rejection


@pytest.fixture
def capacity_order():
    return OrderRequest(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
    )


@pytest.mark.parametrize("side", ["sell", "SELL"])
def test_direction_and_broker_side_cannot_disagree(app_config, capacity_order, side):
    capacity_order.side = side
    reason = reservation_rejection(capacity_order, [], app_config)
    assert reason is not None and "side" in reason.lower()


@pytest.mark.parametrize("existing_risk,drawdown,allowed", [(149, 0, True), (151, 0, False), (51, 0.045, False)])
def test_aggregate_stop_budget_includes_every_reservation(app_config, capacity_order, existing_risk, drawdown, allowed):
    app_config.portfolio.cash = 10000
    app_config.portfolio.max_stop_risk_pct = 0.02
    positions = [{"contract": "IBM", "asset_class": "EQUITY", "notional_value": 1000, "risk_dollars": existing_risk}]
    reason = reservation_rejection(capacity_order, positions, app_config, current_drawdown_pct=drawdown)
    assert (reason is None) is allowed
    if not allowed:
        assert "aggregate" in reason.lower()


@pytest.mark.parametrize(
    "field,value",
    [("notional_value", float("nan")), ("notional_value", -1), ("risk_dollars", None), ("risk_dollars", float("inf"))],
)
def test_unknown_exposure_is_not_zero(app_config, capacity_order, field, value):
    row = {"contract": "IBM", "asset_class": "EQUITY", "notional_value": 1000, "risk_dollars": 10, field: value}
    reason = reservation_rejection(capacity_order, [row], app_config)
    assert reason is not None and "exposure" in reason.lower()


@pytest.mark.parametrize("cash,equity", [(500, None), (10000, 500)])
def test_sizing_never_invents_capital(app_config, cash, equity):
    app_config.portfolio.cash = cash
    result = calculate_dynamic_sizing(10, 1, 2, 1, AssetClass.EQUITY, app_config, current_equity=equity)
    assert result.max_tier.risk_dollars <= 5


def test_explicit_order_risk_is_bounded_by_observed_equity(app_config, capacity_order):
    app_config.portfolio.cash = 100000
    reason = reservation_rejection(capacity_order, [], app_config, current_equity=1000)
    assert reason is not None and "risk cap" in reason.lower()


def test_reward_risk_float_noise_at_the_minimum_is_not_rejected(app_config):
    # Card #20 (2026-09-24 incident): entry 240.46, stop 229.90, target 261.58. Raw
    # floats give (261.58-240.46)/(240.46-229.90) == 1.9999999999999973, strictly below
    # 2.0 by naive float comparison, though the evaluator approved this card at rounded 2.0.
    app_config.risk.min_risk_reward_ratio = 2.0
    order = OrderRequest(
        symbol="CRM",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=10,
        entry_price=240.46,
        stop_loss=229.90,
        take_profit=261.58,
    )
    assert reservation_rejection(order, [], app_config) is None


def test_reward_risk_genuinely_below_minimum_is_rejected(app_config):
    app_config.risk.min_risk_reward_ratio = 2.0
    order = OrderRequest(
        symbol="CRM",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=10,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=119.0,  # risk 10, reward 19 -> rr == 1.9, genuinely below 2.0
    )
    reason = reservation_rejection(order, [], app_config)
    assert reason == "Reward/risk 1.90 is below the required 2.00."


@pytest.mark.parametrize(
    "values",
    [
        {"cash": 0},
        {"cash": float("inf")},
        {"max_stop_risk_pct": 0},
        {"max_stop_risk_pct": 1.1},
        {"max_stop_risk_pct": float("nan")},
    ],
)
def test_capital_policy_is_validated(values):
    with pytest.raises(ValueError):
        PortfolioConfig(**values)


def test_notional_is_priced_at_entry_without_a_current_price(app_config, capacity_order):
    app_config.sizing.max_trade_notional_cap = 1000  # exactly entry 100 x quantity 10
    assert reservation_rejection(capacity_order, [], app_config) is None
    reason = reservation_rejection(capacity_order, [], app_config, current_price=101.0)
    assert reason == "Order exceeds the configured per-trade notional cap."


@pytest.mark.parametrize(
    ("update", "current_price", "expected"),
    [
        ({"entry_price": None}, None, "Quantity and bracket prices must be finite and positive."),
        ({"stop_loss": float("nan")}, None, "Quantity and bracket prices must be finite and positive."),
        ({"take_profit": -1.0}, None, "Quantity and bracket prices must be finite and positive."),
        ({}, float("nan"), "Current exposure price must be finite and positive."),
        ({}, 0.0, "Current exposure price must be finite and positive."),
    ],
)
def test_untrusted_request_numbers_reject_before_any_rule(app_config, capacity_order, update, current_price, expected):
    order = capacity_order.model_copy(update=update)
    assert reservation_rejection(order, [], app_config, current_price=current_price) == expected


@pytest.mark.parametrize(
    ("equity", "drawdown", "cause"),
    [
        (0.0, 0.0, "Observed equity must be finite and positive"),
        (float("nan"), 0.0, "Observed equity must be finite and positive"),
        (None, float("nan"), "Drawdown must be a finite nonnegative ratio"),
        (None, -0.01, "Drawdown must be a finite nonnegative ratio"),
    ],
)
def test_invalid_account_risk_inputs_reject(app_config, capacity_order, equity, drawdown, cause):
    reason = reservation_rejection(capacity_order, [], app_config, current_equity=equity, current_drawdown_pct=drawdown)
    assert reason is not None and reason.startswith("Account risk inputs invalid: ")
    assert reason == f"Account risk inputs invalid: {cause}."


@pytest.mark.parametrize("multiplier", [0.0, -5.0, float("nan"), float("inf"), None])
def test_invalid_configured_multiplier_is_blamed_on_the_configuration(app_config, capacity_order, multiplier):
    contract = ContractConfig(ticker="SPY", name="SPY", asset_class=AssetClass.EQUITY)
    contract.multiplier = multiplier  # the model does not bound it; assignment also reaches None
    app_config.contracts["SPY"] = contract
    assert (
        reservation_rejection(capacity_order, [], app_config)
        == "Configured instrument multiplier must be finite and positive."
    )


def test_asset_class_without_a_configured_cap_is_uncapped(app_config):
    order = OrderRequest(
        symbol="EUR/USD",
        asset_class=AssetClass.FX,
        direction="LONG",
        quantity=10,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
    )
    assert reservation_rejection(order, [], app_config) is None
