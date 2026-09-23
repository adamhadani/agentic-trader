"""Daemon job registration: session-aligned suggestion scans and the scoped intraday job.

The suggestion scans are cron jobs on the New York clock, not an interval that drifts
against the session. The 15-minute intraday job only ever scans the explicitly
configured contracts -- the wide universe is reserved for the session-aligned scans.
"""

from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from agentic_trader.cli.commands import service
from agentic_trader.config import ScanBudget, UniverseConfig, UniverseEntry


def test_suggestion_scans_are_cron_jobs_in_new_york_on_weekdays(config):
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_suggestion_scans(scheduler, copilot, config, use_llm=True)
    calls = scheduler.add_job.call_args_list
    assert len(calls) == 2
    for call, (hour, minute) in zip(calls, [(10, 35), (14, 35)], strict=True):
        assert call.args[1] == "cron"
        assert call.kwargs["hour"] == hour and call.kwargs["minute"] == minute
        assert call.kwargs["day_of_week"] == "mon-fri"
        assert call.kwargs["timezone"] == ZoneInfo("America/New_York")
    assert calls[0].kwargs["kwargs"] == {"digest": False} and calls[1].kwargs["kwargs"] == {"digest": True}


def test_suggestion_scans_never_overlap_and_tolerate_a_late_start(config):
    config.scheduler.suggestion_scan_times_et = ["10:35"]
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=False)
    kwargs = scheduler.add_job.call_args.kwargs
    assert kwargs["coalesce"] is True
    assert kwargs["max_instances"] == 1
    assert kwargs["misfire_grace_time"] == 600


async def test_suggestion_scan_runs_full_budget_only_when_the_equity_session_is_open(config):
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.publish_scan_digest = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(False, "holiday"))
    job = service.make_suggestion_scan(copilot, use_llm=True)
    await job(digest=True)
    copilot.run_scan.assert_not_awaited()
    copilot.publish_scan_digest.assert_awaited_once()  # a quiet day still reports
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    await job(digest=False)
    copilot.run_scan.assert_awaited_once_with(
        use_llm=True, dry_run=False, asset_class="equity", budget=ScanBudget.FULL, shadow_evidence=True
    )


def test_intraday_scan_is_restricted_to_non_universe_contracts(config):
    config.universe = UniverseConfig(groups={"g": [UniverseEntry(symbol="AAPL"), UniverseEntry(symbol="MSFT")]})
    config.contracts = {"SPY": MagicMock(), "AAPL": MagicMock(), "MSFT": MagicMock(), "/MES": MagicMock()}
    config.explicit_contracts = ("SPY", "/MES")
    scheduler = MagicMock()
    copilot = MagicMock()
    service.register_intraday_scan(scheduler, copilot, config, use_llm=True)
    job = scheduler.add_job.call_args.args[0]  # a functools.partial; its keywords are inspectable
    assert job.keywords["symbols"] == ["/MES", "SPY"]


def test_no_configured_times_disables_the_suggestion_scans(config):
    """F7: an empty suggestion_scan_times_et is the documented operator off switch."""
    config.scheduler.suggestion_scan_times_et = []
    scheduler = MagicMock()
    service.register_suggestion_scans(scheduler, MagicMock(), config, use_llm=True)
    scheduler.add_job.assert_not_called()


def test_intraday_scan_is_not_registered_without_explicit_contracts(config):
    """F3: run_scan(symbols=[]) selects the whole universe, so an empty scope must
    register no job at all rather than a 15-minute universe scan."""
    config.universe = UniverseConfig(groups={"g": [UniverseEntry(symbol="AAPL")]})
    config.contracts = {"AAPL": MagicMock()}
    config.explicit_contracts = ()
    scheduler = MagicMock()
    service.register_intraday_scan(scheduler, MagicMock(), config, use_llm=True)
    scheduler.add_job.assert_not_called()


async def test_intraday_scan_runs_only_inside_a_session_and_keeps_its_symbols(config):
    config.contracts = {"SPY": MagicMock()}
    config.explicit_contracts = ("SPY",)
    scheduler = MagicMock()
    copilot = MagicMock()
    copilot.run_scan = AsyncMock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(True, "open"))
    service.register_intraday_scan(scheduler, copilot, config, use_llm=False)
    job = scheduler.add_job.call_args.args[0]
    await job()
    copilot.run_scan.assert_awaited_once_with(
        use_llm=False,
        dry_run=False,
        asset_class="all",
        timeframe="15m",
        symbols=["SPY"],
        budget=ScanBudget.FULL,
    )
    copilot.run_scan.reset_mock()
    copilot.session_provider.is_session_active = AsyncMock(return_value=(False, "closed"))
    await job()
    copilot.run_scan.assert_not_awaited()
