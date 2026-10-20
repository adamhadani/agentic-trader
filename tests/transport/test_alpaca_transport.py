"""Unit tests for BoundedTransport's connection-reset retry (no network)."""

from __future__ import annotations

import logging

import pytest
import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import AssetStatus

from agentic_trader.transport.alpaca import BoundedTradingClient


FAKE_KEY = "fake-key"
FAKE_SECRET = "fake-secret"
FAKE_BASE_URL = "https://example.invalid"


def _make_client() -> BoundedTradingClient:
    # Construction never opens a socket; the SDK only builds a requests.Session.
    return BoundedTradingClient(FAKE_KEY, FAKE_SECRET, url_override=FAKE_BASE_URL, request_timeout=1.0)


def test_get_retries_once_after_connection_error(monkeypatch, caplog):
    client = _make_client()
    calls: list[str] = []

    def fake_parent(self, method, url, opts, retry):
        calls.append(method)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return {"ok": True}

    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", fake_parent)

    observed = []
    with caplog.at_level(logging.INFO), client.observe_responses(lambda page: observed.append(page)):
        result = client._one_request("GET", f"{FAKE_BASE_URL}/v2/calendar", {}, 3)

    assert result == {"ok": True}
    assert calls == ["GET", "GET"]
    assert len(observed) == 1
    assert observed[0].response == {"ok": True}
    assert any(getattr(record, "event", None) == "alpaca_get_connection_retry" for record in caplog.records)


def test_get_propagates_second_connection_error(monkeypatch):
    client = _make_client()
    calls: list[str] = []

    def fake_parent(self, method, url, opts, retry):
        calls.append(method)
        raise requests.exceptions.ConnectionError(f"Connection reset by peer #{len(calls)}")

    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", fake_parent)

    observed = []
    with (
        pytest.raises(requests.exceptions.ConnectionError, match="#2"),
        client.observe_responses(lambda page: observed.append(page)),
    ):
        client._one_request("GET", f"{FAKE_BASE_URL}/v2/calendar", {}, 3)

    assert calls == ["GET", "GET"]
    assert observed == []


def test_post_connection_error_is_never_retried(monkeypatch):
    client = _make_client()
    calls: list[str] = []

    def fake_parent(self, method, url, opts, retry):
        calls.append(method)
        raise requests.exceptions.ConnectionError("Connection reset by peer")

    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", fake_parent)

    with pytest.raises(requests.exceptions.ConnectionError):
        client._one_request("POST", f"{FAKE_BASE_URL}/v2/orders", {}, 0)

    assert calls == ["POST"]


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: requests.exceptions.Timeout("timed out"),
        lambda: APIError("bad request"),
    ],
)
def test_get_non_connection_error_is_never_retried(monkeypatch, make_exc):
    client = _make_client()
    calls: list[str] = []

    def fake_parent(self, method, url, opts, retry):
        calls.append(method)
        raise make_exc()

    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", fake_parent)

    with pytest.raises(Exception):  # noqa: B017 - either exception type is acceptable here
        client._one_request("GET", f"{FAKE_BASE_URL}/v2/calendar", {}, 3)

    assert calls == ["GET"]


def test_list_us_equity_assets_keeps_exchanges_the_sdk_enum_does_not_know(monkeypatch):
    """TXSE listings (2026-10-20) fail the typed ``get_all_assets`` parse; the raw listing keeps them."""
    seen: list[tuple[str, str, dict]] = []

    def fake_parent(self, method, url, opts, retry):
        seen.append((method, url, dict(opts.get("params", {}))))
        return [
            {
                "id": "a1",
                "symbol": "IBOC",
                "name": "International Bancshares",
                "exchange": "TXSE",
                "status": "active",
                "tradable": True,
                "class": "us_equity",
            },
            {
                "id": "a2",
                "symbol": "AAPL",
                "name": "Apple",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "class": "us_equity",
            },
        ]

    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", fake_parent)
    client = BoundedTradingClient("key", "secret", paper=True, request_timeout=5.0)
    rows = client.list_us_equity_assets(AssetStatus.ACTIVE)
    assert [row["symbol"] for row in rows] == ["IBOC", "AAPL"]
    assert rows[0]["exchange"] == "TXSE"
    assert len(seen) == 1 and seen[0][0] == "GET" and seen[0][1].endswith("/v2/assets")
    assert seen[0][2] == {"asset_class": "us_equity", "status": "active"}
    assert client.list_us_equity_assets(None) and seen[1][2] == {"asset_class": "us_equity"}


@pytest.mark.parametrize("payload", [{"assets": []}, [{"symbol": "AAPL"}, "junk"], [{"id": "a1", "status": "active"}]])
def test_list_us_equity_assets_rejects_unexpected_payloads(monkeypatch, payload):
    monkeypatch.setattr("agentic_trader.transport.alpaca.TradingClient._one_request", lambda *a, **k: payload)
    client = BoundedTradingClient("key", "secret", paper=True, request_timeout=5.0)
    with pytest.raises(ValueError, match="asset listing"):
        client.list_us_equity_assets(AssetStatus.ACTIVE)
