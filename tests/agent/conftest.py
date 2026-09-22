from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.research.alpha.models import RegistrySnapshot


@pytest.fixture
def scan_desk(app_config, temp_db, mock_notifier):
    """A TradingCopilot wired for run_scan with a real temp db and a controllable
    fake alpha_repository, but no broker/network/LLM/calendar dependencies."""
    repository = AsyncMock()
    repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=True),
        notifier=mock_notifier,
        alpha_repository=repository,
    )
    copilot.evaluator = AsyncMock()
    copilot.readiness = AsyncMock()
    copilot.strategy_engine = MagicMock()
    copilot.strategy_engine.scan_contract.return_value = []
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_data.return_value = SimpleNamespace(
        daily=pd.DataFrame({"Close": [100.0]}),
        four_hour=pd.DataFrame({"Close": [100.0]}),
    )
    copilot.alpha_shadow = AsyncMock()
    copilot.session_provider = AsyncMock()
    copilot.session_provider.is_session_active.return_value = (True, "Open")
    copilot.calendar = AsyncMock()
    copilot.calendar.is_in_lockout_window.return_value = (False, None)
    copilot.regime_detector = AsyncMock()
    copilot.regime_detector.get_regime.return_value = SimpleNamespace(summary_text="test")
    app_config.contracts = {"SPY": SimpleNamespace(name="SPY", ticker="SPY", asset_class="EQUITY")}
    return copilot
