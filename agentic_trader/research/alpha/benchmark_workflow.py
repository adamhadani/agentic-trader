"""Forecast diagnostics on frozen discovery data through the existing journal."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pandas as pd

from agentic_trader.research.alpha.baselines import FORECAST_BENCHMARK_VERSION, ForecastBenchmarkPlan, benchmark_models
from agentic_trader.research.alpha.data import load_dataset, save_dataset, save_json_report
from agentic_trader.research.alpha.validation import DatasetManifest, frame_digest
from agentic_trader.storage.alpha import AlphaRepository


def _compute(manifest: dict, plan: ForecastBenchmarkPlan, holdout: int, output: Path):
    bars = load_dataset(Path(manifest["artifact"]))
    actual = DatasetManifest.from_frame(
        bars,
        symbol=manifest["symbol"],
        timeframe=plan.target.timeframe,
        feed=bars.attrs.get("feed", ""),
        adjustment=bars.attrs.get("adjustment", ""),
        universe_version=manifest["universe_version"],
    ).to_dict()
    if any(manifest.get(key) != value for key, value in actual.items()):
        raise ValueError("Dataset differs from the frozen source manifest")
    expected_holdout = int(len(bars) * (1 - plan.validation.holdout_fraction))
    if holdout != expected_holdout or str(bars.index[holdout]) != manifest["holdout_start"]:
        raise ValueError("Source holdout boundary differs from the benchmark split")
    result = benchmark_models(bars, plan)
    document = result.document()
    for trial, detail in zip(result.trials, document["trials"], strict=True):
        path = save_dataset(trial.predictions, output, frame_digest(trial.predictions))
        detail.update(predictions_artifact=path.name, artifact_hash=hashlib.sha256(path.read_bytes()).hexdigest())
        for scenario, evidence in zip(trial.execution, detail["execution"], strict=True):
            path = save_dataset(scenario.observations, output, frame_digest(scenario.observations))
            evidence.update(
                observations_artifact=path.name, artifact_hash=hashlib.sha256(path.read_bytes()).hexdigest()
            )
    return document


class AlphaBenchmarkService:
    def __init__(self, repository: AlphaRepository):
        self.repository = repository

    async def run(self, source_run: str, plan: ForecastBenchmarkPlan, output: Path, *, environment: dict):
        saved = await self.repository.get(f"run/{source_run}")
        if not saved:
            raise ValueError("Unknown source run")
        manifest, source = saved["manifest"], saved["run"]
        if source.get("policy") != asdict(plan.validation) or plan.validation != self.repository.validation_policy:
            raise ValueError("Source run requires the current validation policy")
        if plan.target.timeframe != manifest["timeframe"]:
            raise ValueError("Forecast target timeframe differs from the source run")
        start, holdout_at = pd.Timestamp(manifest["start"]), pd.Timestamp(manifest["holdout_start"])
        if start.tzinfo is None or holdout_at.tzinfo is None or start >= holdout_at:
            raise ValueError("Source requires an ordered aware holdout boundary")
        # Exclusive output publication and the frozen protocol precede data access.
        await asyncio.to_thread(output.mkdir, parents=True, mode=0o700)
        run_id = uuid4().hex
        frozen = {
            "run_id": run_id,
            "source_run": source_run,
            "source_manifest": manifest,
            "plan_id": plan.identity,
            "plan": plan.document(),
            "environment": environment,
        }
        await asyncio.to_thread(save_json_report, frozen, output / "manifest.json")
        await self.repository.reserve_run(
            run_id,
            symbol=manifest["symbol"],
            timeframe=manifest["timeframe"],
            trials=plan.trial_count,
        )
        # A later run cannot reuse inspected discovery as fresh qualification data.
        # Hashing/loading the full immutable artifact is integrity verification;
        # all feature/label/model calculations are restricted to its prefix.
        await self.repository.exclude_observed_interval(
            symbol=manifest["symbol"],
            start=start.isoformat(),
            end=(holdout_at - pd.Timedelta(nanoseconds=1)).isoformat(),
            trials=0,
            reason=f"Forecast benchmark {run_id}; frozen plan {plan.identity}",
            actor="forecast_benchmark",
        )
        try:
            result = await asyncio.to_thread(_compute, manifest, plan, source["holdout_start"], output)
            result["status"] = "completed"
        except Exception as exc:
            # Reservations survive interruption/failure; no free retry or raw
            # provider/path-bearing error text in the production journal.
            result = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}
        result.update(run_id=run_id, plan_id=plan.identity, authorizes_promotion=False)
        path = await asyncio.to_thread(save_json_report, result, output / "result.json")
        digest = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
        await self.repository.record_diagnostic(
            run_id,
            {
                "kind": FORECAST_BENCHMARK_VERSION,
                "source_run": source_run,
                "status": result["status"],
                "plan": plan.document(),
                "artifact": str(path),
                "artifact_hash": digest,
                "error_type": result.get("error_type"),
                "authorizes_promotion": False,
            },
        )
        return result
