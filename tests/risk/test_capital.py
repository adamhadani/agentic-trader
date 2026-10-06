import math

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import (
    RiskLimits,
    drawdown_risk_factor,
    macro_risk_factor,
    per_trade_risk_budget,
    requires_account_risk,
    risk_capital,
)


@pytest.mark.parametrize(("multiplier", "factor"), [(1.0, 1.0), (1.5, 1.0), (0.5, 0.5), (0.0, 0.10), (-3.0, 0.10)])
def test_macro_factor_clamps_down_only(multiplier, factor):
    assert macro_risk_factor(multiplier) == factor


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_macro_factor_rejects_non_finite(bad):
    with pytest.raises(ValueError):
        macro_risk_factor(bad)


def test_per_trade_budget_is_one_formula():
    config = AppConfig()
    config.portfolio.cash = 100_000.0
    config.sizing.max_risk_pct_cap = 0.01
    limits = RiskLimits.from_config(config)
    plain = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0)
    assert (plain.capital, plain.drawdown_factor, plain.macro_factor, plain.dollars) == (100_000.0, 1.0, 1.0, 1_000.0)
    # Observed equity below the mandate lowers capital; drawdown and macro scale the budget.
    stressed = per_trade_risk_budget(limits, equity=80_000.0, drawdown_pct=0.045, macro_multiplier=0.5)
    assert stressed.capital == 80_000.0
    assert 0.10 <= stressed.drawdown_factor < 1.0 and stressed.macro_factor == 0.5
    assert stressed.dollars == pytest.approx(80_000.0 * 0.01 * stressed.drawdown_factor * 0.5)
    assert stressed.drawdown_pct == 0.045
    halted = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.06)
    assert halted.drawdown_factor == 0.0 and halted.dollars == 0.0


def test_moved_functions_are_importable_from_the_package():
    assert risk_capital(100.0, 50.0) == 50.0
    assert requires_account_risk(AppConfig()) in (True, False)
    assert drawdown_risk_factor(0.0, AppConfig().sizing) == 1.0
