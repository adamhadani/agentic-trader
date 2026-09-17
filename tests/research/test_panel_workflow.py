import asyncio
import json
import threading
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

import agentic_trader.research.alpha.panel_workflow as workflow
from agentic_trader.cli.main import cli
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.panel_study import PanelTriagePolicy
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.storage.alpha import AlphaRepository


@pytest.fixture
def panel_source(panel_study_input):
    frames, clock, _ = panel_study_input
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    return SimpleNamespace(calendar=lambda *_: sessions, daily=lambda symbol, *_: frames[symbol].copy(deep=True))


async def test_cpu_work_keeps_async_loop_responsive(panel_study_input, panel_source, temp_db, tmp_path, monkeypatch):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    original = workflow._compute
    started = threading.Event()
    released = threading.Event()

    def gated(*args):
        started.set()
        if not released.wait(timeout=3):
            raise RuntimeError("Event loop was blocked by research")
        return original(*args)

    monkeypatch.setattr(workflow, "_compute", gated)
    task = asyncio.create_task(
        AlphaPanelService(repo, panel_source).run(panel_study_input[2], tmp_path / "panel", environment={})
    )

    async def wait_for_worker():
        while not started.is_set():
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait_for_worker(), timeout=2)
    finally:
        released.set()
    assert (await task)["status"] == "completed"


@pytest.mark.parametrize("fault", [None, "missing"])
def test_panel_cli_retains_screen_and_registry_without_runtime_side_effects(
    panel_study_input, panel_source, tmp_path, monkeypatch, fault
):
    frames, clock, plan = panel_study_input
    if fault == "missing":
        frames["AAA"] = frames["AAA"].drop(clock[90])
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.session_source", lambda *_: nullcontext(panel_source))
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({"plan": plan.document(), "triage": asdict(PanelTriagePolicy())}))
    output = tmp_path / "study"
    result = CliRunner().invoke(cli, ["alpha", "panel-study", str(protocol), "--output", str(output)])
    assert (result.exit_code == 0) == (fault is None), result.output
    screen = json.loads((output / "screen.json").read_text())
    assert screen["charged_trials"] == 16 and not screen["authorizes_promotion"]
    assert len(screen["decisions"]) == (2 if fault is None else 0)
    status = CliRunner().invoke(cli, ["alpha", "status"])
    assert status.exit_code == 0, status.output
    evidence = json.loads(status.output)
    assert evidence["research_family"]["trial_count"] == 16
    assert evidence["generation"] == 0 and evidence["active"] == 0
