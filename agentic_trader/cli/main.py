from __future__ import annotations

import logging
import os

import click

from agentic_trader.cli.commands.alpha import alpha_group
from agentic_trader.cli.commands.alpha_liquidity import liquidity_study_cmd
from agentic_trader.cli.commands.alpha_universe import universe_snapshot_cmd
from agentic_trader.cli.commands.backtest import backtest
from agentic_trader.cli.commands.db import db_group
from agentic_trader.cli.commands.options import gex
from agentic_trader.cli.commands.pairs import pairs
from agentic_trader.cli.commands.scan import scan
from agentic_trader.cli.commands.service import daemon, doctor, eval_command, listen
from agentic_trader.cli.commands.stress import stress
from agentic_trader.cli.commands.telemetry import metrics
from agentic_trader.cli.commands.trade import (
    close,
    execute,
    explain_macro,
    flatten,
    panic,
    perf,
    positions,
    resume,
    status,
    test_alert,
)
from agentic_trader.runtime import RuntimeLogFormatter


@click.group()
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose DEBUG logging",
)
@click.option(
    "--db-name",
    default=None,
    help="Target database name (e.g. 'signals', 'test_signals', 'staging')",
)
@click.option(
    "--db-path",
    default=None,
    help="Explicit database SQLite file path or database connection URL",
)
def cli(verbose: bool, db_name: str | None = None, db_path: str | None = None) -> None:
    """Agentic Trader - Multi-asset research, risk review, and operator-approved trading."""
    if db_name:
        os.environ["DB_NAME"] = db_name
        os.environ["DATABASE_URL"] = ""
        os.environ["DB_PATH"] = ""
    if db_path:
        os.environ["DB_PATH"] = db_path
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    for handler in logging.getLogger().handlers:
        handler.setFormatter(RuntimeLogFormatter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# Register subcommands
cli.add_command(scan)
cli.add_command(status)
cli.add_command(positions)
cli.add_command(perf)
cli.add_command(close)
cli.add_command(flatten)
cli.add_command(execute)
cli.add_command(panic)
cli.add_command(resume)
cli.add_command(test_alert)
cli.add_command(explain_macro)
cli.add_command(backtest)
cli.add_command(stress)
cli.add_command(gex)
cli.add_command(pairs)
cli.add_command(metrics)
cli.add_command(daemon)
cli.add_command(listen)
cli.add_command(doctor)
cli.add_command(eval_command, name="eval")
cli.add_command(db_group, name="db")
alpha_group.add_command(universe_snapshot_cmd)
alpha_group.add_command(liquidity_study_cmd)
cli.add_command(alpha_group, name="alpha")


def main() -> None:
    """CLI runner entrypoint."""
    cli()


if __name__ == "__main__":
    main()
