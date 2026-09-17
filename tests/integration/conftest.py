"""Real HTTP/SDK integration fixtures. Only loopback sockets are permitted."""

import contextlib
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from requests import Response
from requests.adapters import BaseAdapter
from sqlalchemy.engine import make_url

from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.storage.migrations import downgrade_migrations
from agentic_trader.transport.alpaca import BoundedStockDataClient, BoundedTradingClient


def order_payload(**overrides):
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid4()),
        "client_order_id": str(uuid4()),
        "symbol": "SPY",
        "created_at": now,
        "updated_at": now,
        "submitted_at": now,
        "asset_class": "us_equity",
        "order_class": "bracket",
        "type": "limit",
        "time_in_force": "gtc",
        "status": "new",
        "extended_hours": False,
        "side": "sell",
        "qty": "10",
        "filled_qty": "0",
        "legs": None,
        **overrides,
    }


class AlpacaHTTP:
    """Small stateful venue double; SDK serialization/parsing remain unmocked."""

    def __init__(self):
        self.stop = order_payload(type="stop", status="held", stop_price="95")
        self.take_profit = order_payload(limit_price="110")
        self.entry = order_payload(
            side="buy",
            status="filled",
            filled_qty="10",
            filled_avg_price="100",
            filled_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            legs=[self.take_profit, self.stop],
        )
        self.orders = {o["id"]: o for o in (self.entry, self.stop, self.take_profit)}
        self.position = {
            "asset_id": str(uuid4()),
            "symbol": "SPY",
            "exchange": "ARCA",
            "asset_class": "us_equity",
            "avg_entry_price": "100",
            "qty": "10",
            "qty_available": "0",
            "side": "long",
            "cost_basis": "1000",
            "current_price": "105",
            "unrealized_pl": "50",
        }
        self.calls = []
        self.override = lambda method, path, query, body: None
        self.quote_price = 99.0
        self.quote_time = datetime.now(UTC)
        self.market_open = True
        self.session_closes_at = datetime.now(UTC) + timedelta(hours=6)
        self.entry_status = "accepted"
        self.close_status = "filled"
        self.replacement_status = "new"

    def dispatch(self, method, path, query, body):
        self.calls.append((method, path, query, body))
        custom = self.override(method, path, query, body)
        if custom is not None:
            return custom
        if path == "/v2/stocks/trades/latest":
            return 200, {
                "trades": {
                    "SPY": {
                        "t": self.quote_time.isoformat(),
                        "p": self.quote_price,
                        "s": 10,
                        "x": "V",
                        "c": [],
                        "i": 1,
                        "z": "A",
                    }
                }
            }
        if path == "/v2/clock":
            now = datetime.now(UTC).isoformat()
            return 200, {
                "timestamp": now,
                "is_open": self.market_open,
                "next_open": now,
                "next_close": self.session_closes_at.isoformat(),
            }
        if path == "/v2/positions":
            return 200, [self.position] if self.position else []
        if path == "/v2/positions/SPY":
            return (200, self.position) if self.position else (404, {"code": 40410000, "message": "no position"})
        if path == "/v2/orders:by_client_order_id":
            match = next((o for o in self.orders.values() if o["client_order_id"] == query["client_order_id"][0]), None)
            return (200, match) if match else (404, {"code": 40410000, "message": "no order"})
        if path == "/v2/orders":
            if method == "GET":
                if query.get("status") == ["all"]:
                    return 200, [self.entry]
                return 200, [o for o in self.orders.values() if o["status"] in ("new", "accepted", "pending_cancel")]
            if method == "DELETE":
                return 207, [{"id": self.stop["id"], "status": 200}, {"id": self.take_profit["id"], "status": 500}]
            if method == "POST":
                is_entry = body.get("order_class") == "bracket"
                order = order_payload(**body, status=self.entry_status if is_entry else self.close_status)
                if is_entry:
                    self.orders.pop(self.entry["id"])
                    self.entry = order
                    order["legs"] = [self.take_profit, self.stop]
                if order["status"] == "filled":
                    order.update(
                        filled_qty=str(body["qty"]), filled_avg_price="105", filled_at=datetime.now(UTC).isoformat()
                    )
                    self.position = None
                self.orders[order["id"]] = order
                return 200, order
        prefix = "/v2/orders/"
        if path.startswith(prefix):
            order = self.orders[path.removeprefix(prefix)]
            if method == "DELETE":
                order["status"] = "pending_cancel"
                return 204, None
            if method == "PATCH":
                replacement = order_payload(
                    **{**order, **body, "id": str(uuid4()), "status": self.replacement_status, "replaces": order["id"]}
                )
                order.update(status="replaced", replaced_by=replacement["id"])
                self.orders[replacement["id"]] = replacement
                return 200, replacement
            if method == "GET":
                if order["status"] == "pending_cancel":
                    order["status"] = "canceled"
                    if self.position:
                        self.position["qty_available"] = "10"
                if order is self.entry and query.get("nested", ["false"])[0].lower() != "true":
                    return 200, {**order, "legs": None}
                return 200, order
        return 404, {"code": 40410000, "message": "unhandled fixture route"}


@pytest.fixture
def alpaca_http(app_config, request):
    venue = AlpacaHTTP()

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            url = urlparse(self.path)
            body = (
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if int(self.headers.get("Content-Length", "0"))
                else None
            )
            status, payload = venue.dispatch(self.command, url.path, parse_qs(url.query), body)
            data = json.dumps(payload).encode() if payload is not None else b""
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

        def log_message(self, *args):
            pass

    server = None
    thread = None
    if request.config.getoption("--alpaca-transport") == "socket":
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
    else:
        base_url = "http://127.0.0.1:1"
    client = BoundedTradingClient("fake-key", "fake-secret", url_override=base_url, request_timeout=0.2)
    data_client = BoundedStockDataClient("fake-key", "fake-secret", url_override=base_url, request_timeout=0.2)
    if server is None:

        class VenueTransport(BaseAdapter):
            def send(self, request, **kwargs):
                parsed = urlparse(request.url)
                body = json.loads(request.body) if request.body else None
                status, payload = venue.dispatch(request.method, parsed.path, parse_qs(parsed.query), body)
                response = Response()
                response.status_code = status
                response._content = json.dumps(payload).encode() if payload is not None else b""
                response.request = request
                response.url = request.url
                return response

            def close(self):
                pass

        client._session.mount(base_url, VenueTransport())
        data_client._session.mount(base_url, VenueTransport())
    broker = AlpacaBroker(app_config, client=client, data_client=data_client)
    broker._connected = True
    app_config.execution.close_cancel_poll_seconds = 0.001
    app_config.execution.close_cancel_timeout_seconds = 0.1
    app_config.execution.stop_replace_timeout_seconds = 0.1
    app_config.trailing_stop.enabled = False
    app_config.copilot_chat_enabled = False
    yield venue, broker
    client._session.close()
    data_client._session.close()
    if server and thread:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.fixture
def postgres_test_db():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to an isolated test_ database and pass --run-postgres.")
    assert (make_url(url).database or "").startswith("test_"), "Integration database name must start with test_"
    downgrade_migrations("base", url)
    yield url
    downgrade_migrations("base", url)


@pytest.fixture
def broker_order_payload():
    return order_payload
