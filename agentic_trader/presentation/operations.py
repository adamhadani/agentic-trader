"""Rendering for typed operational notices sent through the shared outbox."""

import html
from datetime import UTC

from agentic_trader.constants import APP_DISPLAY_NAME
from agentic_trader.diagnostics.incidents import NoticeKind, OperationalNotice


TITLES = {
    NoticeKind.OPENED: "Operational alert",
    NoticeKind.RECOVERED: "Operational recovery",
    NoticeKind.REMINDER: "Operational reminder",
}


def format_operational_notice(notice: OperationalNotice) -> str:
    return (
        f"<b>{APP_DISPLAY_NAME}: {TITLES[notice.kind]}</b>\n"
        f"Component: <code>{html.escape(notice.component)}</code>\n"
        f"Incident: <code>{html.escape(notice.incident_id)}</code>\n"
        f"Observed: {notice.observed_at.astimezone(UTC):%Y-%m-%d %H:%M:%S UTC}\n"
        f"Incident began: {notice.failed_since.astimezone(UTC):%Y-%m-%d %H:%M:%S UTC}\n"
        f"{html.escape(notice.detail)}\n"
        "Inspect <code>copilot doctor --readiness</code> and <code>copilot db incidents</code>. "
        "Delivery may be delayed or duplicated; this notice records the observation time."
    )
