from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.cli.utils import artifact_directory, coro, get_copilot_and_config, session_source
from agentic_trader.config import AppConfig, ScanBudget, load_config
from agentic_trader.constants import MAX_DAILY_COMPARISONS, AuditEventType
from agentic_trader.diagnostics.doctor import format_doctor_cli_output, run_diagnostics
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.market.bars import ObservationStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.research.alpha.daily_observations import DailyPanelService
from agentic_trader.research.alpha.daily_plan import DailyComparisonPlan, DailyPanelPlan
from agentic_trader.research.alpha.decisions import SessionDecisionService
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.research.alpha.observation import SessionObservationService
from agentic_trader.runtime import runtime_identity
from agentic_trader.storage.alpha_daily import DailyCampaignRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.maintenance import RetentionService
from agentic_trader.storage.operations import OperationsStore
from agentic_trader.telemetry.event_loop import monitor_event_loop


logger = logging.getLogger("copilot")
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent


@click.command("eval", help="Run promptfoo evaluations on risk evaluator")
@coro
async def eval_command() -> None:
    """Run promptfoo evaluations on risk evaluator."""
    _copilot, config = get_copilot_and_config()
    logger.info("Running Promptfoo evaluation benchmark on risk prompts...")
    config_file = WORKSPACE_ROOT / "evals" / "promptfooconfig.yaml"
    provider = config.llm_model.replace("/", ":") if "/" in config.llm_model else f"openai:{config.llm_model}"
    logger.info("Evaluating production model: %s (Promptfoo provider: %s)", config.llm_model, provider)
    proc = await asyncio.create_subprocess_exec(
        "npx",
        "-y",
        "promptfoo",
        "eval",
        "--providers",
        provider,
        "-c",
        str(config_file),
        "--no-cache",
        env=os.environ.copy(),
    )
    rc = await proc.wait()
    sys.exit(rc)


@click.command("listen", help="Listen for interactive Telegram bot commands")
@coro
async def listen() -> None:
    """Listen for interactive Telegram bot commands."""
    copilot, _config = get_copilot_and_config()
    await copilot.broker.connect()
    if not copilot.notifier.is_configured():
        logger.error("Telegram is not configured in .envrc")
        return
    logger.info("Starting Telegram Bot listener... (press Ctrl+C to stop)")
    await copilot.notifier.start_polling()
    workflow_task = asyncio.create_task(copilot.workflow_worker())
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Stopping listener...")
        copilot._shutdown_event.set()
        workflow_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await workflow_task
        await copilot.notifier.stop_polling()


MAX_DAILY_PROTOCOL_BYTES = 1_000_000
DAILY_TERMINAL_LABELS = {
    **{
        f"decision:{status}": {"kind": "decision", "status": status}
        for status in (DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE, DecisionStatus.INTERRUPTED)
    },
    **{
        f"outcome:{status}": {"kind": "outcome", "status": status}
        for status in (ObservationStatus.COMPLETE, ObservationStatus.UNAVAILABLE)
    },
}


def _load_daily_protocol_document(path: Path | None):
    """Bound protocol reads before parsing or constructing any provider clients."""
    if path is None:
        raise ValueError("Explicit daily-panel protocol path required")
    with path.expanduser().open("rb") as source:
        payload = source.read(MAX_DAILY_PROTOCOL_BYTES + 1)
    if len(payload) > MAX_DAILY_PROTOCOL_BYTES:
        raise ValueError("Daily-panel protocol exceeds the bounded document size")
    return json.loads(payload)


def load_daily_panel_plan(path: Path | None) -> DailyPanelPlan:
    """Read exactly one explicit parent protocol; never infer it from trading configuration."""
    return DailyPanelPlan.from_document(_load_daily_protocol_document(path))


def load_daily_comparison_plans(paths: tuple[Path, ...], parent: DailyPanelPlan) -> tuple[DailyComparisonPlan, ...]:
    if len(paths) > MAX_DAILY_COMPARISONS:
        raise ValueError("Daily comparison enrollment exceeds its bounded protocol count")
    comparisons = tuple(DailyComparisonPlan.from_document(_load_daily_protocol_document(path)) for path in paths)
    if len({comparison.comparison_id for comparison in comparisons}) != len(comparisons):
        raise ValueError("Daily comparison identities must be unique")
    for comparison in comparisons:
        comparison.validate_parent(parent)
    return comparisons


def _record_research_results(results, metrics, component):
    if component == HealthComponent.ALPHA_DAILY_PANEL:
        if results["status"] not in ("idle", "enrolled", "recorded", "unavailable"):
            raise ValueError("Unknown daily-panel worker status")
        terminal_counts = results["terminal_counts"]
        comparison_counts = results["comparison_counts"]
        for counts in (terminal_counts, comparison_counts):
            if not isinstance(counts, dict) or any(
                key not in DAILY_TERMINAL_LABELS or type(count) is not int or count < 0 for key, count in counts.items()
            ):
                raise ValueError("Invalid daily-panel terminal counts")
        metrics.inc_counter("alpha_daily_panel_polls_total", labels={"status": results["status"]})
        for key in ("decisions", "outcomes"):
            metrics.inc_counter(f"alpha_daily_panel_{key}_total", value=results[key])
        for key, count in terminal_counts.items():
            metrics.inc_counter("alpha_daily_panel_terminals_total", value=count, labels=DAILY_TERMINAL_LABELS[key])
        for key, count in comparison_counts.items():
            metrics.inc_counter("alpha_daily_panel_comparisons_total", value=count, labels=DAILY_TERMINAL_LABELS[key])
        if results["status"] != "idle":
            logger.info(
                "Daily panel progress: %s %s",
                results["campaign_id"],
                results["status"],
                extra={
                    "event": "alpha_daily_panel",
                    "campaign_id": results["campaign_id"],
                    "status": results["status"],
                    "decisions": results["decisions"],
                    "outcomes": results["outcomes"],
                    "terminal_counts": terminal_counts,
                    "comparison_counts": comparison_counts,
                },
            )
        return
    for result in results:
        labels = {"symbol": result["symbol"], "timeframe": result["timeframe"], "feed": result["feed"]}
        if component == HealthComponent.ALPHA_DECISIONS:
            metrics.inc_counter("alpha_session_decisions_total", labels={**labels, "status": result["status"]})
            logger.info(
                "Session decision retained: %s %s %s",
                result["symbol"],
                result["closed_at"],
                result["status"],
                extra={
                    "event": "alpha_session_decision",
                    "decision_id": result["decision_id"],
                    "status": result["status"],
                    "artifact_hash": result.get("artifact_hash"),
                },
            )
            continue
        complete = result["status"] == ObservationStatus.COMPLETE
        metrics.set_gauge("alpha_observation_complete", float(complete), labels=labels)
        metrics.set_gauge("alpha_observation_timestamp_seconds", datetime.now(UTC).timestamp(), labels=labels)
        if complete:
            metrics.set_gauge(
                "alpha_observation_availability_upper_bound_seconds",
                result["availability_upper_bound_seconds"],
                labels=labels,
            )
        logger.info(
            "Session observation retained: %s %s %s",
            result["symbol"],
            result["closed_at"],
            result["status"],
            extra={
                "event": "alpha_session_observation",
                "observation_id": result["observation_id"],
                "status": result["status"],
                "artifact_hash": result["artifact_hash"],
            },
        )


async def _poll_research_worker(worker, readiness, metrics, shutdown, *, component, poll_seconds, offset=0):
    """Shared wall-clock polling; shutdown drains current work before the caller closes readers."""
    while not shutdown.is_set():
        try:
            results = await worker.run_once()
            _record_research_results(results, metrics, component)
            # Durable worker progress is separate from usable data or forecast outcomes.
            await readiness.observe(component, True)
        except Exception as exc:
            logger.exception("Research diagnostic worker failed: %s", component)
            await readiness.observe(component, False, type(exc).__name__)
        delay = poll_seconds - ((datetime.now(UTC).timestamp() - offset) % poll_seconds)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=delay)


async def run_session_worker(config, repository, readiness, metrics, shutdown, *, component):
    """Compose one session observer using its own source policy and owned readers."""
    factory, policy, folder = {
        HealthComponent.ALPHA_OBSERVER: (
            SessionObservationService,
            config.alpha_pipeline.observations,
            "forward-observations",
        ),
        HealthComponent.ALPHA_DECISIONS: (SessionDecisionService, config.alpha_pipeline.decisions, "forward-decisions"),
    }[component]
    with session_source(config, policy.feed.removeprefix("alpaca:")) as source:
        worker = factory(
            repository,
            source,
            policy,
            directory=artifact_directory() / folder,
            runtime=await asyncio.to_thread(runtime_identity),
        )
        await _poll_research_worker(
            worker,
            readiness,
            metrics,
            shutdown,
            component=component,
            poll_seconds=policy.poll_seconds,
            offset=policy.poll_offset_seconds,
        )


async def run_daily_panel_worker(config, repository, readiness, metrics, shutdown):
    """Load one frozen daily protocol; no hot reload, notifier, broker writes or second daemon."""
    policy = config.alpha_pipeline.daily_panel
    if not policy.enabled:
        return
    component = HealthComponent.ALPHA_DAILY_PANEL
    try:
        plan = await asyncio.to_thread(load_daily_panel_plan, policy.protocol_path)
        comparisons = await asyncio.to_thread(load_daily_comparison_plans, policy.comparison_protocol_paths, plan)
        daily_repository = DailyCampaignRepository(repository.store, policy=config.alpha_pipeline)
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            worker = DailyPanelService(
                daily_repository,
                source,
                plan,
                policy,
                acquisition=config.alpha_pipeline.daily_research,
                directory=artifact_directory() / "daily-panel",
                runtime=await asyncio.to_thread(runtime_identity),
                on_progress=lambda: readiness.observe(component, True),
                comparisons=comparisons,
            )
            await _poll_research_worker(
                worker, readiness, metrics, shutdown, component=component, poll_seconds=policy.poll_seconds
            )
    except Exception as exc:
        logger.exception("Daily-panel worker initialization failed")
        await readiness.observe(component, False, type(exc).__name__)
        # Invalid configuration stays visibly failed until a controlled restart.
        await shutdown.wait()


def make_suggestion_scan(copilot: Any, *, use_llm: bool):
    """The session-aligned suggestion scan: a full-budget equity scan, then an optional digest."""

    async def run_suggestion_scan(digest: bool = False) -> None:
        active, reason = await copilot.session_provider.is_session_active(instrument_type="equity")
        if active:
            await copilot.run_scan(
                use_llm=use_llm, dry_run=False, asset_class="equity", budget=ScanBudget.FULL, shadow_evidence=True
            )
        else:
            logger.info(
                "Suggestion scan skipped: %s", reason, extra={"event": "suggestion_scan_skipped", "reason": reason}
            )
        # A closed session is still a reportable session: the digest always runs.
        if digest:
            await copilot.publish_scan_digest()

    return run_suggestion_scan


def register_suggestion_scans(scheduler: Any, copilot: Any, config: AppConfig, *, use_llm: bool) -> None:
    """Register one cron job per configured New York suggestion-scan time (weekdays only).

    An empty ``suggestion_scan_times_et`` is the operator off switch: no cron job, and
    therefore no automatic suggestion scan and no end-of-session digest.
    """
    times = config.scheduler.suggestion_scan_times_et
    if not times:
        logger.info("Suggestion scans disabled (no times configured).")
        return
    job = make_suggestion_scan(copilot, use_llm=use_llm)
    for index, item in enumerate(times):
        hour, minute = (int(part) for part in item.split(":"))
        scheduler.add_job(
            job,
            "cron",
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            timezone=ET_TZ,
            id=f"suggestion_scan_{index}",
            kwargs={"digest": index == len(times) - 1},
            # A scan still running at the next trigger is skipped and logged rather than
            # overlapped; a late start within ten minutes still runs exactly once.
            coalesce=True,
            max_instances=1,
            misfire_grace_time=600,
        )
    logger.info("Scheduled suggestion scans at %s New York on weekdays.", ", ".join(times))


def register_intraday_scan(scheduler: Any, copilot: Any, config: AppConfig, *, use_llm: bool) -> None:
    """Register the 15-minute intraday scan, scoped to the explicitly configured contracts."""

    async def run_intraday_scan(*, symbols: list[str]) -> None:
        session_active, reason = await copilot.session_provider.is_session_active(instrument_type="all")
        if session_active:
            await copilot.run_scan(
                use_llm=use_llm,
                dry_run=False,
                asset_class="all",
                timeframe="15m",
                symbols=symbols,
                budget=ScanBudget.FULL,
            )
        else:
            logger.debug("Intraday scan skipped outside market session: %s", reason)

    symbols = config.non_universe_contracts
    if not symbols:
        # run_scan(symbols=[]) selects the whole universe, so an empty scope must
        # register no job at all rather than a 15-minute universe scan.
        logger.info("Intraday scanner has no explicitly configured contracts; the universe is scanned on the cron.")
        return
    scheduler.add_job(
        functools.partial(run_intraday_scan, symbols=symbols),
        "interval",
        minutes=config.scheduler.intraday_interval_minutes,
        id="intraday_scan",
        next_run_time=datetime.now(UTC),
    )


@click.command("daemon", help="Run continuous daemon scanner and trade manager")
@click.option(
    "--no-llm",
    is_flag=True,
    default=False,
    help="Bypass LLM evaluation in background scheduled scans",
)
@coro
async def daemon(no_llm: bool) -> None:
    """Run continuous daemon scanner and trade manager."""
    copilot, config = get_copilot_and_config()
    if not await copilot.broker.connect():
        raise click.ClickException("Broker connection failed; daemon startup aborted.")
    identity = {**runtime_identity(), "broker": type(copilot.broker).__name__, "alpaca_paper": config.alpaca_paper}
    await copilot.db.record_audit(AuditEventType.RUNTIME_STARTED, identity)
    logger.info("Runtime started: %s", identity)

    if copilot.notifier.is_configured():
        logger.info("Starting Telegram Bot listener for interactive callbacks...")
        await copilot.notifier.start_polling()

    stream_task: asyncio.Task[None] | None = None
    if copilot.broker.supports_trade_stream:
        stream_task = asyncio.create_task(copilot.start_trade_stream())

    if copilot.metrics_server:
        await copilot.metrics_server.start()

    copilot.readiness.started = True
    workflow_task = asyncio.create_task(copilot.workflow_worker())
    lag_task = asyncio.create_task(monitor_event_loop(config.telemetry, copilot.metrics, copilot.db.record_audit))

    session_tasks = [
        asyncio.create_task(
            run_session_worker(
                config,
                copilot.alpha_repository,
                copilot.readiness,
                copilot.metrics,
                copilot._shutdown_event,
                component=component,
            )
        )
        for component, policy in (
            (HealthComponent.ALPHA_OBSERVER, config.alpha_pipeline.observations),
            (HealthComponent.ALPHA_DECISIONS, config.alpha_pipeline.decisions),
        )
        if policy.enabled
    ]

    if config.alpha_pipeline.daily_panel.enabled:
        session_tasks.append(
            asyncio.create_task(
                run_daily_panel_worker(
                    config, copilot.alpha_repository, copilot.readiness, copilot.metrics, copilot._shutdown_event
                )
            )
        )

    scheduler = AsyncIOScheduler(
        job_defaults={
            "misfire_grace_time": config.scheduler.misfire_grace_seconds,
            "coalesce": True,
            "max_instances": 1,
        }
    )
    interval = config.scheduler.cron_hour_interval
    # Schedule regular swing scans
    scheduler.add_job(
        copilot.run_scan,
        "interval",
        hours=interval,
        args=[not no_llm, False],
        kwargs={"budget": ScanBudget.FULL},
        id="swing_scan",
        next_run_time=datetime.now(UTC),
    )
    # Schedule intraday 15-minute scans during active market sessions
    if config.scheduler.intraday_scan_enabled:
        register_intraday_scan(scheduler, copilot, config, use_llm=not no_llm)
        logger.info(
            "Scheduled intraday scanner every %d minutes (session-gated).",
            config.scheduler.intraday_interval_minutes,
        )
    # Session-aligned suggestion scans on the New York clock; the last one publishes the digest.
    register_suggestion_scans(scheduler, copilot, config, use_llm=not no_llm)
    # Schedule automated position monitoring & broker reconciliation every 1 minute
    scheduler.add_job(
        copilot.monitor_positions,
        "interval",
        minutes=1,
        id="position_monitor",
        next_run_time=datetime.now(UTC),
    )
    # Schedule daily morning macro briefing (Monday - Friday)
    if config.scheduler.macro_briefing_enabled:
        scheduler.add_job(
            copilot.broadcast_macro_briefing,
            "cron",
            day_of_week="mon-fri",
            hour=config.scheduler.macro_briefing_hour,
            minute=config.scheduler.macro_briefing_minute,
            id="macro_briefing",
        )
        logger.info(
            "Scheduled morning macro briefing for mon-fri at %02d:%02d UTC.",
            config.scheduler.macro_briefing_hour,
            config.scheduler.macro_briefing_minute,
        )
    scheduler.start()
    logger.info("Scheduler started: scanning every %dh, reconciling positions every 1m.", interval)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Shutting down daemon...")
        copilot._shutdown_event.set()
        copilot.readiness.started = False
        for task in session_tasks:
            await task
        workflow_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await workflow_task
        lag_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lag_task
        if copilot.metrics_server:
            await copilot.metrics_server.stop()
        if stream_task and not stream_task.done():
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
        await copilot.broker.stop_trade_stream()
        scheduler.shutdown()

        await copilot.notifier.stop_polling()


@click.command("doctor", help="Run active diagnostics or inspect/supervise daemon readiness")
@click.option("--readiness", is_flag=True, help="Read daemon freshness without active probes or state changes")
@click.option(
    "--monitor", is_flag=True, help="Persist readiness incidents, enqueue alerts and compact old healthy observations"
)
@coro
async def doctor(readiness: bool, monitor: bool) -> None:
    if readiness and monitor:
        raise click.UsageError("Choose --readiness or --monitor")
    config = load_config()
    if readiness or monitor:
        async with httpx.AsyncClient() as client:
            report = await probe_readiness(config, client)
        if monitor:
            await monitor_once(config, report)
        click.echo(json.dumps(report, indent=2))
        if not report["ready"]:
            raise click.ClickException("Daemon is not ready; inspect component freshness above.")
        return
    diagnostic_report = await run_diagnostics(config)
    click.echo(format_doctor_cli_output(diagnostic_report))


async def monitor_once(config: AppConfig, report: dict[str, Any]) -> None:
    """Composition boundary for an external supervisor, never a second poller."""
    db = await asyncio.to_thread(SignalDatabase, config=config)
    try:
        monitor = OperationsMonitor(OperationsStore(db.workflows), config.operations)
        await monitor.observe(report)
        await RetentionService(db.workflows, config.operations).sweep(apply=True, only_if_due=True)
        # Let the normal consumer deliver. If its loop/process is unavailable,
        # this separate process may deliver one existing outbox item with the same
        # fenced claim/retry protocol. It has no broker or execution consumer.
        checks = report["checks"]
        if checks.get("endpoint", {}).get("ready") is False or checks.get("delivery", {}).get("ready") is False:
            notifier = TelegramNotifier(
                config.telegram_bot_token,
                config.telegram_chat_id,
                db=db,
                settings=config.telegram,
                environment=config.environment,
                execution_mode=config.execution_mode,
            )
            if notifier.app and notifier.is_configured():
                async with asyncio.timeout(config.execution.notification_delivery_timeout_seconds):
                    async with notifier.app.bot:
                        await NotificationDispatcher(db.workflows, notifier, config.execution).dispatch_one()
    finally:
        await db.engine.dispose()
