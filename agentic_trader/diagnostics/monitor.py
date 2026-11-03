"""External supervisor application service; no broker or trading dependency."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import OperationalNotice
from agentic_trader.diagnostics.quiet_hours import alert_since, is_muted
from agentic_trader.storage.operations import OperationsStore


MAX_INCIDENT_DETAIL = 240


def incident_detail(check: Mapping[str, Any]) -> str:
    detail = check.get("detail", "")
    if "age_seconds" in check:
        detail = f"{detail} Observation age: {check['age_seconds']}s; limit: {check['max_age_seconds']}s".strip()
    if "unresolved" in check:
        detail = f"{detail} Unresolved requests: {check['unresolved']}".strip()
    if "dead_letters" in check:
        detail = f"Dead letters: {check['dead_letters']}; pending: {check['pending']}"
    return str(detail)[:MAX_INCIDENT_DETAIL]


class OperationsMonitor:
    def __init__(self, store: OperationsStore, policy: OperationsConfig):
        self.store, self.policy = store, policy

    async def observe(self, report: dict[str, Any]) -> list[OperationalNotice]:
        observed_at = datetime.fromisoformat(report["checked_at"])
        checks = dict(report["checks"])
        # Endpoint recovers independently; absent component observations never clear incidents.
        if "started_at" in report:
            checks["endpoint"] = {"ready": True}
        grace = (
            "started_at" in report
            and (observed_at - datetime.fromisoformat(report["started_at"])).total_seconds()
            < self.policy.startup_grace_seconds
        )
        quiet = self.policy.quiet_hours
        since = alert_since(observed_at, quiet)
        notices = []
        for component, check in checks.items():
            if grace and not check["ready"]:
                continue
            notice = await self.store.observe(
                component,
                ready=check["ready"],
                observed_at=observed_at,
                detail=incident_detail(check),
                policy=self.policy,
                muted=is_muted(component, observed_at, quiet),
                alert_since=since,
            )
            if notice:
                notices.append(notice)
        return notices
