from __future__ import annotations

import asyncio
import logging

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.telemetry import MetricsServer, global_metrics


logger = logging.getLogger("copilot")


@click.command("metrics", help="Export or print Prometheus telemetry metrics")
@click.option(
    "--serve",
    is_flag=True,
    default=False,
    help="Run standalone Prometheus HTTP exporter server",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="HTTP port for exporter server (default: config.telemetry.metrics_port)",
)
@coro
async def metrics(serve: bool, port: int | None) -> None:
    """Export or print Prometheus telemetry metrics."""
    config = load_config()
    target_port = port or config.telemetry.metrics_port
    target_host = config.telemetry.metrics_host

    if serve:
        server = MetricsServer(
            host=target_host,
            port=target_port,
            collector=global_metrics,
        )
        await server.start()
        click.echo(
            f"Prometheus metrics exporter server listening at http://{target_host}:{target_port}/metrics (press Ctrl+C to stop)"
        )
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt, SystemExit, asyncio.CancelledError:
            click.echo("Stopping metrics exporter server...")
            await server.stop()
    else:
        # Default snapshot output to stdout
        if not global_metrics._types:
            global_metrics.set_gauge(
                "trader_up",
                1.0,
                help_text="Agentic trader process status (1 = running)",
            )
        output = global_metrics.format_prometheus()
        click.echo(output.strip())
