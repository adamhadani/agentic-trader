from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import IncidentPhase, IncidentState, NoticeKind, advance


@pytest.fixture
def policy():
    return OperationsConfig(failure_seconds=10, recovery_seconds=5, reminder_seconds=100)


@pytest.mark.parametrize(
    "samples,notices,phase",
    [
        ([(0, False), (9, False), (10, False)], [NoticeKind.OPENED], IncidentPhase.OPEN),
        ([(0, False), (9, True)], [], IncidentPhase.HEALTHY),
        (
            [(0, False), (10, False), (11, True), (16, True)],
            [NoticeKind.OPENED, NoticeKind.RECOVERED],
            IncidentPhase.HEALTHY,
        ),
        ([(0, False), (10, False), (11, True), (12, False)], [NoticeKind.OPENED], IncidentPhase.OPEN),
        ([(0, False), (10, False), (110, False)], [NoticeKind.OPENED, NoticeKind.REMINDER], IncidentPhase.OPEN),
    ],
)
def test_incident_debounce_recovery_and_reminders(policy, samples, notices, phase):
    state = IncidentState()
    actual = []
    epoch = datetime(2026, 9, 16, tzinfo=UTC)
    for seconds, ready in samples:
        state, notice = advance(
            state, ready=ready, observed_at=epoch + timedelta(seconds=seconds), policy=policy, delivery_complete=True
        )
        if notice:
            actual.append(notice)
    assert actual == notices
    assert state.phase == phase


@pytest.mark.parametrize("complete", [False, True])
def test_dead_or_pending_notification_does_not_spawn_reminders(policy, complete):
    epoch = datetime(2026, 9, 16, tzinfo=UTC)
    state, _ = advance(IncidentState(), ready=False, observed_at=epoch, policy=policy, delivery_complete=False)
    state, _ = advance(
        state, ready=False, observed_at=epoch + timedelta(seconds=10), policy=policy, delivery_complete=False
    )
    state, notice = advance(
        state, ready=False, observed_at=epoch + timedelta(seconds=200), policy=policy, delivery_complete=complete
    )
    assert (notice == NoticeKind.REMINDER) is complete
