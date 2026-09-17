import asyncio
import hashlib
import json
import threading
from dataclasses import asdict

import pytest
from sqlalchemy import select

from agentic_trader.research.alpha.baselines import ForecastBenchmarkPlan, ForecastTarget, benchmark_models
from agentic_trader.research.alpha.benchmark_workflow import AlphaBenchmarkService
from agentic_trader.research.alpha.data import load_dataset, save_dataset
from agentic_trader.research.alpha.validation import DatasetManifest, ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import DomainEventRecord


@pytest.fixture
async def benchmark_source(temp_db, forecast_market, tmp_path):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    manifest = DatasetManifest.from_frame(
        forecast_market, symbol="SPY", timeframe="1d", feed="synthetic", adjustment="raw", universe_version="fixture"
    ).to_dict()
    path = save_dataset(forecast_market, tmp_path, manifest["content_hash"])
    manifest.update(artifact=str(path), holdout_start=str(forecast_market.index[480]), incumbents=[])
    await repo.record_run(
        "source", {"policy": asdict(ValidationPolicy()), "trial_count": 0, "trials": [], "holdout_start": 480}, manifest
    )
    return repo, path


@pytest.mark.parametrize("failure", [None, "missing", "corrupt", "altered", "compute"])
async def test_benchmark_retains_plan_budget_exposure_and_failure_before_computation(
    benchmark_source, tmp_path, monkeypatch, temp_db, failure
):
    repo, source_path = benchmark_source
    if failure == "missing":
        source_path.unlink()
    if failure == "corrupt":
        source_path.write_bytes(b"invalid saved dataset")
    if failure == "altered":
        altered = load_dataset(source_path)
        altered.iloc[0, 0] *= 2
        changed_path = save_dataset(altered, tmp_path / "changed", "changed")
        source_path.write_bytes(changed_path.read_bytes())
    if failure == "compute":

        def fail(*args):
            raise ValueError("forced computation failure")

        monkeypatch.setattr("agentic_trader.research.alpha.benchmark_workflow.benchmark_models", fail)
    output = tmp_path / "benchmark"
    plan = ForecastBenchmarkPlan(ForecastTarget("1d", 5), method="ridge", budget=2)
    result = await AlphaBenchmarkService(repo).run("source", plan, output, environment={"test": True})
    assert result["status"] == ("completed" if failure is None else "failed")
    assert not result["authorizes_promotion"]
    assert (await repo.get("family/all"))["trial_count"] == 2
    saved = await repo.get(f"diagnostic/{result['run_id']}")
    assert saved["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    assert json.loads((output / "manifest.json").read_text())["plan_id"] == plan.identity
    if failure is None:
        assert len(result["trials"]) == 2
        assert len(list(output.glob("*.npz"))) == 2
    assert (await repo.snapshot()).generation == 0
    assert not (await repo.get(repo._variance_family_key("1d")))["sharpes"]
    async with temp_db.session_factory() as session:
        events = (await session.scalars(select(DomainEventRecord).order_by(DomainEventRecord.id))).all()
    exclusions = [
        json.loads(event.payload)["value"]
        for event in events
        if json.loads(event.payload)["key"].startswith("holdout/")
    ]
    assert exclusions and exclusions[-1]["intervals"][-1]["end"] < "2021-04-25"
    await repo.rebuild()
    assert await repo.get(f"diagnostic/{result['run_id']}") == saved
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())


async def test_output_collision_cannot_spend_trials(benchmark_source, tmp_path):
    repo, _ = benchmark_source
    output = tmp_path / "already_exists"
    output.mkdir()
    with pytest.raises(FileExistsError):
        await AlphaBenchmarkService(repo).run(
            "source", ForecastBenchmarkPlan(ForecastTarget("1d")), output, environment={}
        )
    assert (await repo.get("family/all"))["trial_count"] == 0


async def test_cpu_evaluation_leaves_event_loop_responsive(benchmark_source, tmp_path, monkeypatch):
    repo, _ = benchmark_source
    started, released = threading.Event(), threading.Event()

    def gated_compute(*args):
        started.set()
        if not released.wait(timeout=3):
            raise RuntimeError("Event loop did not release the research worker")
        return benchmark_models(*args)

    monkeypatch.setattr("agentic_trader.research.alpha.benchmark_workflow.benchmark_models", gated_compute)
    task = asyncio.create_task(
        AlphaBenchmarkService(repo).run(
            "source",
            ForecastBenchmarkPlan(ForecastTarget("1d"), budget=1),
            tmp_path / "responsive",
            environment={},
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
