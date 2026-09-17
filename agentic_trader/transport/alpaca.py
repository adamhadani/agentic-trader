"""Bounded official Alpaca SDK clients shared by broker and data adapters."""

import math
from typing import Any

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.trading.client import TradingClient


class BoundedTransport:
    """SDK transport boundary: bounded sockets and no automatic mutation replay.

    alpaca-py exposes neither timeout nor retry options on TradingClient. Keep
    this single SDK extension covered by real HTTP contract tests when upgrading.
    GET retains the SDK's bounded rate-limit/server retry policy.
    """

    def __init__(self, *args: Any, request_timeout: float, **kwargs: Any):
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("Positive finite Alpaca transport timeout required")
        super().__init__(*args, **kwargs)
        self.request_timeout = request_timeout

    def _one_request(self, method: str, url: str, opts: dict, retry: int) -> dict:
        return super()._one_request(  # type: ignore[misc]
            method, url, {**opts, "timeout": self.request_timeout}, retry if method.upper() == "GET" else 0
        )


class BoundedTradingClient(BoundedTransport, TradingClient):
    pass


class BoundedStockDataClient(BoundedTransport, StockHistoricalDataClient):
    pass


class BoundedCryptoDataClient(BoundedTransport, CryptoHistoricalDataClient):
    pass
