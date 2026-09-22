"""Pin the scan-fetch/global-read-pool collision this fix exists for (C1).

Every scan fetch runs through CompositeMarketDataProvider -> RunnableWithFallbacks
-> BoundedReadExecutor.submit, which is non-blocking and fails fast with
ReadCapacityExceeded once its fixed worker count is exhausted. When a scan's own
concurrency exceeds that width, some fraction of "concurrent" fetches come back as
an empty DataFrame purely from pool contention, not from any real provider failure.
Giving the scan's own CompositeMarketDataProvider a dedicated BoundedReadExecutor
sized to the scan's own concurrency bound makes that impossible.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd

from agentic_trader.data.providers import CompositeMarketDataProvider
from agentic_trader.resilience.reads import DEFAULT_READ_WORKERS, BoundedReadExecutor


class SlowFakeProvider:
    """A provider whose fetch_bars blocks briefly and always returns real data."""

    name = "slow_fake"

    def supports_symbol(self, symbol: str) -> bool:
        return True

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        time.sleep(0.1)
        return pd.DataFrame({"Close": [1.0, 2.0, 3.0]})

    def fetch_latest_price(self, symbol: str) -> float | None:
        return 1.0


def _burst_fetch(provider: CompositeMarketDataProvider, burst: int) -> list[pd.DataFrame]:
    with ThreadPoolExecutor(max_workers=burst) as pool:
        futures = [pool.submit(provider.fetch_bars, f"SYM{i}", "1d") for i in range(burst)]
        return [f.result() for f in futures]


def test_dedicated_read_executor_sized_to_concurrency_never_fails_fast():
    dedicated = BoundedReadExecutor(workers=8)
    provider = CompositeMarketDataProvider([SlowFakeProvider()], read_executor=dedicated)

    frames = _burst_fetch(provider, 8)

    assert all(not f.empty for f in frames)


def test_default_global_pool_fails_fast_under_a_burst_exceeding_its_width():
    provider = CompositeMarketDataProvider([SlowFakeProvider()])  # default: global provider_reads pool

    burst = DEFAULT_READ_WORKERS * 2
    frames = _burst_fetch(provider, burst)

    assert any(f.empty for f in frames), (
        "expected at least one fetch to be starved by the shared global read pool "
        f"(width={DEFAULT_READ_WORKERS}) when burst={burst} exceeds it"
    )
