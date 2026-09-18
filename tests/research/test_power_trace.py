"""Execution diagnosis observes the existing engine, including its stateful losses."""

import json
from dataclasses import replace

import pandas as pd
import pytest

from agentic_trader.execution.lifetime_policy import TradeLifetimePolicy
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.power_trace import _summarize_trace, execution_diagnostics
from agentic_trader.research.alpha.simulation import BracketIntent, simulate_execution, simulate_strategy
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, TimedAlphaExecutionPolicy


@pytest.fixture
def pulse_case():
    definition = AlphaDefinition(
        "volume_control",
        "Volume control",
        "volume",
        direction="long",
        timeframe="1d",
        normalization_window=2,
        entry_threshold=0.5,
        execution=AlphaExecutionPolicy(
            atr_window=2,
            swing_window=1,
            stop_atr=10,
            reward_risk=2,
            structural_buffer_ticks=0,
            friction_per_side=0.001,
            trail_trigger_r=100,
        ),
    )
    bars = pd.DataFrame(
        {
            "open": [100, 100, 102, 102, 102, 100, 100, 100, 100],
            "high": [101, 101, 103, 103, 103, 101, 101, 101, 101],
            "low": [99, 99, 101, 101, 101, 99, 99, 99, 99],
            "close": [100, 100, 102, 102, 102, 100, 100, 100, 100],
            "volume": [1, 10, 1, 10, 1, 10, 1, 10, 1],
        },
        index=pd.date_range("2025-01-01", periods=9, tz="UTC"),
    )
    return definition, bars, bars.volume.eq(10)


@pytest.mark.parametrize("end,close", [(3, False), (5, False), (9, False), (9, True)])
def test_trace_on_off_preserves_every_existing_result(pulse_case, end, close):
    definition, bars, _ = pulse_case
    if close:
        bars.loc[bars.index[-1], ["high", "close"]] = [145, 141]
    plain = simulate_strategy(definition, bars, end=end)
    traced = simulate_strategy(definition, bars, end=end, trace=True)
    assert traced.pop("events")
    pd.testing.assert_series_equal(traced.pop("net_returns"), plain.pop("net_returns"))
    assert traced == plain


def test_pending_and_holding_suppress_later_pulses_without_claiming_delayed_capture(pulse_case):
    definition, bars, pulse = pulse_case
    before = bars.copy(deep=True)
    report = execution_diagnostics(definition, bars, start=2, pulse_mask=pulse)
    counts = report["counts"]
    assert counts["eligible_intents"] == 4
    assert counts["orders_created"] == counts["entries"] == 1
    assert counts["closes"] == 0
    assert counts["suppressed_pending"] == 1
    assert counts["suppressed_holding"] == 2
    assert counts["pending_at_close_bars"] == 3
    assert counts["holding_at_close_bars"] == 4
    assert counts["flat_at_close_bars"] == 0
    assert report["pulses"]["opportunities"] == 4
    assert report["pulses"]["timely_entries"] == 0
    assert report["pulses"]["missed_timely_entries"] == 4
    assert report["pulses"]["delayed_entries"] == 1
    (order,) = report["orders"]
    assert order["pending_bars"] == 3
    assert order["holding_bars"] == 4
    assert order["pending_outcome"] == "filled"
    assert order["holding_outcome"] == "censored"
    assert report["boundary"]["open_position"]
    assert not report["boundary"]["pending_entry"]
    assert report["economics"]["entry_fees"] == pytest.approx(0.001)
    assert report["economics"]["exit_fees"] == 0
    assert report["economics"]["total_return_pct"] == pytest.approx(-0.1)
    pd.testing.assert_frame_equal(before, bars)
    json.dumps(report, allow_nan=False)


def test_pending_boundary_is_censored_not_an_expired_or_losing_trade(pulse_case):
    definition, bars, pulse = pulse_case
    report = execution_diagnostics(definition, bars, start=2, end=5, pulse_mask=pulse)
    assert report["boundary"]["pending_entry"]
    assert not report["boundary"]["open_position"]
    (order,) = report["orders"]
    assert order["pending_bars"] == 3
    assert order["pending_outcome"] == "censored"
    assert order["entry_bar"] is None
    assert report["counts"]["entry_expirations"] == 0
    assert report["counts"]["closes"] == 0
    assert report["economics"]["total_fees"] == 0
    assert report["economics"]["total_return_pct"] == 0


def test_exit_on_pulse_bar_still_suppresses_its_proposal_and_retains_exact_fees(pulse_case):
    definition, bars, pulse = pulse_case
    bars.loc[bars.index[-1], ["high", "close"]] = [145, 141]
    report = execution_diagnostics(definition, bars, start=2, pulse_mask=pulse)
    assert report["counts"]["orders_created"] == 1
    assert report["counts"]["closes"] == 1
    assert report["counts"]["suppressed_holding"] == 2
    assert report["opportunities"][-1]["admission"] == "suppressed_holding"
    assert report["orders"][0]["holding_bars"] == 3
    assert report["orders"][0]["holding_outcome"] == "closed"
    assert report["counts"]["flat_at_close_bars"] == 1
    economics = report["economics"]
    assert economics["entry_fees"] == pytest.approx(0.001)
    assert economics["exit_fees"] == pytest.approx(0.0014)
    assert economics["closed_gross_pnl"] == pytest.approx(0.4)
    assert economics["closed_net_pnl"] == pytest.approx(0.3976)
    assert economics["total_return_pct"] == pytest.approx(39.76)


def test_timely_pulse_entry_is_distinct_from_exposure_or_later_fill(pulse_case):
    definition, bars, pulse = pulse_case
    bars.loc[bars.index[2], ["open", "high", "low", "close"]] = [100, 101, 99, 100]
    report = execution_diagnostics(definition, bars, start=2, pulse_mask=pulse)
    assert report["pulses"]["timely_entries"] == 1
    assert report["pulses"]["missed_timely_entries"] == 3
    assert report["pulses"]["delayed_entries"] == 0
    assert report["pulses"]["held_at_close"] == 4


def test_fold_has_history_but_does_not_inherit_execution_and_ignores_future(pulse_case):
    definition, bars, pulse = pulse_case
    expected = execution_diagnostics(definition, bars, start=4, end=7, pulse_mask=pulse)
    bars.loc[bars.index[7] :, ["open", "high", "low", "close", "volume"]] = float("nan")
    pulse = pulse.astype(object)
    pulse.iloc[7:] = None
    assert execution_diagnostics(definition, bars, start=4, end=7, pulse_mask=pulse) == expected
    assert expected["orders"][0]["created_bar"] == 4
    assert expected["orders"][0]["signal_timestamp"] == str(bars.index[3])


@pytest.mark.parametrize("malformation", ["misaligned", "numeric", "missing"])
def test_pulse_mask_requires_observed_boolean_aligned_support(pulse_case, malformation):
    definition, bars, pulse = pulse_case
    if malformation == "misaligned":
        pulse.index = pulse.index + pd.Timedelta(days=1)
    elif malformation == "numeric":
        pulse = pulse.astype(int)
    else:
        pulse = pulse.astype(object)
        pulse.iloc[3] = None
    with pytest.raises(ValueError, match="Pulse"):
        execution_diagnostics(definition, bars, pulse_mask=pulse)


def test_missing_pulse_predicate_does_not_infer_pulses_from_volume(pulse_case):
    definition, bars, _ = pulse_case
    report = execution_diagnostics(definition, bars, start=2)
    assert report["pulses"] is None
    assert all(row["pulse"] is None for row in report["opportunities"])


def test_ineligible_pulse_and_last_observation_are_not_hidden(pulse_case):
    definition, bars, pulse = pulse_case
    definition = replace(definition, entry_threshold=2)
    pulse.iloc[-1] = True
    report = execution_diagnostics(definition, bars, start=2, pulse_mask=pulse)
    assert report["pulses"]["opportunities"] == 4
    assert report["pulses"]["no_valid_intent"] == 4
    assert report["pulses"]["outside_execution_boundary"] == 1
    assert report["counts"]["orders_created"] == 0


def test_trace_projection_distinguishes_expiration_before_admission_from_close_after_admission():
    """The generic projection also understands events from timed minute replay."""
    index = pd.date_range("2026-09-17 14:00Z", periods=6, freq="min")
    frame = pd.DataFrame({"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0}, index=index)
    policy = TimedAlphaExecutionPolicy(
        lifetime=TradeLifetimePolicy(resting_seconds=120, holding_seconds=120),
        friction_per_side=0.001,
        trail_trigger_r=100,
    )
    intents = {
        1: BracketIntent(1, 100, 90, 120, str(index[0])),
        3: BracketIntent(1, 102, 90, 120, str(index[2])),
        5: BracketIntent(1, 102, 90, 120, str(index[4])),
    }
    plain = simulate_execution(frame, intents, policy)
    traced = simulate_execution(frame, intents, policy, trace=True)
    pd.testing.assert_series_equal(plain["net_returns"], traced["net_returns"])
    assert plain["trades"] == traced["trades"]
    assert plain["entries"] == traced["entries"]
    report = _summarize_trace(
        frame,
        intents,
        traced,
        start=0,
        pulse_mask=pd.Series([True, False, True, False, True, False], index=index),
    )
    counts = report["counts"]
    assert counts["orders_created"] == 2
    assert counts["entries"] == counts["closes"] == 1
    assert counts["entry_expirations"] == counts["holding_expirations"] == 1
    assert counts["pending_at_close_bars"] == counts["holding_at_close_bars"] == 2
    assert counts["flat_at_close_bars"] == 2
    assert counts["suppressed_pending"] == 0
    assert counts["suppressed_holding"] == 1
    expired, filled = report["orders"]
    assert expired["pending_outcome"] == "expired"
    assert expired["pending_resolution_bar"] == 3
    assert expired["pending_bars"] == 2
    assert filled["created_bar"] == 3
    assert filled["pending_bars"] == 0
    assert filled["holding_outcome"] == "closed"
    assert not report["boundary"]["pending_entry"]
    assert not report["boundary"]["open_position"]
    assert report["pulses"]["timely_entries"] == 1


def test_pulse_is_not_claimed_captured_when_an_older_pending_order_fills_on_it(pulse_case):
    definition, bars, pulse = pulse_case
    # The original pulse at decision 1 waits until execution 4. Decision 3 is
    # another pulse, but its new intent is suppressed by the existing order.
    bars.loc[bars.index[4], ["open", "high", "low", "close"]] = [100, 101, 99, 100]
    report = execution_diagnostics(definition, bars, start=2, pulse_mask=pulse)
    assert report["pulses"]["any_entry_on_pulse_bar"] == 1
    assert report["pulses"]["timely_entries"] == 0
    assert report["pulses"]["delayed_entries"] == 1
    filled_bar = next(row for row in report["opportunities"] if row["execution_bar"] == 4)
    assert filled_bar["admission"] == "suppressed_pending"
    assert filled_bar["any_entry"]
    assert not filled_bar["timely_entry"]
