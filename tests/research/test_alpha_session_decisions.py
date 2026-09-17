"""New session versions share causal, receipt-aware decision windows."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.market.bars import SessionClockPolicy, SessionSnapshot, build_session_bars
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot
from agentic_trader.research.alpha.replay import simulate_session_strategy
from agentic_trader.research.alpha.shadow import AlphaShadowService, observe_definition
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.strategy import alpha_scores
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import AlphaProjectionRecord


@pytest.fixture
def session_input(schedule_for, minute_bars):
    schedule = schedule_for(("2024-11-27", "16:00"), ("2024-11-29", "13:00"))
    minutes = minute_bars(schedule)
    # Nonconstant, causal signal with enough history for normalization/protection.
    price = 100 + np.arange(len(minutes)) ** 2 / 100000
    minutes.loc[:, ["open", "close"]] = np.column_stack([price, price])
    minutes.loc[:, "high"] = price + 1
    minutes.loc[:, "low"] = price - 1
    minutes.attrs["feed"] = "alpaca:sip"
    definition = AlphaDefinition(
        "clock",
        "Clock",
        "delta(close,3)",
        timeframe="15m",
        eligible_symbols=("SPY",),
        data_feed="alpaca:sip",
        semantics_version=3,
        clock=SessionClockPolicy(),
        normalization_window=10,
        entry_threshold=0.1,
    )
    return definition, schedule, minutes


def test_existing_version_identity_and_wire_document_are_frozen():
    old = AlphaDefinition(
        "clock", "Clock", "delta(close,3)", timeframe="15m", eligible_symbols=("SPY",), data_feed="alpaca:sip"
    )
    assert old.version_id == "9b9a42e2accac4034d58ba813926a47d429e35e25129b7db2a1a3452525c412e"
    assert "clock" not in old.to_dict()
    assert AlphaDefinition.from_dict(old.to_dict()) == old
    new = replace(old, semantics_version=3, clock=SessionClockPolicy())
    assert new.version_id != old.version_id
    assert AlphaDefinition.from_dict(new.to_dict()) == new
    assert replace(new, clock=replace(new.clock, max_lateness_seconds=121)).version_id != new.version_id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"semantics_version": 3},
        {"clock": SessionClockPolicy()},
        {"semantics_version": True},
        {"semantics_version": 2.0},
        {"semantics_version": 3, "clock": SessionClockPolicy(), "data_feed": "unverified"},
    ],
)
def test_incomplete_or_ambiguous_clock_contract_is_rejected(kwargs):
    with pytest.raises(ValueError):
        AlphaDefinition("a", "A", "close", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"decision_delay_seconds": -1},
        {"decision_delay_seconds": True},
        {"decision_delay_seconds": 86401},
        {"max_lateness_seconds": 0},
        {"max_lateness_seconds": 0.5},
        {"max_lateness_seconds": 86401},
        {"bar_layout": "unknown"},
    ],
)
def test_clock_policy_requires_bounded_explicit_values(kwargs):
    with pytest.raises(ValueError):
        SessionClockPolicy(**kwargs)


def live_input(definition, schedule, minutes, *, received="2024-11-27 20:00:05Z"):
    received = pd.Timestamp(received)
    bars = build_session_bars(minutes, schedule, definition.timeframe, as_of=received.floor("15min"))
    snapshot = SessionSnapshot(
        bars, symbol="SPY", requested_at=received - pd.Timedelta(seconds=1), received_at=received
    )
    return ContractMarketData(symbol="SPY", session_bars={definition.timeframe: snapshot})


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ("2024-11-27 20:00:59Z", False),
        ("2024-11-27 20:01:00Z", True),
        ("2024-11-27 20:02:59Z", True),
        ("2024-11-27 20:03:00Z", False),
        ("2024-11-29 14:30:00Z", False),
    ],
)
def test_screening_and_shadow_share_decision_delay_and_exclusive_expiry(session_input, when, expected):
    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    now = pd.Timestamp(when)
    candidates = FormulaicAlphaStrategy(definition, clock=lambda: now).evaluate(data, AssetClass.EQUITY)
    observation = observe_definition(definition, data, now)
    assert bool(candidates) is expected
    # Session-clock diagnostic evidence cannot earn qualified shadow credit yet.
    assert observation["valid"] is False
    if expected:
        frame = data.session_bars["15m"].bars.signals
        expected_score = alpha_scores(definition, frame).iloc[-1]
        assert candidates[0].alpha_score == pytest.approx(expected_score)
        assert observation["score"] == pytest.approx(expected_score)
        assert observation["completed_at"] == "2024-11-27T20:00:00+00:00"
        assert observation["reason"] == "session_clock_diagnostic"
    else:
        assert "score" not in observation


@pytest.mark.parametrize(
    "defect",
    ["missing_snapshot", "native_substitute", "future_receipt", "wrong_feed", "wrong_symbol", "reversed_receipt"],
)
def test_session_screening_cannot_use_native_future_or_wrong_feed_data(session_input, defect):
    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    snapshot = data.session_bars["15m"]
    if defect == "missing_snapshot":
        data.session_bars.clear()
    elif defect == "native_substitute":
        data.fifteen_minute = snapshot.bars.signals
        data.session_bars.clear()
    elif defect == "future_receipt":
        data.session_bars["15m"] = replace(snapshot, received_at=pd.Timestamp("2024-11-27 20:02Z"))
    elif defect == "wrong_symbol":
        data.session_bars["15m"] = replace(snapshot, symbol="QQQ")
    elif defect == "wrong_feed":
        snapshot.bars.signals.attrs["feed"] = "alpaca:iex"
    else:
        with pytest.raises(ValueError):
            replace(snapshot, requested_at=snapshot.received_at + pd.Timedelta(seconds=1))
        return
    assert FormulaicAlphaStrategy(definition, clock=lambda: pd.Timestamp("2024-11-27 20:01Z")).evaluate(data) == []


def test_expired_proposal_never_becomes_new_order_after_holiday(session_input):
    definition, schedule, minutes = session_input
    bars = build_session_bars(minutes, schedule, "15m", as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    scores = pd.Series(np.nan, index=bars.signals.index)
    scores.iloc[25] = 2  # Wednesday final close: no remaining executable minute.
    result = simulate_session_strategy(definition, bars, scores=scores)
    assert not result["entries"] and not result["pending_entry"]
    decision = result["decisions"][25]
    assert decision["eligible_bar"] is None
    assert decision["expired_before_eligibility"]
    assert result["authorizes_promotion"] is False


def test_historical_corrected_bars_cannot_be_backdated_as_a_live_snapshot(session_input):
    _definition, schedule, minutes = session_input
    bars = build_session_bars(minutes, schedule, "15m", as_of=pd.Timestamp("2024-11-30", tz="UTC"))
    with pytest.raises(ValueError, match="receipt"):
        SessionSnapshot(
            bars,
            symbol="SPY",
            requested_at=pd.Timestamp("2024-11-27 20:00Z"),
            received_at=pd.Timestamp("2024-11-27 20:00:05Z"),
        )


@pytest.mark.parametrize("defect", ["closes", "overlap", "layout", "execution_layout"])
def test_snapshot_rejects_malformed_session_clock(session_input, defect):
    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    snapshot = data.session_bars["15m"]
    bars = snapshot.bars
    if defect == "closes":
        bars = replace(bars, closed_at=bars.closed_at + pd.Timedelta(minutes=1))
    elif defect == "overlap":
        bars = replace(bars, closed_at=pd.DatetimeIndex([bars.closed_at[1], *bars.closed_at[1:]]))
    elif defect == "layout":
        bars.signals.attrs["bar_layout"] = "fixed_duration_v1"
    else:
        bars.execution.attrs["bar_layout"] = "fixed_duration_v1"
    with pytest.raises(ValueError):
        replace(snapshot, bars=bars)


def test_delayed_live_score_uses_same_causal_prefix_as_replay(session_input):
    definition, schedule, minutes = session_input
    definition = replace(definition, clock=SessionClockPolicy(decision_delay_seconds=3600))
    data = live_input(definition, schedule, minutes)
    now = pd.Timestamp("2024-11-27 20:00:05Z")
    candidates = FormulaicAlphaStrategy(definition, clock=lambda: now).evaluate(data)
    bars = data.session_bars["15m"].bars
    # At 20:00:05, the last eligible signal is the one closed at 19:00.
    prefix = bars.signals.loc[bars.closed_at <= pd.Timestamp("2024-11-27 19:00Z")]
    assert candidates[0].alpha_score == pytest.approx(alpha_scores(definition, prefix).iloc[-1])
    # Later observed values exist in the snapshot but must not affect that score.
    bars.signals.loc[bars.closed_at > pd.Timestamp("2024-11-27 19:00Z"), :] = np.nan
    again = FormulaicAlphaStrategy(definition, clock=lambda: now).evaluate(data)
    assert again[0].alpha_score == candidates[0].alpha_score


async def test_repeated_session_forecasts_replay_without_shadow_credit(session_input, temp_db):

    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    service = AlphaShadowService(repo)
    try:
        for second in (0, 30):
            await service.observe(
                RegistrySnapshot(0, (), (definition,)),
                data,
                as_of=pd.Timestamp("2024-11-27 20:01Z") + pd.Timedelta(seconds=second),
            )
        async with temp_db.session_factory() as db:
            records = (
                await db.scalars(select(AlphaProjectionRecord).where(AlphaProjectionRecord.key.like("forecast/%")))
            ).all()
        assert len(records) == 1
        assert await repo.get(f"shadow/{definition.version_id}") is None
        await repo.rebuild()
        assert await repo.get(f"shadow/{definition.version_id}") is None
    finally:
        await temp_db.engine.dispose()


def test_session_version_cannot_enter_fixed_duration_simulator(session_input):
    definition, _schedule, minutes = session_input
    with pytest.raises(ValueError, match="session"):
        simulate_strategy(definition, minutes)


def test_expired_snapshot_retains_candle_identity_for_durable_diagnostics(session_input):
    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    observation = observe_definition(definition, data, pd.Timestamp("2024-11-27 20:03Z"))
    assert observation["candle_timestamp"] == "2024-11-27T19:45:00+00:00"
    assert observation["completed_at"] == "2024-11-27T20:00:00+00:00"
    assert observation["reason"] == "session_decision_expired"


def test_missing_execution_minute_cannot_be_hidden_in_prepared_container(session_input):
    definition, schedule, minutes = session_input
    data = live_input(definition, schedule, minutes)
    snapshot = data.session_bars["15m"]
    broken = replace(snapshot.bars, execution=snapshot.bars.execution.drop(snapshot.bars.execution.index[10]))
    with pytest.raises(ValueError, match="execution"):
        replace(snapshot, bars=broken)


@pytest.mark.parametrize(("atr_window", "swing_window", "expected"), [(2, 1, True), (30, 1, False), (2, 30, False)])
def test_session_proposal_warmup_uses_versioned_execution_windows(session_input, atr_window, swing_window, expected):
    definition, schedule, minutes = session_input
    definition = replace(
        definition,
        normalization_window=2,
        execution=replace(definition.execution, atr_window=atr_window, swing_window=swing_window),
    )
    data = live_input(definition, schedule, minutes, received="2024-11-27 16:30:05Z")
    candidates = FormulaicAlphaStrategy(definition, clock=lambda: pd.Timestamp("2024-11-27 16:31Z")).evaluate(data)
    assert bool(candidates) is expected
