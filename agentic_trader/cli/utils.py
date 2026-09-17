from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

import click

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import ExecutionMode, RuntimeEnvironment
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.data.sessions import AlpacaSessionSource
from agentic_trader.transport.alpaca import BoundedStockDataClient, BoundedTradingClient


def coro[F: Callable[..., Any]](f: F) -> F:
    """Decorator to run an async Click command function synchronously via asyncio.run."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return asyncio.run(f(*args, **kwargs))
        except click.ClickException:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Command %s failed", f.__name__)
            raise click.ClickException(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def get_copilot_and_config(*, dry_run: bool = False) -> tuple[TradingCopilot, AppConfig]:
    """Load configuration and initialize TradingCopilot instance."""
    config = load_config()
    if dry_run:
        # Keep market-data/LLM access, but never construct a production DB or broker.
        root = os.environ.get("COPILOT_TEST_ROOT") if config.environment == RuntimeEnvironment.TEST else None
        directory = tempfile.TemporaryDirectory(prefix="copilot-dry-run-", dir=root)
        config = config.model_copy(deep=True)
        config.environment = RuntimeEnvironment.TEST if root else RuntimeEnvironment.DEVELOPMENT
        config.db_path = str(Path(directory.name) / "signals.db")
        config.db_url = None
        config.database.path = config.db_path
        config.database.url = None
        config.execution_mode = ExecutionMode.PAPER
        config.redundancy.enabled = False
        config.telegram_bot_token = None
        config.telegram_chat_id = None
    copilot = TradingCopilot(config)
    if dry_run:
        copilot._dry_run_directory = directory

    return copilot, config


@contextmanager
def session_source(config: AppConfig, feed: str):
    """Composition owns dedicated bounded SDK readers; callers own async offloading."""

    with ExitStack() as clients:
        calendar_client = BoundedTradingClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            paper=config.alpaca_paper,
            request_timeout=config.market_data.timeout_seconds,
        )
        clients.callback(calendar_client._session.close)
        data_client = BoundedStockDataClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            request_timeout=config.market_data.timeout_seconds,
        )
        clients.callback(data_client._session.close)
        yield AlpacaSessionSource(AlpacaDataProvider(stock_client=data_client, feed=feed), calendar_client)


def artifact_directory() -> Path:
    root = os.environ.get("COPILOT_TEST_ROOT")
    return Path(root) / "research" if root else Path.home() / ".local/state/agentic-trader/research"
