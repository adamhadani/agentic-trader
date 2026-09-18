"""The daily research worker shares daemon ownership, bounded polling and shutdown."""

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.cli.commands import service
from agentic_trader.config import DailyPanelWorkerConfig
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.research.alpha.daily_plan import DailyComparisonPlan


@pytest.mark.parametrize(
    "terminal_counts",
    [
        {},
        {"decision:scored": 1},
        {"decision:unavailable": 2, "decision:interrupted": 1, "outcome:unavailable": 3},
        {"outcome:complete": 1},
    ],
)
@pytest.mark.parametrize("field", ["terminal_counts", "comparison_counts"])
def test_daily_progress_metrics_and_logs_report_actual_bounded_terminals(caplog, terminal_counts, field):
    results = {
        "status": "recorded",
        "decisions": 3,
        "outcomes": 3,
        "campaign_id": "fixture",
        "terminal_counts": {},
        "comparison_counts": {},
    }
    results[field] = terminal_counts
    metrics = MagicMock()
    with caplog.at_level("INFO", logger="copilot"):
        service._record_research_results(results, metrics, HealthComponent.ALPHA_DAILY_PANEL)
    metric = (
        "alpha_daily_panel_terminals_total" if field == "terminal_counts" else "alpha_daily_panel_comparisons_total"
    )
    terminal_calls = [c for c in metrics.inc_counter.call_args_list if c.args == (metric,)]
    assert len(terminal_calls) == len(terminal_counts)
    for key, count in terminal_counts.items():
        kind, status = key.split(":")
        metrics.inc_counter.assert_any_call(metric, value=count, labels={"kind": kind, "status": status})
    assert getattr(caplog.records[-1], field) == terminal_counts


@pytest.mark.parametrize(
    "terminal_counts",
    [
        {"decision:arbitrary-provider-error": 1},
        {"outcome:scored": 1},
        {"decision:scored": -1},
        {"decision:scored": True},
        {"decision:scored": 1.5},
        None,
    ],
)
@pytest.mark.parametrize("field", ["terminal_counts", "comparison_counts"])
def test_daily_progress_rejects_unknown_or_invalid_terminal_labels_before_metrics(terminal_counts, field):
    metrics = MagicMock()
    results = {
        "status": "recorded",
        "decisions": 1,
        "outcomes": 0,
        "campaign_id": "fixture",
        "terminal_counts": {},
        "comparison_counts": {},
    }
    results[field] = terminal_counts
    with pytest.raises(ValueError):
        service._record_research_results(results, metrics, HealthComponent.ALPHA_DAILY_PANEL)
    metrics.inc_counter.assert_not_called()


@pytest.mark.parametrize("defect", [None, "wrapper", "altered", "malformed", "oversized"])
def test_protocol_loader_requires_exact_bounded_direct_document(tmp_path, defect):
    document = json.loads(
        (Path(__file__).parents[2] / "config/research/prospective-equity-panel-iex-v1.json").read_bytes()
    )
    path = tmp_path / "protocol.json"
    if defect == "wrapper":
        document = {"plan": document}
    elif defect == "altered":
        document["authorizes_promotion"] = True
    path.write_text(json.dumps(document))
    if defect == "malformed":
        path.write_text("{invalid")
    elif defect == "oversized":
        path.write_bytes(b" " * (service.MAX_DAILY_PROTOCOL_BYTES + 1))
    if defect:
        with pytest.raises(ValueError):
            service.load_daily_panel_plan(path)
    else:
        assert service.load_daily_panel_plan(path).document() == document


@pytest.mark.parametrize("defect", [None, "parent", "duplicate", "oversized", "tampered"])
def test_comparison_loader_binds_complete_frozen_companions_before_source_construction(tmp_path, defect):
    parent = service.load_daily_panel_plan(
        Path(__file__).parents[2] / "config/research/prospective-equity-panel-iex-v1.json"
    )
    comparison = DailyComparisonPlan(
        "baseline-support", parent.campaign_id, "c" * 64 if defect == "parent" else parent.identity, parent.start_date
    )
    document = comparison.document()
    if defect == "tampered":
        document["charged_trials"] = 0
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(document))
    if defect == "oversized":
        path.write_bytes(b" " * (service.MAX_DAILY_PROTOCOL_BYTES + 1))
    paths = (path, path) if defect == "duplicate" else (path,)
    if defect:
        with pytest.raises(ValueError):
            service.load_daily_comparison_plans(paths, parent)
    else:
        assert service.load_daily_comparison_plans(paths, parent) == (comparison,)
        assert service.load_daily_comparison_plans((), parent) == ()


async def test_disabled_daily_worker_does_not_read_protocol_or_construct_clients(config, monkeypatch):
    monkeypatch.setattr(service, "load_daily_panel_plan", lambda path: pytest.fail("disabled protocol read"))
    monkeypatch.setattr(service, "session_source", lambda *args: pytest.fail("disabled SDK clients"))
    readiness = SimpleNamespace(observe=AsyncMock())
    await service.run_daily_panel_worker(config, object(), readiness, MagicMock(), asyncio.Event())
    readiness.observe.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["idle", "recorded", "error"])
async def test_daily_worker_pins_protocol_feed_and_drains_capture_before_closing_clients(
    config, monkeypatch, tmp_path, outcome
):
    config.alpha_pipeline.daily_panel = DailyPanelWorkerConfig(enabled=True, protocol_path=tmp_path / "protocol.json")
    config.market_data.alpaca_feed = "sip"
    plan = SimpleNamespace(feed="alpaca:iex", identity="a" * 64, campaign_id="fixture")
    entered, release, stopped, closed = (asyncio.Event() for _ in range(4))
    monkeypatch.setattr(service, "load_daily_panel_plan", lambda path: plan)

    @contextmanager
    def source(actual_config, feed):
        assert actual_config is config and feed == "iex"
        try:
            yield "owned-reader"
        finally:
            closed.set()

    repository = SimpleNamespace(store=object(), policy=config.alpha_pipeline)
    daily_repository = object()
    monkeypatch.setattr(service, "DailyCampaignRepository", lambda store, policy: daily_repository)
    monkeypatch.setattr(service, "session_source", source)
    monkeypatch.setattr(service, "artifact_directory", lambda: tmp_path)

    class Collector:
        def __init__(
            self, repo, reader, actual_plan, policy, *, acquisition, directory, runtime, on_progress, comparisons
        ):
            assert repo is daily_repository and reader == "owned-reader" and actual_plan is plan
            assert policy is config.alpha_pipeline.daily_panel and acquisition is config.alpha_pipeline.daily_research
            assert directory == tmp_path / "daily-panel" and runtime
            assert comparisons == ()
            self.on_progress = on_progress

        async def run_once(self):
            entered.set()
            await release.wait()
            assert not closed.is_set()
            if outcome == "error":
                raise RuntimeError("fixture failure")
            await self.on_progress()
            return {
                "status": outcome,
                "decisions": int(outcome == "recorded"),
                "outcomes": 0,
                "campaign_id": "fixture",
                "terminal_counts": {"decision:scored": 1} if outcome == "recorded" else {},
                "comparison_counts": {},
            }

    monkeypatch.setattr(service, "DailyPanelService", Collector)
    readiness = SimpleNamespace(observe=AsyncMock())
    task = asyncio.create_task(service.run_daily_panel_worker(config, repository, readiness, MagicMock(), stopped))
    await asyncio.wait_for(entered.wait(), 2)
    stopped.set()
    assert not closed.is_set() and not task.done()
    release.set()
    await asyncio.wait_for(task, 2)
    assert closed.is_set()
    assert readiness.observe.await_args.args[:2] == (HealthComponent.ALPHA_DAILY_PANEL, outcome != "error")
    assert readiness.observe.await_count == (1 if outcome == "error" else 2)


async def test_protocol_read_is_off_loop_and_invalid_protocol_never_constructs_clients(config, monkeypatch, tmp_path):
    config.alpha_pipeline.daily_panel = DailyPanelWorkerConfig(enabled=True, protocol_path=tmp_path / "invalid.json")
    entered, release = Event(), Event()
    stopped = asyncio.Event()
    readiness = SimpleNamespace(observe=AsyncMock())

    def invalid_protocol(path):
        entered.set()
        assert release.wait(2), "Protocol read blocked the event loop"
        raise ValueError("invalid frozen protocol")

    monkeypatch.setattr(service, "load_daily_panel_plan", invalid_protocol)
    monkeypatch.setattr(
        service, "session_source", lambda *args: pytest.fail("invalid protocol constructed SDK clients")
    )
    task = asyncio.create_task(service.run_daily_panel_worker(config, object(), readiness, MagicMock(), stopped))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)
        stopped.set()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert readiness.observe.await_args.args == (HealthComponent.ALPHA_DAILY_PANEL, False, "ValueError")
