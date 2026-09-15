"""LangGraph Conversational Copilot State Machine & Reasoning Engine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_litellm import ChatLiteLLM
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agentic_trader.agent.copilot_tools import make_copilot_tools
from agentic_trader.constants import ExecutionMode


if TYPE_CHECKING:
    from agentic_trader.agent.copilot import TradingCopilot

logger = logging.getLogger(__name__)

COPILOT_SYSTEM_PROMPT = """You are the Antigravity Trading Copilot, an institutional-grade quantitative trading assistant.
You have real-time access to the trading desk's portfolio, broker positions, risk budgets, technical screeners, backtesting engines, and macro regime monitors via tools.

Operational Guidelines:
1. Always be concise, direct, and quantitatively accurate.
2. Use tools to verify live facts before answering questions regarding open positions, balances, market regime, technical analysis, or backtests.
3. Safety Invariant: Destructive actions (such as closing a position, emergency panic halt, or live order placement) must be executed by the operator using explicit slash commands (e.g. /close <SYMBOL>, /panic, /resume) or Telegram interactive buttons. Direct the operator to the appropriate command if requested.
4. Format responses cleanly for Telegram: use bold for tickers, bullet points for lists, and monospaced code blocks for numbers, tables, or commands.
"""


class CopilotState(MessagesState):
    """Conversational state inheriting message history."""


def _should_continue(state: CopilotState) -> str:
    """Route to tools if the LLM emitted tool calls, otherwise conclude turn."""
    messages = state["messages"]
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


def create_copilot_graph(
    copilot: TradingCopilot,
    model_name: str | None = None,
    chat_model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Construct a stateful ReAct trading copilot graph using LangGraph."""
    tools = make_copilot_tools(copilot)
    tool_node = ToolNode(tools)

    if chat_model is not None:
        model_with_tools = chat_model.bind_tools(tools)
    else:
        resolved_model: str = str(model_name or getattr(copilot.config, "llm_model", "gpt-4o-mini"))
        llm = ChatLiteLLM(model=resolved_model, api_key=copilot.config.llm_api_key)
        model_with_tools = llm.bind_tools(tools)

    async def call_model(state: CopilotState) -> dict[str, list[AnyMessage]]:
        messages = list(state["messages"])
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=COPILOT_SYSTEM_PROMPT)] + messages
        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    workflow = StateGraph(CopilotState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", _should_continue, ["tools", END])
    workflow.add_edge("tools", "agent")

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)


async def ask_copilot(
    graph: CompiledStateGraph,
    query: str,
    chat_id: str | int = "default",
    execution_mode: str = ExecutionMode.PAPER,
) -> str:
    """Execute a conversational turn through the compiled LangGraph copilot."""
    thread_id = f"tg_{chat_id}"
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "tags": ["telegram", "copilot", execution_mode],
        "metadata": {"chat_id": str(chat_id), "execution_mode": execution_mode},
    }
    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=query)]},
            config=config,
        )
        last_message = result["messages"][-1]
        if isinstance(last_message, AIMessage):
            return str(last_message.content)
        return str(getattr(last_message, "content", "No response generated."))
    except Exception as e:
        logger.exception("Error executing copilot turn for chat %s", chat_id)
        return f"❌ Error processing copilot request: {e}"
