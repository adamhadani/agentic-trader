import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd


def frame(rows=30):
    return pd.DataFrame({"Close": [100.0] * rows, "Volume": [1000] * rows})


def instrument(symbol, asset_class="EQUITY"):
    return SimpleNamespace(name=symbol, ticker=symbol, asset_class=asset_class)


async def test_fetch_phase_is_bounded_concurrent_and_accounts_failures(scan_desk, app_config):
    app_config.contracts = {s: instrument(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    app_config.market_data.scan_concurrency = 2
    counter = {"in_flight": 0, "peak": 0}
    lock = threading.Lock()

    def fetch(contract, ticker, include_fifteen_min=True):
        # asyncio.to_thread runs this in a worker thread; guard the counters with a real lock
        with lock:
            counter["in_flight"] += 1
            counter["peak"] = max(counter["peak"], counter["in_flight"])
        time.sleep(0.05)
        with lock:
            counter["in_flight"] -= 1
        if contract == "BBB":
            raise ConnectionError("boom")
        if contract == "CCC":
            return SimpleNamespace(daily=pd.DataFrame(), four_hour=pd.DataFrame(), hourly=pd.DataFrame())
        return SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())

    scan_desk.data_fetcher.fetch_data.side_effect = fetch
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    assert counter["peak"] <= 2
    calls = scan_desk.data_fetcher.fetch_data.call_args_list
    assert len(calls) == 4
    assert all(call.kwargs.get("include_fifteen_min") is False for call in calls)
    summary = scan_desk.last_scan_summary
    assert summary["fetch_failed"] == ["BBB"] and summary["insufficient"] == ["CCC"]
    assert summary["scanned"] == 2


async def test_fifteen_minute_scans_still_fetch_fifteen_minute_bars(scan_desk, app_config):
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True, timeframe="15m")
    assert scan_desk.data_fetcher.fetch_data.call_args.kwargs.get("include_fifteen_min") is True


async def test_closed_equity_session_skips_equity_fetches_but_not_futures(scan_desk, app_config):
    app_config.contracts = {"AAA": instrument("AAA"), "/MES": instrument("/MES", "FUTURES")}

    async def is_session_active(instrument_type="all", timestamp=None):
        return (instrument_type != "equity", "test")

    scan_desk.session_provider.is_session_active.side_effect = is_session_active
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    fetched = {call.args[0] for call in scan_desk.data_fetcher.fetch_data.call_args_list}
    assert fetched == {"/MES"}
    assert scan_desk.last_scan_summary["skipped_closed_session"] == ["AAA"]


async def test_scan_duration_is_recorded(scan_desk, app_config):
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.metrics = MagicMock()
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    names = [call.args[0] for call in scan_desk.metrics.observe_histogram.call_args_list]
    assert "trader_scan_duration_seconds" in names
