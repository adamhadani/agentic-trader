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
from agentic_trader.diagnostics.doctor import format_doctor_cli_output, run_diagnostics


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
    await copilot.broker.connect()

    scheduler = AsyncIOScheduler()
    interval = config.scheduler.cron_hour_interval
    # Schedule regular scans
    scheduler.add_job(
        copilot.run_scan,
        "interval",
        hours=interval,
        args=[not no_llm, False],
        next_run_time=datetime.now(UTC),
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
    scheduler.start()
    logger.info("Scheduler started: scanning every %dh, reconciling positions every 1m.", interval)

    if copilot.notifier.is_configured():
        logger.info("Starting Telegram Bot listener for interactive callbacks...")
        await copilot.notifier.start_polling()

    stream_task: asyncio.Task[None] | None = None
    if getattr(copilot.broker, "supports_trade_stream", False):
        stream_task = asyncio.create_task(copilot.start_trade_stream())

    if copilot.metrics_server:
        await copilot.metrics_server.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
        logger.info("Shutting down daemon...")
        copilot._shutdown_event.set()
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
