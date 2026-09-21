import pytest

from agentic_trader.agent.position_sizing import calculate_dynamic_sizing
from agentic_trader.constants import AssetClass


def size(app_config, **kwargs):
    return calculate_dynamic_sizing(
        entry=200.0,
        stop_distance=4.0,
        target_distance=8.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=app_config,
        **kwargs,
    )


def test_probe_cap_bounds_every_tier_and_never_raises_risk(app_config):
    uncapped = size(app_config)
    capped = size(app_config, risk_dollars_cap=100.0)
    assert uncapped.max_tier.risk_dollars > 100.0
    assert all(tier.risk_dollars <= 100.0 for tier in capped.tiers)
    assert capped.max_tier.quantity == 25  # floor(100 / 4)
    assert any("probe" in reason.lower() for reason in capped.gating_reasons)
    generous = size(app_config, risk_dollars_cap=1_000_000.0)
    assert [t.quantity for t in generous.tiers] == [t.quantity for t in uncapped.tiers]


def test_probe_cap_below_one_share_blocks_instead_of_rounding_up(app_config):
    blocked = size(app_config, risk_dollars_cap=3.0)  # one share risks $4
    assert blocked.default_tier.tier_id == "blocked" and blocked.default_tier.quantity == 0


@pytest.mark.parametrize("cap", [0.0, -5.0])
def test_nonpositive_cap_is_rejected(app_config, cap):
    with pytest.raises(ValueError, match="risk cap"):
        size(app_config, risk_dollars_cap=cap)
