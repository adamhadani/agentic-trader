"""``TradingCopilot._regime_gate`` reward:risk comparison (incident 2026-09-24 #20).

Pure sync method: only touches ``self.config``, so it is exercised directly against a
minimal stand-in rather than a fully constructed ``TradingCopilot``.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.regime import RegimeSnapshot
from agentic_trader.broker.base import OrderRequest
from agentic_trader.constants import AssetClass, StrategyType, VolatilityRegime


def _regime(min_rr_threshold=2.0, risk_multiplier=1.0, breakout_allowed=True):
    return RegimeSnapshot(
        vix=15.0,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=None,
        dxy=None,
        breakout_allowed=breakout_allowed,
        min_rr_threshold=min_rr_threshold,
        timestamp=datetime.now(UTC),
        summary_text="calm",
        risk_multiplier=risk_multiplier,
    )


def _order(entry, stop, target, quantity=10.0):
    return OrderRequest(
        symbol="CRM",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        quantity=quantity,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
    )


def _gate(order, regime, app_config):
    fake_self = SimpleNamespace(config=app_config)
    return TradingCopilot._regime_gate(fake_self, order, {"strategy": StrategyType.TREND_PULLBACK}, regime)


def test_regime_gate_passes_card_20_float_noise_boundary(app_config):
    # Card #20: entry 240.46, stop 229.90, target 261.58. Raw floats give
    # (261.58-240.46)/(240.46-229.90) == 1.9999999999999973, strictly below 2.0 in
    # naive float comparison though the evaluator approved this card at rounded 2.0.
    app_config.risk.min_risk_reward_ratio = 2.0
    order = _order(240.46, 229.90, 261.58)
    reason = _gate(order, _regime(min_rr_threshold=2.0), app_config)
    assert reason is None


def test_regime_gate_refuses_a_genuinely_low_reward_risk(app_config):
    app_config.risk.min_risk_reward_ratio = 2.0
    order = _order(100.0, 90.0, 119.0)  # risk 10, reward 19 -> rr == 1.9, genuinely below 2.0
    reason = _gate(order, _regime(min_rr_threshold=2.0), app_config)
    assert reason == "Current macro/volatility policy requires a higher reward/risk ratio."
