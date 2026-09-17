"""Raw acquisition evidence through the real SDK and loopback HTTP transport."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pandas as pd
import pytest

from agentic_trader.config import MarketDataEvidenceConfig
from agentic_trader.data import evidence
from agentic_trader.data.evidence import BarAcquisitionError, BarEvidenceStore
from agentic_trader.data.providers import AlpacaDataProvider, BarResponseError
from agentic_trader.data.sessions import AlpacaSessionSource, SessionAcquisitionError
from agentic_trader.market.bars import SessionCoverageError, SessionSchedule, TradingSession, build_session_bars
from agentic_trader.research.alpha.panel import align_daily_panel
from agentic_trader.transport.alpaca import BoundedCryptoDataClient


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]
START = pd.Timestamp("2024-06-03T13:30:00Z")


def row(offset=0, **overrides):
    return {
        "t": (START + pd.Timedelta(minutes=offset)).isoformat(),
        "o": 100,
        "h": 101,
        "l": 99,
        "c": 100,
        "v": 1000,
        "n": 10,
        "vw": 100,
        **overrides,
    }


@pytest.fixture
def captured_provider(alpaca_http, tmp_path):
    venue, broker = alpaca_http
    store = BarEvidenceStore(tmp_path / "raw", MarketDataEvidenceConfig())
    return venue, broker, AlpacaDataProvider(stock_client=broker.data_client, evidence=store)


def read_reference(reference):
    path = Path(reference["artifact"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    assert path.stat().st_mode & 0o777 == 0o600
    return json.loads(path.read_text())


@pytest.mark.parametrize("bars", [{}, {"SPY": []}])
@pytest.mark.parametrize("timeframe", ["1m", "1d"])
def test_successful_empty_response_retains_unknown_coverage(captured_provider, bars, timeframe):
    venue, broker, provider = captured_provider
    payload = {"bars": bars, "next_page_token": None}
    venue.override = lambda *args: (200, payload)
    source = AlpacaSessionSource(provider, broker.client)
    if timeframe == "1m":
        frame = source.minutes("SPY", START, START + pd.Timedelta(minutes=15), "alpaca:sip")
        reference = frame.attrs["acquisition"][0]["evidence"]
        schedule = SessionSchedule(
            START.date(),
            START.date(),
            (TradingSession(START.date(), START, START + pd.Timedelta(minutes=15)),),
            "fixture",
        )
        with pytest.raises(SessionCoverageError) as caught:
            build_session_bars(frame, schedule, "15m", as_of=START + pd.Timedelta(minutes=15))
        assert caught.value.coverage["missing_minutes"] == 15
        assert caught.value.coverage["observed_minutes"] == 0
    else:
        frame = source.daily("SPY", START.date(), START.date(), "alpaca:sip")
        reference = frame.attrs["evidence"]
        expected = pd.DatetimeIndex(["2024-06-03"], tz="America/New_York")
        panel = align_daily_panel({"SPY": frame}, expected, feed="alpaca:sip")
        assert not panel.complete
        assert panel.coverage["SPY"] == {"expected": 1, "observed": 0, "missing_dates": ["2024-06-03"]}
        assert panel.close.isna().all().all()
    assert frame.empty
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(frame.index, pd.DatetimeIndex) and str(frame.index.tz) == "UTC"
    assert {k: frame.attrs[k] for k in ("feed", "adjustment", "timeframe")} == {
        "feed": "alpaca:sip",
        "adjustment": "raw",
        "timeframe": timeframe,
    }
    result = read_reference(reference)
    assert result["status"] == "complete"
    assert result["normalization"]["parsed_rows"] == result["normalization"]["normalized_rows"] == 0
    assert result["normalization"]["dropped_rows"] == []
    assert read_reference(result["pages"][0])["response"] == payload
    assert len(venue.calls) == 1 and venue.calls[0][0] == "GET"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"trades": {}},
        {"bars": None},
        {"bars": []},
        {"bars": {"SPY": {}}},
        {"bars": {"SPY": ""}},
        {"bars": {"SPY": None}},
        {"bars": {"QQQ": []}},
        {"bars": {"SPY": [], "QQQ": [row()]}},
        {"bars": {}, "next_page_token": 1},
        {"bars": {}, "next_page_token": ""},
    ],
)
@pytest.mark.parametrize("capture", [False, True])
def test_malformed_envelopes_are_not_successful_empty_bars(captured_provider, payload, capture):
    venue, _, provider = captured_provider
    if not capture:
        provider.evidence = None
    venue.override = lambda *args: (200, payload)
    with pytest.raises(BarAcquisitionError if capture else BarResponseError) as caught:
        provider.fetch_bars("SPY", "1d", start=START)
    if capture:
        result = read_reference(caught.value.evidence)
        assert result["status"] == "failed"
        assert result["error_type"] == "BarResponseError"
        assert result["normalization"] is None
        assert read_reference(result["pages"][0])["response"] == payload
    assert len(venue.calls) == 1


@pytest.mark.parametrize("case", ["null", "cleaner", "malformed", "pagination", "empty"])
def test_capture_precedes_sdk_parsing_and_survives_failure(captured_provider, case):
    venue, broker, provider = captured_provider
    rows = {
        "null": [row(), None, row(1, unknown_vendor_field={"retained": True})],
        "cleaner": [row(), row(1, c="NaN")],
        "malformed": [row(), row(1, c=None)],
        "pagination": [row()],
        "empty": [],
    }[case]

    def response(method, path, query, body):
        assert method == "GET" and path == "/v2/stocks/bars"
        if query.get("page_token"):
            return 403, {"message": "private provider error text"}
        return 200, {"bars": {"SPY": rows}, "next_page_token": "next" if case == "pagination" else None}

    venue.override = response
    if case in ("malformed", "pagination"):
        source = AlpacaSessionSource(provider, broker.client)
        with pytest.raises(SessionAcquisitionError) as caught:
            source.minutes("SPY", START, START + pd.Timedelta(minutes=3), "alpaca:sip")
        result = read_reference(caught.value.receipts[0]["evidence"])
        assert result["status"] == "failed"
        assert result["error_type"] == ("ValidationError" if case == "malformed" else "APIError")
        assert "private provider error text" not in json.dumps(result)
    else:
        frame = provider.fetch_bars("SPY", "1m", start=START, end=START + pd.Timedelta(minutes=3))
        result = read_reference(frame.attrs["evidence"])
        assert frame.attrs["feed"] == "alpaca:sip"
        assert len(frame) == {"null": 2, "cleaner": 1, "empty": 0}[case]
        assert result["normalization"]["dropped_rows"] == (
            [{"position": 1, "timestamp": row(1)["t"], "missing": ["Close"]}] if case == "cleaner" else []
        )
        assert result["status"] == "complete"
    page = read_reference(result["pages"][0])
    assert page["response"]["bars"]["SPY"] == rows
    assert result["pages"][0]["null_rows"] == int(case == "null")
    assert result["pages"][0]["raw_rows"] == len(rows)
    assert pd.Timestamp(page["received_at"]) >= pd.Timestamp(page["requested_at"])
    assert "fake-secret" not in json.dumps(page)
    assert len(venue.calls) == (2 if case == "pagination" else 1)
    venue.override = lambda *args: None
    assert provider.fetch_latest_price("SPY") == venue.quote_price
    assert len(list(provider.evidence.directory.rglob("page-*.json"))) == 1


def test_concurrent_captures_on_one_client_have_no_crosstalk(captured_provider):
    venue, _, provider = captured_provider
    barrier = Barrier(2)

    def response(method, path, query, body):
        barrier.wait(timeout=5)
        return 200, {"bars": {query["symbols"][0]: [row()]}, "next_page_token": None}

    venue.override = response
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda symbol: provider.fetch_bars(symbol, "1m", start=START), ("SPY", "QQQ")))
    assert results[0].attrs["evidence"] != results[1].attrs["evidence"]
    for symbol, frame in zip(("SPY", "QQQ"), results, strict=True):
        result = read_reference(frame.attrs["evidence"])
        page = read_reference(result["pages"][0])
        assert list(page["response"]["bars"]) == [symbol]
    # Scope resets after both calls; unrelated broker reads never enter captures.
    broker_pages = len(list(provider.evidence.directory.rglob("page-*.json")))
    assert broker_pages == 2


def test_page_limit_retains_prior_pages_and_refuses_partial_data(captured_provider):
    venue, _, provider = captured_provider
    provider.evidence.policy = MarketDataEvidenceConfig(max_pages=1)
    venue.override = lambda *args: (200, {"bars": {"SPY": [row()]}, "next_page_token": "loop"})
    with pytest.raises(BarAcquisitionError) as caught:
        provider.fetch_bars("SPY", "1m", start=START)
    result = read_reference(caught.value.evidence)
    assert result["status"] == "failed"
    assert result["error_type"] == "EvidenceCapacityError"
    assert len(result["pages"]) == 1
    assert len(venue.calls) == 2


@pytest.mark.parametrize("fault", ["disk", "bytes", "completion"])
def test_storage_failures_never_return_unaudited_bars(captured_provider, monkeypatch, fault):
    venue, _, provider = captured_provider
    venue.override = lambda *args: (200, {"bars": {"SPY": [row(unknown="x" * 2000)]}, "next_page_token": None})
    if fault == "disk":
        provider.evidence.policy = MarketDataEvidenceConfig(min_free_bytes=10**30)
    elif fault == "bytes":
        provider.evidence.policy = MarketDataEvidenceConfig(max_capture_bytes=1024)
    else:
        original = evidence.save_json_report

        def fail_completion(document, path):
            if path.name == "result.json":
                raise OSError("fixture write failure")
            return original(document, path)

        monkeypatch.setattr(evidence, "save_json_report", fail_completion)
    with pytest.raises(BarAcquisitionError) as caught:
        provider.fetch_bars("SPY", "1m", start=START)
    result = read_reference(caught.value.evidence)
    if fault == "completion":
        assert caught.value.evidence["incomplete"]
        assert "status" not in result  # An unfinished manifest is not a completed capture.
        assert len(list(provider.evidence.directory.rglob("page-*.json"))) == 1
    else:
        assert result["status"] == "failed"
        assert result["error_type"] == "EvidenceCapacityError"
    assert len(venue.calls) == (0 if fault == "disk" else 1)
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in provider.evidence.directory.rglob("*") if p.is_dir())


def test_crypto_capture_preserves_its_own_endpoint_and_feed(captured_provider):
    venue, broker, provider = captured_provider
    client = BoundedCryptoDataClient(
        "fake-key", "fake-secret", url_override=broker.data_client._base_url, request_timeout=0.2
    )
    # Reuse the fixture session so both TCP and optional in-process transports work.
    client._session.close()
    client._session = broker.data_client._session
    crypto = AlpacaDataProvider(crypto_client=client, evidence=provider.evidence)
    venue.override = lambda *args: (200, {"bars": {"BTC/USD": [row()]}, "next_page_token": None})
    frame = crypto.fetch_bars("BTC/USD", "1m", start=START)
    assert frame.attrs["feed"] == "alpaca:crypto"
    result = read_reference(frame.attrs["evidence"])
    page = read_reference(result["pages"][0])
    assert page["path"] == "/v1beta3/crypto/us/bars"
    assert page["parameters"]["symbols"] == "BTC/USD"
