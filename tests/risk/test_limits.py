from types import MappingProxyType

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import RiskLimits, normalize_symbol


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
