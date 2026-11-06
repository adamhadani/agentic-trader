"""EDGAR HTTP helpers shared by the daemon's SEC captures (the insider Form 4 capture and the filings capture).

One GET is allowed only on ``https://www.sec.gov`` or ``https://data.sec.gov``, follows no redirect (the
callers' clients set ``follow_redirects=False``), has a total deadline (``REQUEST_DEADLINE_SECONDS``) and a body
budget (``MAX_DOCUMENT_BYTES``), and treats any response but a 200 (or a 404 the caller allows) as a failure.
``retrying`` paces every attempt and applies one ``RetryPolicy``: the insider capture's default retries a
transport error once at once; the filings capture's also retries an HTTP 429 or 5xx, after 5 seconds.

The ``EDGAR_USER_AGENT`` contact goes in the request's ``User-Agent`` header only. Nothing here logs it, and
callers record failures by exception type name or HTTP status, never by exception text.
"""

from __future__ import annotations

import asyncio
import math
import time as monotonic_time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlparse

import httpx

from agentic_trader.research.insider_filings import REQUESTS_PER_SECOND


__all__ = [
    "EDGAR_HOSTS",
    "MAX_DOCUMENT_BYTES",
    "REQUEST_DEADLINE_SECONDS",
    "TIMEOUT_SECONDS",
    "TRANSIENT_RETRY",
    "TRANSPORT_RETRY",
    "USER_AGENT_ENV",
    "BodyTooLarge",
    "RetryPolicy",
    "check_edgar_url",
    "edgar_fetch",
    "edgar_get",
    "edgar_pacer",
    "retrying",
]

EDGAR_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
USER_AGENT_ENV = "EDGAR_USER_AGENT"
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
TIMEOUT_SECONDS = 60.0
# The bounded total of one request attempt (connect, headers and the whole body).
REQUEST_DEADLINE_SECONDS = 120.0


class BodyTooLarge(ValueError):
    """A response body exceeded ``MAX_DOCUMENT_BYTES``."""


@dataclass(frozen=True)
class RetryPolicy:
    """At most one retry of a failed GET, paced like the first attempt after ``delay_seconds``.

    A transport error is always retried; an HTTP 429 or 5xx only when ``transient_status``. Any other HTTP
    status, a total-deadline ``TimeoutError`` or an over-budget body is never retried.
    """

    transient_status: bool = False
    delay_seconds: float = 0.0

    def retries(self, exc: BaseException) -> bool:
        if isinstance(exc, httpx.TransportError):
            return True
        if self.transient_status and isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status == 429 or status >= 500
        return False


TRANSPORT_RETRY = RetryPolicy()  # the insider capture's
TRANSIENT_RETRY = RetryPolicy(transient_status=True, delay_seconds=5.0)  # the filings capture's


def edgar_pacer(per_second: float = REQUESTS_PER_SECOND) -> Callable[[], Awaitable[None]]:
    """At most ``per_second`` requests per second (the SEC fair-access pacing of the research acquisition)."""
    interval = 1.0 / per_second
    last = -math.inf

    async def pace() -> None:
        nonlocal last
        wait = last + interval - monotonic_time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last = monotonic_time.monotonic()

    return pace


def check_edgar_url(url: str) -> None:
    """Raise ``ValueError`` unless ``url`` is https on one of ``EDGAR_HOSTS``."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in EDGAR_HOSTS:
        raise ValueError(f"{url}: host is not one of {', '.join(f'https://{h}' for h in sorted(EDGAR_HOSTS))}")


async def _stream(client: httpx.AsyncClient, url: str, user_agent: str, *, missing_ok: bool) -> bytes | None:
    async with client.stream("GET", url, headers={"User-Agent": user_agent}, timeout=TIMEOUT_SECONDS) as response:
        if missing_ok and response.status_code == 404:
            return None
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError(f"{url}: unexpected HTTP {response.status_code}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                raise BodyTooLarge(f"{url}: exceeded the {MAX_DOCUMENT_BYTES}-byte budget")
            chunks.append(chunk)
        return b"".join(chunks)


async def _attempt(client: httpx.AsyncClient, url: str, user_agent: str, *, missing_ok: bool) -> bytes | None:
    async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
        return await _stream(client, url, user_agent, missing_ok=missing_ok)


async def retrying[T](
    attempt: Callable[[], Awaitable[T]],
    pace: Callable[[], Awaitable[None]],
    *,
    retry: RetryPolicy = TRANSPORT_RETRY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """``attempt()`` after ``pace()``; one more paced attempt when ``retry`` retries the failure."""
    await pace()
    try:
        return await attempt()
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        if not retry.retries(exc):
            raise
    if retry.delay_seconds > 0:
        await sleep(retry.delay_seconds)
    await pace()
    return await attempt()


async def edgar_get(client: httpx.AsyncClient, url: str, user_agent: str) -> bytes:
    """One unpaced attempt on an EDGAR host; any response but a 200 raises."""
    check_edgar_url(url)
    body = await _attempt(client, url, user_agent, missing_ok=False)
    assert body is not None
    return body


async def edgar_fetch(
    client: httpx.AsyncClient,
    url: str,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    *,
    missing_ok: bool = False,
) -> bytes | None:
    """One paced GET on an EDGAR host (a transport error is retried once); a 404 is ``None`` when ``missing_ok``."""
    check_edgar_url(url)
    return await retrying(partial(_attempt, client, url, user_agent, missing_ok=missing_ok), pace)
