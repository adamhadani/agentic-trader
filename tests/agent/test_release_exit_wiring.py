"""The desk's release exit: the service's collaborators, the exempt scopes and the daemon tick."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.data.earnings_capture import EarningsCaptureStore
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID


@pytest.fixture
def desk(app_config, temp_db):
    app_config.copilot_chat_enabled = False
    return TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=MagicMock(),
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )


def test_the_service_reuses_the_desks_collaborators(desk, temp_db):
    release = desk.release_exit
    assert release.config is desk.config.release_exit
    assert release.close_service is desk.close_service
    assert release.earnings is desk.earnings_calendar
    assert release.calendar is desk.lifetime_service.calendar
    assert isinstance(release.captures, EarningsCaptureStore) and release.captures.workflows is temp_db.workflows


@pytest.mark.parametrize(
    ("position", "scope"),
    [
        ({"strategy": PEAD_STRATEGY_ID}, "pead"),
        # Even with insider watch disabled, an open watch position is never treated as native.
        ({"strategy": INSIDER_WATCH_STRATEGY_ID}, "insider_watch"),
        ({"strategy": "TREND_PULLBACK", "decision_provenance": {"paper_probe": True}}, "probe"),
        ({"strategy": "x_long", "alpha_version": "v1", "decision_provenance": {"paper_probe": False}}, "alpha"),
        ({"strategy": "x_long", "alpha_policy": {"lifetime": None}}, "alpha"),
        ({"strategy": "TREND_PULLBACK", "decision_provenance": {"setup_quality": 0.7}}, "native"),
        ({"strategy": "TREND_PULLBACK", "decision_provenance": None}, "native"),
    ],
)
def test_release_exit_scope(desk, position, scope):
    assert desk._release_exit_scope(position) == scope


async def test_run_release_exit_delegates_one_tick(desk):
    desk.release_exit = AsyncMock()
    desk.release_exit.run.return_value = "waiting"
    now = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
    assert await desk.run_release_exit(now) == "waiting"
    desk.release_exit.run.assert_awaited_once_with(now)
