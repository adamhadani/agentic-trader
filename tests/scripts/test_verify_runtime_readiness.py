"""verify_runtime's readiness read: a 503 is retried and named, never a traceback."""

import io
import json
import urllib.error

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from scripts import verify_runtime
from scripts.verify_runtime import auto_entry_report, fetch_readiness


READY = {"ready": True, "run_id": "r1", "checks": {"accounting": {"ready": True}}}
NOT_READY = {
    "ready": False,
    "run_id": "r1",
    "checks": {
        "accounting": {"ready": True, "detail": "fresh"},
        "insider_capture": {"ready": False, "detail": "no capture yet"},
        "macro_calendar": {"ready": False, "detail": "expired"},
    },
}


class _Response:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()

    def read(self, *_):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/readyz", code, "status", {}, io.BytesIO(json.dumps(body).encode()))


def _fake_urlopen(monkeypatch, outcomes: list) -> list[str]:
    urls: list[str] = []

    def urlopen(url, timeout=None):
        urls.append(url)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(verify_runtime.urllib.request, "urlopen", urlopen)
    return urls


def test_verify_runtime_retries_503_then_succeeds(monkeypatch):
    urls = _fake_urlopen(monkeypatch, [_http_error(503, NOT_READY), _http_error(503, NOT_READY), READY])

    assert fetch_readiness("http://127.0.0.1:9108", delay_seconds=0) == READY
    assert urls == ["http://127.0.0.1:9108/readyz"] * 3


def test_verify_runtime_persistent_503_names_checks(monkeypatch):
    urls = _fake_urlopen(monkeypatch, [_http_error(503, NOT_READY) for _ in range(3)])

    with pytest.raises(RuntimeError) as raised:
        fetch_readiness("http://127.0.0.1:9108", delay_seconds=0)

    message = str(raised.value)
    assert message.startswith("Readiness is not ready: ")
    assert "insider_capture: no capture yet" in message and "macro_calendar: expired" in message
    assert "accounting" not in message and "Traceback" not in message
    assert len(urls) == 3


def test_a_503_without_check_detail_still_raises_cleanly(monkeypatch):
    _fake_urlopen(
        monkeypatch,
        [_http_error(503, {"ready": False, "detail": "No daemon readiness provider"}) for _ in range(3)],
    )

    with pytest.raises(RuntimeError, match="Readiness is not ready: No daemon readiness provider"):
        fetch_readiness("http://127.0.0.1:9108", delay_seconds=0)


def test_only_a_503_is_retried(monkeypatch):
    urls = _fake_urlopen(monkeypatch, [_http_error(500, {}), READY])

    with pytest.raises(urllib.error.HTTPError):
        fetch_readiness("http://127.0.0.1:9108", delay_seconds=0)
    assert len(urls) == 1


def test_verify_runtime_reports_auto_entry():
    config = AppConfig(execution_mode=ExecutionMode.ALPACA, alpaca_paper=True)
    config.execution.native_entry_lifetime = "session"
    config.execution.auto_entry = "paper"
    assert auto_entry_report(config) == {"setting": "paper", "active": True}
    config.execution.auto_entry = "off"
    assert auto_entry_report(config) == {"setting": "off", "active": False}
