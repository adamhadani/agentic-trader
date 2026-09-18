"""Elapsed lifetime policy is immutable and shared with broker action planning."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from agentic_trader.execution.lifetime_policy import TRADE_LIFETIME_VERSION_INDEPENDENT, TradeLifetimePolicy
from agentic_trader.market.bars import FixedDailyClockPolicy, SessionClockPolicy
from agentic_trader.research.alpha.models import AlphaDefinition, AlphaEvaluationMetrics
from agentic_trader.research.alpha.simulation import BracketIntent, return_statistics, simulate_execution
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, TimedAlphaExecutionPolicy


@pytest.fixture
def timed_policy():
    return TimedAlphaExecutionPolicy(
        lifetime=TradeLifetimePolicy(resting_seconds=120, holding_seconds=180),
        friction_per_side=0.001,
        trail_trigger_r=100,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("resting_seconds", 0),
        ("holding_seconds", -1),
        ("resting_seconds", True),
        ("holding_seconds", 1.5),
        ("holding_seconds", 31 * 86400 + 1),
    ],
)
def test_lifetime_policy_rejects_invalid_or_unbounded_durations(field, value):
    with pytest.raises(ValueError):
        TradeLifetimePolicy(**{"resting_seconds": 60, "holding_seconds": 600, field: value})


def test_lifetime_deadlines_use_elapsed_utc_and_exact_boundary():
    policy = TradeLifetimePolicy(resting_seconds=60, holding_seconds=86400)
    submitted = datetime(2026, 9, 18, 20, tzinfo=UTC)  # Friday: weekends do not pause elapsed time.
    assert policy.entry_deadline(submitted) == submitted + timedelta(seconds=60)
    assert policy.holding_deadline(submitted) == submitted + timedelta(days=1)
    with pytest.raises(ValueError, match="aware"):
        policy.entry_deadline(submitted.replace(tzinfo=None))


def test_timed_policy_changes_identity_without_rewriting_old_documents(timed_policy):
    definition = AlphaDefinition(
        "control",
        "Control",
        "returns",
        timeframe="15m",
        semantics_version=3,
        clock=SessionClockPolicy(),
        data_feed="alpaca:sip",
    )
    assert definition.version_id == "6f000c2f60635b9bd074fa82e9bddb22f0833ca7a69b48cbeef0e0be00b74006"
    old = definition.to_dict()
    assert "lifetime" not in old["execution"]
    timed = replace(definition, execution=timed_policy)
    assert timed.version_id != definition.version_id
    assert AlphaDefinition.from_dict(old).to_dict() == old
    assert AlphaDefinition.from_dict(timed.to_dict()) == timed
    assert (
        replace(
            timed,
            execution=replace(timed_policy, lifetime=TradeLifetimePolicy(resting_seconds=121, holding_seconds=180)),
        ).version_id
        != timed.version_id
    )


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("fill_minute,expected_entries", [(1, 1), (2, 0), (3, 0)])
def test_resting_order_expires_before_a_deadline_touch(timed_policy, direction, fill_minute, expected_entries):
    index = pd.date_range("2026-09-17 14:00Z", periods=5, freq="min")
    untouched, touched = 100 + direction, 100
    frame = pd.DataFrame({"open": untouched, "high": untouched, "low": untouched, "close": untouched}, index=index)
    frame.iloc[fill_minute:] = touched
    intent = BracketIntent(direction, 100, 100 - direction * 10, 100 + direction * 20, str(index[0]))
    result = simulate_execution(frame, {0: intent}, timed_policy, trace=True)
    assert len(result["entries"]) == expected_entries
    assert result["pending_entry"] is False
    if not expected_entries:
        assert any(e["kind"] == "entry_expired" for e in result["events"])
        assert result["net_returns"].eq(0).all()


@pytest.mark.parametrize("direction", [-1, 1])
def test_holding_expiry_uses_next_open_and_actual_two_sided_cost(timed_policy, direction):
    index = pd.date_range("2026-09-17 14:00Z", periods=5, freq="min")
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=index)
    frame.iloc[3:] = 102.0
    result = simulate_execution(
        frame,
        {0: BracketIntent(direction, 100, 100 - direction * 10, 100 + direction * 20, str(index[0]))},
        timed_policy,
        trace=True,
    )
    (trade,) = result["trades"]
    assert trade["exit_timestamp"] == str(index[3]) and trade["exit_price"] == 102
    assert trade["net_return"] == pytest.approx(direction * 0.02 - 0.001 * (1 + 1.02))
    assert result["open_position"] is False


def test_fixed_duration_simulation_cannot_silently_use_session_lifetimes(timed_policy):
    with pytest.raises(ValueError, match="session"):
        AlphaDefinition("timed", "Timed", "returns", execution=timed_policy)
    assert AlphaExecutionPolicy().to_dict().get("lifetime") is None


def test_independent_lifetime_policy_roundtrips_and_preserves_disabled_side():
    policy = TradeLifetimePolicy(None, 86400, version=TRADE_LIFETIME_VERSION_INDEPENDENT)
    assert policy.holding_deadline(datetime(2026, 9, 17, tzinfo=UTC)) == datetime(2026, 9, 18, tzinfo=UTC)
    assert policy.entry_deadline(datetime(2026, 9, 17, tzinfo=UTC)) is None


def test_fixed_daily_timed_definition_roundtrips_and_is_simulatable():
    definition = AlphaDefinition(
        "timed_daily",
        "Timed daily",
        "returns",
        timeframe="1d",
        semantics_version=4,
        clock=FixedDailyClockPolicy(),
        data_feed="synthetic",
        execution=TimedAlphaExecutionPolicy(
            lifetime=TradeLifetimePolicy(None, 86400, version=TRADE_LIFETIME_VERSION_INDEPENDENT),
            trail_trigger_r=100,
        ),
    )
    assert AlphaDefinition.from_dict(definition.to_dict()) == definition


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("gap", [False, True])
def test_holding_deadline_waits_for_next_observed_session_and_preserves_gap_priority(timed_policy, direction, gap):
    index = pd.DatetimeIndex(["2026-09-18 19:59Z", "2026-09-21 13:30Z"])
    price = 100 + direction * (25 if gap else 2)
    frame = pd.DataFrame(
        {"open": [100, price], "high": [100, price], "low": [100, price], "close": [100, price]}, index=index
    )
    result = simulate_execution(
        frame,
        {0: BracketIntent(direction, 100, 100 - direction * 10, 100 + direction * 20, str(index[0]))},
        timed_policy,
        trace=True,
    )
    assert result["trades"][0]["exit_timestamp"] == str(index[1])
    assert result["trades"][0]["exit_price"] == price
    assert any(e["kind"] == "holding_expired" for e in result["events"]) is (not gap)
    assert (1 + result["net_returns"]).prod() - 1 == pytest.approx(result["trades"][0]["net_return"])


def test_annualization_overflow_does_not_discard_actual_equity_evidence():

    returns = pd.Series([0, 0.02], index=pd.date_range("2026-09-17", periods=2, freq="min", tz="UTC"))
    stats = return_statistics(returns, [])
    assert stats["annualized_return_pct"] is None
    assert stats["total_return_pct"] == pytest.approx(2)
    metrics = AlphaEvaluationMetrics(annualized_return_pct=None)
    assert AlphaEvaluationMetrics.from_dict(metrics.to_dict()).annualized_return_pct is None
