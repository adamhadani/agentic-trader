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
from agentic_trader.config import DailyAcquisitionConfig
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
        AlphaPanelService(repo, panel_source, acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0)).run(
            panel_study_input[2], tmp_path / "panel", environment={}
        )
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


def test_panel_cli_requires_frozen_matched_diagnostic_contract(panel_study_input, panel_source, tmp_path, monkeypatch):
    _frames, _clock, plan = panel_study_input
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.session_source", lambda *_: nullcontext(panel_source))
    protocol = tmp_path / "protocol.json"
    document = {
        "plan": plan.document(),
        "triage": asdict(PanelTriagePolicy()),
        "diagnostics": {
            "version": "matched_panel_diagnostics_v1",
            "market_exposure_window": 60,
            "turnover": "absolute_weight_change",
        },
        "universe_source": {
            "kind": "equity_liquidity_screen_v1",
            "result_sha256": "a" * 64,
            "selected_count": len(plan.symbols),
            "feed": plan.feed,
        },
    }
    protocol.write_text(json.dumps(document))
    output = tmp_path / "matched"
    result = CliRunner().invoke(cli, ["alpha", "panel-study", str(protocol), "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads((output / "result.json").read_text())["diagnostics"]["version"] == "matched_panel_diagnostics_v1"
    document["diagnostics"]["market_exposure_window"] = 20
    protocol.write_text(json.dumps(document))
    rejected = CliRunner().invoke(cli, ["alpha", "panel-study", str(protocol), "--output", str(tmp_path / "bad")])
    assert rejected.exit_code != 0 and "frozen causal" in rejected.output


@pytest.mark.parametrize("fault", ["provider", "budget"])
async def test_partial_acquisition_is_checkpointed_and_never_used_by_complete_study(
    panel_study_input, panel_source, temp_db, tmp_path, fault, monkeypatch
):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    plan = panel_study_input[2]
    output = tmp_path / "study"
    original = panel_source.daily
    requested = []
    elapsed = [0.0]
    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))

    def daily(symbol, *args):
        if requested:
            assert (output / "members" / f"{len(requested) - 1:04d}.json").exists()
        requested.append(symbol)
        if fault == "budget":
            elapsed[0] = 11.0
        elif symbol == plan.acquisition_symbols[0]:
            raise ConnectionError("fixture unavailable")
        return original(symbol, *args)

    panel_source.daily = daily
    result = await AlphaPanelService(
        repo, panel_source, acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0, max_elapsed_seconds=10)
    ).run(plan, output, environment={})
    assert result["status"] == "failed" and result["error_type"] == "DailyAcquisitionError"
    inputs = json.loads((output / "inputs.json").read_text())
    assert len(inputs["members"]) == len(plan.acquisition_symbols)
    assert len(requested) == (1 if fault == "budget" else len(plan.acquisition_symbols))
    failures = inputs["failures"]
    assert len(failures) == (len(plan.acquisition_symbols) - 1 if fault == "budget" else 1)
    assert (await repo.get("family/all"))["trial_count"] == plan.trial_count
    for ref in inputs["members"]:
        assert (output / ref["artifact"]).exists()


async def test_pacing_cannot_start_read_past_elapsed_budget(
    panel_study_input, panel_source, temp_db, tmp_path, monkeypatch
):
    await temp_db.init_db()
    elapsed = [0.0]
    attempted = []
    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))

    async def advance(delay):
        elapsed[0] += delay

    monkeypatch.setattr(workflow.asyncio, "sleep", advance)
    panel_source.daily = lambda symbol, *_: attempted.append(symbol)
    result = await AlphaPanelService(
        AlphaRepository(temp_db.workflows),
        panel_source,
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=2, max_elapsed_seconds=1),
    ).run(panel_study_input[2], tmp_path / "study", environment={})
    assert not attempted and result["status"] == "failed"
    inputs = json.loads((tmp_path / "study" / "inputs.json").read_text())
    assert len(inputs["receipts"]) == 1  # Calendar only; no stale receipt attached to skipped members.
    for reference in inputs["members"]:
        member = json.loads((tmp_path / "study" / reference["artifact"]).read_text())
        assert member["status"] == "not_attempted" and "receipt" not in member


async def test_cancellation_drains_inflight_member_and_checkpoints_without_next_read(
    panel_study_input, panel_source, temp_db, tmp_path
):
    await temp_db.init_db()
    entered, released = threading.Event(), threading.Event()
    original = panel_source.daily
    calls = []

    def gated(symbol, *args):
        calls.append(symbol)
        entered.set()
        assert released.wait(timeout=5)
        return original(symbol, *args)

    panel_source.daily = gated
    output = tmp_path / "interrupted"
    task = asyncio.create_task(
        AlphaPanelService(
            AlphaRepository(temp_db.workflows),
            panel_source,
            acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
        ).run(panel_study_input[2], output, environment={})
    )

    async def wait_for_read():
        while not entered.is_set():
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait_for_read(), timeout=3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1
    checkpoint = json.loads((output / "members" / "0000.json").read_text())
    assert checkpoint["status"] == "observed"
    assert not (output / "result.json").exists()  # An interrupted charge is never claimed complete or replayed.


async def test_calendar_failure_retains_all_unattempted_members(panel_study_input, panel_source, temp_db, tmp_path):
    await temp_db.init_db()

    def fail(*_):
        raise ConnectionError("calendar unavailable")

    panel_source.calendar = fail
    output = tmp_path / "calendar-failed"
    result = await AlphaPanelService(
        AlphaRepository(temp_db.workflows),
        panel_source,
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
    ).run(panel_study_input[2], output, environment={})
    assert result["status"] == "failed"
    inputs = json.loads((output / "inputs.json").read_text())
    assert len(inputs["receipts"]) == 1 and not inputs["datasets"]
    assert set(inputs["failures"]) == set(panel_study_input[2].acquisition_symbols)
    for ref in inputs["members"]:
        member = json.loads((output / ref["artifact"]).read_text())
        assert member["status"] == "not_attempted" and member["cause"] == "ConnectionError"
