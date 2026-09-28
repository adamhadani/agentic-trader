"""The default TradeLifetimeService must get a strict Alpaca calendar, never the
composite session provider's calendar (which silently falls back Alpaca ->
Finnhub -> deterministic on any error or timeout). A session-counted holding
deadline requires exact broker evidence; a guessed holiday calendar must never
stand in for a calendar read failure -- that failure has to surface as a
lifecycle review instead (see agentic_trader.execution.lifetimes.TradeLifetimeService._sessions).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import BaseBroker
from agentic_trader.market.session import AlpacaCalendarProvider


def test_default_lifetime_service_gets_a_strict_alpaca_calendar_bound_to_the_client(app_config, temp_db, mock_notifier):
    fake_client = MagicMock(name="alpaca_trading_client")
    broker = MagicMock(spec=BaseBroker)
    broker.client = fake_client
    copilot = TradingCopilot(app_config, db=temp_db, broker=broker, notifier=mock_notifier)
    calendar = copilot.lifetime_service.calendar
    assert isinstance(calendar, AlpacaCalendarProvider)
    assert calendar.client is fake_client
    # Never the composite session calendar with its silent provider fallback.
    assert calendar is not copilot.session_provider.calendar


def test_default_lifetime_service_has_no_calendar_without_an_alpaca_client(app_config, temp_db, mock_notifier):
    broker = MagicMock(spec=BaseBroker)  # no `.client`: getattr(..., "client", None) is None
    copilot = TradingCopilot(app_config, db=temp_db, broker=broker, notifier=mock_notifier)
    assert copilot.lifetime_service.calendar is None
