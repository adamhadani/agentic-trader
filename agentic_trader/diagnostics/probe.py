"""One passive HTTP readiness contract for CLI, watchdog and integration tests."""

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, StrictBool

from agentic_trader.config import AppConfig


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: StrictBool
    detail: str = ""


class ReadinessSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: StrictBool
    run_id: str
    checked_at: AwareDatetime
    started_at: AwareDatetime
    checks: dict[str, ReadinessCheck]


async def probe_readiness(config: AppConfig, client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get(
            f"http://127.0.0.1:{config.telemetry.metrics_port}/readyz", timeout=config.operations.probe_timeout_seconds
        )
        snapshot = ReadinessSnapshot.model_validate(response.json())
        now = datetime.now(UTC)
        if not snapshot.checks or snapshot.ready != all(c.ready for c in snapshot.checks.values()):
            raise ValueError("Inconsistent readiness result")
        if response.status_code != (200 if snapshot.ready else 503):
            raise ValueError("Invalid readiness HTTP status")
        if not 0 <= (now - snapshot.checked_at).total_seconds() <= config.operations.snapshot_max_age_seconds:
            raise ValueError("Stale readiness response")
        if snapshot.started_at > snapshot.checked_at:
            raise ValueError("Invalid daemon startup time")
        return snapshot.model_dump(mode="json")
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ready": False,
            "checked_at": datetime.now(UTC).isoformat(),
            "checks": {"endpoint": {"ready": False, "detail": f"Readiness endpoint unavailable: {type(exc).__name__}"}},
        }
