import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from agentic_trader.config import OperationsConfig
from agentic_trader.diagnostics.incidents import ComponentTally, QuietHoursSummary
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.storage.models import DomainEventRecord, WorkItemRecord
from agentic_trader.storage.operations import OperationsStore


@pytest.fixture
async def desk(temp_db):
    return (
        OperationsStore(temp_db.workflows),
        OperationsConfig(failure_seconds=10, recovery_seconds=5),
        datetime(2026, 9, 16, tzinfo=UTC),
    )


async def test_atomic_concurrent_open_stale_observation_and_replay(desk):
    store, policy, now = desk

    async def observe(at, ready=False):
        return await store.observe("outbox", ready=ready, observed_at=at, detail="Dead letters", policy=policy)

    await observe(now)
    results = await asyncio.gather(*(observe(now + timedelta(seconds=10)) for _ in range(2)))
    assert sum(x is not None for x in results) == 1
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    assert await observe(now, True) is None
    before = await store.incidents()
    await store.rebuild()
    assert await store.incidents() == before
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    await observe(now + timedelta(seconds=11), True)
    await observe(now + timedelta(seconds=16), True)
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 2


async def test_healthy_samples_do_not_grow_journal_and_alert_disable_is_explicit(desk):
    store, policy, now = desk
    for seconds in (0, 1, 2):
        await store.observe(
            "worker", ready=True, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.events("incident/worker")
    policy.notifications_enabled = False
    for seconds in (3, 13):
        await store.observe(
            "worker", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    assert (await store.incidents())[0]["phase"] == "open"


async def test_reenabling_notifications_reports_existing_open_incident(desk):
    store, policy, now = desk
    policy.notifications_enabled = False
    for seconds in (0, 10):
        await store.observe(
            "worker", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    policy.notifications_enabled = True
    notice = await store.observe(
        "worker", ready=False, observed_at=now + timedelta(seconds=11), detail="", policy=policy
    )
    assert notice is not None and notice.kind == "opened"
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    assert (
        await store.observe("worker", ready=False, observed_at=now + timedelta(seconds=12), detail="", policy=policy)
        is None
    )


async def test_muted_opening_journals_withheld_and_queues_nothing(desk):
    store, policy, now = desk
    for seconds in (0, 10):
        await store.observe(
            "scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy, muted=True
        )
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    events = await store.store.events("incident/scan")
    assert events[-1]["payload"]["withheld"] == {"kind": "opened", "reason": "quiet_hours"}
    before = await store.incidents()
    await store.rebuild()
    assert await store.incidents() == before


async def test_unmuted_transition_journals_null_withheld(desk):
    store, policy, now = desk
    for seconds in (0, 10):
        await store.observe("scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    events = await store.store.events("incident/scan")
    assert events[-1]["payload"]["withheld"] is None


async def test_reenabled_notifications_do_not_send_recovery_for_unannounced_incident(desk):
    store, policy, now = desk
    policy.notifications_enabled = False
    for seconds in (0, 10):
        await store.observe("scan", ready=False, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    policy.notifications_enabled = True
    for seconds in (11, 16):
        await store.observe("scan", ready=True, observed_at=now + timedelta(seconds=seconds), detail="", policy=policy)
    assert not await store.store.list_work(WorkKind.NOTIFICATION)


START = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
END = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)


async def backdate_events(store):
    """Events record wall-clock time; align it with the injected observation instants."""
    async with store.store.db.session_factory() as session, session.begin():
        for row in await session.scalars(select(DomainEventRecord)):
            row.recorded_at = datetime.fromisoformat(json.loads(row.payload)["observed_at"])


async def quiet_cycle(store, policy, component, at, *, recover=True):
    """Open (withheld) at ``at``; recover (withheld) after it when ``recover``."""
    steps = [(False, 0), (False, 10)] + ([(True, 11), (True, 16)] if recover else [])
    for ready, seconds in steps:
        await store.observe(
            component,
            ready=ready,
            observed_at=at + timedelta(seconds=seconds),
            detail="",
            policy=policy,
            muted=True,
        )


def summary_kwargs(policy, **extra):
    return {
        "period_start": START,
        "period_end": END,
        "observed_at": END,
        "details": {},
        "ready_now": (),
        "policy": policy,
        **extra,
    }


async def test_summary_tallies_withheld_notices_and_lists_open_incidents(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=1))
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=2))
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=3), recover=False)
    await backdate_events(store)
    detail = "Observation age: 40000s; limit: 18000s"
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, details={"scan": detail}))
    assert summary is not None
    assert summary.tallies == [
        ComponentTally(component="accounting", opened=2, recovered=2),
        ComponentTally(component="scan", opened=1, recovered=0),
    ]
    assert [i.component for i in summary.open_incidents] == ["scan"]
    assert summary.open_incidents[0].detail == detail
    assert summary.open_incidents[0].ready_now is False
    items = await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i.payload["kind"] for i in items] == ["operations_summary"]
    async with store.store.db.session_factory() as session:
        keys = list(await session.scalars(select(WorkItemRecord.dedup_key).where(WorkItemRecord.id == items[0].id)))
    assert keys == ["operations-summary/2026-11-03"]


async def test_summary_is_queued_once_per_window(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await backdate_events(store)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is not None
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    assert len(await store.store.list_work(WorkKind.NOTIFICATION)) == 1
    decisions = await store.store.events("operations/summary")
    assert [e["payload"]["queued"] for e in decisions] == [True]


async def test_empty_decision_is_final_for_the_window(desk):
    store, policy, _ = desk
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    decisions = await store.store.events("operations/summary")
    assert [e["payload"]["queued"] for e in decisions] == [False]
    for seconds in (45 * 60, 45 * 60 + 130):
        await store.observe("scan", ready=False, observed_at=END + timedelta(seconds=seconds), detail="", policy=policy)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    items = await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i.payload["kind"] for i in items] == ["operational"]
    assert len(await store.store.events("operations/summary")) == 1


async def test_summary_counts_an_event_exactly_at_period_start(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START - timedelta(seconds=10), recover=False)  # opens at START
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy))
    assert summary is not None
    assert summary.tallies == [ComponentTally(component="scan", opened=1, recovered=0)]


async def test_summary_ignores_events_outside_the_period_and_other_reasons(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START - timedelta(minutes=1))
    await quiet_cycle(store, policy, "worker", END)
    policy.notifications_enabled = False
    inside = START + timedelta(hours=2)
    for seconds in (0, 10):
        await store.observe(
            "accounting", ready=False, observed_at=inside + timedelta(seconds=seconds), detail="", policy=policy
        )
    policy.notifications_enabled = True
    for seconds in (11, 16):
        await store.observe(
            "accounting", ready=True, observed_at=inside + timedelta(seconds=seconds), detail="", policy=policy
        )
    await backdate_events(store)
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None
    assert not await store.store.list_work(WorkKind.NOTIFICATION)


async def test_summary_lists_trading_halt_first(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await quiet_cycle(store, policy, "trading_halt", START + timedelta(hours=2), recover=False)
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy))
    assert summary is not None
    assert [i.component for i in summary.open_incidents] == ["trading_halt", "scan"]


async def test_summary_decided_at_reads_the_first_pass_of_the_window(desk):
    store, policy, _ = desk
    assert await store.summary_decided_at(END) is None
    first = END + timedelta(minutes=75)
    await store.queue_quiet_summary(**summary_kwargs(policy, observed_at=first))
    await store.queue_quiet_summary(**summary_kwargs(policy, observed_at=first + timedelta(minutes=1)))
    assert await store.summary_decided_at(END) == first
    assert await store.summary_decided_at(END + timedelta(days=1)) is None


async def test_summary_marks_open_incidents_whose_check_is_ready_now(desk):
    store, policy, _ = desk
    await quiet_cycle(store, policy, "scan", START + timedelta(hours=1), recover=False)
    await quiet_cycle(store, policy, "accounting", START + timedelta(hours=2), recover=False)
    await backdate_events(store)
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, ready_now={"scan"}))
    assert summary is not None
    assert [(i.component, i.ready_now) for i in summary.open_incidents] == [("scan", True), ("accounting", False)]


# --- release-exit gaps (spec Part C) ---------------------------------------------------------------------------

TRADING_DAY = "2026-11-02"  # START's New York date (Monday)


async def journal_release_exit(store, *, positions=None, closes=(), raw_closes=(), day=TRADING_DAY):
    """Append a pass (when ``positions`` is not None) and ``release_exit_close`` events, as the daemon does.

    ``closes`` are ``(signal_id, notified, outcome)`` in the daemon's shape; ``raw_closes`` are
    ``(signal_id, payload)`` journaled verbatim (malformed shapes).
    """
    async with store.store.db.session_factory() as session, session.begin():
        await store.store.lock(session)
        if positions is not None:
            await store.store.append(
                session,
                stream="release-exit",
                kind=EventKind.RELEASE_EXIT_PASS,
                key=f"release-exit/{day}",
                payload={"mode": "enforce", "trading_day": day, "positions": positions},
            )
        for signal_id, notified, outcome in closes:
            await store.store.append(
                session,
                stream="release-exit",
                kind=EventKind.RELEASE_EXIT_CLOSE,
                key=f"release-exit/{day}/{signal_id}",
                payload={
                    "trading_day": day,
                    "signal_id": signal_id,
                    "symbol": "ACME",
                    "outcome": outcome,
                    "notified": notified,
                },
            )
        for signal_id, payload in raw_closes:
            await store.store.append(
                session,
                stream="release-exit",
                kind=EventKind.RELEASE_EXIT_CLOSE,
                key=f"release-exit/{day}/{signal_id}",
                payload=payload,
            )


def position(signal_id, symbol, status, scope="native"):
    return {"signal_id": signal_id, "symbol": symbol, "direction": "long", "scope": scope, "status": status}


async def test_release_exit_missing_pass_queues_a_summary_with_no_tallies(desk):
    store, policy, _ = desk
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce"))
    assert summary is not None and summary.tallies == [] and summary.open_incidents == []
    gaps = summary.release_exit
    assert gaps is not None and gaps.trading_day.isoformat() == TRADING_DAY
    assert gaps.missing_pass and not gaps.unknown and not gaps.unnotified_closes
    items = await store.store.list_work(WorkKind.NOTIFICATION)
    assert [i.payload["kind"] for i in items] == ["operations_summary"]


async def test_release_exit_lists_native_unknown_positions_only(desk):
    store, policy, _ = desk
    await journal_release_exit(
        store,
        positions=[
            position("sig-1", "ACME", "unknown"),
            position("sig-2", "BETA", "none"),
            position("sig-3", "GAMMA", "unknown", scope="probe"),
        ],
    )
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="preview"))
    gaps = summary.release_exit
    assert gaps is not None and not gaps.missing_pass
    assert [(p.symbol, p.signal_id) for p in gaps.unknown] == [("ACME", "sig-1")]


async def test_release_exit_lists_only_failed_closes_that_were_never_notified(desk):
    store, policy, _ = desk
    # The daemon's real shapes: a submitted or already-exiting close is journaled `notified: false` (no notice is
    # ever built for it); only a refused, failed or errored close outside the notice bound is a silent gap.
    await journal_release_exit(
        store,
        positions=[],
        closes=[
            ("sig-6", False, "submitted"),
            ("sig-7", False, "refused"),
            ("sig-8", True, "refused"),
            ("sig-9", False, "already_exiting"),
            ("sig-10", False, "failed"),
            ("sig-11", False, "error"),
        ],
    )
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce"))
    gaps = summary.release_exit
    assert gaps is not None and not gaps.missing_pass
    assert [(c.signal_id, c.outcome) for c in gaps.unnotified_closes] == [
        ("sig-10", "failed"),
        ("sig-11", "error"),
        ("sig-7", "refused"),
    ]


async def test_release_exit_successful_closes_leave_no_gap(desk):
    store, policy, _ = desk
    await journal_release_exit(
        store,
        positions=[position("sig-2", "BETA", "none")],
        closes=[("sig-6", False, "submitted"), ("sig-9", False, "already_exiting"), ("sig-8", True, "refused")],
    )
    assert await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce")) is None


async def test_release_exit_lists_owed_closes_that_were_never_attempted(desk):
    store, policy, _ = desk
    await journal_release_exit(
        store,
        positions=[
            position("sig-1", "ACME", "close"),
            position("sig-2", "BETA", "close"),
            position("sig-3", "GAMMA", "close", scope="probe"),
            position("sig-4", "DELTA", "would_close"),
            position("sig-5", "EPS", "none"),
        ],
        closes=[("sig-2", True, "submitted")],
    )
    summary = await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce"))
    gaps = summary.release_exit
    assert gaps is not None and not gaps.missing_pass and not gaps.unknown
    assert [(p.symbol, p.signal_id) for p in gaps.unattempted_closes] == [("ACME", "sig-1")]


async def test_release_exit_malformed_payloads_are_skipped_without_losing_the_summary(desk, caplog):
    store, policy, _ = desk
    secret = "SECRET-PAYLOAD-TEXT"
    await journal_release_exit(
        store,
        positions=[
            {"signal_id": "sig-0", "scope": "native", "status": "unknown", "note": secret},
            position("sig-1", "ACME", "unknown"),
        ],
        raw_closes=[
            ("sig-7", {"notified": False, "outcome": "refused", "note": secret}),
            ("sig-8", {"notified": False, "outcome": "failed", "symbol": "OK", "signal_id": "sig-8"}),
        ],
    )
    with caplog.at_level("WARNING", logger="agentic_trader.storage.operations"):
        summary = await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce"))
    gaps = summary.release_exit
    assert gaps is not None
    assert [(p.symbol, p.signal_id) for p in gaps.unknown] == [("ACME", "sig-1")]
    assert [(c.symbol, c.signal_id) for c in gaps.unnotified_closes] == [("OK", "sig-8")]
    skipped = [r for r in caplog.records if getattr(r, "event", None) == "release_exit_gap_event_skipped"]
    assert [r.error_type for r in skipped] == ["KeyError", "KeyError"]
    assert secret not in caplog.text and not any(secret in str(vars(r)) for r in caplog.records)


async def test_release_exit_complete_pass_adds_no_section_and_queues_nothing(desk):
    store, policy, _ = desk
    await journal_release_exit(
        store, positions=[position("sig-2", "BETA", "none")], closes=[("sig-8", True, "refused")]
    )
    assert await store.queue_quiet_summary(**summary_kwargs(policy, release_exit_mode="enforce")) is None
    assert not await store.store.list_work(WorkKind.NOTIFICATION)
    decisions = await store.store.events("operations/summary")
    assert [e["payload"]["queued"] for e in decisions] == [False]


async def test_release_exit_monday_morning_window_reads_fridays_pass(desk):
    store, policy, _ = desk
    friday = "2026-11-06"
    await journal_release_exit(store, positions=[position("sig-1", "ACME", "unknown")], day=friday)
    start = datetime(2026, 11, 6, 21, 30, tzinfo=UTC)  # Friday 16:30 ET, as quiet_period_before produces it
    end = datetime(2026, 11, 9, 14, 15, tzinfo=UTC)  # Monday 09:15 ET
    kwargs = summary_kwargs(policy, period_start=start, period_end=end, observed_at=end, release_exit_mode="enforce")
    summary = await store.queue_quiet_summary(**kwargs)
    gaps = summary.release_exit
    assert gaps is not None and gaps.trading_day.isoformat() == friday and not gaps.missing_pass
    assert [(p.symbol, p.signal_id) for p in gaps.unknown] == [("ACME", "sig-1")]


async def test_release_exit_section_is_absent_for_mode_off_and_non_alert_days(desk):
    store, policy, _ = desk
    assert await store.queue_quiet_summary(**summary_kwargs(policy)) is None  # default mode: off
    saturday = datetime(2026, 11, 7, 12, tzinfo=UTC)  # Saturday-to-Monday period never asks for Saturday's pass
    monday = datetime(2026, 11, 9, 14, 15, tzinfo=UTC)
    kwargs = summary_kwargs(
        policy, period_start=saturday, period_end=monday, observed_at=monday, release_exit_mode="enforce"
    )
    assert await store.queue_quiet_summary(**kwargs) is None
    assert not await store.store.list_work(WorkKind.NOTIFICATION)


def test_release_exit_gaps_are_optional_in_old_payloads():
    payload = {
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "observed_at": END.isoformat(),
        "failure_seconds": 120.0,
        "tallies": [],
        "open_incidents": [],
    }
    assert QuietHoursSummary.model_validate(payload).release_exit is None


@pytest.mark.parametrize("module", ["agentic_trader/storage/operations.py", "agentic_trader/diagnostics/monitor.py"])
def test_supervisor_modules_import_no_release_exit_or_agent_module(module):
    tree = ast.parse((Path(__file__).resolve().parents[2] / module).read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    banned = (
        "agentic_trader.agent",
        "agentic_trader.broker",
        "agentic_trader.data.earnings_capture",
        "agentic_trader.execution.closing",
        "agentic_trader.execution.entries",
        "agentic_trader.execution.lifetimes",
        "agentic_trader.execution.release_exit",
    )
    assert not [n for n in names if any(n == b or n.startswith(b + ".") for b in banned)]


async def test_monitor_passes_the_release_exit_mode_to_the_morning_summary(desk):
    store, policy, _ = desk
    policy.quiet_hours.enabled = True
    monitor = OperationsMonitor(store, policy, "enforce")
    # Tuesday 10:00 ET: the first pass of the window, Monday journaled no release-exit pass.
    at = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    await monitor.observe({"checked_at": at.isoformat(), "checks": {"scan": {"ready": True}}})
    (item,) = await store.store.list_work(WorkKind.NOTIFICATION)
    assert item.payload["kind"] == "operations_summary"
    assert QuietHoursSummary.model_validate(item.payload["arguments"]).release_exit.missing_pass is True
