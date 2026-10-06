import math
from types import MappingProxyType

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import RiskLimits, drawdown_risk_factor, normalize_symbol, per_trade_risk_budget


@pytest.mark.parametrize(("raw", "key"), [("/MES", "MES"), ("mes", "MES"), (" SPY ", "SPY"), ("BTC/USD", "BTC/USD")])
def test_normalize_symbol(raw, key):
    assert normalize_symbol(raw) == key


def test_limits_snapshot_normalises_groups_and_caps():
    config = AppConfig()
    config.portfolio.correlation_groups = {"index": ["/MES", "spy"], "empty": []}
    config.portfolio.max_crypto_exposure = None
    limits = RiskLimits.from_config(config)
    assert limits.correlation_groups == {"index": frozenset({"MES", "SPY"}), "empty": frozenset()}
    assert isinstance(limits.correlation_groups, MappingProxyType)
    assert set(limits.asset_class_caps) == {"EQUITY", "FUTURES"}  # crypto uncapped when None; FX never configured
    assert limits.cash == config.portfolio.cash and limits.max_risk_pct_cap == config.sizing.max_risk_pct_cap
    assert limits.min_risk_reward_ratio == config.risk.min_risk_reward_ratio
    assert (limits.lockout_pre_minutes, limits.lockout_post_minutes) == (
        config.risk.lockout_pre_event_minutes,
        config.risk.lockout_post_event_minutes,
    )
    assert limits.earnings_blackout_days == config.risk.earnings_blackout_days
    assert limits.enforce_rth is config.session.enforce_rth


def test_empty_groups_mean_disabled_not_default():
    config = AppConfig()
    config.portfolio.correlation_groups = {}
    assert RiskLimits.from_config(config).correlation_groups == {}


def test_limits_snapshot_the_drawdown_policy_not_the_sizing_config():
    config = AppConfig()
    config.sizing.drawdown_gating_enabled = True
    config.sizing.drawdown_haircut_threshold_pct = 0.02
    config.sizing.max_drawdown_stop_pct = 0.05
    config.sizing.drawdown_min_risk_multiplier = 0.25
    limits = RiskLimits.from_config(config)
    assert not hasattr(limits, "sizing")
    policy = limits.drawdown_policy
    assert (
        policy.drawdown_gating_enabled,
        policy.drawdown_haircut_threshold_pct,
        policy.max_drawdown_stop_pct,
        policy.drawdown_min_risk_multiplier,
    ) == (True, 0.02, 0.05, 0.25)
    assert drawdown_risk_factor(0.035, policy) == drawdown_risk_factor(0.035, config.sizing) == pytest.approx(0.5)
    before = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.035)
    # A later edit of the config object never reaches the frozen snapshot.
    config.sizing.max_drawdown_stop_pct = 0.03
    config.sizing.drawdown_gating_enabled = False
    assert per_trade_risk_budget(limits, equity=None, drawdown_pct=0.035) == before
    assert before.drawdown_factor == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("section", "name", "value"),
    [
        ("sizing", "max_trade_notional_cap", math.nan),
        ("sizing", "max_risk_pct_cap", math.inf),
        ("portfolio", "max_notional_exposure", -1.0),
        ("portfolio", "max_equity_exposure", math.nan),
        ("portfolio", "max_concurrent_positions", -1),
        ("risk", "min_risk_reward_ratio", math.nan),
    ],
)
def test_limits_refuse_a_non_finite_or_negative_cap_or_ratio(section, name, value):
    config = AppConfig()
    setattr(getattr(config, section), name, value)
    with pytest.raises(ValueError, match=name):
        RiskLimits.from_config(config)
