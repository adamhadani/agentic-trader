"""Tests for quantitative desk tools exposed to the LangGraph conversational copilot."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from agentic_trader.agent.copilot_tools import make_copilot_tools
from agentic_trader.constants import ExecutionMode
from agentic_trader.research.alpha.models import RegistrySnapshot


@pytest.fixture
def mock_copilot():
    copilot = MagicMock()
    copilot.config.execution_mode = ExecutionMode.PAPER
    copilot.config.portfolio.cash = 100_000.0
    copilot.is_halted = False
    copilot.halt_reason = None

    # Mock DB
    copilot.db.get_active_positions = AsyncMock(
        return_value=[
            {
                "symbol": "SPY",
                "quantity": 39.0,
                "side": "buy",
                "entry_price": 500.0,
                "current_price": 505.0,
                "unrealized_pnl": 195.0,
                "stop_loss": 495.0,
                "take_profit": 515.0,
                "trailing_stop": None,
            }
        ]
    )
    copilot.db.get_account_balance = AsyncMock(return_value=100_000.0)

    # Mock providers
    copilot.get_status_text_html = AsyncMock(return_value="<b>System Status:</b> Operational")
    copilot.get_positions_summary_html = AsyncMock(return_value="<b>Positions:</b> SPY 39x")
    copilot.get_macro_summary_html = AsyncMock(
        return_value="<b>Macro Intelligence:</b> Stress LOW, Curve NORMAL_STEEP, OAS 265 bps"
    )
    copilot.run_scan_summary_html = AsyncMock(return_value="<b>Scan Results:</b> 1 setup found")
    copilot.run_backtest_summary_html = AsyncMock(return_value="<b>Backtest:</b> Sharpe 1.85, Return +12.4%")
    copilot.run_gex_summary_html = AsyncMock(return_value="<b>GEX Surface:</b> Net GEX +$1.2B, Flip $502")
    copilot.run_pairs_summary_html = AsyncMock(return_value="<b>Pairs:</b> E-mini S&P vs Nasdaq spread z-score: -1.2")

    # Mock Data Fetcher
    df = pd.DataFrame(
        {
            "close": [100.0 + i * 0.5 for i in range(50)],
            "high": [101.0 + i * 0.5 for i in range(50)],
            "low": [99.0 + i * 0.5 for i in range(50)],
            "volume": [1_000_000 for _ in range(50)],
        }
    )
    copilot.data_fetcher.provider.fetch_bars = MagicMock(return_value=df)
    return copilot


@pytest.mark.asyncio
async def test_make_copilot_tools_returns_all_tools(mock_copilot):
    tools = make_copilot_tools(mock_copilot)
    tool_names = {t.name for t in tools}
    expected = {
        "get_open_positions",
        "get_portfolio_status",
        "get_macro_intelligence",
        "trigger_market_scan",
        "run_backtest",
        "get_gex_surface",
        "get_pairs_cointegration",
        "get_technical_summary",
        "get_system_health",
    }
    assert expected.issubset(tool_names)


@pytest.mark.asyncio
async def test_tool_get_open_positions_with_data(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_open_positions"].ainvoke({})
    assert "SPY" in res
    assert "39" in res
    mock_copilot.get_positions_summary_html.assert_awaited_once()
    mock_copilot.db.get_active_positions.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_get_open_positions_empty(mock_copilot):
    mock_copilot.get_positions_summary_html = AsyncMock(return_value="No open positions")
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_open_positions"].ainvoke({})
    assert "No open positions" in res or "0 open positions" in res


@pytest.mark.asyncio
async def test_tool_get_portfolio_status(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_portfolio_status"].ainvoke({})
    assert res == "System Status: Operational"
    mock_copilot.get_status_text_html.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_get_macro_intelligence(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_macro_intelligence"].ainvoke({})
    assert "Macro Intelligence" in res
    assert "NORMAL_STEEP" in res
    mock_copilot.get_macro_summary_html.assert_called_once()


@pytest.mark.asyncio
async def test_tool_trigger_market_scan(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["trigger_market_scan"].ainvoke({"asset_class": "equities", "strategy": "trend_pullback"})
    assert "Scan Results" in res
    mock_copilot.run_scan_summary_html.assert_called_once()


@pytest.mark.asyncio
async def test_tool_run_backtest(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["run_backtest"].ainvoke({"symbol": "SPY", "strategy": "trend_pullback", "lookback_days": 60})
    assert "Sharpe" in res
    mock_copilot.run_backtest_summary_html.assert_called_once_with(symbol="SPY", lookback="60d")


@pytest.mark.asyncio
async def test_tool_get_gex_surface(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_gex_surface"].ainvoke({"symbol": "SPY"})
    assert "GEX Surface" in res
    mock_copilot.run_gex_summary_html.assert_called_once_with("SPY")


@pytest.mark.asyncio
async def test_tool_get_pairs_cointegration(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_pairs_cointegration"].ainvoke({})
    assert "Pairs" in res
    mock_copilot.run_pairs_summary_html.assert_called_once()


@pytest.mark.asyncio
async def test_tool_get_technical_summary(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_technical_summary"].ainvoke({"symbol": "SPY"})
    assert "SPY" in res
    assert "RSI" in res
    mock_copilot.data_fetcher.provider.fetch_bars.assert_called_once_with("SPY", "1d", period="60d")


@pytest.mark.asyncio
async def test_tool_get_system_health(mock_copilot):
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_system_health"].ainvoke({})
    assert "Operational" in res or "Normal" in res
    assert "Halted: False" in res or "Active" in res


@pytest.mark.asyncio
async def test_tool_get_alpha_catalog(mock_copilot):

    mock_copilot.alpha_repository.snapshot = AsyncMock(return_value=RegistrySnapshot(0, (), ()))
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}
    res = await tools["get_alpha_catalog"].ainvoke({})
    assert "Catalog Library" in res
    assert "alpha_wq_006" in res
