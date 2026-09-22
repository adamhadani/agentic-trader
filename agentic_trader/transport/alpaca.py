"""Bounded official Alpaca SDK clients shared by broker and data adapters."""

import logging
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import requests
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.trading.client import TradingClient


logger = logging.getLogger(__name__)


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
        is_get = method.upper() == "GET"
        merged_opts = {**opts, "timeout": self.request_timeout}
        try:
            requested = datetime.now(UTC)
            result = super()._one_request(method, url, merged_opts, retry if is_get else 0)  # type: ignore[misc]
        except requests.exceptions.ConnectionError:
            # Only idempotent GETs retry here: the SDK's own `retry` only covers
            # HTTP 429/504 status codes, never a transport-level reset raised by
            # `requests` (e.g. a pooled connection that idled for minutes and was
            # reset by peer on reuse; `ConnectTimeout` also inherits it, so a hung
            # GET can take up to twice the request timeout). Retrying immediately is safe because the
            # underlying connection pool opens a fresh socket for the next
            # attempt rather than reusing the dead one, so there is nothing to
            # wait out; POST/PATCH/PUT/DELETE are never retried automatically
            # (repo contract). `requested`/the observer reflect only the
            # attempt that ultimately succeeds.
            if not is_get:
                raise
            logger.info(
                "Retrying Alpaca GET once after a transport-level connection reset",
                extra={"event": "alpaca_get_connection_retry", "path": urlsplit(url).path},
            )
            requested = datetime.now(UTC)
            result = super()._one_request(method, url, merged_opts, retry)  # type: ignore[misc]
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
