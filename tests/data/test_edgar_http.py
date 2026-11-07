"""The shared EDGAR HTTP helpers: the two-host allow-list and the retry policies.

Fake HTTP only (``httpx.MockTransport``). The user agent is a fake contact.
"""

from __future__ import annotations

import httpx
import pytest

from agentic_trader.data.edgar_http import (
    EDGAR_HOSTS,
    TRANSIENT_RETRY,
    TRANSPORT_RETRY,
    edgar_fetch,
    edgar_get,
    retrying,
)


UA = "Filings Test ops-test@example.invalid"


async def _no_pace() -> None:
    return None


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception):
        self.responses = list(responses)
        self.requests: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), request.headers.get("user-agent")))
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def test_the_allow_list_is_exactly_the_two_sec_hosts():
    assert frozenset({"www.sec.gov", "data.sec.gov"}) == EDGAR_HOSTS


@pytest.mark.parametrize(
    "url", ["https://data.sec.gov/submissions/CIK0000320193.json", "https://www.sec.gov/files/company_tickers.json"]
)
async def test_fetch_and_get_accept_both_sec_hosts(url):
    recorder = Recorder(httpx.Response(200, content=b"{}"))
    async with recorder.client() as client:
        assert await edgar_fetch(client, url, UA, _no_pace) == b"{}"
        assert await edgar_get(client, url, UA) == b"{}"
    assert recorder.requests == [(url, UA), (url, UA)]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/files/company_tickers.json",
        "http://www.sec.gov/files/company_tickers.json",
        "http://data.sec.gov/submissions/CIK0000320193.json",
        "https://sec.gov/files/company_tickers.json",
        "https://data.sec.gov.example.com/submissions/CIK0000320193.json",
        "https://efts.sec.gov/LATEST/search-index",
    ],
)
async def test_fetch_and_get_reject_any_other_host_before_a_request(url):
    recorder = Recorder(httpx.Response(200, content=b"{}"))
    paced: list[int] = []

    async def pace() -> None:
        paced.append(1)

    async with recorder.client() as client:
        with pytest.raises(ValueError, match="host"):
            await edgar_fetch(client, url, UA, pace)
        with pytest.raises(ValueError, match="host"):
            await edgar_get(client, url, UA)
    assert recorder.requests == [] and paced == []


async def test_the_insider_default_retries_a_transport_error_once_and_never_an_http_status():
    url = "https://www.sec.gov/files/company_tickers.json"
    flaky = Recorder(httpx.ConnectError("refused"), httpx.Response(200, content=b"ok"))
    async with flaky.client() as client:
        assert await edgar_fetch(client, url, UA, _no_pace) == b"ok"
    assert len(flaky.requests) == 2

    throttled = Recorder(httpx.Response(503), httpx.Response(200, content=b"ok"))
    async with throttled.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await edgar_fetch(client, url, UA, _no_pace)
    assert len(throttled.requests) == 1


async def test_a_redirect_is_not_followed():
    url = "https://www.sec.gov/files/company_tickers.json"
    recorder = Recorder(httpx.Response(301, headers={"location": "https://example.com/"}))
    async with recorder.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await edgar_get(client, url, UA)
    assert len(recorder.requests) == 1


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://data.sec.gov/x")
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(code, request=request))


@pytest.mark.parametrize(
    ("error", "transport_only", "transient"),
    [
        (httpx.ConnectError("refused"), True, True),
        (httpx.ReadTimeout("slow"), True, True),
        (_status(429), False, True),
        (_status(500), False, True),
        (_status(503), False, True),
        (_status(404), False, False),
        (_status(403), False, False),
        (TimeoutError(), False, False),
        (ValueError("budget"), False, False),
    ],
)
def test_which_failures_each_policy_retries(error, transport_only, transient):
    assert TRANSPORT_RETRY.retries(error) is transport_only
    assert TRANSIENT_RETRY.retries(error) is transient


async def test_retrying_waits_the_policy_delay_and_paces_every_attempt():
    attempts: list[int] = []
    paced: list[int] = []
    slept: list[float] = []

    async def attempt() -> bytes:
        attempts.append(1)
        if len(attempts) == 1:
            raise _status(503)
        return b"ok"

    async def pace() -> None:
        paced.append(1)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    assert await retrying(attempt, pace, retry=TRANSIENT_RETRY, sleep=sleep) == b"ok"
    assert (len(attempts), len(paced), slept) == (2, 2, [5.0])


async def test_retrying_gives_up_after_one_retry():
    attempts: list[int] = []

    async def attempt() -> bytes:
        attempts.append(1)
        raise _status(503)

    async def sleep(seconds: float) -> None:
        return None

    with pytest.raises(httpx.HTTPStatusError):
        await retrying(attempt, _no_pace, retry=TRANSIENT_RETRY, sleep=sleep)
    assert len(attempts) == 2
