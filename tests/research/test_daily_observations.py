"""Orchestration preserves claims, receipts, cancellation and immutable private evidence."""

import asyncio
import json
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_observations import DailyPanelService
from agentic_trader.research.alpha.daily_plan import DailyPanelPlan


@pytest.fixture
def daily_service(tmp_path):
    plan = DailyPanelPlan(
        "worker-test",
        tuple(f"A{i:02}" for i in range(20)),
        tuple(f"F{i}" for i in range(9)),
        date(2026, 9, 18),
        date(2027, 9, 17),
        date(2021, 1, 1),
        "a" * 64,
        "b" * 64,
    )
    clock = pd.bdate_range("2021-01-01", "2026-12-31", tz="America/New_York")
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    now = [pd.Timestamp("2026-09-19T04:35Z")]
    repo = SimpleNamespace(
        enroll=AsyncMock(return_value={"enrolled_at": "2026-09-18T10:00Z"}),
        expire=AsyncMock(),
        records=AsyncMock(return_value={"records": [], "truncated": False, "next_after": None}),
        claim_decision=AsyncMock(return_value=None),
        claim_outcome=AsyncMock(return_value=None),
        finish_decision=AsyncMock(side_effect=lambda claim, **kw: {"status": kw["status"]}),
        finish_outcome=AsyncMock(side_effect=lambda claim, **kw: {"status": kw["status"]}),
        exclude_observed_interval=AsyncMock(),
    )
    frame = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0], "volume": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-09-18", tz="America/New_York")]),
    )
    frame.attrs.update(feed=plan.feed, adjustment=plan.adjustment, timeframe="1d")
    calls = []

    def daily(symbol, *args):
        assert repo.exclude_observed_interval.await_count == len(plan.acquisition_symbols)
        calls.append(symbol)
        now[0] += pd.Timedelta(milliseconds=1)
        return frame.copy()

    source = SimpleNamespace(
        calendar=lambda start, end: tuple(s for s in sessions if start <= s.date <= end), daily=daily
    )
    service = DailyPanelService(
        repo,
        source,
        plan,
        SimpleNamespace(calendar_refresh_seconds=300),
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
        directory=tmp_path,
        runtime={"revision": "fixture"},
        clock=lambda: now[0],
    )
    return SimpleNamespace(
        service=service, plan=plan, repo=repo, source=source, now=now, calls=calls, tmp=tmp_path, sessions=sessions
    )


async def test_idle_claim_does_not_acquire_or_score(daily_service):
    c = daily_service
    result = await c.service.run_once()
    assert result["decisions"] == 0 and c.calls == []
    c.repo.enroll.assert_awaited_once()
    c.repo.claim_decision.assert_awaited_once()
    assert c.repo.claim_decision.call_args.kwargs["context"]["decision_window"]["economic_scheduled"]


async def test_baseline_outcomes_continue_when_primary_abstained_and_config_has_no_companion(daily_service):
    c = daily_service
    window = c.plan.decision_window(c.plan.start_date, c.sessions)
    companion = {"comparison_id": "baselines", "protocol_hash": "b" * 64}
    forecast_ref = {"artifact": str(c.tmp / "forecast.json"), "artifact_hash": "a" * 64}
    decision = {
        "session_date": c.plan.start_date.isoformat(),
        "decision_id": "d" * 64,
        "status": "unavailable",
        "comparisons": [companion],
        "context": {"decision_window": window.document()},
        "evidence": {
            "forecast": forecast_ref,
            "comparisons": [{**companion, "status": "scored", "forecast": forecast_ref}],
        },
    }
    c.now[0] = window.outcome_available_at + pd.Timedelta(minutes=1)

    async def records(campaign_id, *, kind, **kwargs):
        return {"records": [decision] if kind == "decision" else [], "truncated": False, "next_after": None}

    c.repo.records.side_effect = records
    c.repo.claim_outcome.return_value = {"decision_id": decision["decision_id"], "comparisons": [companion]}
    c.service._outcome = AsyncMock(
        return_value={
            "status": "unavailable",
            "comparisons": [companion],
            "evidence": {"comparisons": [{**companion, "status": "complete"}]},
        }
    )
    result = await c.service.run_once()
    c.service._outcome.assert_awaited_once()
    assert c.service._outcome.await_args.args[1] == decision
    assert result["outcomes"] == 1 and result["terminal_counts"] == {"outcome:unavailable": 1}
    assert result["comparison_counts"] == {"outcome:complete": 1}


@pytest.mark.parametrize("terminal_status", ["scored", "interrupted"])
async def test_claim_precedes_reads_and_result_and_state_commit_together(daily_service, monkeypatch, terminal_status):
    c = daily_service
    c.repo.claim_decision.return_value = {"decision_id": "d" * 64, "claim_id": "claim", "parent_state": None}
    c.repo.finish_decision.side_effect = lambda claim, **kw: {"status": terminal_status}

    def compute(batch, sessions, plan, **kwargs):
        assert set(batch.frames) == set(plan.acquisition_symbols)
        assert all(pd.Timestamp(r["received_at"]) < kwargs["fit_cutoff"] for r in kwargs["receipts"].values())
        return {
            "primary": {
                "status": "scored",
                "arms": [{"model": "ridge", "status": "scored"}],
                "residual_state": {"last_date": "2026-09-18"},
                "authorizes_promotion": False,
            },
            "companions": {},
        }

    # The real wall clock always advances; the fixture advances explicitly at fit.
    original = c.service.clock

    def tick():
        c.now[0] += pd.Timedelta(microseconds=1)
        return original()

    c.service.clock = tick

    async def progress():
        c.repo.finish_decision.assert_awaited_once()

    c.service.on_progress = AsyncMock(side_effect=progress)
    monkeypatch.setattr("agentic_trader.research.alpha.daily_observations.compute_daily_forecast_bundle", compute)
    result = await c.service.run_once()
    assert result["decisions"] == 1 and len(c.calls) == 29
    c.service.on_progress.assert_awaited_once()
    assert result["terminal_counts"] == {f"decision:{terminal_status}": 1}
    assert result["status"] == ("recorded" if terminal_status == "scored" else "unavailable")
    finish = c.repo.finish_decision.call_args.kwargs
    assert finish["state_ref"] and finish["evidence"]["forecast"]
    artifact = json.loads(Path(finish["evidence"]["forecast"]["artifact"]).read_text())
    assert not artifact["authorizes_promotion"]
    assert len(list(c.tmp.rglob("members/*.json"))) == 29


async def test_threaded_provider_drains_on_cancel_and_loop_stays_responsive(daily_service):
    c = daily_service
    started, release = threading.Event(), threading.Event()

    def blocked(*_):
        started.set()
        assert release.wait(3), "event loop blocked"
        return c.sessions

    c.source.calendar = blocked
    task = asyncio.create_task(c.service.run_once())

    async def wait_started():
        while not started.is_set():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_started(), 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not c.calls


async def test_clock_rollback_fails_closed(daily_service):
    c = daily_service
    await c.service.run_once()
    c.now[0] -= pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="backwards"):
        await c.service.run_once()


async def test_cancelled_daily_read_retains_receipt_and_member_before_shutdown(daily_service):
    c = daily_service
    c.repo.claim_decision.return_value = {"decision_id": "d" * 64, "claim_id": "claim", "parent_state": None}
    original = c.source.daily
    started, release = threading.Event(), threading.Event()

    def blocked(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    c.source.daily = blocked
    task = asyncio.create_task(c.service.run_once())

    async def wait_started():
        while not started.is_set():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_started(), 2)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    files = list(c.tmp.rglob("members/*.json"))
    assert len(files) == 1 and len(c.calls) == 1
    member = json.loads(files[0].read_text())
    assert member["dataset"] and member["receipt"]["status"] == "received"
    assert member["receipt"]["received_at"] >= member["receipt"]["requested_at"]
    c.repo.finish_decision.assert_not_awaited()


async def test_serialization_failure_preserves_raw_capture_evidence(daily_service, monkeypatch):
    c = daily_service
    c.repo.claim_decision.return_value = {"decision_id": "d" * 64, "claim_id": "claim", "parent_state": None}
    original = c.source.daily

    def daily(*args):
        frame = original(*args)
        frame.attrs["evidence"] = {"raw_capture": "private-fixture-reference"}
        return frame

    def fail(*_):
        raise OSError("injected disk failure")

    c.source.daily = daily
    monkeypatch.setattr("agentic_trader.research.alpha.daily_observations.save_observations", fail)
    await c.service.run_once()
    first = json.loads(next(c.tmp.rglob("members/0000.json")).read_text())
    assert first["failure"]["error_type"] == "OSError"
    assert first["receipt"]["evidence"]["raw_capture"] == "private-fixture-reference"
    assert c.repo.finish_decision.call_args.kwargs["status"] == "unavailable"


async def test_budget_exhaustion_never_fabricates_source_receipts(daily_service, monkeypatch):
    c = daily_service
    c.repo.claim_decision.return_value = {"decision_id": "d" * 64, "claim_id": "claim", "parent_state": None}
    elapsed = [0.0]
    monkeypatch.setattr(
        "agentic_trader.research.alpha.daily_observations.time", SimpleNamespace(monotonic=lambda: elapsed[0])
    )
    original = c.source.daily

    def daily(*args):
        elapsed[0] = 901.0
        return original(*args)

    c.source.daily = daily
    await c.service.run_once()
    assert len(c.calls) == 1
    member = json.loads(next(c.tmp.rglob("members/0001.json")).read_text())
    assert member["receipt"] == {"status": "not_attempted"}
    assert member["failure"]["error_type"] == "AcquisitionBudgetExceeded"
