"""Daily panel progress stays distinct from intraday scores and qualification."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from agentic_trader.config import AlphaPipelineConfig, DailyPanelWorkerConfig
from agentic_trader.research.alpha import evidence
from agentic_trader.research.alpha.evidence import build_daily_panel_evidence, load_daily_panel_evidence


@pytest.fixture
def daily_records():
    now = pd.Timestamp("2026-09-18T12:00Z")
    return now, {
        "campaigns": [
            {
                "campaign_id": "fixture",
                "protocol_hash": "a" * 64,
                "enrolled_at": "2026-09-01T00:00Z",
                "last_session": "2026-09-17",
                "protocol": {"feed": "alpaca:iex", "symbols": ["AAA", "BBB"], "models": ["ridge"]},
            }
        ],
        "comparisons": [],
        "decisions": [
            {
                "campaign_id": "fixture",
                "status": "scored",
                "session_date": "2026-09-17",
                "projection_recorded_at": now.isoformat(),
                "evidence": {"error": "private URL/token"},
            }
        ],
        "outcomes": [
            {
                "campaign_id": "fixture",
                "status": "unavailable",
                "projection_recorded_at": now.isoformat(),
                "evidence": {"error": "private URL/token"},
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize("truncated", [False, True])
def test_daily_summary_counts_campaign_sessions_and_retains_unknown_outcomes(daily_records, truncated):
    now, records = daily_records
    records["truncated"] = truncated
    result = build_daily_panel_evidence(DailyPanelWorkerConfig(), records, now=now, days=7)
    assert result["decision_sessions"] == 1 and result["outcome_sessions"] == 1
    assert result["decision_counts"]["scored"] == 1 and result["outcome_counts"]["unavailable"] == 1
    assert result["truncated"] is truncated and result["rows_loaded"] == 3
    assert not result["authorizes_promotion"] and not result["worker_enabled"]
    assert result["campaigns"][0]["symbols"] == 2
    assert "private URL/token" not in str(result)
    assert "protocol" not in result["campaigns"][0]
    assert result["comparisons"] == [] and result["comparison_totals"]["protocols"] == 0


@pytest.fixture
def comparison_records(daily_records):
    now, records = daily_records
    comparison = {
        "campaign_id": "fixture",
        "comparison_id": "baseline-v1",
        "protocol_hash": "b" * 64,
        "enrolled_at": "2026-09-02T00:00Z",
        "trial_count": 3,
        "protocol": {"models": ["rank_blend", "volatility20", "ridge"], "private": "/private/protocol.json"},
    }
    later = {**comparison, "comparison_id": "later", "protocol_hash": "c" * 64}
    records["comparisons"] = [comparison, later]
    decision, outcome = deepcopy(records["decisions"][0]), deepcopy(records["outcomes"][0])

    def row(template, primary_status, comparison_status, *, pinned=True):
        result = {**deepcopy(template), "status": primary_status, "comparisons": [comparison] if pinned else []}
        result["evidence"]["comparisons"] = (
            []
            if comparison_status is None
            else [
                {
                    "comparison_id": comparison["comparison_id"],
                    "protocol_hash": comparison["protocol_hash"],
                    "status": comparison_status,
                    "forecast": {"artifact": "/private/forecast.json"},
                    "outcome": {"artifact": "/private/outcome.json"},
                    "error": "private URL/token",
                }
            ]
        )
        return result

    records["decisions"] = [
        row(decision, "unavailable", "scored"),
        row(decision, "scored", "unavailable"),
        row(decision, "interrupted", None),
        row(decision, "scored", None, pinned=False),
    ]
    records["outcomes"] = [row(outcome, "unavailable", "complete"), row(outcome, "complete", None)]
    return now, records


def test_comparison_counts_follow_pinned_enrollment_not_primary_status_or_later_enrollment(comparison_records):
    now, records = comparison_records
    result = build_daily_panel_evidence(DailyPanelWorkerConfig(), records, now=now, days=7)
    assert result["decision_counts"]["scored"] == 2 and result["outcome_counts"]["complete"] == 1
    baseline, later = result["comparisons"]
    assert baseline["comparison_id"] == "baseline-v1" and baseline["models"] == 3
    assert baseline["decision_sessions"] == 3 and baseline["outcome_sessions"] == 2
    assert baseline["decision_recorded_sessions"] == 2 and baseline["decision_missing_summaries"] == 1
    assert baseline["outcome_recorded_sessions"] == 1 and baseline["outcome_missing_summaries"] == 1
    assert baseline["decision_counts"]["scored"] == baseline["decision_counts"]["unavailable"] == 1
    assert baseline["decision_counts"]["interrupted"] == 0  # Missing summary cannot inherit primary status.
    assert baseline["outcome_counts"]["complete"] == 1 and baseline["outcome_counts"]["unavailable"] == 0
    assert later["decision_sessions"] == later["outcome_sessions"] == 0
    totals = result["comparison_totals"]
    assert totals["protocols"] == 2 and totals["models"] == 6
    assert totals["decision_sessions"] == 3 and totals["outcome_sessions"] == 2
    assert totals["decision_counts"]["scored"] == totals["outcome_counts"]["complete"] == 1
    assert result["rows_loaded"] == 9 and not result["authorizes_promotion"]
    assert "private" not in str(result)


@pytest.mark.parametrize("keep_decisions", [False, True])
def test_truncation_preserves_comparison_outcome_denominators_without_metadata_or_decisions(
    comparison_records, keep_decisions
):
    now, records = comparison_records
    records["comparisons"] = []
    records["truncated"] = True
    if not keep_decisions:
        records["decisions"] = []
    result = build_daily_panel_evidence(DailyPanelWorkerConfig(), records, now=now, days=7)
    baseline = result["comparisons"][0]
    assert baseline["metadata_available"] is False and baseline["models"] is None
    assert baseline["decision_sessions"] == (3 if keep_decisions else 0)
    assert baseline["outcome_sessions"] == 2 and baseline["outcome_missing_summaries"] == 1
    assert result["comparison_totals"]["metadata_missing"] == 1
    assert result["comparison_totals"]["models"] is None and result["truncated"]


@pytest.mark.parametrize("defect", ["unpinned", "hash", "duplicate", "invalid_status"])
def test_invalid_comparison_summary_cannot_gain_reported_success(comparison_records, defect):
    now, records = comparison_records
    row = records["decisions"][0]
    summary = row["evidence"]["comparisons"][0]
    if defect == "unpinned":
        row["comparisons"] = []
    elif defect == "hash":
        summary["protocol_hash"] = "d" * 64
    elif defect == "duplicate":
        row["evidence"]["comparisons"].append(deepcopy(summary))
    else:
        summary["status"] = "complete"
    with pytest.raises(ValueError):
        build_daily_panel_evidence(DailyPanelWorkerConfig(), records, now=now, days=7)


def test_truncated_campaign_metadata_does_not_hide_retained_decisions(daily_records):
    now, records = daily_records
    records["campaigns"] = []
    records["truncated"] = True
    result = build_daily_panel_evidence(DailyPanelWorkerConfig(), records, now=now, days=7)
    assert result["decision_sessions"] == 1
    assert result["campaigns"][0]["campaign_id"] == "fixture"
    assert result["campaigns"][0]["metadata_available"] is False


async def test_daily_query_is_bounded_and_does_not_read_protocol_or_provider(daily_records, monkeypatch):
    now, records = daily_records
    repository = SimpleNamespace(policy=AlphaPipelineConfig(), store=object())
    query = AsyncMock(return_value=records)
    monkeypatch.setattr(evidence, "DailyCampaignRepository", lambda store, policy: SimpleNamespace(report=query))
    result = await load_daily_panel_evidence(repository, now=now, days=7, limit=50_000)
    query.assert_awaited_once_with(since=now - timedelta(days=7), now=now, limit=1000)
    assert result["row_limit"] == 1000


@pytest.mark.parametrize("days,limit", [(0, 10), (32, 10), (7, 0), (7, 50001)])
async def test_daily_query_rejects_invalid_bounds(days, limit):
    with pytest.raises(ValueError, match="bounds"):
        await load_daily_panel_evidence(None, days=days, limit=limit)
