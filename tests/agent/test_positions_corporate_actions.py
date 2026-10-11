"""`/positions` notes for held symbols' corporate actions; stale/missing evidence is one note; disabled adds none."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, CorporateActionsLookup


def _position(symbol):
    position = MagicMock()
    position.symbol, position.direction, position.quantity = symbol, "LONG", 10.0
    position.entry_price, position.current_price, position.unrealized_pnl = 100.0, 101.0, 10.0
    position.model_dump = MagicMock(return_value={"symbol": symbol})
    return position


@pytest.fixture
def copilot(scan_desk):
    scan_desk.broker.authoritative_positions = True
    scan_desk.broker.get_positions = AsyncMock(return_value=[_position("XOM"), _position("NVDA")])
    return scan_desk


def _lookup(copilot, status, *actions):
    copilot.corporate_actions = MagicMock()
    today = datetime.now(UTC).date()
    copilot.corporate_actions.lookup = AsyncMock(return_value=CorporateActionsLookup(status, today, tuple(actions)))
    return today


async def test_notes_name_upcoming_and_recent_actions_for_held_symbols(copilot):
    _lookup(
        copilot,
        "ok",
        CorporateAction(
            id="d",
            kind=CorporateActionKind.CASH_DIVIDEND,
            symbol="XOM",
            effective_on=today_plus(2),
            rate=0.99,
            payable_date=today_plus(30),
        ),
        CorporateAction(
            id="s",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="NVDA",
            effective_on=today_plus(-3),
            old_rate=1.0,
            new_rate=10.0,
        ),
        CorporateAction(
            id="far",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="NVDA",
            effective_on=today_plus(40),
            old_rate=1.0,
            new_rate=2.0,
        ),
        CorporateAction(
            id="other",
            kind=CorporateActionKind.FORWARD_SPLIT,
            symbol="AAPL",
            effective_on=today_plus(1),
            old_rate=1.0,
            new_rate=2.0,
        ),
    )
    report = await copilot.get_positions_report()
    assert (
        f"XOM: cash dividend $0.99/share, ex {today_plus(2).isoformat()} (in 2 days), pays {today_plus(30).isoformat()}."
        in report.notes
    )
    assert "NVDA: forward split 10-for-1 effective" in report.notes and "predate it until reconciled" in report.notes
    assert "2-for-1" not in report.notes and "AAPL" not in report.notes
    assert "Corporate-action evidence" not in report.notes


@pytest.mark.parametrize("status", ["stale", "unavailable"])
async def test_stale_capture_adds_one_note(copilot, status):
    _lookup(copilot, status)
    report = await copilot.get_positions_report()
    assert report.notes.count("Corporate-action evidence") == 1 and f"evidence {status}." in report.notes


async def test_notes_absent_when_disabled(copilot):
    copilot.config = copilot.config.model_copy(
        update={"corporate_actions": copilot.config.corporate_actions.model_copy(update={"enabled": False})}
    )
    copilot.corporate_actions = MagicMock()
    copilot.corporate_actions.lookup = AsyncMock(side_effect=AssertionError("must not be read"))
    report = await copilot.get_positions_report()
    assert "Corporate-action" not in report.notes and "NVDA: forward split" not in report.notes


def today_plus(days: int) -> date:
    return datetime.now(UTC).date() + timedelta(days=days)
