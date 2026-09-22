import logging
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot


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


async def test_shadow_observes_even_insufficient_data_when_not_dry_run(scan_desk, app_config):
    """I2: base order is fetch -> alpha_shadow.observe -> insufficient-data check.

    A name with empty daily/4h frames must still reach observe() (which persists a
    durable, if invalid, observation row) before being classified as insufficient.
    """
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(
        daily=pd.DataFrame(), four_hour=pd.DataFrame(), hourly=pd.DataFrame()
    )
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    scan_desk.alpha_shadow.observe.assert_awaited_once()
    assert scan_desk.last_scan_summary["insufficient"] == ["AAA"]


async def test_fetch_failures_degrade_scan_readiness(scan_desk, app_config):
    """I3: a fetch exception must feed scan_errors so SCAN readiness reflects it."""
    app_config.contracts = {"AAA": instrument("AAA")}

    def fetch(contract, ticker, include_fifteen_min=True):
        raise ConnectionError("boom")

    scan_desk.data_fetcher.fetch_data.side_effect = fetch
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    scan_calls = [c for c in scan_desk.readiness.observe.await_args_list if c.args[0] == HealthComponent.SCAN]
    assert len(scan_calls) == 1
    assert scan_calls[0].args[1] is False
    assert "1" in scan_calls[0].args[2]


async def test_insufficient_data_alone_does_not_degrade_scan_readiness(scan_desk, app_config):
    """I3 (controller ruling correction): pre-change, an empty-but-fetched frame never
    incremented scan_errors -- only exceptions did. A wide universe legitimately
    contains thin names, so an insufficient-data-only name must still report SCAN
    readiness healthy; the count is surfaced in the detail string only. At least one
    name must still reach strategy scanning -- a scan that scanned nothing at all is
    covered by the zero-scanned test below."""
    app_config.contracts = {"AAA": instrument("AAA"), "THIN": instrument("THIN")}

    def fetch(contract, ticker, include_fifteen_min=True):
        if contract == "THIN":
            return SimpleNamespace(daily=pd.DataFrame(), four_hour=pd.DataFrame(), hourly=pd.DataFrame())
        return SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())

    scan_desk.data_fetcher.fetch_data.side_effect = fetch
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    scan_calls = [c for c in scan_desk.readiness.observe.await_args_list if c.args[0] == HealthComponent.SCAN]
    assert len(scan_calls) == 1
    assert scan_calls[0].args[1] is True
    assert "1 insufficient" in scan_calls[0].args[2]


async def test_a_selection_that_scans_nothing_degrades_scan_readiness(scan_desk, app_config):
    """F1: the selection was non-empty but nothing reached strategy scanning (every
    frame came back empty). That is a silent whole-scan failure, so SCAN readiness
    must be degraded and must say how many of how many instruments were scanned."""
    app_config.contracts = {"AAA": instrument("AAA"), "BBB": instrument("BBB")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(
        daily=pd.DataFrame(), four_hour=pd.DataFrame(), hourly=pd.DataFrame()
    )
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    scan_calls = [c for c in scan_desk.readiness.observe.await_args_list if c.args[0] == HealthComponent.SCAN]
    assert len(scan_calls) == 1
    assert scan_calls[0].args[1] is False
    assert "0 of 2 instruments scanned" in scan_calls[0].args[2]
    assert "2 insufficient" in scan_calls[0].args[2]


async def test_futures_only_selection_skips_the_coverage_gate_silently(scan_desk, app_config, caplog):
    """F8: the equity coverage reference is irrelevant to a futures-only selection, so
    an absent SPY must not raise a coverage WARNING on every futures scan."""
    app_config.contracts = {"/MES": instrument("/MES", "FUTURES")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    with caplog.at_level(logging.WARNING):
        await scan_desk.run_scan(use_llm=False, dry_run=False)
    assert not [r for r in caplog.records if getattr(r, "event", None) == "coverage_gate_skipped"]
    assert scan_desk.last_scan_summary["coverage_note"] is None
    assert scan_desk.last_scan_summary["coverage_excluded"] == []


async def test_shadow_observe_uses_each_symbols_fetch_receipt_as_of(scan_desk, app_config):
    """I4: observe() must be stamped with the symbol's own fetch-receipt time, not a
    scan-wide timestamp taken well after the fetch phase completed."""
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    before = datetime.now(UTC)
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    after = datetime.now(UTC)
    as_of = scan_desk.alpha_shadow.observe.call_args.kwargs.get("as_of")
    assert as_of is not None
    assert before <= as_of <= after


async def test_swing_scan_still_fetches_fifteen_minutes_when_a_15m_alpha_is_installed(scan_desk, app_config):
    """I5: include_fifteen_min must OR in any active/shadow/probe 15m definition,
    even for a swing scan with no explicit timeframe requested."""
    app_config.contracts = {"AAA": instrument("AAA")}
    shadow_def = AlphaDefinition("alpha_15m_test", "Fifteen", "close", timeframe="15m")
    scan_desk.alpha_repository.snapshot.return_value = RegistrySnapshot(1, (), (shadow_def,))
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    assert scan_desk.data_fetcher.fetch_data.call_args.kwargs.get("include_fifteen_min") is True


def hourly_frame(days=12, bars_per_day=7, volume=1000):
    idx = pd.date_range("2026-08-03 09:30", periods=days * 24, freq="h", tz="America/New_York")
    idx = idx[idx.indexer_between_time("09:30", "15:30")][: days * bars_per_day]
    return pd.DataFrame({"Close": 100.0, "Volume": volume}, index=idx)


async def test_coverage_gate_excludes_thin_names_from_strategy_scanning(scan_desk, app_config):
    """Task 3: a name with far fewer active hourly bars than the reference (SPY) must
    still be observed by alpha_shadow (evidence on thin names remains valid) but must
    never reach strategy_engine.scan_contract, and must be reported in the summary."""
    app_config.contracts = {"SPY": instrument("SPY"), "THIN": instrument("THIN")}

    def fetch(contract, ticker, include_fifteen_min=True):
        volume = 1000 if contract == "SPY" else 0
        return SimpleNamespace(contract=contract, daily=frame(), four_hour=frame(), hourly=hourly_frame(volume=volume))

    scan_desk.data_fetcher.fetch_data.side_effect = fetch
    await scan_desk.run_scan(use_llm=False, dry_run=False)

    scanned_contracts = {call.args[0].contract for call in scan_desk.strategy_engine.scan_contract.call_args_list}
    assert scanned_contracts == {"SPY"}
    observed_contracts = {call.args[1].contract for call in scan_desk.alpha_shadow.observe.call_args_list}
    assert observed_contracts == {"SPY", "THIN"}  # both still get shadow evidence
    assert scan_desk.last_scan_summary["coverage_excluded"] == ["THIN"]


async def test_coverage_gate_failure_never_fails_the_scan(scan_desk, app_config, monkeypatch):
    """The gate must never raise out of run_scan: any exception computing exclusions
    is treated as a skipped gate (empty exclusions, a note set), not a scan failure."""
    app_config.contracts = {"SPY": instrument("SPY"), "THIN": instrument("THIN")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())

    def boom(*args, **kwargs):
        raise RuntimeError("coverage gate exploded")

    monkeypatch.setattr("agentic_trader.agent.copilot.coverage_exclusions", boom)
    await scan_desk.run_scan(use_llm=False, dry_run=False)
    assert scan_desk.last_scan_summary["coverage_excluded"] == []
    assert scan_desk.last_scan_summary["coverage_note"]
    assert scan_desk.strategy_engine.scan_contract.call_count == 2


async def test_last_scan_summary_is_reset_before_an_early_return(scan_desk, app_config):
    """Minor: a halted/blocked scan must not leave a previous scan's counts visible."""
    app_config.contracts = {"AAA": instrument("AAA")}
    scan_desk.data_fetcher.fetch_data.return_value = SimpleNamespace(daily=frame(), four_hour=frame(), hourly=frame())
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    assert scan_desk.last_scan_summary["scanned"] == 1

    scan_desk.session_provider.is_session_active.return_value = (False, "closed")
    await scan_desk.run_scan(use_llm=False, dry_run=True)
    assert scan_desk.last_scan_summary == {}
