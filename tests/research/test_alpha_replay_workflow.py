"""Replay diagnostic journals attempts/exposure before data access and keeps evidence."""

import asyncio
import hashlib
import json
import threading
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import AlphaPromotionService
from agentic_trader.research.alpha.replay import ReplayPlan, SessionReplayPolicy
from agentic_trader.research.alpha.replay_workflow import AlphaReplayService
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.workflow import encode


@pytest.fixture
async def replay_workflow(temp_db, tmp_path, schedule_for, minute_bars):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    schedule = schedule_for(("2024-11-27", "16:00"))
    bars = minute_bars(schedule)
    bars.attrs["feed"] = "alpaca:iex"
    plan = ReplayPlan(
        "SPY",
        date(2024, 11, 27),
        date(2024, 11, 27),
        AlphaDefinition(
            "replay", "Replay", "close", timeframe="15m", data_feed="alpaca:iex", eligible_symbols=("SPY",)
        ),
        SessionReplayPolicy(),
    )
    yield repo, plan, bars, schedule, tmp_path / "replay"
    await temp_db.engine.dispose()


@pytest.mark.parametrize("failure", [None, "coverage", "transport"])
async def test_real_replay_workflow_retains_budget_and_result_on_failure(replay_workflow, failure):
    repo, plan, bars, schedule, output = replay_workflow
    if failure == "coverage":
        bars = bars.iloc[1:]

    def capture(*args):
        assert (output / "manifest.json").exists()
        if failure == "transport":
            raise OSError("fixture transport unavailable")
        return bars, schedule

    result = await AlphaReplayService(repo, SimpleNamespace(capture=capture)).run(
        plan, output, environment={"fixture": True}, as_of=pd.Timestamp("2024-11-28", tz="UTC")
    )
    assert (await repo.get("family/all"))["trial_count"] == 1
    assert result["status"] == ("completed" if failure is None else "failed")
    assert not result["authorizes_promotion"]
    assert result["manifest_hash"]
    with pytest.raises(ValueError, match="Unknown research run"):
        await AlphaPromotionService(repo).qualify(result["run_id"], plan.definition.version_id, bars)
    saved = json.loads((output / "result.json").read_text())
    assert saved["status"] == result["status"]
    if failure == "coverage":
        assert saved["coverage"]["missing_minutes"] == 1
    diagnostic = await repo.get(f"diagnostic/{result['run_id']}")
    assert diagnostic["artifact_hash"] and diagnostic["trial_count"] == 1
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == diagnostic
    with pytest.raises(FileExistsError):
        await AlphaReplayService(repo, SimpleNamespace(capture=capture)).run(plan, output, environment={})
    assert (await repo.get("family/all"))["trial_count"] == 1
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.rglob("*") if p.is_file())


async def test_capture_is_off_loop_and_exposure_is_durable_before_io(replay_workflow):
    repo, plan, bars, schedule, output = replay_workflow
    entered, release = threading.Event(), threading.Event()

    def capture(*args):
        entered.set()
        assert release.wait(timeout=5)
        return bars, schedule

    task = asyncio.create_task(
        AlphaReplayService(repo, SimpleNamespace(capture=capture)).run(
            plan, output, environment={}, as_of=pd.Timestamp("2024-11-28", tz="UTC")
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert (await repo.get("family/all"))["trial_count"] == 1
        # An overlapping holdout claim must already see the diagnostic exposure.

        key = "holdout/" + hashlib.sha256(encode({"symbol": "SPY"}).encode()).hexdigest()
        assert (await repo.get(key))["intervals"]
    finally:
        release.set()
    assert (await task)["status"] == "completed"
