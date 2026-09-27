from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.data.enums import Adjustment, DataFeed

from agentic_trader.data.providers import DAILY_BATCH_SYMBOLS, AlpacaDataProvider


def _symbols(n: int) -> list[str]:
    return [f"SYM{i:04d}" for i in range(n)]


class FakeStockClient:
    """Records every batched request and serves bars for every symbol but ``missing``."""

    def __init__(self, missing: set[str]):
        self.missing = missing
        self.requests: list = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        present = [s for s in request.symbol_or_symbols if s not in self.missing]
        if not present:
            empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            empty.index = pd.MultiIndex.from_arrays([[], []], names=["symbol", "timestamp"])
            return SimpleNamespace(df=empty)
        dates = pd.date_range("2026-09-01", periods=3, freq="D", tz="UTC")
        frames = []
        for symbol in present:
            index = pd.MultiIndex.from_product([[symbol], dates], names=["symbol", "timestamp"])
            frames.append(
                pd.DataFrame(
                    {
                        "open": [10.0, 10.5, 11.0],
                        "high": [11.0, 11.5, 12.0],
                        "low": [9.5, 10.0, 10.5],
                        "close": [10.5, 11.0, 11.5],
                        "volume": [1_000_000, 1_100_000, 1_200_000],
                    },
                    index=index,
                )
            )
        return SimpleNamespace(df=pd.concat(frames))


@pytest.fixture
def start_end():
    return pd.Timestamp("2026-09-01", tz="UTC").to_pydatetime(), pd.Timestamp("2026-09-05", tz="UTC").to_pydatetime()


def test_fetch_daily_many_batches_requests_and_omits_missing_symbols(start_end):
    start, end = start_end
    symbols = _symbols(250)
    missing = symbols[137]
    client = FakeStockClient(missing={missing})
    provider = AlpacaDataProvider(stock_client=client, feed="sip")

    result = provider.fetch_daily_many(symbols, start, end, adjustment="raw")

    assert len(client.requests) == 3
    assert all(len(request.symbol_or_symbols) <= DAILY_BATCH_SYMBOLS for request in client.requests)
    assert sum(len(request.symbol_or_symbols) for request in client.requests) == 250

    assert missing not in result
    assert set(result) == set(symbols) - {missing}

    sample = result[symbols[0]]
    assert list(sample.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert not isinstance(sample.index, pd.MultiIndex)

    for request in client.requests:
        assert request.feed == DataFeed.SIP
        assert request.adjustment == Adjustment.RAW


def test_fetch_daily_many_passes_the_requested_adjustment(start_end):
    start, end = start_end
    client = FakeStockClient(missing=set())
    provider = AlpacaDataProvider(stock_client=client, feed="iex")

    provider.fetch_daily_many(["AAA", "BBB"], start, end, adjustment="all")

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.feed == DataFeed.IEX
    assert request.adjustment == Adjustment.ALL


class _CountingBarSet:
    """A `BarSet` stand-in whose `.df` is a property, like the real SDK's (see
    `alpaca.data.models.base.TimeSeriesMixin.df`): every access rebuilds the frame from
    scratch, so a normalizer that reads it once per symbol instead of once per chunk pays
    for that rebuild `len(chunk)` times over.
    """

    def __init__(self, frame: pd.DataFrame, reads: list[int]):
        self._frame = frame
        self._reads = reads
        self._count = 0

    @property
    def df(self) -> pd.DataFrame:
        self._count += 1
        self._reads.append(self._count)
        return self._frame


class CountingStockClient:
    def __init__(self, reads: list[int]):
        self.reads = reads

    def get_stock_bars(self, request):
        dates = pd.date_range("2026-09-01", periods=3, freq="D", tz="UTC")
        frames = []
        for symbol in request.symbol_or_symbols:
            index = pd.MultiIndex.from_product([[symbol], dates], names=["symbol", "timestamp"])
            frames.append(
                pd.DataFrame(
                    {"open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3, "close": [1.0] * 3, "volume": [1] * 3},
                    index=index,
                )
            )
        return _CountingBarSet(pd.concat(frames), self.reads)


def test_fetch_daily_many_reads_the_expensive_df_property_once_per_chunk(start_end):
    start, end = start_end
    symbols = _symbols(150)  # two chunks: 100 + 50
    reads: list[int] = []
    provider = AlpacaDataProvider(stock_client=CountingStockClient(reads), feed="sip")

    result = provider.fetch_daily_many(symbols, start, end, adjustment="raw")

    assert len(result) == 150
    # One `.df` read per chunk (a fresh BarSet per chunk, each read exactly once), not
    # once per symbol: normalizing 100 symbols from one chunk must not cost 100 rebuilds.
    assert reads == [1, 1]
