"""Bounded official Alpaca SDK clients shared by broker and data adapters."""

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.trading.client import TradingClient


@dataclass(frozen=True)
class ResponsePage:
    method: str
    path: str
    parameters: dict
    requested_at: datetime
    received_at: datetime
    response: Any


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
        self._response_observer: ContextVar[Callable[[ResponsePage], None] | None] = ContextVar(
            "alpaca_response_observer", default=None
        )

    @contextmanager
    def observe_responses(self, observer: Callable[[ResponsePage], None]) -> Iterator[None]:
        """Scope an injected observer to this caller, never to shared client state.

        Invoked before SDK pagination aggregation and typed parsing. No headers,
        credentials or URL query strings cross this observation boundary.
        """
        token = self._response_observer.set(observer)
        try:
            yield
        finally:
            self._response_observer.reset(token)

    def _one_request(self, method: str, url: str, opts: dict, retry: int) -> dict:
        requested = datetime.now(UTC)
        result = super()._one_request(  # type: ignore[misc]
            method, url, {**opts, "timeout": self.request_timeout}, retry if method.upper() == "GET" else 0
        )
        if observer := self._response_observer.get():
            observer(
                ResponsePage(
                    method, urlsplit(url).path, dict(opts.get("params", {})), requested, datetime.now(UTC), result
                )
            )
        return result


class BoundedTradingClient(BoundedTransport, TradingClient):
    pass


class BoundedStockDataClient(BoundedTransport, StockHistoricalDataClient):
    pass


class BoundedCryptoDataClient(BoundedTransport, CryptoHistoricalDataClient):
    pass
