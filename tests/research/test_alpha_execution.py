from dataclasses import replace

import pandas as pd
import pytest

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, bracket_prices


@pytest.fixture
def definition():
    return AlphaDefinition(
        "alpha_execution",
        "Execution",
        "close",
        normalization_window=2,
        execution=AlphaExecutionPolicy(
            atr_window=2,
            swing_window=1,
            stop_atr=1,
            reward_risk=2,
            structural_buffer_ticks=0,
            friction_per_side=0,
            trail_trigger_r=100,
        ),
    )


@pytest.mark.parametrize("direction", [-1, 1])
def test_shared_brackets_retain_risk_reward_under_rounding(direction):
    stop, target = bracket_prices(100, direction, 1.027, 99.5, 100.5, AlphaExecutionPolicy())
    assert direction * (100 - stop) >= 1.5 * 1.027
    assert direction * (target - 100) >= 2 * direction * (100 - stop) - 1e-9


@pytest.mark.parametrize(
    ("opening", "high", "low", "expected_exit"),
    [
        (100, 105, 95, 98),  # both brackets touched: conservative stop first
        (96, 99, 95, 96),  # stop gap executes at worse open
        (106, 108, 104, 106),  # favorable target gap
    ],
)
def test_execution_uses_next_open_and_conservative_ohlc(definition, opening, high, low, expected_exit):

    bars = pd.DataFrame(
        {
            "open": [100, 100, 100, opening],
            "high": [101, 101, 101, high],
            "low": [99, 99, 99, low],
            "close": [100, 100, 100, opening],
        },
        index=pd.date_range("2025-01-01", periods=4),
    )
    scores = pd.Series([0, 2, 0, 0], index=bars.index)
    sim = simulate_strategy(definition, bars, scores=scores)
    assert len(sim["trades"]) == 1
    trade = sim["trades"][0]
    assert trade["entry_timestamp"] == str(bars.index[2])
    assert trade["exit_price"] == expected_exit
    assert sim["total_return_pct"] == pytest.approx(expected_exit - 100)


def test_validation_has_history_but_no_inherited_position(definition):

    bars = pd.DataFrame(
        {"open": [100] * 10, "high": [101] * 10, "low": [99] * 10, "close": [100] * 10},
        index=pd.date_range("2025-01-01", periods=10),
    )
    scores = pd.Series([2, 2, 2, 0, 0, 0, 0, 0, 0, 0], index=bars.index)
    result = simulate_strategy(definition, bars, scores=scores, start=5)
    assert result["net_returns"].index.equals(bars.index[5:])
    assert not result["trades"]
    assert result["total_return_pct"] == 0


def test_costs_apply_on_both_fills(definition):

    bars = pd.DataFrame(
        {"open": [100] * 4, "high": [101] * 3 + [105], "low": [99] * 4, "close": [100] * 4},
        index=pd.date_range("2025-01-01", periods=4),
    )
    scores = pd.Series([0, 2, 0, 0], index=bars.index)
    policy = replace(definition.execution, friction_per_side=0.001)
    sim = simulate_strategy(replace(definition, execution=policy), bars, scores=scores)
    assert sim["trades"][0]["net_return"] == pytest.approx(0.04 - 0.001 - 1.04 * 0.001)
    assert sim["win_rate"] == 1
    assert sim["profit_factor"] is None  # undefined, never invented as 2.0


@pytest.mark.parametrize("direction", [-1, 1])
def test_limit_order_never_fills_at_an_adverse_open_and_remains_pending(definition, direction):
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100 + direction, 100],
            "high": [101, 101, 101.5 if direction == 1 else 99.5, 101],
            "low": [99, 99, 100.5 if direction == 1 else 98.5, 99],
            "close": [100, 100, 100 + direction, 100],
        },
        index=pd.date_range("2025-01-01", periods=4),
    )
    scores = pd.Series([0, direction * 2, 0, 0], index=bars.index)
    pending = simulate_strategy(definition, bars.iloc[:3], scores=scores.iloc[:3])
    assert not pending["open_position"]
    assert pending["pending_entry"]
    filled = simulate_strategy(definition, bars, scores=scores)
    assert filled["open_position"]
    assert not filled["pending_entry"]
    assert filled["entries"][0]["price"] == 100
    assert filled["entries"][0]["timestamp"] == str(bars.index[3])


def test_intrabar_limit_touch_does_not_claim_an_earlier_target(definition):
    bars = pd.DataFrame(
        {"open": [100, 100, 105], "high": [101, 101, 106], "low": [99, 99, 99.5], "close": [100, 100, 101]},
        index=pd.date_range("2025-01-01", periods=3),
    )
    result = simulate_strategy(definition, bars, scores=pd.Series([0, 2, 0], index=bars.index))
    assert result["open_position"]
    assert result["entries"][0]["price"] == 100
    assert not result["trades"]


def test_exit_return_is_retained_when_last_score_is_missing(definition):
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100, 96],
            "high": [101, 101, 101, 99],
            "low": [99, 99, 99, 95],
            "close": [100, 100, 100, 96],
        },
        index=pd.date_range("2025-01-01", periods=4),
    )
    scores = pd.Series([0, 2, float("nan"), 0], index=bars.index)
    result = simulate_strategy(definition, bars, scores=scores)
    assert result["total_return_pct"] == pytest.approx(-4)


def test_trailing_uses_original_reserved_risk_after_a_better_limit_fill(definition):
    definition = replace(definition, execution=replace(definition.execution, trail_trigger_r=1.5))
    bars = pd.DataFrame(
        {
            "open": [100, 100, 99, 100],
            "high": [101, 101, 101, 101],
            "low": [99, 99, 98.8, 98.5],
            "close": [100, 100, 101, 100],
        },
        index=pd.date_range("2025-01-01", periods=4),
    )
    result = simulate_strategy(definition, bars, scores=pd.Series([0, 2, 0, 0], index=bars.index))
    assert result["entries"][0]["price"] == 99
    assert not result["trades"]
    assert result["open_position"]
