"""`AlpacaDataProvider(end_delay=…)`: every bar request ends no later than ``now - end_delay``.

Without the SIP subscription a SIP request ending less than 15 minutes ago is HTTP 403; the clamp keeps every
scan-path SIP request old enough that the composite provider never falls back to yfinance for it.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.data.enums import DataFeed

from agentic_trader.config import AppConfig, MarketDataConfig
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.data.providers import AlpacaDataProvider


NOW = datetime(2026, 11, 9, 15, 35, tzinfo=UTC)  # 10:35 New York
DELAY = timedelta(minutes=16)
THROUGH = NOW - DELAY


def sent(moment: datetime) -> datetime:
    """The SDK request models carry UTC as naive datetimes."""
    return moment.astimezone(UTC).replace(tzinfo=None)


class RecordingStockClient:
    """Captures every bars request and serves one bar per requested symbol; latest trades at 101.5."""

    def __init__(self):
        self.requests: list = []
        self.latest: list = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        symbols = (
            [request.symbol_or_symbols] if isinstance(request.symbol_or_symbols, str) else request.symbol_or_symbols
        )
        index = pd.MultiIndex.from_product(
            [symbols, pd.DatetimeIndex([THROUGH - timedelta(hours=1)])], names=["symbol", "timestamp"]
        )
        frame = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1_000.0}, index=index
        )
        return SimpleNamespace(df=frame)

    def get_stock_latest_trade(self, request):
        self.latest.append(request)
        return {request.symbol_or_symbols: SimpleNamespace(price=101.5)}


def provider(end_delay=DELAY, feed="sip"):
    client = RecordingStockClient()
    return AlpacaDataProvider(stock_client=client, feed=feed, end_delay=end_delay, clock=lambda: NOW), client


def test_a_missing_end_becomes_now_minus_the_delay():
    bars, client = provider()

    bars.fetch_bars("AAPL", "1h", period="60d")

    [request] = client.requests
    assert request.end == sent(THROUGH)
    assert request.start == sent(NOW - timedelta(days=60))  # the same injected clock
    assert request.feed == DataFeed.SIP


def test_a_later_explicit_end_is_clamped_and_an_earlier_one_kept():
    bars, client = provider()
    start = NOW - timedelta(days=5)
    earlier = THROUGH - timedelta(minutes=1)

    bars.fetch_bars("AAPL", "1h", start=start, end=NOW)
    bars.fetch_bars("AAPL", "1h", start=start, end=THROUGH + timedelta(seconds=1))
    bars.fetch_bars("AAPL", "1h", start=start, end=earlier)
    bars.fetch_bars("AAPL", "1h", start=start, end=THROUGH)

    assert [request.end for request in client.requests] == [sent(t) for t in (THROUGH, THROUGH, earlier, THROUGH)]
    assert {request.start for request in client.requests} == {sent(start)}


def test_fetch_daily_many_is_clamped_the_same_way():
    bars, client = provider()
    start = NOW - timedelta(days=60)
    earlier = NOW - timedelta(days=1)

    later_frames = bars.fetch_daily_many(["AAPL", "MSFT"], start, NOW, adjustment="raw")
    bars.fetch_daily_many(["AAPL"], start, earlier, adjustment="raw")

    assert set(later_frames) == {"AAPL", "MSFT"}
    assert [request.end for request in client.requests] == [sent(THROUGH), sent(earlier)]


def test_a_naive_end_reads_as_utc_instead_of_raising():
    """A naive bound compared with the aware clamp would raise TypeError, and the composite provider would turn
    that into a silent yfinance fallback; the SDK reads a naive bound as UTC, and so does the clamp."""
    bars, client = provider()
    naive = sent

    bars.fetch_bars("AAPL", "1h", start=naive(NOW - timedelta(days=5)), end=naive(NOW))
    bars.fetch_bars("AAPL", "1h", start=naive(NOW - timedelta(days=5)), end=naive(THROUGH - timedelta(hours=1)))
    bars.fetch_daily_many(["AAPL"], naive(NOW - timedelta(days=60)), naive(NOW), adjustment="raw")

    assert [request.end for request in client.requests] == [
        sent(THROUGH),
        sent(THROUGH - timedelta(hours=1)),
        sent(THROUGH),
    ]
    assert [request.start for request in client.requests] == [
        sent(NOW - timedelta(days=5)),
        sent(NOW - timedelta(days=5)),
        sent(NOW - timedelta(days=60)),
    ]


@pytest.mark.parametrize("start", [THROUGH, THROUGH + timedelta(minutes=5), NOW - timedelta(minutes=1)])
def test_a_start_at_or_after_the_clamped_end_sends_nothing_and_returns_no_bars(start):
    """A decision made within the delay has no bar old enough to request: an empty frame, not a refused request
    (which `cards outcomes` would label a fetch failure)."""
    bars, client = provider()

    hourly = bars.fetch_bars("AAPL", "1h", start=start, end=NOW)
    naive = bars.fetch_bars("AAPL", "1h", start=sent(start), end=sent(NOW))
    daily = bars.fetch_daily_many(["AAPL", "MSFT"], start, NOW, adjustment="raw")

    assert client.requests == []
    assert daily == {}
    for frame in (hourly, naive):
        assert frame.empty
        assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert isinstance(frame.index, pd.DatetimeIndex) and str(frame.index.tz) == "UTC"
        assert (frame.attrs["feed"], frame.attrs["adjustment"], frame.attrs["timeframe"]) == ("alpaca:sip", "raw", "1h")


def test_a_start_just_before_the_clamped_end_is_still_requested():
    bars, client = provider()

    bars.fetch_bars("AAPL", "1h", start=THROUGH - timedelta(seconds=1), end=NOW)

    [request] = client.requests
    assert (request.start, request.end) == (sent(THROUGH - timedelta(seconds=1)), sent(THROUGH))


def test_without_a_delay_every_end_is_untouched():
    bars, client = provider(end_delay=None, feed="iex")

    bars.fetch_bars("AAPL", "1h", period="60d")
    bars.fetch_bars("AAPL", "1h", start=NOW - timedelta(days=5), end=NOW)
    bars.fetch_daily_many(["AAPL"], NOW - timedelta(days=60), NOW, adjustment="raw")

    assert [request.end for request in client.requests] == [None, sent(NOW), sent(NOW)]
    assert {request.feed for request in client.requests} == {DataFeed.IEX}


def test_the_latest_price_is_unaffected():
    bars, client = provider()

    assert bars.fetch_latest_price("AAPL") == 101.5

    [request] = client.latest
    assert request.feed is None  # the subscription's default feed, exactly as before
    assert client.requests == []


def _alpaca(fetcher: MarketDataFetcher) -> AlpacaDataProvider:
    [alpaca] = [p for p in fetcher.provider.providers if isinstance(p, AlpacaDataProvider)]
    return alpaca


@pytest.mark.parametrize("feed", ["sip", "iex"])
def test_the_scan_fetcher_reads_delayed_sip_under_sip_delayed(feed):
    config = AppConfig(market_data=MarketDataConfig(alpaca_feed=feed, scan_bars="sip_delayed"))

    alpaca = _alpaca(MarketDataFetcher(config=config))

    assert (alpaca.feed, alpaca.end_delay) == (DataFeed.SIP, DELAY)


@pytest.mark.parametrize("feed", ["sip", "iex"])
def test_the_scan_fetcher_reads_the_configured_feed_undelayed_by_default(feed):
    config = AppConfig(market_data=MarketDataConfig(alpaca_feed=feed))

    alpaca = _alpaca(MarketDataFetcher(config=config))

    assert (alpaca.feed, alpaca.end_delay) == (DataFeed(feed), None)
