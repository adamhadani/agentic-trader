from __future__ import annotations

import logging

import click

from agentic_trader.cli.commands.backtest import backtest
from agentic_trader.cli.commands.db import db_group
from agentic_trader.cli.commands.options import gex
from agentic_trader.cli.commands.pairs import pairs
from agentic_trader.cli.commands.research import optimize, retune
from agentic_trader.cli.commands.scan import scan
from agentic_trader.cli.commands.service import daemon, doctor, eval_command, listen
from agentic_trader.cli.commands.stress import stress
from agentic_trader.cli.commands.telemetry import metrics
from agentic_trader.cli.commands.trade import (
    close,
    execute,
    panic,
    positions,
    resume,
    status,
    test_alert,
)


@click.group()
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose DEBUG logging",
)
def cli(verbose: bool) -> None:
    """Agentic Trader - Autonomous Multi-Asset Quantitative Trading System."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# Register subcommands
cli.add_command(scan)
cli.add_command(status)
cli.add_command(positions)
cli.add_command(close)
cli.add_command(execute)
cli.add_command(panic)
cli.add_command(resume)
cli.add_command(test_alert)
cli.add_command(backtest)
cli.add_command(optimize)
cli.add_command(retune)
cli.add_command(stress)
cli.add_command(gex)
cli.add_command(pairs)
cli.add_command(metrics)
cli.add_command(daemon)
cli.add_command(listen)
cli.add_command(doctor)
cli.add_command(eval_command, name="eval")
cli.add_command(db_group, name="db")


def main() -> None:
    """CLI runner entrypoint."""
    cli()


if __name__ == "__main__":
    main()
