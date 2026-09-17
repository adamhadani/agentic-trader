"""A forward window is consumed once, including failed or interrupted reads."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from agentic_trader.config import SessionDecisionConfig
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.decisions import SessionDecisionService
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.shadow import AlphaShadowService
from agentic_trader.storage.alpha import AlphaRepository


@pytest.fixture
async def forward_case(temp_db, tmp_path, schedule_for, minute_bars):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    schedule = schedule_for(("2024-11-27", "16:00"), ("2024-11-29", "13:00"))
    minutes = minute_bars(schedule)
    price = 100 + np.arange(len(minutes)) ** 2 / 100000
    minutes.loc[:, ["open", "close"]] = np.column_stack([price, price])
    minutes.loc[:, "high"], minutes.loc[:, "low"] = price + 1, price - 1
    minutes.attrs["feed"] = "alpaca:sip"
    definition = AlphaDefinition(
        "forward",
        "Forward",
        "delta(close,3)",
        timeframe="15m",
        eligible_symbols=("SPY",),
        data_feed="alpaca:sip",
        semantics_version=3,
        clock=SessionClockPolicy(),
        normalization_window=10,
        entry_threshold=0.1,
    )
    await repo.register(definition, actor="fixture")
    await repo.set_shadow(definition.version_id, actor="fixture", expected_generation=0)
    now = [pd.Timestamp("2024-11-27 19:59Z")]
    source = SimpleNamespace(
        calendar=Mock(side_effect=lambda start, end: tuple(s for s in schedule.sessions if start <= s.date <= end)),
        minutes=Mock(return_value=minutes),
    )
    kwargs = {"feed": "alpaca:sip", "directory": tmp_path, "clock": lambda: now[0], "runtime": {"run_id": "fixture"}}
    policy = SessionDecisionConfig(enabled=True, history_days=7)
    service = SessionDecisionService(repo, source, policy, **kwargs)
    case = SimpleNamespace(
        repo=repo,
        service=service,
        now=now,
        source=source,
        definition=definition,
        minutes=minutes,
        kwargs=kwargs,
        policy=policy,
    )
    yield case
    await temp_db.engine.dispose()


async def test_forward_score_is_immutable_and_replays_without_shadow_credit(forward_case):
    c = forward_case
    assert await c.service.run_once() == []  # Enrollment never backfills.
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    (first,) = await c.service.run_once()
    assert first["status"] == "scored" and first["forecast"]["score"] > 0
    assert first["forecast"]["completed_at"] == "2024-11-27T20:00:00+00:00"
    assert first["forecast"]["valid"] is False and first["authorizes_promotion"] is False
    assert first["requested_at"] <= first["received_at"] <= first["finished_at"]
    c.minutes.loc[:, "close"] = np.nan  # A revision cannot replace or retry this decision.
    assert await c.service.run_once() == []
    again = SessionDecisionService(c.repo, c.source, c.policy, **c.kwargs)
    assert await again.run_once() == []
    c.source.minutes.assert_called_once()
    await c.repo.rebuild()
    assert await c.repo.get(f"session-decision/{first['decision_id']}") == first
    assert await c.repo.get(f"shadow/{c.definition.version_id}") is None
    assert (await c.repo.status())["latest_session_decision"] == first
    assert (await c.repo.get("family/all"))["trial_count"] == 0


@pytest.mark.parametrize("defect", ["missing", "feed", "slow", "backwards", "provider", "registry"])
async def test_failed_window_retains_receipts_and_is_never_retried(forward_case, defect):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    if defect == "missing":
        c.source.minutes.return_value = c.minutes.drop(c.minutes.index[0])
    elif defect == "feed":
        c.minutes.attrs["feed"] = "alpaca:iex"
    elif defect == "provider":
        c.source.minutes.side_effect = TimeoutError("fixture")
    elif defect in ("slow", "backwards"):

        def read(*args):
            c.now[0] += pd.Timedelta(seconds=120 if defect == "slow" else -1)
            return c.minutes

        c.source.minutes.side_effect = read
    else:
        original = c.repo.finish_session_decision

        async def finish(*args, **kwargs):
            await c.repo.demote(c.definition.version_id, actor="fixture", expected_generation=1)
            return await original(*args, **kwargs)

        c.repo.finish_session_decision = finish
    (result,) = await c.service.run_once()
    assert result["status"] == "unavailable"
    assert result.get("artifact_hash")
    assert not result.get("forecast", {}).get("valid")
    if defect == "backwards":
        with pytest.raises(ValueError, match="backwards"):
            await c.service.run_once()
        c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    assert await c.service.run_once() == []
    c.source.minutes.assert_called_once()


async def test_missed_windows_do_not_read_prices_and_calendar_errors_do_not_advance(forward_case):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:33Z")
    c.source.calendar.side_effect = TimeoutError("fixture")
    with pytest.raises(TimeoutError):
        await c.service.run_once()
    c.source.calendar.side_effect = None
    c.source.calendar.return_value = ()
    # Empty calendar spanning a previously observed session cannot erase windows.
    with pytest.raises(ValueError, match="calendar"):
        await c.service.run_once()


async def test_restart_expires_claim_instead_of_rescoring(forward_case, monkeypatch):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    original = c.service._capture

    async def crash(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(c.service, "_capture", crash)
    with pytest.raises(asyncio.CancelledError):
        await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:03Z")
    monkeypatch.setattr(c.service, "_capture", original)
    (result,) = await c.service.run_once()
    assert result["status"] == "interrupted"
    c.source.minutes.assert_not_called()
    assert await c.service.run_once() == []


async def test_expired_and_unobserved_gap_outcomes_are_explicit(forward_case):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:33Z")
    results = await c.service.run_once()
    assert len(results) == 3 and all(r["status"] == "missed" for r in results)
    c.source.minutes.assert_not_called()
    c.now[0] = pd.Timestamp("2024-12-20 19:00Z")
    await c.service.run_once()
    state = await c.repo.get(f"session-cursor/{c.definition.version_id}/SPY")
    assert state["gap"]["reason"] == "outside_calendar_horizon"


async def test_unrelated_registry_change_requires_fresh_enrollment(forward_case):
    c = forward_case
    await c.service.run_once()
    await c.repo.set_shadow(c.definition.version_id, actor="fixture", expected_generation=1)
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    assert await c.service.run_once() == []
    c.source.minutes.assert_not_called()


async def test_idle_worker_does_not_fetch_without_eligible_candidates(forward_case):
    c = forward_case
    await c.repo.demote(c.definition.version_id, actor="fixture", expected_generation=1)
    assert await c.service.run_once() == []
    c.source.calendar.assert_not_called()
    c.source.minutes.assert_not_called()


async def test_native_scans_leave_session_diagnostics_to_the_dedicated_worker(forward_case):
    c = forward_case
    observations = await AlphaShadowService(c.repo).observe(
        await c.repo.snapshot(), ContractMarketData(symbol="SPY"), as_of=c.now[0]
    )
    assert observations == []


async def test_expired_completion_cannot_overwrite_recovery_and_preserves_late_artifact(forward_case, monkeypatch):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    finish = c.repo.finish_session_decision

    async def recover_first(claim, evidence, **kwargs):
        c.now[0] = pd.Timestamp("2024-11-27 20:03Z")
        await c.repo.expire_session_decisions(c.now[0])
        return await finish(claim, evidence, **kwargs)

    monkeypatch.setattr(c.repo, "finish_session_decision", recover_first)
    (result,) = await c.service.run_once()
    assert result["status"] == "interrupted"
    late = await c.repo.get(f"session-decision-late/{result['decision_id']}")
    assert late["status"] == "scored" and late["artifact_hash"]
    await c.repo.rebuild()
    assert (await c.repo.get(f"session-decision/{result['decision_id']}"))["status"] == "interrupted"


async def test_blocking_price_read_leaves_event_loop_responsive(forward_case):
    c = forward_case
    await c.service.run_once()
    c.now[0] = pd.Timestamp("2024-11-27 20:01Z")
    entered, release = Event(), Event()

    def read(*args):
        entered.set()
        assert release.wait(2), "Price read blocked the asyncio loop"
        return c.minutes

    c.source.minutes.side_effect = read
    task = asyncio.create_task(c.service.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not task.done()
    finally:
        release.set()
        results = await task
    assert results[0]["status"] == "scored"
