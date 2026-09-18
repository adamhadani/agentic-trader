"""Retained forecast controls use real artifacts and durable, isolated accounting."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.forecast_controls import compute_forecast_controls
from agentic_trader.research.alpha.forecast_controls_plan import (
    CONTROL_MODELS,
    ENDPOINT_SCOPES,
    ForecastControlsPlan,
)
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.retained_panel import RetainedPanelSource
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget
from agentic_trader.storage.models import DomainEventRecord


@pytest.fixture
def controls_parent_case():
    clock = pd.date_range("2023-01-02", periods=160, freq="B", tz="America/New_York")
    symbols = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    rng = np.random.default_rng(73)
    close = 100 * np.exp(rng.normal(0.0003, np.linspace(0.003, 0.02, 6), (len(clock), 6)).cumsum(axis=0))
    opening = np.vstack((np.full(6, 100.0), close[:-1]))
    frames = {}
    for index, symbol in enumerate(symbols):
        frames[symbol] = pd.DataFrame(
            {
                "open": opening[:, index],
                "high": np.maximum(opening[:, index], close[:, index]) * 1.001,
                "low": np.minimum(opening[:, index], close[:, index]) * 0.999,
                "close": close[:, index],
                "volume": 1000.0,
            },
            index=clock,
        )
        frames[symbol].attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
    plan = PanelForecastPlan(
        campaign_id="retained_integration_fixture",
        cohorts=(ForecastCohort("equities", symbols, top_k=1, min_assets=4),),
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(PanelFold("evaluation", clock[100].date(), clock[-1].date()),),
        features=(
            PanelHypothesis("momentum60", "roc(close,60)"),
            PanelHypothesis("reversal5", "-roc(close,5)"),
            PanelHypothesis("volatility20", "realized_vol(returns,20)"),
        ),
        economic_features=("momentum60", "reversal5"),
        target=ForecastTarget("1d", 2, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        ic=ICPolicy(min_assets=4, min_observations=10, hac_lags=2),
        selection=ForecastSelection("equities", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2024-01-01T12:00Z")),
        train_sessions=40,
        min_train_sessions=10,
        min_train_rows=20,
        refit_sessions=5,
        history_sessions=61,
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    return SimpleNamespace(clock=clock, plan=plan, frames=frames, sessions=sessions)


def _tree_hashes(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("fault", [None, "zero_endpoint_volume", "dataset_tamper"])
async def test_retained_controls_preserve_accounting_inputs_and_outcome_support(
    controls_repository, controls_parent_case, tmp_path, fault
):
    repository = controls_repository
    case = controls_parent_case
    parent_directory, output = tmp_path / "parent", tmp_path / "controls"
    original_reads = []
    if fault == "zero_endpoint_volume":
        # The observation is genuine input to the parent, not a rewritten artifact.
        for frame in case.frames.values():
            frame.loc[case.clock[101], "volume"] = 0

    class SyntheticSource:
        def calendar(self, start, end):
            assert (start, end) == (case.plan.start, case.plan.end)
            original_reads.append("calendar")
            return case.sessions

        def daily(self, symbol, start, end, feed, adjustment):
            assert (start, end, feed, adjustment) == (
                case.plan.start,
                case.plan.end,
                case.plan.feed,
                case.plan.adjustment,
            )
            original_reads.append(symbol)
            return case.frames[symbol].copy(deep=True)

    acquisition = DailyAcquisitionConfig(min_request_interval_seconds=0)
    parent = await AlphaPanelService(
        repository, SyntheticSource(), acquisition=acquisition, compute=compute_panel_forecast
    ).run(case.plan, parent_directory, environment={"fixture": True}, as_of=pd.Timestamp("2024-01-02T00:00Z"))
    assert parent["status"] == "completed", parent
    assert original_reads == ["calendar", *case.plan.acquisition_symbols]
    parent_inputs = json.loads((parent_directory / "inputs.json").read_bytes())
    parent_hash = hashlib.sha256((parent_directory / "result.json").read_bytes()).hexdigest()
    plan = ForecastControlsPlan(case.plan, "equities", parent_hash)
    assert case.plan.trial_count == 16 and plan.trial_count == 30
    if fault == "dataset_tamper":
        path = parent_directory / parent_inputs["datasets"]["BBB"]["artifact"]
        with path.open("ab") as artifact:
            artifact.write(b"tampered fixture bytes")
    parent_before = _tree_hashes(parent_directory)
    loop = asyncio.get_running_loop()
    retained_reads = []

    async def verify_charge_and_exclusions():
        assert (await repository.get("family/all"))["trial_count"] == case.plan.trial_count + plan.trial_count
        async with repository.store.db.session_factory() as session:
            events = (await session.scalars(select(DomainEventRecord).order_by(DomainEventRecord.id))).all()
        exclusions = [
            json.loads(event.payload)["value"]
            for event in events
            if json.loads(event.payload)["key"].startswith("external-observation/")
            and f"frozen plan {plan.identity}" in json.loads(event.payload)["value"]["reason"]
        ]
        assert len(exclusions) == len(plan.acquisition_symbols)
        assert {row["symbol"] for row in exclusions} == set(plan.acquisition_symbols)
        assert all(pd.Timestamp(row["start"]) == case.clock[0] for row in exclusions)
        assert all(pd.Timestamp(row["end"]) == case.clock[-1] + pd.DateOffset(days=1) for row in exclusions)

    class ObservedRetainedSource(RetainedPanelSource):
        def calendar(self, *args):
            assert json.loads((output / "manifest.json").read_bytes())["plan"] == plan.document()
            asyncio.run_coroutine_threadsafe(verify_charge_and_exclusions(), loop).result(timeout=5)
            retained_reads.append("calendar")
            return super().calendar(*args)

        def daily(self, symbol, *args):
            retained_reads.append(symbol)
            return super().daily(symbol, *args)

    source = ObservedRetainedSource(parent_directory, plan)

    def compute(batch, clock, actual_plan, sessions):
        return compute_forecast_controls(batch, clock, actual_plan, sessions, parent_result=source.parent_result)

    service = AlphaPanelService(repository, source, acquisition=acquisition, compute=compute)
    result = await service.run(plan, output, environment={"input_origin": "retained_artifacts"})
    completed = fault != "dataset_tamper"
    assert result["status"] == ("completed" if completed else "failed"), result
    assert result["charged_trials"] == plan.trial_count
    assert result["completed_comparisons"] == (plan.trial_count if completed else 0)
    assert not result["authorizes_promotion"]
    assert retained_reads == ["calendar", *plan.acquisition_symbols]
    assert original_reads == ["calendar", *case.plan.acquisition_symbols]
    assert _tree_hashes(parent_directory) == parent_before
    for name in ("manifest", "inputs", "calendar"):
        assert result[f"{name}_hash"] == hashlib.sha256((output / f"{name}.json").read_bytes()).hexdigest()
    inputs = json.loads((output / "inputs.json").read_bytes())
    assert len(inputs["receipts"]) == 1 + len(plan.acquisition_symbols)
    checkpoints = []
    for reference in inputs["members"]:
        path = output / reference["artifact"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
        assert path.stat().st_mode & 0o777 == 0o600
        checkpoints.append(json.loads(path.read_bytes()))
    assert {row["symbol"] for row in checkpoints} == set(plan.acquisition_symbols)
    assert inputs["datasets"]["FFF"] == parent_inputs["datasets"]["FFF"]
    assert inputs["receipts"] != parent_inputs["receipts"]
    assert all(
        pd.Timestamp(receipt["requested_at"]) >= pd.Timestamp(parent_inputs["receipts"][-1]["received_at"])
        for receipt in inputs["receipts"]
    )
    if completed:
        assert not inputs["failures"] and inputs["datasets"] == parent_inputs["datasets"]
        assert result["refitted_models"] == 0
        assert {(t["model"], t["outcome_scope"]) for t in result["trials"]} == {
            (model, scope) for model in CONTROL_MODELS for scope in ENDPOINT_SCOPES
        }
        ridge = next(t for t in parent["trials"] if t["model"] == "ridge")
        for model in CONTROL_MODELS:
            trials = {t["outcome_scope"]: t for t in result["trials"] if t["model"] == model}
            price, strict = (trials[scope] for scope in ENDPOINT_SCOPES)
            assert price["predictions"] == strict["predictions"]
            assert [b["weights"] for b in price["baskets"]] == [b["weights"] for b in strict["baskets"]]
            assert price["cost_summaries"][0]["curve_complete"]
            assert strict["cost_summaries"][0]["curve_complete"] is (fault is None)
            assert strict["baskets"][0]["status"] == ("observed" if fault is None else "unavailable")
            if model == "ridge":
                assert price["predictions"] == ridge["predictions"]
                assert price["cost_summaries"] == [
                    s for s in ridge["cost_summaries"] if s["cost_bps"] in plan.costs_bps
                ]
        for comparison in result["paired_comparisons"]:
            assert len(comparison["observations"]) == comparison["expected_baskets"]
            if fault == "zero_endpoint_volume" and comparison["outcome_scope"] == "positive_endpoint_volume":
                assert comparison["observations"][0]["difference"] is None
                assert comparison["paired_baskets"] < comparison["expected_baskets"]
                assert comparison["complete_arithmetic_difference_sum"] is None
    else:
        assert inputs["failures"]["BBB"]["error_type"] == "ValueError"
        assert inputs["failures"]["BBB"]["status"] == "unavailable"
        assert "BBB" not in inputs["datasets"]
        assert len(inputs["datasets"]) == len(plan.acquisition_symbols) - 1
    accounting = await repository.get("family/all")
    assert accounting["trial_count"] == case.plan.trial_count + plan.trial_count
    assert await repository.versions() == []
    snapshot = await repository.snapshot()
    assert snapshot.generation == 0 and not snapshot.active and not snapshot.shadow
    assert await repository.get(repository._variance_family_key("1d")) is None
    diagnostic = await repository.get(f"diagnostic/{result['run_id']}")
    assert diagnostic["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    before_replay = _tree_hashes(output)
    await repository.rebuild()
    assert await repository.get(f"diagnostic/{result['run_id']}") == diagnostic
    assert await repository.get("family/all") == accounting
    assert await repository.snapshot() == snapshot
    with pytest.raises(FileExistsError):
        await service.run(plan, output, environment={"input_origin": "retained_artifacts"})
    assert _tree_hashes(output) == before_replay and _tree_hashes(parent_directory) == parent_before
    assert retained_reads == ["calendar", *plan.acquisition_symbols]
    assert (await repository.get("family/all"))["trial_count"] == accounting["trial_count"]
