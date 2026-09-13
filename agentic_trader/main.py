"""Entrypoint for Agentic Trader CLI and Copilot orchestration."""

from agentic_trader.agent.copilot import FuturesCopilot, TradingCopilot
from agentic_trader.cli import cli, main


__all__ = ["FuturesCopilot", "TradingCopilot", "cli", "main"]

if __name__ == "__main__":
    main()
