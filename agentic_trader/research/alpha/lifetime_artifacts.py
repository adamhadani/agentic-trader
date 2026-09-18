"""Exclusive artifact lifecycle for paired synthetic lifetime attribution."""

from pathlib import Path

from agentic_trader.research.alpha.lifetime_attribution import (
    LifetimeAttributionProtocol,
    evaluate_lifetime_job,
    lifetime_endpoint_rows,
    lifetime_jobs,
    summarize_lifetime_study,
)
from agentic_trader.research.alpha.study_artifacts import execute_study
from agentic_trader.storage.artifacts import save_json_report


def execute_lifetime_study(protocol: LifetimeAttributionProtocol, directory: Path, environment: dict, *, progress=None):
    def evaluate(job):
        return evaluate_lifetime_job(
            job,
            protocol,
            checkpoint=lambda selection: save_json_report(
                {**selection, "job_id": job.identity, "protocol_id": protocol.identity},
                directory / "selection" / f"{job.identity}.json",
            ),
        )

    return execute_study(
        protocol,
        directory,
        environment,
        evaluator=evaluate,
        progress=progress,
        jobs_factory=lifetime_jobs,
        endpoints=lifetime_endpoint_rows,
        summarizer=summarize_lifetime_study,
    )
