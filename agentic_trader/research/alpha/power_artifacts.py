"""Power diagnosis composed through the existing exclusive study artifact lifecycle."""

from pathlib import Path

from agentic_trader.research.alpha.power_study import (
    evaluate_power_job,
    power_endpoint_rows,
    power_jobs,
    summarize_power_study,
)
from agentic_trader.research.alpha.study_artifacts import execute_study
from agentic_trader.storage.artifacts import save_json_report


def execute_power_study(protocol, directory: Path, environment: dict, snapshot, *, progress=None):
    if (snapshot.identity if snapshot else None) != protocol.family_snapshot_hash:
        raise ValueError("Family snapshot differs from frozen protocol")

    def evaluate(job):
        return evaluate_power_job(
            job,
            protocol,
            snapshot,
            checkpoint=lambda selection: save_json_report(
                {**selection, "job_id": job.identity, "protocol_id": protocol.identity},
                directory / "selection" / f"{job.identity}.json",
            ),
        )

    return execute_study(
        protocol,
        directory,
        {**environment, "family_snapshot": snapshot.model_dump(mode="json") if snapshot else None},
        evaluator=evaluate,
        progress=progress,
        jobs_factory=power_jobs,
        endpoints=power_endpoint_rows,
        summarizer=summarize_power_study,
    )
