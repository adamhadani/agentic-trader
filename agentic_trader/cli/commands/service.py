from __future__ import annotations

import asyncio
import contextlib
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
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AuditEventType
from agentic_trader.diagnostics.doctor import format_doctor_cli_output, run_diagnostics
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.market.bars import ObservationStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.research.alpha.observation import SessionObservationService
from agentic_trader.runtime import runtime_identity
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


async def run_session_observer(config, repository, readiness, metrics, shutdown):
    """One read-only consumer; finish in-flight reads before closing owned clients."""
    policy = config.alpha_pipeline.observations
    with session_source(config, config.market_data.alpaca_feed) as source:
        observer = SessionObservationService(
            repository,
            source,
            policy,
            feed=f"alpaca:{config.market_data.alpaca_feed}",
            directory=artifact_directory() / "forward-observations",
            runtime=await asyncio.to_thread(runtime_identity),
        )
        while not shutdown.is_set():
            try:
                results = await observer.run_once()
                for result in results:
                    labels = {"symbol": result["symbol"], "timeframe": result["timeframe"], "feed": result["feed"]}
                    complete = result["status"] == ObservationStatus.COMPLETE
                    metrics.set_gauge("alpha_observation_complete", float(complete), labels=labels)
                    metrics.set_gauge(
                        "alpha_observation_timestamp_seconds", datetime.now(UTC).timestamp(), labels=labels
                    )
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
                # This checks the collector's durable progress, not feed completeness.
                await readiness.observe(HealthComponent.ALPHA_OBSERVER, True)
            except Exception as exc:
                logger.exception("Session observation worker failed")
                await readiness.observe(HealthComponent.ALPHA_OBSERVER, False, type(exc).__name__)
            # Align polls to the wall clock, independent of daemon startup drift.
            # A configured offset leaves a small initial publication window.
            offset = policy.poll_offset_seconds
            delay = policy.poll_seconds - ((datetime.now(UTC).timestamp() - offset) % policy.poll_seconds)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=delay)


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

    observation_task = (
        asyncio.create_task(
            run_session_observer(
                config,
                copilot.alpha_repository,
                copilot.readiness,
                copilot.metrics,
                copilot._shutdown_event,
            )
        )
        if config.alpha_pipeline.observations.enabled
        else None
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
        id="swing_scan",
        next_run_time=datetime.now(UTC),
    )
    # Schedule intraday 15-minute scans during active market sessions
    if config.scheduler.intraday_scan_enabled:
        intraday_interval = config.scheduler.intraday_interval_minutes

        async def run_intraday_scan() -> None:
            session_active, reason = await copilot.session_provider.is_session_active(instrument_type="all")
            if session_active:
                await copilot.run_scan(
                    use_llm=not no_llm,
                    dry_run=False,
                    asset_class="all",
                    timeframe="15m",
                )
            else:
                logger.debug("Intraday scan skipped outside market session: %s", reason)

        scheduler.add_job(
            run_intraday_scan,
            "interval",
            minutes=intraday_interval,
            id="intraday_scan",
            next_run_time=datetime.now(UTC),
        )
        logger.info(
            "Scheduled intraday scanner every %d minutes (session-gated).",
            intraday_interval,
        )
    # Schedule automated position monitoring & broker reconciliation every 1 minute
    scheduler.add_job(
        copilot.monitor_positions,
        "interval",
        minutes=1,
        id="position_monitor",
        next_run_time=datetime.now(UTC),
    )
    # Schedule weekend automated parameter retuning
    if config.scheduler.retune_enabled:
        scheduler.add_job(
            copilot.run_auto_retune,
            "cron",
            day_of_week=config.scheduler.retune_day_of_week,
            hour=config.scheduler.retune_hour,
            minute=config.scheduler.retune_minute,
            id="auto_retune",
        )
        logger.info(
            "Scheduled auto-retuning job for %s at %02d:%02d UTC.",
            config.scheduler.retune_day_of_week,
            config.scheduler.retune_hour,
            config.scheduler.retune_minute,
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
        if observation_task is not None:
            await observation_task
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
