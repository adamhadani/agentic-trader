from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import click
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.cli.utils import coro, get_copilot_and_config
from agentic_trader.constants import AuditEventType
from agentic_trader.diagnostics.doctor import format_doctor_cli_output, run_diagnostics
from agentic_trader.runtime import runtime_identity
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
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Stopping listener...")
        await copilot.notifier.stop_polling()


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

    scheduler = AsyncIOScheduler()
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
    if getattr(config.scheduler, "intraday_scan_enabled", True):
        intraday_interval = getattr(config.scheduler, "intraday_interval_minutes", 15)

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
    if getattr(config.scheduler, "macro_briefing_enabled", True):
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
    if copilot.notifier.is_configured():
        logger.info("Starting Telegram Bot listener for interactive callbacks...")
        await copilot.notifier.start_polling()

    stream_task: asyncio.Task[None] | None = None
    if getattr(copilot.broker, "supports_trade_stream", False):
        stream_task = asyncio.create_task(copilot.start_trade_stream())

    if copilot.metrics_server:
        await copilot.metrics_server.start()

    lag_task = asyncio.create_task(monitor_event_loop(config.telemetry, copilot.metrics, copilot.db.record_audit))
    scheduler.start()
    logger.info("Scheduler started: scanning every %dh, reconciling positions every 1m.", interval)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Shutting down daemon...")
        copilot._shutdown_event.set()
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


@click.command("doctor", help="Run pre-flight system diagnostics and connectivity checks")
@coro
async def doctor() -> None:
    """Run pre-flight system diagnostics and connectivity checks across all subsystems."""
    _copilot, config = get_copilot_and_config()
    report = await run_diagnostics(config)
    click.echo(format_doctor_cli_output(report))
