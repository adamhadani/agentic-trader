import threading
from datetime import UTC, datetime, timedelta

import pandas as pd

from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.data.pacing import RequestPacer


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_pacer_allows_a_burst_up_to_the_limit_then_waits_for_the_window():
    clock = FakeClock()
    pacer = RequestPacer(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.acquire()
    assert clock.slept == []
    pacer.acquire()  # fourth request within the same minute must wait for the oldest to age out
    assert len(clock.slept) == 1 and 59.9 <= clock.slept[0] <= 60.0


def test_pacer_frees_capacity_as_the_window_slides():
    clock = FakeClock()
    pacer = RequestPacer(2, clock=clock, sleep=clock.sleep)
    pacer.acquire()
    clock.now += 30
    pacer.acquire()
    clock.now += 31  # first request is now 61 s old
    pacer.acquire()
    assert clock.slept == []


def test_pacer_is_safe_across_threads():
    clock = FakeClock()
    lock = threading.Lock()

    def sleep(seconds):
        with lock:
            clock.sleep(seconds)

    pacer = RequestPacer(5, clock=clock, sleep=sleep)
    threads = [threading.Thread(target=pacer.acquire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(clock.slept) >= 1


class FakeProvider:
    def fetch_bars(self, symbol, timeframe, start=None, end=None, period=None):
        base = datetime(2026, 1, 1, tzinfo=UTC)
        index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(3)])
        return pd.DataFrame(
            {"Open": [1.0] * 3, "High": [1.0] * 3, "Low": [1.0] * 3, "Close": [1.0] * 3, "Volume": [100] * 3},
            index=index,
        )


class RecordingPacer:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def test_fetch_data_acquires_the_pacer_once_per_provider_read():
    pacer = RecordingPacer()
    fetcher = MarketDataFetcher(provider=FakeProvider(), pacer=pacer)

    fetcher.fetch_data(contract="X", ticker="X", include_fifteen_min=False)
    assert pacer.calls == 2  # daily + hourly, no 15m read

    pacer.calls = 0
    fetcher.fetch_data(contract="X", ticker="X", include_fifteen_min=True)
    assert pacer.calls == 3  # daily + hourly + 15m
