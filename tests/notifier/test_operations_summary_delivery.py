from datetime import UTC, datetime

from agentic_trader.config import load_config
from agentic_trader.diagnostics.incidents import ComponentTally, QuietHoursSummary
from agentic_trader.execution.durable import NotificationKind
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.presentation.operations import format_quiet_hours_summary


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def is_configured(self):
        return True

    async def send_message(self, text):
        self.sent.append(text)
        return True


async def test_dispatcher_renders_operations_summary(temp_db):
    summary = QuietHoursSummary(
        period_start=datetime(2026, 11, 2, 21, 30, tzinfo=UTC),
        period_end=datetime(2026, 11, 3, 14, 15, tzinfo=UTC),
        observed_at=datetime(2026, 11, 3, 14, 15, tzinfo=UTC),
        failure_seconds=120,
        tallies=[ComponentTally(component="scan", opened=1, recovered=1)],
        open_incidents=[],
    )
    await temp_db.workflows.enqueue_notification(
        "operations-summary/2026-11-03", NotificationKind.OPERATIONS_SUMMARY, summary.model_dump(mode="json")
    )
    notifier = RecordingNotifier()
    dispatcher = NotificationDispatcher(temp_db.workflows, notifier, load_config().execution)
    assert await dispatcher.dispatch_one() is True
    assert notifier.sent == [format_quiet_hours_summary(summary)]
