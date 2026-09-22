"""Pin the injected-dependency ownership of the scan's dedicated read-capacity pool
(round-2 fix for the C1 follow-up): TradingCopilot owns exactly one BoundedReadExecutor
for the lifetime of its default (non-injected) MarketDataFetcher, sized to its own
scan_concurrency, distinct from the process-global provider_reads pool. Injecting a
data_fetcher must not create any pool at all -- the copilot has nothing to own then.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.resilience.reads import provider_reads


def test_copilot_owns_one_dedicated_scan_sized_read_pool(app_config, temp_db, mock_notifier):
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
    )
    read_executor = copilot.data_fetcher.provider.read_executor
    assert read_executor is not provider_reads
    assert read_executor is copilot._scan_read_executor
    assert read_executor._executor._max_workers == app_config.market_data.scan_concurrency


def test_copilot_with_injected_data_fetcher_creates_no_read_pool(app_config, temp_db, mock_notifier, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("A copilot with an injected data_fetcher must not construct a read pool")

    monkeypatch.setattr(copilot_module, "BoundedReadExecutor", boom)

    TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
        data_fetcher=MagicMock(),
    )
