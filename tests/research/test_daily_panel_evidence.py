"""Daily panel progress stays distinct from intraday scores and qualification."""

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
