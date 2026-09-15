"""Tests for LangGraph Conversational Copilot state machine, tool routing, and memory."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agentic_trader.agent.copilot_graph import (
    ask_copilot,
    create_copilot_graph,
)
from agentic_trader.constants import ExecutionMode


@pytest.fixture
def mock_copilot():
    copilot = MagicMock()
    copilot.config.execution_mode = ExecutionMode.PAPER
    copilot.config.portfolio.cash = 100_000.0
    copilot.config.llm_model = "test-model"
    copilot.is_halted = False
    copilot.halt_reason = None

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
    copilot.get_status_text_html = AsyncMock(return_value="<b>Status:</b> All systems nominal")
    copilot.get_positions_summary_html = AsyncMock(return_value="<b>Open:</b> SPY 39x")
    copilot.get_macro_summary_html = AsyncMock(return_value="<b>Regime:</b> VIX 14.5")
    copilot.run_scan_summary_html = AsyncMock(return_value="<b>Scan:</b> Clean")
    copilot.run_backtest_summary_html = AsyncMock(return_value="<b>Backtest:</b> Sharpe 1.5")
    copilot.run_gex_summary_html = AsyncMock(return_value="<b>GEX:</b> Neutral")
    copilot.run_pairs_summary_html = AsyncMock(return_value="<b>Pairs:</b> None")
    copilot.data_fetcher.provider.fetch_bars = MagicMock(return_value=MagicMock(empty=True))
    return copilot


class MockToolCallingChatModel:
    """Mock LangChain chat model that simulates tool calling and final answers."""

    def __init__(self, responses: list[AIMessage]):
        self.responses = list(responses)
        self.call_count = 0
        self.bound_tools = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, **kwargs):
        if not self.responses:
            return AIMessage(content="Default mock response")
        resp = self.responses.pop(0)
        self.call_count += 1
        return resp


@pytest.mark.asyncio
async def test_copilot_graph_creation(mock_copilot):
    mock_llm = MockToolCallingChatModel([AIMessage(content="Hello!")])
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)
    assert graph is not None


@pytest.mark.asyncio
async def test_copilot_graph_direct_response(mock_copilot):
    mock_llm = MockToolCallingChatModel([AIMessage(content="Quantitative trading is disciplined.")])
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)

    config = {"configurable": {"thread_id": "tg_test_1"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="What is quantitative trading?")]},
        config=config,
    )
    last_msg = result["messages"][-1]
    assert "Quantitative trading is disciplined." in last_msg.content


@pytest.mark.asyncio
async def test_copilot_graph_tool_calling_flow(mock_copilot):
    # Step 1: LLM decides to call tool 'get_open_positions'
    step1 = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_open_positions",
                "args": {},
                "id": "call_123",
                "type": "tool_call",
            }
        ],
    )
    # Step 2: LLM receives tool response and answers operator
    step2 = AIMessage(content="We currently hold 39 shares of SPY.")

    mock_llm = MockToolCallingChatModel([step1, step2])
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)

    config = {"configurable": {"thread_id": "tg_test_2"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="What are our open positions?")]},
        config=config,
    )

    last_msg = result["messages"][-1]
    assert "We currently hold 39 shares of SPY." in last_msg.content
    assert mock_llm.call_count == 2
    # Verify tool was actually executed and tool message added
    tool_messages = [m for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert len(tool_messages) == 1
    assert "SPY" in tool_messages[0].content


@pytest.mark.asyncio
async def test_copilot_graph_multi_turn_state_persistence(mock_copilot):
    mock_llm = MockToolCallingChatModel(
        [
            AIMessage(content="Noted, your risk budget target is 2.0%."),
            AIMessage(content="Your target is 2.0% as previously stated."),
        ]
    )
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)

    config = {"configurable": {"thread_id": "tg_chat_abc"}}

    # Turn 1
    res1 = await graph.ainvoke(
        {"messages": [HumanMessage(content="My risk budget target is 2.0%")]},
        config=config,
    )
    assert "2.0%" in res1["messages"][-1].content

    # Turn 2 on SAME thread
    res2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="What is my risk budget target?")]},
        config=config,
    )
    # Check messages in state: should contain Turn 1 messages + Turn 2 messages
    all_messages = res2["messages"]
    assert len(all_messages) >= 4
    human_texts = [m.content for m in all_messages if isinstance(m, HumanMessage)]
    assert "My risk budget target is 2.0%" in human_texts
    assert "What is my risk budget target?" in human_texts


@pytest.mark.asyncio
async def test_ask_copilot_helper_wrapper(mock_copilot):
    mock_llm = MockToolCallingChatModel([AIMessage(content="All systems operational.")])
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)

    response = await ask_copilot(graph, "How is the system doing?", chat_id="12345")
    assert "All systems operational." in response


@pytest.mark.asyncio
async def test_ask_copilot_handles_exceptions_gracefully(mock_copilot):
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LiteLLM API Rate Limit Exceeded"))
    graph = create_copilot_graph(mock_copilot, chat_model=mock_llm)

    response = await ask_copilot(graph, "Status please", chat_id="12345")
    assert "Error processing copilot request" in response
    assert "LiteLLM API Rate Limit Exceeded" in response
