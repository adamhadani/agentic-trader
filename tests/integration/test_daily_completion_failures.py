"""Durable failure semantics across private artifacts and the real daily journal."""

import asyncio
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from agentic_trader.cli.commands.service import _record_research_results
from agentic_trader.config import DailyAcquisitionConfig, DailyPanelWorkerConfig
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha import daily_observations as module
from agentic_trader.research.alpha.daily_forecasts import (
    COMPARISON_SHARED_FIELDS,
    DAILY_COMPARISON_FORECAST_VERSION,
    DAILY_FORECAST_VERSION,
)
from agentic_trader.research.alpha.daily_plan import (
    DAILY_BASELINE_MODELS,
    DAILY_MODELS,
    DailyComparisonPlan,
    DailyPanelPlan,
)
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.storage.alpha_daily import DailyCampaignRepository


def seal(document, key="forecast_id"):
    return {**document, key: document_hash(document)}


@pytest.fixture
async def completion_case(temp_db, tmp_path, monkeypatch):
    await temp_db.init_db()
    repo = DailyCampaignRepository(temp_db.workflows)
    plan = DailyPanelPlan(
        "completion-test",
        tuple(f"A{i:02}" for i in range(20)),
        tuple(f"F{i}" for i in range(9)),
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 8, 1),
        "a" * 64,
        "b" * 64,
    )
    dates = pd.bdate_range("2026-08-03", "2026-11-03", tz="America/New_York")
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in dates
    )
    window = plan.decision_window(plan.end_date, sessions)
    comparisons = tuple(
        DailyComparisonPlan(f"baseline-{name}", plan.campaign_id, plan.identity, plan.start_date) for name in ("a", "b")
    )
    now, calls = [pd.Timestamp("2026-09-17T12:00Z")], []

    def clock():
        now[0] += pd.Timedelta(microseconds=1)
        return now[0]

    async def database_clock(_session):
        return clock()

    monkeypatch.setattr(repo, "_now", database_clock)

    def daily(symbol, _start, end, feed, adjustment):
        calls.append(symbol)
        frame = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0], "volume": [1.0]},
            index=pd.DatetimeIndex([pd.Timestamp(end, tz="America/New_York")]),
        )
        frame.attrs.update(feed=feed, adjustment=adjustment, timeframe="1d")
        return frame

    def bundle(*args, **kwargs):
        primary = seal(
            {
                "version": DAILY_FORECAST_VERSION,
                "plan_id": plan.identity,
                "decision_date": plan.end_date.isoformat(),
                "fit_cutoff": clock().isoformat(),
                "latest_received_at": now[0].isoformat(),
                "receipts": {},
                "input_hashes": {},
                "vintage_id": "c" * 64,
                "coverage": {},
                "entry_date": window.entry_date.isoformat(),
                "exit_date": window.exit_date.isoformat(),
                "economic_scheduled": window.economic_scheduled,
                "ridge_fit": {},
                "authorizes_promotion": False,
                "status": "scored",
                "arms": [{"model": model, "status": "scored"} for model in DAILY_MODELS],
                "residual_state": {"fixture": True, "last_date": plan.end_date.isoformat()},
            }
        )
        return {
            "primary": primary,
            "companions": {
                comparison.comparison_id: seal(
                    {
                        **{key: deepcopy(primary[key]) for key in COMPARISON_SHARED_FIELDS},
                        "version": DAILY_COMPARISON_FORECAST_VERSION,
                        "comparison_id": comparison.comparison_id,
                        "comparison_plan_id": comparison.identity,
                        "comparison_plan": comparison.document(),
                        "primary_forecast_id": primary["forecast_id"],
                        "status": "scored",
                        "arms": [{"model": model, "status": "scored"} for model in DAILY_BASELINE_MODELS],
                    }
                )
                for comparison in comparisons
            },
        }

    def outcomes(primary, companions, *_args, **_kwargs):
        return {
            "primary": {"status": "complete", "forecast_id": primary["forecast_id"]},
            "companions": {
                key: {"status": "complete", "forecast_id": value["forecast_id"]} for key, value in companions.items()
            },
        }

    monkeypatch.setattr(module, "compute_daily_forecast_bundle", bundle)
    monkeypatch.setattr(module, "evaluate_daily_outcome_bundle", outcomes)
    worker = module.DailyPanelService(
        repo,
        SimpleNamespace(calendar=lambda start, end: tuple(s for s in sessions if start <= s.date <= end), daily=daily),
        plan,
        DailyPanelWorkerConfig(),
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
        directory=tmp_path / "artifacts",
        runtime={"fixture": True},
        clock=clock,
        comparisons=comparisons,
    )
    await worker.run_once()
    assert not calls
    yield SimpleNamespace(
        repo=repo,
        plan=plan,
        worker=worker,
        now=now,
        calls=calls,
        window=window,
        comparisons=comparisons,
        directory=tmp_path / "artifacts",
        bundle=bundle,
    )
    await temp_db.engine.dispose()


def assert_unavailable_without_success_metrics(result, kind):
    assert result["terminal_counts"] == {f"{kind}:unavailable": 1}
    assert result["comparison_counts"] == {}
    metrics = MagicMock()
    _record_research_results(result, metrics, HealthComponent.ALPHA_DAILY_PANEL)
    assert not any(call.args == ("alpha_daily_panel_comparisons_total",) for call in metrics.inc_counter.call_args_list)


def fail_artifact(monkeypatch, filename):
    save = module._save

    def injected(document, path):
        if path.name == filename:
            raise OSError("Injected artifact publication failure")
        return save(document, path)

    monkeypatch.setattr(module, "_save", injected)


@pytest.mark.parametrize("filename", ["comparison-baseline-b.json", "residual-state.json"])
async def test_decision_artifact_failure_commits_unavailable_without_partial_success(
    completion_case, monkeypatch, filename
):
    c = completion_case
    before = await c.repo.campaign(c.plan.campaign_id)
    fail_artifact(monkeypatch, filename)
    c.now[0] = c.window.available_at + pd.Timedelta(minutes=1)
    result = await c.worker.run_once()
    assert_unavailable_without_success_metrics(result, "decision")
    decision = next(
        row
        for row in (await c.repo.records(c.plan.campaign_id))["records"]
        if row["session_date"] == c.plan.end_date.isoformat()
    )
    assert decision["status"] == "unavailable"
    assert {"inputs", "failure"} <= set(decision["evidence"])
    assert not {"forecast", "comparisons"} & set(decision["evidence"])
    after = await c.repo.campaign(c.plan.campaign_id)
    assert after["inflight_decision"] is None
    assert (after["state_generation"], after["state_ref"]) == (before["state_generation"], before["state_ref"])
    assert list(c.directory.rglob("comparison-baseline-a.json")), "Already published artifacts remain forensic"
    await c.repo.rebuild()
    assert await c.repo.decision(decision["decision_id"]) == decision


async def test_outcome_artifact_failure_commits_unavailable_without_partial_success(completion_case, monkeypatch):
    c = completion_case
    c.now[0] = c.window.available_at + pd.Timedelta(minutes=1)
    await c.worker.run_once()
    before = await c.repo.campaign(c.plan.campaign_id)
    fail_artifact(monkeypatch, "comparison-baseline-b.json")
    c.now[0] = c.window.outcome_available_at + pd.Timedelta(minutes=1)
    result = await c.worker.run_once()
    assert_unavailable_without_success_metrics(result, "outcome")
    outcome = (await c.repo.records(c.plan.campaign_id, kind="outcome"))["records"][0]
    assert outcome["status"] == "unavailable"
    assert {"inputs", "failure"} <= set(outcome["evidence"])
    assert not {"outcome", "comparisons"} & set(outcome["evidence"])
    after = await c.repo.campaign(c.plan.campaign_id)
    assert after["inflight_decision"] is None
    assert (after["state_generation"], after["state_ref"]) == (before["state_generation"], before["state_ref"])
    assert len(list(c.directory.rglob("comparison-baseline-a.json"))) == 2


async def test_resealed_different_comparison_protocol_fails_before_outcome_price_access(completion_case):
    c = completion_case
    c.now[0] = c.window.available_at + pd.Timedelta(minutes=1)
    claim = await c.repo.claim_decision(
        c.plan.campaign_id,
        c.plan.end_date,
        native_close=c.window.native_closed_at,
        available_at=c.window.available_at,
        expires_at=c.window.expires_at,
        context={"decision_window": c.window.document(), "calendar_hash": "d" * 64},
    )
    bundle = c.bundle()
    alternative = replace(c.comparisons[0], first_decision_date=c.plan.end_date)
    wrong = deepcopy(bundle["companions"][alternative.comparison_id])
    wrong.update(comparison_plan=alternative.document(), comparison_plan_id=alternative.identity)
    del wrong["forecast_id"]
    bundle["companions"][alternative.comparison_id] = seal(wrong)
    evidence = {"forecast": module._save(bundle["primary"], c.directory / "seed-primary.json"), "comparisons": []}
    for comparison in c.comparisons:
        reference = module._save(
            bundle["companions"][comparison.comparison_id], c.directory / f"seed-{comparison.comparison_id}.json"
        )
        evidence["comparisons"].append(
            {
                "comparison_id": comparison.comparison_id,
                "protocol_hash": comparison.identity,
                "status": "scored",
                "forecast": reference,
            }
        )
    state = module._save(bundle["primary"]["residual_state"], c.directory / "seed-state.json")
    await c.repo.finish_decision(claim, status="scored", evidence=evidence, state_ref=state)
    c.now[0] = c.window.outcome_available_at + pd.Timedelta(minutes=1)
    result = await c.worker.run_once()
    assert c.calls == [], "A changed comparison identity must be rejected before provider access"
    assert_unavailable_without_success_metrics(result, "outcome")
    outcome = (await c.repo.records(c.plan.campaign_id, kind="outcome"))["records"][0]
    assert outcome["status"] == "unavailable" and outcome["evidence"]["error_type"] == "ValueError"


async def test_removed_enrollment_paths_preserve_comparisons_in_future_decision_claims(completion_case):
    c = completion_case
    family = await c.repo.get("family/all")
    restarted = module.DailyPanelService(
        c.repo,
        c.worker.source,
        c.plan,
        c.worker.policy,
        acquisition=c.worker.acquisition,
        directory=c.directory,
        runtime={"fixture": True},
        clock=c.worker.clock,
        comparisons=(),
    )
    c.now[0] = c.window.available_at + pd.Timedelta(minutes=1)
    result = await restarted.run_once()
    assert restarted.comparisons == ()
    assert result["terminal_counts"] == {"decision:scored": 1}
    assert result["comparison_counts"] == {"decision:scored": 2}
    decision = next(
        row
        for row in (await c.repo.records(c.plan.campaign_id))["records"]
        if row["session_date"] == c.plan.end_date.isoformat()
    )
    assert {row["comparison_id"] for row in decision["comparisons"]} == {
        comparison.comparison_id for comparison in c.comparisons
    }
    assert await c.repo.get("family/all") == family
    assert len(c.calls) == len(c.plan.acquisition_symbols)


@pytest.mark.parametrize("kind", ["decision", "outcome"])
async def test_companion_hash_validation_does_not_block_the_event_loop(completion_case, monkeypatch, kind):
    c = completion_case
    c.now[0] = c.window.available_at + pd.Timedelta(minutes=1)
    if kind == "outcome":
        await c.worker.run_once()
        c.now[0] = c.window.outcome_available_at + pd.Timedelta(minutes=1)
    entered, release, blocked = (threading.Event() for _ in range(3))
    validate = module.validate_daily_comparison_forecast

    def slow_validation(*args):
        entered.set()
        if not release.wait(2):
            blocked.set()
            raise RuntimeError("Validation blocked the event loop")
        validate(*args)

    monkeypatch.setattr(module, "validate_daily_comparison_forecast", slow_validation)
    task = asyncio.create_task(c.worker.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not blocked.is_set(), "Full artifact hashing ran on the asyncio event loop"
    finally:
        release.set()
        result = await task
    status = "scored" if kind == "decision" else "complete"
    assert result["comparison_counts"] == {f"{kind}:{status}": 2}
