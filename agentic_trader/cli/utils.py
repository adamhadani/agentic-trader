from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import AppConfig, load_config


def coro[F: Callable[..., Any]](f: F) -> F:
    """Decorator to run an async Click command function synchronously via asyncio.run."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(f(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def get_copilot_and_config() -> tuple[TradingCopilot, AppConfig]:
    """Load configuration and initialize TradingCopilot instance."""
    config = load_config()
    copilot = TradingCopilot(config)
    return copilot, config
