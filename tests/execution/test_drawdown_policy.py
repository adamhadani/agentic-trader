"""Every suggestion and explicit quantity must obey the same drawdown cap."""

import pytest

from agentic_trader.agent.position_sizing import calculate_dynamic_sizing
from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import PositionSizingConfig
from agentic_trader.constants import AssetClass
from agentic_trader.execution.admission import reservation_rejection


@pytest.mark.parametrize("mode", ["static", "volatility_targeted"])
@pytest.mark.parametrize("asset,multiplier", [(AssetClass.EQUITY, 1), (AssetClass.FUTURES, 5)])
def test_haircut_caps_every_tier(app_config, mode, asset, multiplier):
    app_config.portfolio.cash = 10000
    app_config.portfolio.max_notional_exposure = 1000000
    app_config.sizing.mode = mode
    app_config.sizing.max_trade_notional_cap = 1000000
    app_config.sizing.max_contracts_per_trade = 100
    result = calculate_dynamic_sizing(100, 1, 2, multiplier, asset, app_config, current_drawdown_pct=0.045)
    assert result.drawdown_factor == pytest.approx(0.5)
    assert all(t.risk_dollars <= 50 for t in result.tiers)
    assert result.max_tier.risk_dollars == 50


@pytest.mark.parametrize(
    "drawdown,quantity,allowed",
    [(0, 20, True), (0.03, 20, True), (0.045, 10, True), (0.045, 20, False), (0.06, 1, False)],
)
def test_explicit_quantity_cannot_bypass_drawdown(app_config, drawdown, quantity, allowed):
    app_config.portfolio.cash = 10000
    request = OrderRequest(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=quantity,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
    )
    reason = reservation_rejection(request, [], app_config, current_drawdown_pct=drawdown)
    assert (reason is None) is allowed
    if not allowed:
        assert "drawdown" in reason.lower()


@pytest.mark.parametrize(
    "settings",
    [
        {"drawdown_haircut_threshold_pct": -1},
        {"max_drawdown_stop_pct": 0},
        {"drawdown_haircut_threshold_pct": 0.07, "max_drawdown_stop_pct": 0.06},
        {"max_drawdown_stop_pct": float("nan")},
        {"drawdown_min_risk_multiplier": 0},
    ],
)
def test_invalid_drawdown_policy_rejected(settings):
    with pytest.raises(ValueError):
        PositionSizingConfig(**settings)
