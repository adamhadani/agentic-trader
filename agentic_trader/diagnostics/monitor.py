"""External supervisor application service; no broker or trading dependency."""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agentic_trader.config import EntryBlockMode, OperationsConfig
from agentic_trader.diagnostics.incidents import OperationalNotice
from agentic_trader.diagnostics.quiet_hours import alert_since, is_muted, quiet_period_before
from agentic_trader.storage.operations import OperationsStore


logger = logging.getLogger(__name__)

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
    def __init__(self, store: OperationsStore, policy: OperationsConfig, release_exit_mode: EntryBlockMode = "off"):
        self.store, self.policy, self.release_exit_mode = store, policy, release_exit_mode

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
        if self.policy.notifications_enabled and since is not None:
            since = await self._alert_clock(since, observed_at, checks)
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
                # Exempt checks are never held, so they keep today's clock and page as soon as they are due.
                alert_since=None if component in quiet.exempt_components else since,
            )
            if notice:
                notices.append(notice)
        return notices

    async def _alert_clock(
        self, window_start: datetime, observed_at: datetime, checks: Mapping[str, Mapping[str, Any]]
    ) -> datetime:
        """Decide the window's summary, then start the alert clock at the pass that decided it.

        Held incidents then page only after ``failure_seconds`` of observed alert time, even when the host slept
        through the window start. If the summary or its decision cannot be read, the clock stays at the window
        start: at worst a held incident pages early, never later.
        """
        try:
            period_start, period_end = quiet_period_before(window_start, self.policy.quiet_hours)
            await self.store.queue_quiet_summary(
                period_start=period_start,
                period_end=period_end,
                observed_at=observed_at,
                details={name: incident_detail(check) for name, check in checks.items()},
                ready_now={name for name, check in checks.items() if check["ready"]},
                policy=self.policy,
                release_exit_mode=self.release_exit_mode,
            )
        except Exception as exc:  # deliberate fail-open: the summary must never block observations
            logger.warning("quiet_summary_failed", extra={"event": "quiet_summary_failed", "error": type(exc).__name__})
            return window_start
        try:
            decided = await self.store.summary_decided_at(window_start)
        except Exception as exc:  # deliberate fail-open: an unreadable decision keeps the window-start clock
            logger.warning(
                "quiet_summary_read_failed", extra={"event": "quiet_summary_read_failed", "error": type(exc).__name__}
            )
            return window_start
        return max(window_start, decided) if decided else window_start
