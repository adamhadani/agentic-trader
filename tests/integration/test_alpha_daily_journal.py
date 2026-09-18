"""Daily claims use one replayable transaction across independent database clients."""

import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import event, text

from agentic_trader.market.bars import ObservationStatus
from agentic_trader.research.alpha.daily_plan import MAX_DAILY_COMPARISONS, DailyComparisonPlan, DailyPanelPlan
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.evidence import load_daily_panel_evidence
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.storage.alpha_daily import DailyCampaignRepository
from agentic_trader.storage.db import SignalDatabase


PROTOCOL = {"version": "daily-test-v1", "campaign_id": "daily", "models": ["a", "b", "c", "d"]}
STATE = {"artifact": "/private/test/state.json", "artifact_hash": "a" * 64}
NEXT_STATE = {"artifact": "/private/test/state-2.json", "artifact_hash": "b" * 64}
EVIDENCE = {"forecast": {"artifact": "/private/test/forecast.json", "artifact_hash": "c" * 64}, "arms": 4}
OUTCOME_EVIDENCE = {"outcome": {"artifact": "/private/test/outcome.json", "artifact_hash": "d" * 64}}
COMPARISON_FORECAST = {"artifact": "/private/test/baseline.json", "artifact_hash": "e" * 64}
COMPARISON_OUTCOME = {"artifact": "/private/test/baseline-outcome.json", "artifact_hash": "f" * 64}


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.postgres,
                pytest.mark.enable_socket,
                pytest.mark.allow_hosts(["localhost", "127.0.0.1"]),
            ],
        ),
    ]
)
async def daily_clients(request, temp_db, monkeypatch):
    first = temp_db if request.param == "sqlite" else SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
    await first.init_db()
    second = SignalDatabase(db_url=first.db_url)
    repos = [DailyCampaignRepository(db.workflows) for db in (first, second)]
    now = [pd.Timestamp("2026-09-17 20:00Z")]

    async def clock(_session):
        return now[0]

    for repo in repos:
        monkeypatch.setattr(repo, "_now", clock)
    try:
        yield SimpleNamespace(repos=repos, now=now, databases=(first, second), backend=request.param)
    finally:
        await second.engine.dispose()
        await first.engine.dispose()


def window(day="2026-09-17"):
    close = pd.Timestamp(day, tz="America/New_York") + pd.DateOffset(days=1)
    return {
        "native_close": close,
        "available_at": close + pd.Timedelta(minutes=30),
        "expires_at": close + pd.Timedelta(hours=3),
        "context": {
            "decision_window": {
                "outcome_available_at": "2026-10-17T04:30:00+00:00",
                "outcome_expires_at": "2026-10-17T07:00:00+00:00",
            },
            "calendar_hash": "d" * 64,
        },
    }


async def enroll_and_claim(case):
    await case.repos[0].enroll("daily", PROTOCOL, document_hash(PROTOCOL))
    case.now[0] = pd.Timestamp("2026-09-18 04:31Z")
    return await case.repos[0].claim_decision("daily", "2026-09-17", **window())


@pytest.fixture
def comparison_parent():
    return DailyPanelPlan(
        "daily",
        tuple(f"A{i:02}" for i in range(20)),
        tuple(f"F{i}" for i in range(9)),
        date(2026, 9, 17),
        date(2027, 9, 17),
        date(2021, 1, 1),
        "a" * 64,
        "b" * 64,
    )


@pytest.fixture
def comparison_plan(comparison_parent):
    return DailyComparisonPlan("baseline", "daily", comparison_parent.identity, date(2026, 9, 17))


def comparison_evidence(plan, *, kind="forecast", status=None):
    return {
        "comparison_id": plan.comparison_id,
        "protocol_hash": plan.identity,
        "status": status or (DecisionStatus.SCORED if kind == "forecast" else ObservationStatus.COMPLETE),
        kind: COMPARISON_FORECAST if kind == "forecast" else COMPARISON_OUTCOME,
    }


async def enroll_comparison(case, parent, plan):
    await case.repos[0].enroll(parent.campaign_id, parent.document(), parent.identity)
    return await case.repos[0].enroll_comparison(parent.campaign_id, plan.document(), plan.identity)


async def test_comparison_enrollment_charges_once_and_replays_without_changing_parent(
    daily_clients, comparison_parent, comparison_plan
):
    c, p = daily_clients, comparison_plan
    parent = await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    rows = await asyncio.gather(*(r.enroll_comparison("daily", p.document(), p.identity) for r in c.repos))
    assert rows[0] == rows[1]
    assert rows[0]["enrolled_at"] == c.now[0].isoformat()
    assert rows[0]["trial_count"] == 3 and rows[0]["protocol"] == p.document()
    assert (await c.repos[0].get("family/all"))["trial_count"] == 7
    assert await c.repos[0].campaign("daily") == parent
    changed = replace(p, first_decision_date=date(2026, 9, 18))
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[1].enroll_comparison("daily", changed.document(), changed.identity)
    await c.repos[1].rebuild()
    assert await c.repos[1].comparisons("daily") == rows[:1]
    assert (await c.repos[1].get("family/all"))["trial_count"] == 7
    now = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=1)
    report = await c.repos[0].report(since=now, now=now)
    assert len(report["comparisons"]) == 1 and report["comparisons"][0]["protocol"] == p.document()
    assert not report["decisions"] and not report["outcomes"]


@pytest.mark.parametrize("defect", ["campaign", "parent", "first_date", "hash"])
async def test_comparison_enrollment_rejects_contract_mismatch_without_charging(
    daily_clients, comparison_parent, comparison_plan, defect
):
    c, p = daily_clients, comparison_plan
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    if defect == "parent":
        p = replace(p, parent_protocol_hash="c" * 64)
    elif defect == "first_date":
        p = replace(p, first_decision_date=date(2027, 9, 18))
    with pytest.raises(ValueError):
        await c.repos[0].enroll_comparison(
            "other" if defect == "campaign" else "daily", p.document(), "d" * 64 if defect == "hash" else p.identity
        )
    assert (await c.repos[0].get("family/all"))["trial_count"] == 4
    assert await c.repos[0].comparisons("daily") == []


async def test_comparison_registration_limit_does_not_charge_rejected_or_duplicate_attempts(
    daily_clients, comparison_parent, comparison_plan
):
    c = daily_clients
    for i in range(MAX_DAILY_COMPARISONS):
        await enroll_comparison(c, comparison_parent, replace(comparison_plan, comparison_id=f"baseline-{i}"))
    with pytest.raises(ValueError, match="limit|bounded"):
        await enroll_comparison(c, comparison_parent, replace(comparison_plan, comparison_id="overflow"))
    p = replace(comparison_plan, comparison_id="baseline-0")
    await c.repos[0].enroll_comparison("daily", p.document(), p.identity)
    assert len(await c.repos[1].comparisons("daily")) == MAX_DAILY_COMPARISONS
    assert (await c.repos[0].get("family/all"))["trial_count"] == 4 + 3 * MAX_DAILY_COMPARISONS


@pytest.mark.parametrize(
    "enrolled_at,first_date,expected",
    [
        ("2026-09-18T03:59:59Z", date(2026, 9, 17), True),
        ("2026-09-18T04:00Z", date(2026, 9, 17), False),
        ("2026-09-18T04:01Z", date(2026, 9, 17), False),
        ("2026-09-18T03:59Z", date(2026, 9, 18), False),
    ],
)
async def test_comparison_pin_requires_preclose_enrollment_and_declared_first_date(
    daily_clients, comparison_parent, comparison_plan, enrolled_at, first_date, expected
):
    c = daily_clients
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    p = replace(comparison_plan, first_decision_date=first_date)
    c.now[0] = pd.Timestamp(enrolled_at)
    enrollment = await c.repos[0].enroll_comparison("daily", p.document(), p.identity)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[1].claim_decision("daily", "2026-09-17", **window())
    assert claim["comparisons"] == ([enrollment] if expected else [])


async def test_primary_abstention_keeps_successful_comparison_and_frozen_outcome_after_restart(
    daily_clients, comparison_parent, comparison_plan
):
    c, p = daily_clients, comparison_plan
    enrollment = await enroll_comparison(c, comparison_parent, p)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    evidence = {**EVIDENCE, "comparisons": [comparison_evidence(p)]}
    with pytest.raises(ValueError, match="state"):
        await c.repos[0].finish_decision(claim, status=DecisionStatus.UNAVAILABLE, evidence=evidence)
    decision = await c.repos[0].finish_decision(
        claim, status=DecisionStatus.UNAVAILABLE, evidence=evidence, state_ref=STATE
    )
    assert decision["status"] == DecisionStatus.UNAVAILABLE
    assert (await c.repos[0].campaign("daily"))["state_ref"] == STATE
    # Registering another comparison cannot change the already saved decision.
    later = replace(p, comparison_id="later")
    await c.repos[1].enroll_comparison("daily", later.document(), later.identity)
    await c.repos[1].rebuild()
    c.now[0] = pd.Timestamp("2026-10-17T04:31Z")
    outcome = await c.repos[1].claim_outcome(
        claim["decision_id"], available_at="2026-10-17T04:30Z", expires_at="2026-10-17T07:00Z"
    )
    assert outcome["comparisons"] == [enrollment]
    completed = await c.repos[1].finish_outcome(
        outcome,
        status=ObservationStatus.UNAVAILABLE,
        evidence={**OUTCOME_EVIDENCE, "comparisons": [comparison_evidence(p, kind="outcome")]},
    )
    assert completed["status"] == ObservationStatus.UNAVAILABLE
    assert completed["evidence"]["comparisons"][0]["status"] == ObservationStatus.COMPLETE
    assert await c.repos[0].decision(claim["decision_id"]) == decision
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[0].finish_outcome(
            outcome,
            status=ObservationStatus.UNAVAILABLE,
            evidence={
                **OUTCOME_EVIDENCE,
                "comparisons": [comparison_evidence(p, kind="outcome", status="unavailable")],
            },
        )
    events = await c.repos[0].store.events()
    now = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=1)
    summary = await load_daily_panel_evidence(c.repos[0], now=now)
    baseline = next(row for row in summary["comparisons"] if row["comparison_id"] == p.comparison_id)
    assert baseline["metadata_available"] and baseline["models"] == 3
    assert baseline["decision_sessions"] == baseline["decision_counts"]["scored"] == 1
    assert baseline["outcome_sessions"] == baseline["outcome_counts"]["complete"] == 1
    assert summary["decision_counts"]["unavailable"] == summary["outcome_counts"]["unavailable"] == 1
    assert not baseline["decision_missing_summaries"] and not baseline["outcome_missing_summaries"]
    truncated = await load_daily_panel_evidence(c.repos[0], now=now, limit=1)
    assert truncated["truncated"] and truncated["rows_loaded"] == 1
    (baseline,) = truncated["comparisons"]
    assert not baseline["metadata_available"] and baseline["models"] is None
    assert baseline["decision_sessions"] == 0
    assert baseline["outcome_sessions"] == baseline["outcome_counts"]["complete"] == 1
    assert "/private/test" not in str(summary) + str(truncated)
    assert not summary["authorizes_promotion"]
    assert await c.repos[0].store.events() == events


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown", "hash", "artifact", "status"])
async def test_completion_refuses_unpinned_or_missing_comparison_evidence(
    daily_clients, comparison_parent, comparison_plan, defect
):
    c, p = daily_clients, comparison_plan
    await enroll_comparison(c, comparison_parent, p)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    row = comparison_evidence(p)
    rows = [] if defect == "missing" else [row, row] if defect == "duplicate" else [row]
    if defect == "unknown":
        row["comparison_id"] = "unknown"
    elif defect == "hash":
        row["protocol_hash"] = "f" * 64
    elif defect == "artifact":
        row["forecast"] = {"artifact": "/private/test/invalid"}
    elif defect == "status":
        row["status"] = "promoted"
    with pytest.raises(ValueError, match="(?i)comparison|artifact"):
        await c.repos[0].finish_decision(
            claim, status=DecisionStatus.UNAVAILABLE, evidence={**EVIDENCE, "comparisons": rows}, state_ref=STATE
        )
    assert await c.repos[1].decision(claim["decision_id"]) == claim


async def test_failed_capture_preserves_pins_and_does_not_invent_forecasts(
    daily_clients, comparison_parent, comparison_plan
):
    c = daily_clients
    await enroll_comparison(c, comparison_parent, comparison_plan)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    failed = await c.repos[1].finish_decision(
        claim,
        status=DecisionStatus.UNAVAILABLE,
        evidence={"failure": COMPARISON_FORECAST, "error_type": "BarAcquisitionError", "comparisons": []},
    )
    assert failed["comparisons"] == claim["comparisons"]
    assert not failed["evidence"]["comparisons"] and failed["state_ref"] is None
    assert (await c.repos[0].campaign("daily"))["state_generation"] == 0


async def test_enrollment_after_claim_cannot_retrofit_existing_forecast(
    daily_clients, comparison_parent, comparison_plan
):
    c, p = daily_clients, comparison_plan
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    assert claim["comparisons"] == []
    await c.repos[1].enroll_comparison("daily", p.document(), p.identity)
    with pytest.raises(ValueError, match="(?i)comparison"):
        await c.repos[0].finish_decision(
            claim,
            status=DecisionStatus.SCORED,
            evidence={**EVIDENCE, "comparisons": [comparison_evidence(p)]},
            state_ref=STATE,
        )
    result = await c.repos[1].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    assert result["comparisons"] == []


async def test_historical_claim_without_pins_stays_primary_only_after_enrollment(
    daily_clients, comparison_parent, comparison_plan
):
    c = daily_clients
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    original = c.repos[0]._write

    async def historical_claim(session, kind, identity, payload):
        if kind == "decision":
            payload.pop("comparisons")
            payload.pop("claim_hash")
            payload["claim_hash"] = document_hash(payload)
        await original(session, kind, identity, payload)

    # Persist the original v1 wire contract, without retrospectively editing it.
    c.repos[0]._write = historical_claim
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    c.repos[0]._write = original
    assert "comparisons" not in claim
    await c.repos[1].enroll_comparison("daily", comparison_plan.document(), comparison_plan.identity)
    decision = await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    assert "comparisons" not in decision
    c.now[0] = pd.Timestamp("2026-10-17T04:31Z")
    outcome = await c.repos[1].claim_outcome(
        claim["decision_id"], available_at="2026-10-17T04:30Z", expires_at="2026-10-17T07:00Z"
    )
    assert outcome["comparisons"] == []
    await c.repos[1].finish_outcome(outcome, status=ObservationStatus.COMPLETE, evidence=OUTCOME_EVIDENCE)
    await c.repos[1].rebuild()
    assert await c.repos[0].decision(claim["decision_id"]) == decision


async def test_unavailable_comparison_cannot_gain_complete_outcome(daily_clients, comparison_parent, comparison_plan):
    c, p = daily_clients, comparison_plan
    await enroll_comparison(c, comparison_parent, p)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    await c.repos[0].finish_decision(
        claim,
        status=DecisionStatus.UNAVAILABLE,
        evidence={**EVIDENCE, "comparisons": [comparison_evidence(p, status=DecisionStatus.UNAVAILABLE)]},
    )
    c.now[0] = pd.Timestamp("2026-10-17T04:31Z")
    outcome = await c.repos[0].claim_outcome(
        claim["decision_id"], available_at="2026-10-17T04:30Z", expires_at="2026-10-17T07:00Z"
    )
    with pytest.raises(ValueError, match="unavailable comparison"):
        await c.repos[1].finish_outcome(
            outcome,
            status=ObservationStatus.UNAVAILABLE,
            evidence={**OUTCOME_EVIDENCE, "comparisons": [comparison_evidence(p, kind="outcome")]},
        )
    unavailable = {**comparison_evidence(p, kind="outcome", status="unavailable"), "reason": "forecast_abstained"}
    unavailable.pop("outcome")
    result = await c.repos[1].finish_outcome(
        outcome, status=ObservationStatus.UNAVAILABLE, evidence={"comparisons": [unavailable]}
    )
    assert result["evidence"]["comparisons"] == [unavailable]


async def test_late_companion_score_is_forensic_only_and_cannot_advance_state(
    daily_clients, comparison_parent, comparison_plan
):
    c, p = daily_clients, comparison_plan
    await enroll_comparison(c, comparison_parent, p)
    c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
    claim = await c.repos[0].claim_decision("daily", "2026-09-17", **window())
    c.now[0] = pd.Timestamp(claim["expires_at"])
    evidence = {**EVIDENCE, "comparisons": [comparison_evidence(p)]}
    result = await c.repos[1].finish_decision(
        claim, status=DecisionStatus.UNAVAILABLE, evidence=evidence, state_ref=STATE
    )
    assert result["status"] == DecisionStatus.INTERRUPTED and "evidence" not in result
    assert (await c.repos[0].campaign("daily"))["state_generation"] == 0
    assert any(e["payload"]["value"].get("evidence") == evidence for e in await c.repos[0].store.events())


async def test_failed_comparison_enrollment_rolls_back_charge_and_reservation(
    daily_clients, comparison_parent, comparison_plan, monkeypatch
):
    c, p = daily_clients, comparison_plan
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    original = c.repos[0]._write

    async def fail_enrollment(session, kind, identity, payload):
        if kind == "comparison":
            raise RuntimeError("injected enrollment failure")
        await original(session, kind, identity, payload)

    monkeypatch.setattr(c.repos[0], "_write", fail_enrollment)
    before = await c.repos[0].store.events()
    with pytest.raises(RuntimeError, match="injected"):
        await c.repos[0].enroll_comparison("daily", p.document(), p.identity)
    assert await c.repos[1].store.events() == before
    assert await c.repos[1].comparisons("daily") == []
    assert (await c.repos[1].get("family/all"))["trial_count"] == 4
    await c.repos[1].enroll_comparison("daily", p.document(), p.identity)
    assert (await c.repos[1].get("family/all"))["trial_count"] == 7


@pytest.mark.parametrize("first_operation", ["enrollment", "claim"])
async def test_postgres_comparison_enrollment_and_claim_have_one_lock_order(
    daily_clients, comparison_parent, comparison_plan, first_operation, monkeypatch
):
    c, p = daily_clients, comparison_plan
    if c.backend != "postgres":
        pytest.skip("Real PostgreSQL lock contention is exercised by the gated fixture")
    await c.repos[0].enroll("daily", comparison_parent.document(), comparison_parent.identity)
    paused, release, submitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    owner_pid = writer_pid = None
    original = c.repos[0]._write

    async def pause_first_transaction(session, kind, identity, payload):
        nonlocal owner_pid
        await original(session, kind, identity, payload)
        if kind == ("comparison" if first_operation == "enrollment" else "decision"):
            owner_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            paused.set()
            await release.wait()

    def observe(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal writer_pid
        if "workflow_locks" in statement:
            writer_pid = connection.connection.driver_connection.get_server_pid()
            submitted.set()

    async def enroll(repo):
        return await repo.enroll_comparison("daily", p.document(), p.identity)

    async def claim(repo):
        return await repo.claim_decision("daily", "2026-09-17", **window())

    first_action, second_action = (enroll, claim) if first_operation == "enrollment" else (claim, enroll)
    c.now[0] = pd.Timestamp("2026-09-18T03:59Z" if first_operation == "enrollment" else "2026-09-18T04:31Z")
    monkeypatch.setattr(c.repos[0], "_write", pause_first_transaction)
    event.listen(c.databases[1].engine.sync_engine, "before_cursor_execute", observe)
    tasks = []
    try:
        async with asyncio.timeout(10):
            tasks.append(asyncio.create_task(first_action(c.repos[0])))
            await paused.wait()
            c.now[0] = pd.Timestamp("2026-09-18T04:31Z")
            tasks.append(asyncio.create_task(second_action(c.repos[1])))
            await submitted.wait()
            async with c.databases[0].session_factory() as observer:
                while not await observer.scalar(
                    text("SELECT :owner = ANY(pg_blocking_pids(:writer))"), {"owner": owner_pid, "writer": writer_pid}
                ):
                    if tasks[1].done():
                        await tasks[1]
                        pytest.fail("Enrollment and claim bypassed their shared journal lock")
                    await asyncio.sleep(0.01)
            release.set()
            results = await asyncio.gather(*tasks)
        enrollment, decision = results if first_operation == "enrollment" else reversed(results)
        assert decision["comparisons"] == ([enrollment] if first_operation == "enrollment" else [])
        assert (await c.repos[1].get("family/all"))["trial_count"] == 7
        assert await c.repos[1].decision(decision["decision_id"]) == decision
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        event.remove(c.databases[1].engine.sync_engine, "before_cursor_execute", observe)


async def test_enrollment_claim_and_state_are_atomic_immutable_and_replayable(daily_clients):
    c = daily_clients
    enrolled = await asyncio.gather(*(r.enroll("daily", PROTOCOL, document_hash(PROTOCOL)) for r in c.repos))
    assert enrolled[0] == enrolled[1]
    assert enrolled[0]["enrolled_at"] == c.now[0].isoformat()
    assert (await c.repos[0].get("family/all"))["trial_count"] == 4
    changed = {**PROTOCOL, "version": "changed"}
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[1].enroll("daily", changed, document_hash(changed))
    c.now[0] = pd.Timestamp("2026-09-18 04:31Z")
    claims = await asyncio.gather(*(r.claim_decision("daily", "2026-09-17", **window()) for r in c.repos))
    (claim,) = [x for x in claims if x is not None]
    assert claim["parent_generation"] == 0 and claim["parent_state"] is None
    assert claim["status"] == DecisionStatus.CLAIMED
    result = await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    campaign = await c.repos[1].campaign("daily")
    assert campaign["state_ref"] == STATE and campaign["state_generation"] == 1
    assert campaign["inflight_decision"] is None
    assert result["evidence"] == EVIDENCE and result["recorded_at"] == c.now[0].isoformat()
    assert (
        await c.repos[1].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
        == result
    )
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[1].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=NEXT_STATE)
    await c.repos[1].rebuild()
    assert await c.repos[0].campaign("daily") == campaign
    assert await c.repos[0].decision(claim["decision_id"]) == result
    assert (await c.repos[0].get("family/all"))["trial_count"] == 4
    assert (await c.repos[0].snapshot()).active == ()


@pytest.mark.parametrize("terminal_status", [DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE])
async def test_only_one_inflight_and_timely_state_advances_even_if_scores_unavailable(daily_clients, terminal_status):
    c = daily_clients
    claim = await enroll_and_claim(c)
    overlapping = {
        **window("2026-09-18"),
        "native_close": window()["native_close"],
        "available_at": window()["available_at"],
    }
    assert await c.repos[1].claim_decision("daily", "2026-09-18", **overlapping) is None
    await c.repos[0].finish_decision(claim, status=terminal_status, evidence=EVIDENCE, state_ref=STATE)
    c.now[0] = pd.Timestamp("2026-09-19 04:31Z")
    next_claim = await c.repos[1].claim_decision("daily", "2026-09-18", **window("2026-09-18"))
    assert next_claim["parent_state"] == STATE and next_claim["parent_generation"] == 1
    assert await c.repos[0].claim_decision("daily", "2026-09-17", **window()) is None


@pytest.mark.parametrize("expire_first", [False, True])
async def test_expired_and_late_results_never_advance_state_or_replay(daily_clients, expire_first):
    c = daily_clients
    claim = await enroll_and_claim(c)
    c.now[0] = pd.Timestamp("2026-09-18 07:00Z")
    if expire_first:
        expired = await c.repos[1].expire("daily")
        assert len(expired) == 1
    result = await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    assert result["status"] == DecisionStatus.INTERRUPTED
    assert (await c.repos[0].campaign("daily"))["state_generation"] == 0
    assert (await c.repos[0].campaign("daily"))["state_ref"] is None
    assert await c.repos[1].claim_decision("daily", "2026-09-17", **window()) is None
    events = await c.repos[0].store.events()
    assert any(e["payload"]["value"].get("evidence") == EVIDENCE for e in events)


async def test_no_backdated_enrollment_or_changed_calendar_and_bounded_history(daily_clients):
    c = daily_clients
    await c.repos[0].enroll("daily", PROTOCOL, document_hash(PROTOCOL))
    c.now[0] = pd.Timestamp("2026-09-19 05:00Z")
    assert await c.repos[0].claim_decision("daily", "2026-09-16", **window("2026-09-16")) is None
    assert await c.repos[0].claim_decision("daily", "2026-09-17", **window()) is None
    claim = await c.repos[0].claim_decision("daily", "2026-09-18", **window("2026-09-18"))
    assert claim is not None
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[1].claim_decision(
            "daily", "2026-09-18", **{**window("2026-09-18"), "expires_at": c.now[0] + pd.Timedelta(days=1)}
        )
    first = await c.repos[0].records("daily", limit=1)
    assert first["truncated"] and first["next_after"]
    second = await c.repos[0].records("daily", limit=10, after=first["next_after"])
    assert not second["truncated"]
    records = first["records"] + second["records"]
    assert len(records) == 3
    assert [r["status"] for r in records] == [DecisionStatus.MISSED, DecisionStatus.MISSED, DecisionStatus.CLAIMED]


async def test_outcomes_are_distinct_frozen_one_shot_and_unavailable_is_terminal(daily_clients):
    c = daily_clients
    claim = await enroll_and_claim(c)
    decision = await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    windows = claim["context"]["decision_window"]
    kwargs = {"available_at": windows["outcome_available_at"], "expires_at": windows["outcome_expires_at"]}
    assert await c.repos[0].claim_outcome(claim["decision_id"], **kwargs) is None
    c.now[0] = pd.Timestamp(kwargs["available_at"])
    with pytest.raises(ValueError, match="frozen"):
        await c.repos[0].claim_outcome(
            claim["decision_id"], **{**kwargs, "expires_at": c.now[0] + pd.Timedelta(days=1)}
        )
    claims = await asyncio.gather(*(r.claim_outcome(claim["decision_id"], **kwargs) for r in c.repos))
    (outcome,) = [x for x in claims if x is not None]
    result = await c.repos[0].finish_outcome(
        outcome, status=ObservationStatus.UNAVAILABLE, evidence={"reason": "zero_source_volume", **OUTCOME_EVIDENCE}
    )
    assert await c.repos[1].claim_outcome(claim["decision_id"], **kwargs) is None
    with pytest.raises(ValueError, match="immutable"):
        await c.repos[1].finish_outcome(outcome, status=ObservationStatus.COMPLETE, evidence=OUTCOME_EVIDENCE)
    await c.repos[1].rebuild()
    assert await c.repos[0].outcome(claim["decision_id"]) == result
    assert await c.repos[0].decision(claim["decision_id"]) == decision


@pytest.mark.parametrize("mutation", ["claim_id", "parent_generation", "parent_state"])
async def test_forged_claim_cannot_write_forecast_or_state(daily_clients, mutation):
    c = daily_clients
    claim = await enroll_and_claim(c)
    forged = {**claim, mutation: "forged"}
    with pytest.raises(ValueError, match="claim"):
        await c.repos[1].finish_decision(forged, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    assert (await c.repos[0].campaign("daily"))["state_generation"] == 0


async def test_forecast_and_state_pointer_roll_back_together(daily_clients, monkeypatch):
    c = daily_clients
    claim = await enroll_and_claim(c)
    before = await c.repos[0].campaign("daily")
    original = c.repos[0]._write

    async def fail_campaign(session, kind, identity, payload):
        if kind == "campaign":
            raise RuntimeError("injected failure after forecast event")
        await original(session, kind, identity, payload)

    monkeypatch.setattr(c.repos[0], "_write", fail_campaign)
    with pytest.raises(RuntimeError, match="injected failure"):
        await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    assert await c.repos[1].campaign("daily") == before
    assert await c.repos[1].decision(claim["decision_id"]) == claim
    await c.repos[1].rebuild()
    assert await c.repos[1].campaign("daily") == before
    assert await c.repos[1].decision(claim["decision_id"]) == claim


@pytest.mark.parametrize("expire_first", [False, True])
async def test_outcome_crash_or_late_finish_keeps_unknown_primary(daily_clients, expire_first):
    c = daily_clients
    claim = await enroll_and_claim(c)
    await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    w = claim["context"]["decision_window"]
    c.now[0] = pd.Timestamp(w["outcome_available_at"])
    outcome = await c.repos[1].claim_outcome(
        claim["decision_id"], available_at=w["outcome_available_at"], expires_at=w["outcome_expires_at"]
    )
    c.now[0] = pd.Timestamp(w["outcome_expires_at"])
    if expire_first:
        assert len(await c.repos[1].expire("daily")) == 1
    result = await c.repos[0].finish_outcome(outcome, status=ObservationStatus.COMPLETE, evidence=OUTCOME_EVIDENCE)
    assert result["status"] == ObservationStatus.UNAVAILABLE and result["reason"] == "outcome_expired"
    assert "evidence" not in result
    await c.repos[1].rebuild()
    assert await c.repos[0].outcome(claim["decision_id"]) == result


@pytest.mark.parametrize(
    "kind,evidence",
    [
        ("decision", {}),
        ("decision", {"forecast": None}),
        ("decision", {"forecast": {"artifact": "/private/missing-hash.json"}}),
        ("decision", {**EVIDENCE, "inputs": {"artifact": "input.json", "artifact_hash": "wrong"}}),
        ("outcome", {}),
        ("outcome", {"outcome": {"artifact": "outcome.json", "artifact_hash": "wrong"}}),
    ],
)
async def test_scientific_completion_requires_valid_immutable_artifact_references(daily_clients, kind, evidence):
    c = daily_clients
    claim = await enroll_and_claim(c)
    if kind == "decision":
        with pytest.raises(ValueError, match="artifact"):
            await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=evidence, state_ref=STATE)
        assert await c.repos[0].decision(claim["decision_id"]) == claim
    else:
        await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
        w = claim["context"]["decision_window"]
        c.now[0] = pd.Timestamp(w["outcome_available_at"])
        outcome = await c.repos[0].claim_outcome(
            claim["decision_id"], available_at=w["outcome_available_at"], expires_at=w["outcome_expires_at"]
        )
        with pytest.raises(ValueError, match="artifact"):
            await c.repos[0].finish_outcome(outcome, status=ObservationStatus.COMPLETE, evidence=evidence)
        assert await c.repos[0].outcome(claim["decision_id"]) == outcome


async def test_report_is_one_bounded_projection_read_with_actual_event_times(daily_clients):
    c = daily_clients
    before = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=1)
    claim = await enroll_and_claim(c)
    await c.repos[0].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
    windows = claim["context"]["decision_window"]
    c.now[0] = pd.Timestamp(windows["outcome_available_at"])
    outcome = await c.repos[0].claim_outcome(
        claim["decision_id"],
        available_at=windows["outcome_available_at"],
        expires_at=windows["outcome_expires_at"],
    )
    await c.repos[0].finish_outcome(
        outcome,
        status=ObservationStatus.UNAVAILABLE,
        evidence={**OUTCOME_EVIDENCE, "error": "provider-token-fixture"},
    )
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(c.databases[0].engine.sync_engine, "before_cursor_execute", observe)
    try:
        now = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=1)
        report = await c.repos[0].report(since=before, now=now, limit=10)
        assert len(statements) == 1
        assert len(report["campaigns"]) == len(report["decisions"]) == len(report["outcomes"]) == 1
        assert not report["truncated"]
        assert all(
            before <= pd.Timestamp(r["projection_recorded_at"]) <= now
            for r in report["campaigns"] + report["decisions"] + report["outcomes"]
        )
        truncated = await c.repos[0].report(since=before, now=now, limit=1)
        assert truncated["truncated"]
        assert sum(len(truncated[k]) for k in ("campaigns", "decisions", "outcomes")) == 1
        older = await c.repos[0].report(since=before - pd.Timedelta(days=1), now=before, limit=10)
        assert not any(older[k] for k in ("campaigns", "decisions", "outcomes"))
        idle = await c.repos[0].report(since=now, now=now, limit=10)
        assert len(idle["campaigns"]) == 1 and not idle["decisions"]
        events_before = await c.repos[0].store.events()
        statements.clear()
        summary = await load_daily_panel_evidence(c.repos[0], now=now)
        assert len(statements) == 1, "Application reports must preserve the single bounded projection read"
        assert summary["rows_loaded"] == 3 and len(summary["campaigns"]) == 1
        assert summary["campaigns"][0]["protocol_hash"] == document_hash(PROTOCOL)
        assert summary["decision_sessions"] == summary["decision_counts"]["scored"] == 1
        assert summary["outcome_sessions"] == summary["outcome_counts"]["unavailable"] == 1
        assert not summary["authorizes_promotion"] and not summary["worker_enabled"]
        assert not summary["truncated"]
        assert "/private/test" not in str(summary) and "provider-token-fixture" not in str(summary)
        assert await c.repos[0].store.events() == events_before
    finally:
        event.remove(c.databases[0].engine.sync_engine, "before_cursor_execute", observe)


@pytest.mark.postgres
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["localhost", "127.0.0.1"])
async def test_postgres_clock_is_sampled_after_actual_cross_client_lock_wait(postgres_test_db, monkeypatch):
    databases = [SignalDatabase(db_url=postgres_test_db) for _ in range(2)]
    repos = [DailyCampaignRepository(db.workflows) for db in databases]
    writer_pid = None
    submitted = asyncio.Event()

    def observe(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal writer_pid
        if "workflow_locks" in statement:
            writer_pid = connection.connection.driver_connection.get_server_pid()
            submitted.set()

    original = repos[1]._now
    finish = None
    event.listen(databases[1].engine.sync_engine, "before_cursor_execute", observe)
    try:
        # Fixture setup is historical. Restore the actual database clock before
        # the contested transition; no injected caller timestamp can pass it.
        async def past(_session):
            return pd.Timestamp("2020-01-01T00:00Z")

        monkeypatch.setattr(repos[0], "_now", past)
        await repos[0].enroll("daily", PROTOCOL, document_hash(PROTOCOL))

        async def claim_time(_session):
            return pd.Timestamp("2026-09-18 04:31Z")

        monkeypatch.setattr(repos[0], "_now", claim_time)
        claim = await repos[0].claim_decision("daily", "2026-09-17", **window())
        # Set an actual near-future deadline on a new fixture campaign/date via
        # its windows, instead of depending on the machine's calendar date.
        async with databases[0].session_factory() as session:
            actual_now = await original(session)
        await repos[0].finish_decision(claim, status=DecisionStatus.UNAVAILABLE, evidence={"reason": "fixture"})
        kwargs = window("2099-01-01")
        kwargs.update(
            native_close=actual_now - pd.Timedelta(seconds=1),
            available_at=actual_now - pd.Timedelta(milliseconds=100),
            expires_at=actual_now + pd.Timedelta(milliseconds=500),
        )
        monkeypatch.setattr(repos[0], "_now", original)
        claim = await repos[0].claim_decision("daily", "2099-01-01", **kwargs)
        async with asyncio.timeout(10):
            async with databases[0].session_factory() as session, session.begin():
                await databases[0].workflows.lock(session, resource="alpha")
                owner = await session.scalar(text("SELECT pg_backend_pid()"))
                finish = asyncio.create_task(
                    repos[1].finish_decision(claim, status=DecisionStatus.SCORED, evidence=EVIDENCE, state_ref=STATE)
                )
                await submitted.wait()
                while not await session.scalar(
                    text("SELECT :owner = ANY(pg_blocking_pids(:writer))"), {"owner": owner, "writer": writer_pid}
                ):
                    if finish.done():
                        await finish
                        pytest.fail("Daily completion bypassed the journal lock")
                    await asyncio.sleep(0.01)
                while await original(session) < pd.Timestamp(claim["expires_at"]):
                    await asyncio.sleep(0.01)
            result = await finish
        assert result["status"] == DecisionStatus.INTERRUPTED
        assert pd.Timestamp(result["recorded_at"]) >= pd.Timestamp(claim["expires_at"])
        assert (await repos[0].campaign("daily"))["state_generation"] == 0
    finally:
        if finish is not None:
            if not finish.done():
                finish.cancel()
            await asyncio.gather(finish, return_exceptions=True)
        event.remove(databases[1].engine.sync_engine, "before_cursor_execute", observe)
        for db in databases:
            await db.engine.dispose()
