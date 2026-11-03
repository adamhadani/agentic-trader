from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import (
    IncidentPhase,
    IncidentState,
    NoticeKind,
    WithheldNotice,
    advance,
)


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
        state, notice, _ = advance(
            state, ready=ready, observed_at=epoch + timedelta(seconds=seconds), policy=policy, delivery_complete=True
        )
        if notice:
            actual.append(notice)
    assert actual == notices
    assert state.phase == phase


@pytest.mark.parametrize("complete", [False, True])
def test_dead_or_pending_notification_does_not_spawn_reminders(policy, complete):
    epoch = datetime(2026, 9, 16, tzinfo=UTC)
    state, _, _ = advance(IncidentState(), ready=False, observed_at=epoch, policy=policy, delivery_complete=False)
    state, _, _ = advance(
        state, ready=False, observed_at=epoch + timedelta(seconds=10), policy=policy, delivery_complete=False
    )
    state, notice, _ = advance(
        state, ready=False, observed_at=epoch + timedelta(seconds=200), policy=policy, delivery_complete=complete
    )
    assert (notice == NoticeKind.REMINDER) is complete


EPOCH = datetime(2026, 9, 16, tzinfo=UTC)


def step(state, policy, seconds, ready, **kwargs):
    kwargs.setdefault("delivery_complete", True)
    return advance(state, ready=ready, observed_at=EPOCH + timedelta(seconds=seconds), policy=policy, **kwargs)


def unannounced_open(policy):
    state = IncidentState()
    for seconds in (0, 10):
        state, notice, withheld = step(state, policy, seconds, False, muted=True)
    return state, notice, withheld


def test_muted_opening_is_withheld_and_unannounced(policy):
    state, notice, withheld = unannounced_open(policy)
    assert notice is None
    assert withheld == WithheldNotice(kind="opened", reason="quiet_hours")
    assert state.phase == "open" and state.last_notified_at is None


def test_held_incident_announces_after_failure_seconds_of_alert_time(policy):
    state, _, _ = unannounced_open(policy)
    since = EPOCH + timedelta(seconds=1000)
    _, notice, withheld = advance(
        state,
        ready=False,
        observed_at=since + timedelta(seconds=9),
        policy=policy,
        delivery_complete=True,
        alert_since=since,
    )
    assert (notice, withheld) == (None, None)
    _, notice, _ = advance(
        state,
        ready=False,
        observed_at=since + timedelta(seconds=10),
        policy=policy,
        delivery_complete=True,
        alert_since=since,
    )
    assert notice == "opened"


def test_unannounced_open_without_alert_since_announces_immediately(policy):
    state, _, _ = unannounced_open(policy)
    _, notice, _ = step(state, policy, 11, False)
    assert notice == "opened"


def test_unannounced_recovery_is_silent(policy):
    state, _, _ = unannounced_open(policy)
    state, _, _ = step(state, policy, 20, True)
    state, notice, withheld = step(state, policy, 25, True)
    assert notice is None
    assert withheld == WithheldNotice(kind="recovered", reason="not_announced")
    assert state.phase == "healthy"


def test_announced_recovery_during_quiet_hours_is_withheld(policy):
    state = IncidentState()
    for seconds in (0, 10):
        state, notice, _ = step(state, policy, seconds, False)
    assert notice == "opened"
    state, _, _ = step(state, policy, 11, True, muted=True)
    _, notice, withheld = step(state, policy, 16, True, muted=True)
    assert notice is None
    assert withheld == WithheldNotice(kind="recovered", reason="quiet_hours")


def test_quiet_hours_take_precedence_over_disabled_notifications(policy):
    policy.notifications_enabled = False
    _, _, withheld = unannounced_open(policy)
    assert withheld.reason == "quiet_hours"


def test_muted_reminder_keeps_last_notified_at(policy):
    state = IncidentState()
    for seconds in (0, 10):
        state, _, _ = step(state, policy, seconds, False)
    state, notice, withheld = step(state, policy, 200, False, muted=True)
    assert notice is None and withheld.kind == "reminder"
    assert state.last_notified_at == EPOCH + timedelta(seconds=10)


def test_pending_incident_from_before_the_alert_clock_opens_unannounced(policy):
    state, _, _ = step(IncidentState(), policy, 0, False, muted=True)
    assert state.phase == "pending"
    since = EPOCH + timedelta(seconds=1000)
    state, notice, withheld = step(state, policy, 1000, False, alert_since=since)
    assert state.phase == "open" and state.last_notified_at is None
    assert (notice, withheld) == (None, None)
    state, notice, _ = step(state, policy, 1009, False, alert_since=since)
    assert state.phase == "open" and notice is None
    state, notice, _ = step(state, policy, 1010, False, alert_since=since)
    assert state.phase == "open" and notice == "opened"


def test_pending_incident_inside_the_alert_clock_debounces_as_before(policy):
    since = EPOCH
    state, _, _ = step(IncidentState(), policy, 5, False, alert_since=since)
    state, notice, _ = step(state, policy, 14, False, alert_since=since)
    assert state.phase == "pending" and notice is None
    state, notice, _ = step(state, policy, 15, False, alert_since=since)
    assert state.phase == "open" and notice == "opened"
