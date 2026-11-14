"""Rendering for typed operational notices sent through the shared outbox."""

import html
from datetime import UTC, datetime

from agentic_trader.constants import APP_DISPLAY_NAME
from agentic_trader.diagnostics.incidents import (
    IncidentPhase,
    NoticeKind,
    OpenIncident,
    OperationalNotice,
    QuietHoursSummary,
    ReleaseExitGaps,
)
from agentic_trader.market.session import ET_TZ


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


MAX_SUMMARY_TALLIES = 12
MAX_SUMMARY_OPEN = 10
MAX_SUMMARY_GAPS = 10


def _et(at: datetime) -> str:
    return f"{at.astimezone(ET_TZ):%a %H:%M}"


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _open_line(incident: OpenIncident) -> str:
    """Phase from the projection before this pass; readiness and detail from this pass's check."""
    since = _et(incident.failed_since)
    if incident.phase == IncidentPhase.RECOVERING:
        status = f"recovering; failing since {since} ET"
    elif incident.ready_now:
        status = f"{incident.phase} since {since} ET; ready now"
    else:
        status = f"{incident.phase} since {since} ET"
    detail = f": {html.escape(incident.detail)}" if incident.detail and not incident.ready_now else ""
    return f"• {html.escape(incident.component)} ({status}){detail}"


def _release_exit_lines(gaps: ReleaseExitGaps) -> list[str]:
    day = gaps.trading_day.isoformat()
    lines = [f"Release exit {day}:"]
    if gaps.missing_pass:
        lines.append(f"• No release-exit pass was journaled for {day}.")
    for position in gaps.unknown[:MAX_SUMMARY_GAPS]:
        signal = html.escape(position.signal_id)
        lines.append(
            f"• {html.escape(position.symbol)} ({signal}): earnings timing unknown; "
            f"review and run <code>/close {signal}</code> if needed."
        )
    if len(gaps.unknown) > MAX_SUMMARY_GAPS:
        lines.append(f"… +{len(gaps.unknown) - MAX_SUMMARY_GAPS} more unknown")
    for close in gaps.unnotified_closes[:MAX_SUMMARY_GAPS]:
        signal = html.escape(close.signal_id)
        lines.append(
            f"• {html.escape(close.symbol)} ({signal}): close outcome {html.escape(close.outcome)} "
            f"was never notified; run <code>/close {signal}</code>."
        )
    if len(gaps.unnotified_closes) > MAX_SUMMARY_GAPS:
        lines.append(f"… +{len(gaps.unnotified_closes) - MAX_SUMMARY_GAPS} more unnotified")
    for position in gaps.unattempted_closes[:MAX_SUMMARY_GAPS]:
        signal = html.escape(position.signal_id)
        lines.append(
            f"• {html.escape(position.symbol)} ({signal}): owed close was never attempted; "
            f"run <code>/close {signal}</code>."
        )
    if len(gaps.unattempted_closes) > MAX_SUMMARY_GAPS:
        lines.append(f"… +{len(gaps.unattempted_closes) - MAX_SUMMARY_GAPS} more not attempted")
    return lines


def format_quiet_hours_summary(summary: QuietHoursSummary) -> str:
    span = f"Quiet hours {_et(summary.period_start)} → {_et(summary.period_end)} ET"
    lines = [f"<b>{APP_DISPLAY_NAME}: Overnight operations summary</b>"]
    if summary.tallies:
        alerts = sum(t.opened for t in summary.tallies)
        recoveries = sum(t.recovered for t in summary.tallies)
        count = len(summary.tallies)
        lines.append(
            f"{span}: {alerts} {_plural(alerts, 'alert', 'alerts')} and "
            f"{recoveries} {_plural(recoveries, 'recovery', 'recoveries')} held across "
            f"{count} {_plural(count, 'component', 'components')}."
        )
    else:
        lines.append(f"{span}: no alerts held.")
    for tally in summary.tallies[:MAX_SUMMARY_TALLIES]:
        parts = [f"{n} {word}" for n, word in ((tally.opened, "opened"), (tally.recovered, "recovered")) if n]
        lines.append(f"• {html.escape(tally.component)}: {', '.join(parts)}")
    if len(summary.tallies) > MAX_SUMMARY_TALLIES:
        more = len(summary.tallies) - MAX_SUMMARY_TALLIES
        lines.append(f"… +{more} more {_plural(more, 'component', 'components')}")
    if summary.open_incidents:
        lines.append("Still open:")
        lines.extend(_open_line(incident) for incident in summary.open_incidents[:MAX_SUMMARY_OPEN])
        if len(summary.open_incidents) > MAX_SUMMARY_OPEN:
            lines.append(f"… +{len(summary.open_incidents) - MAX_SUMMARY_OPEN} more")
        seconds = summary.failure_seconds
        wait = f"{seconds / 60:g} min" if seconds % 60 == 0 else f"{seconds:g} s"
        lines.append(f"An incident still failing {wait} after this summary sends its own alert.")
    if summary.release_exit is not None:
        lines.extend(_release_exit_lines(summary.release_exit))
    if summary.tallies:
        lines.append(
            "Incidents that open and recover overnight usually mean the host slept (<code>pmset -g log</code>)."
        )
    lines.append("Inspect <code>copilot doctor --readiness</code> and <code>copilot db incidents</code>.")
    return "\n".join(lines)
