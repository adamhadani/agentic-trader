"""Exclusive local artifact adapter for synthetic studies; no runtime services."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from agentic_trader.research.alpha.study import (
    StudyPhase,
    StudyStatus,
    endpoint_rows,
    evaluate_job,
    study_jobs,
    summarize_study,
)
from agentic_trader.storage.artifacts import save_json_report


def execute_study(protocol, directory: Path, environment: dict, *, evaluator=None, progress=None):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report(protocol.document(), directory / "protocol.json")
    jobs = list(study_jobs(protocol))
    save_json_report(
        {
            "protocol_id": protocol.identity,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "planned_jobs": len(jobs),
            "reserved_trials": sum(protocol.trial_budget for j in jobs if j.kind == "search"),
            "synthetic_only": True,
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    records = []
    development_failed = False
    for job in jobs:
        if development_failed and job.phase == StudyPhase.VALIDATION:
            break  # Preserve unexamined validation when the development harness fails.
        started = time.monotonic()
        try:
            record = evaluator(job) if evaluator else evaluate_job(job, protocol)
        except Exception as exc:
            record = {
                "job_id": job.identity,
                "job_key": job.key,
                "protocol_id": protocol.identity,
                "status": StudyStatus.FAILED,
                "error": f"{type(exc).__name__}: {exc}",
                "rows": endpoint_rows(job, protocol),
                "synthetic_only": True,
                "authorizes_promotion": False,
            }
        record["elapsed_seconds"] = time.monotonic() - started
        record["recorded_at"] = datetime.now(UTC).isoformat()
        save_json_report(record, directory / "replicates" / f"{job.identity}.json")
        records.append(record)
        if record["status"] != StudyStatus.COMPLETED and job.phase == StudyPhase.DEVELOPMENT:
            development_failed = True
        if progress:
            progress(f"{len(records)}/{len(jobs)} {job.key}: {record['status']}")
    summary = summarize_study(protocol, records)
    save_json_report(summary, directory / "completion.json")
    return summary
