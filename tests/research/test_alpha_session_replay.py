"""Separate completed signal observations from minute execution eligibility."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import build_session_bars
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.replay import SessionReplayPolicy, simulate_session_strategy
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy


@pytest.fixture
def replay_input(schedule_for, minute_bars):
    schedule = schedule_for(("2024-11-27", "16:00"), ("2024-11-29", "13:00"))
    bars = minute_bars(schedule)
    definition = AlphaDefinition(
        "session",
        "Session",
        "close",
        timeframe="15m",
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
    return definition, schedule, bars


def prepared(replay_input):
    definition, schedule, bars = replay_input
    data = build_session_bars(bars, schedule, definition.timeframe, as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    scores = pd.Series(np.nan, index=data.signals.index)
    return definition, data, scores


@pytest.mark.parametrize("delay", [0, 1, 60, 61])
def test_signal_cannot_trade_before_its_close_and_declared_delay(replay_input, delay):
    definition, data, scores = prepared(replay_input)
    scores.iloc[1] = 2  # 09:45 bar closes at 10:00 ET.
    result = simulate_session_strategy(
        definition, data, policy=SessionReplayPolicy(decision_delay_seconds=delay), scores=scores
    )
    eligible = pd.Timestamp("2024-11-27 15:00", tz="UTC") + pd.Timedelta(minutes=(delay + 59) // 60)
    assert result["entries"][0]["timestamp"] == str(eligible)
    assert result["net_returns"].index.equals(data.execution.index)
    assert result["sample_length"] == 600
    assert len(result["entries"]) == 1  # No re-entry per minute from a stale coarse signal.
    assert result["authorizes_promotion"] is False


@pytest.mark.parametrize("direction", [-1, 1])
def test_pending_order_crosses_holiday_and_fills_only_in_next_session(replay_input, direction):
    definition, schedule, bars = replay_input
    definition = replace(definition, direction="long" if direction == 1 else "short")
    later = bars.index >= pd.Timestamp("2024-11-27 15:00", tz="UTC")
    bars.loc[later, ["open", "high", "low", "close"]] = [
        100 + direction,
        101.5 if direction == 1 else 99.5,
        100.5 if direction == 1 else 98.5,
        100 + direction,
    ]
    friday = pd.Timestamp("2024-11-29 14:30", tz="UTC")
    bars.loc[friday, ["open", "high", "low", "close"]] = [100, 100.5, 99.5, 100]
    extra = bars.iloc[[0]].copy()
    extra.index = pd.DatetimeIndex(["2024-11-27 21:01Z"])
    extra.loc[:, ["open", "high", "low", "close"]] = [100, 110, 90, 100]
    bars = pd.concat([bars, extra]).sort_index()
    definition, data, scores = prepared((definition, schedule, bars))
    scores.iloc[1] = 2 * direction
    result = simulate_session_strategy(definition, data, policy=SessionReplayPolicy(), scores=scores)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["timestamp"] == str(friday)
    assert result["entries"][0]["price"] == 100
    assert not result["trades"]
    assert result["open_position"]
    assert [e["kind"] for e in result["events"]][:2] == ["order_created", "entry_filled"]


def test_closed_signal_is_not_replayed_after_a_fold_starts_flat(replay_input):
    definition, data, scores = prepared(replay_input)
    scores.iloc[1] = 2
    start = pd.Timestamp("2024-11-27 16:00", tz="UTC")
    result = simulate_session_strategy(definition, data, policy=SessionReplayPolicy(), scores=scores, start=start)
    assert not result["entries"] and not result["pending_entry"]
    assert result["net_returns"].index[0] == start


def test_trailing_observation_cannot_change_protection_earlier_in_same_minute(replay_input):
    definition, schedule, bars = replay_input
    definition = replace(definition, execution=replace(definition.execution, trail_trigger_r=1.5))
    # Signal closes 15:00; entry 15:01, favorable close 15:02 schedules a stop for 15:03.
    bars.loc[pd.Timestamp("2024-11-27 15:02Z"), ["open", "high", "low", "close"]] = [100, 103.5, 99.5, 103.1]
    bars.loc[pd.Timestamp("2024-11-27 15:03Z"), ["open", "high", "low", "close"]] = [103, 103, 99, 100]
    definition, data, scores = prepared((definition, schedule, bars))
    scores.iloc[1] = 2
    result = simulate_session_strategy(definition, data, policy=SessionReplayPolicy(), scores=scores)
    assert result["trades"][0]["exit_timestamp"] == str(pd.Timestamp("2024-11-27 15:03Z"))
    assert result["trades"][0]["exit_price"] == pytest.approx(100.1)
    assert any(e["kind"] == "stop_updated" for e in result["events"])


@pytest.mark.parametrize("value", [-1, True, 0.5, 86401])
def test_latency_policy_requires_bounded_integer_seconds(value):
    with pytest.raises(ValueError):
        SessionReplayPolicy(decision_delay_seconds=value)


@pytest.mark.parametrize("latest_score", [float("nan"), 0, 2])
def test_delayed_observations_use_latest_decision_at_next_session_open(replay_input, latest_score):
    definition, data, scores = prepared(replay_input)
    scores.iloc[24] = 2
    scores.iloc[25] = latest_score
    result = simulate_session_strategy(
        definition, data, policy=SessionReplayPolicy(decision_delay_seconds=3600), scores=scores
    )
    assert len(result["entries"]) == (1 if latest_score == 2 else 0)
    assert result["decisions"][24]["superseded_before_eligibility"]
    if result["entries"]:
        assert result["entries"][0]["timestamp"] == str(pd.Timestamp("2024-11-29 14:30Z"))


def test_held_bracket_cannot_fill_on_extended_hours_spike(replay_input):
    definition, schedule, bars = replay_input
    extra = bars.iloc[[0]].copy()
    extra.index = pd.DatetimeIndex(["2024-11-27 21:01Z"])
    extra.loc[:, ["open", "high", "low", "close"]] = [100, 110, 90, 100]
    bars = pd.concat([bars, extra]).sort_index()
    definition, data, scores = prepared((definition, schedule, bars))
    scores.iloc[1] = 2
    result = simulate_session_strategy(definition, data, policy=SessionReplayPolicy(), scores=scores)
    assert result["open_position"] and not result["trades"]
    assert result["coverage"]["excluded_minutes"] == 1
