"""Operator evidence must retain failures and never imply fills or qualification."""

import asyncio
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from agentic_trader.config import SessionDecisionConfig
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha import evidence as evidence_module
from agentic_trader.research.alpha.evidence import build_forward_evidence, load_forward_evidence
from agentic_trader.research.alpha.models import AlphaDefinition, DecisionStatus, RegistrySnapshot


@pytest.fixture
def evidence_case():
    definition = AlphaDefinition(
        "control",
        "Control",
        "returns",
        timeframe="15m",
        eligible_symbols=("SPY",),
        data_feed="alpaca:sip",
        semantics_version=3,
        clock=SessionClockPolicy(),
    )
    now = pd.Timestamp("2026-09-17 15:00Z")
    snapshot = RegistrySnapshot(1, (), (definition,))
    cursor = {
        "generation": 1,
        "enrolled_at": (now - timedelta(days=2)).isoformat(),
        "checked_at": now.isoformat(),
        "calendar": {},
    }
    record = {
        "version_id": definition.version_id,
        "symbol": "SPY",
        "status": DecisionStatus.SCORED,
        "closed_at": "2026-09-17T14:45:00+00:00",
        "expires_at": "2026-09-17T14:48:00+00:00",
        "requested_at": "2026-09-17T14:46:05+00:00",
        "received_at": "2026-09-17T14:46:07+00:00",
        "committed_at": "2026-09-17T14:46:08+00:00",
        "dataset_hash": "fixture",
        "forecast": {"score": 2.5, "decision": 1, "valid": False},
    }
    inputs = {
        "decisions": [record],
        "cursors": {f"session-cursor/{definition.version_id}/SPY": cursor},
        "truncated": False,
    }

    def report():
        return build_forward_evidence(snapshot, SessionDecisionConfig(enabled=True), inputs, now=now, days=7)

    return record, inputs, report


@pytest.mark.parametrize("status", list(DecisionStatus))
def test_only_canonical_scored_outcomes_contribute_scores(evidence_case, status):
    record, _, report = evidence_case
    record["status"] = status  # Failed outcomes can retain a computed forecast.
    result = report()
    row = result["candidates"][0]
    assert row["counts"][status] == 1 and row["recorded_decisions"] == 1
    assert row["scores"]["count"] == (1 if status == DecisionStatus.SCORED else 0)
    assert row["recorded_score_fraction"] == (1 if status == DecisionStatus.SCORED else 0)
    assert row["overdue_pending"] == (1 if status == DecisionStatus.CLAIMED else 0)
    assert result["authorizes_promotion"] is False and result["coverage_basis"] == "recorded_decisions"


def test_receipts_measure_observed_upper_bound_and_read_duration(evidence_case):
    _, _, report = evidence_case
    row = report()["candidates"][0]
    assert row["receipt_lag_seconds"]["median"] == 67
    assert row["read_duration_seconds"]["median"] == 2
    assert row["score_directions"] == {"long": 1, "flat": 0, "short": 0}


@pytest.mark.parametrize("defect", ["absent", "reversed", "provider_failure"])
def test_missing_or_invalid_receipts_are_not_zero_latency(evidence_case, defect):
    record, _, report = evidence_case
    if defect == "absent":
        del record["received_at"]
    elif defect == "reversed":
        record["received_at"] = "2026-09-17T14:46:00+00:00"
    else:
        record["status"] = DecisionStatus.UNAVAILABLE
        del record["dataset_hash"]  # Provider exceptions have a completion time, not a receipt.
        record["reason"] = "private provider URL/token"
        record["error_type"] = "TimeoutError"
    row = report()["candidates"][0]
    assert row["receipt_lag_seconds"]["count"] == 0
    assert row["receipt_lag_seconds"]["median"] is None
    assert "private provider" not in str(row)


@pytest.mark.parametrize("condition", ["empty", "truncated", "gap", "not_enrolled", "generation"])
def test_incomplete_evidence_is_explicit(evidence_case, condition):
    _, inputs, report = evidence_case
    cursor = next(iter(inputs["cursors"].values()))
    if condition == "empty":
        inputs["decisions"] = []
    elif condition == "truncated":
        inputs["truncated"] = True
    elif condition == "gap":
        cursor["gap"] = {"start": "2026-09-16T00:00:00Z", "end": "2026-09-17T00:00:00Z", "reason": "registry_changed"}
    elif condition == "not_enrolled":
        inputs["cursors"] = {}
    else:
        cursor["generation"] = 0
    row = report()["candidates"][0]
    if condition in ("empty", "truncated"):
        assert row["recorded_score_fraction"] is None
    if condition == "empty":
        assert row["evidence_state"] == "no_recorded_decisions"
    elif condition == "truncated":
        assert report()["truncated"] is True
    else:
        assert row["coverage_warnings"]


def test_window_and_cohort_filtering_preserve_denominators(evidence_case):
    record, inputs, report = evidence_case
    inputs["decisions"] += [{**record, "closed_at": "2026-09-01T00:00:00Z"}, {**record, "version_id": "retired"}]
    result = report()
    assert result["outside_window_rows"] == 1 and result["other_cohort_rows"] == 1
    assert result["candidates"][0]["recorded_decisions"] == 1


async def test_report_rejects_registry_change_during_read(evidence_case):
    _, inputs, _ = evidence_case
    repository = SimpleNamespace(
        snapshot=AsyncMock(side_effect=[RegistrySnapshot(1, (), ()), RegistrySnapshot(2, (), ())]),
        forward_records=AsyncMock(return_value=inputs),
    )
    with pytest.raises(ValueError, match="registry changed"):
        await load_forward_evidence(repository)


@pytest.mark.parametrize("days,limit", [(0, 10), (32, 10), (7, 0), (7, 50001)])
async def test_report_rejects_unbounded_queries(days, limit):
    with pytest.raises(ValueError, match="bounds"):
        await load_forward_evidence(None, days=days, limit=limit)


async def test_evidence_statistics_leave_event_loop_responsive(monkeypatch):
    entered, release = Event(), Event()
    original = evidence_module.build_forward_evidence

    def slow_statistics(*args, **kwargs):
        entered.set()
        assert release.wait(2), "Evidence aggregation blocked the event loop"
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence_module, "build_forward_evidence", slow_statistics)
    repository = SimpleNamespace(
        snapshot=AsyncMock(return_value=RegistrySnapshot(0, (), ())),
        forward_records=AsyncMock(return_value={"decisions": [], "cursors": {}, "truncated": False}),
        policy=SimpleNamespace(decisions=SessionDecisionConfig()),
    )
    task = asyncio.create_task(load_forward_evidence(repository))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)  # Other callbacks can run while statistics remain blocked.
    finally:
        release.set()
    assert (await task)[1]["candidates"] == []
