"""Spread-reversion lane commands: power check A, null check C and the predeclared pairs study."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import (
    _apriori_clients,
    _require_journal,
    _uncommitted_research_files,
    alpha_repository,
    research_environment,
)
from agentic_trader.cli.utils import coro
from agentic_trader.research.pooled.campaign_run import journal_identity, require_clean_revision
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.research.spread.panel import PanelBuild, build_spread_panel
from agentic_trader.research.spread.protocol import (
    LANE,
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)
from agentic_trader.research.spread.study import execute_spread_null, execute_spread_power, execute_spread_study


_OUTPUT_HELP = "New private directory; no overwrite"
_CACHE_HELP = "Daily bar cache directory (the pooled cache's `bars` directory holds the cohort already)"


def _progress(message: str) -> None:
    click.echo(f"[spread] {message}", err=True)


def _refuse_existing(output: Path) -> None:
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")


def _load(protocol_path: Path) -> tuple[LoadedSpreadProtocol, LoadedSpreadCohort]:
    loaded = load_spread_protocol(protocol_path)
    cohort_path = Path(loaded.protocol.cohort)
    if not cohort_path.is_absolute():
        cohort_path = REPO_ROOT / cohort_path
    return loaded, load_spread_cohort(cohort_path)


def _echo(result: dict, failure: str) -> None:
    click.echo(
        json.dumps({k: result.get(k) for k in ("status", "decision", "passes", "error") if k in result}, default=str)
    )
    if result.get("status") == "failed":
        raise click.ClickException(failure)


def _panel_builder(loaded: LoadedSpreadProtocol, cohort: LoadedSpreadCohort, clients, cache: Path):
    protocol = loaded.protocol

    async def build() -> PanelBuild:
        return await build_spread_panel(
            [*cohort.cohort.symbols, cohort.cohort.market],
            bars=clients.bars,
            calendar=clients.calendar,
            cache_dir=cache,
            start=protocol.bars.start,
            through=protocol.bars.through,
            adjustment=protocol.adjustment,
            pace=clients.pace,
        )

    return build


@click.command("spread-power")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@coro
async def spread_power_cmd(protocol_path: Path, output: Path) -> None:
    """Check A: the spread discovery pipeline on synthetic worlds with planted pairs (no provider access)."""
    _refuse_existing(output)
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    environment = await asyncio.to_thread(research_environment)
    result = await execute_spread_power(loaded, output, cohort=cohort, environment=environment, progress=_progress)
    _echo(result, "Spread power check failed; see result.json for the reason")


@click.command("spread-null")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_CACHE_HELP)
@coro
async def spread_null_cmd(protocol_path: Path, output: Path, cache: Path) -> None:
    """Check C: the spread discovery pipeline on per-symbol shifted real bars (false-acceptance rate)."""
    _refuse_existing(output)
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_spread_null(
            loaded,
            output,
            cohort=cohort,
            build=_panel_builder(loaded, cohort, clients, cache),
            environment=environment,
            progress=_progress,
        )
    _echo(result, "Spread null check failed; see result.json for the reason")


@click.command("spread-study")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True, help="Check A's directory"
)
@click.option(
    "--null-check", "null_dir", type=click.Path(exists=True, path_type=Path), required=True, help="Check C's directory"
)
@click.option("--output", type=click.Path(path_type=Path), required=True, help=_OUTPUT_HELP)
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_CACHE_HELP)
@click.option(
    "--journal-scope", required=True, help="The journal scope the one-use confirmation is recorded in (must match)"
)
@coro
async def spread_study_cmd(
    protocol_path: Path, power_dir: Path, null_dir: Path, output: Path, cache: Path, journal_scope: str
) -> None:
    """The predeclared pairs study: discovery, then a journaled one-use confirmation. Research only; never promotes."""
    _refuse_existing(output)
    uncommitted = await asyncio.to_thread(_uncommitted_research_files)
    if uncommitted:
        raise click.ClickException(f"refusing to run with uncommitted research files:\n{uncommitted}")
    environment = await asyncio.to_thread(research_environment)
    try:
        require_clean_revision(environment)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    loaded, cohort = await asyncio.to_thread(_load, protocol_path)
    async with alpha_repository() as repository:
        _require_journal(repository, journal_scope)
        journal = journal_identity(repository)

        async def confirm(interval, detail):
            return await repository.consume_lane_confirmation(
                LANE, protocol_sha256=loaded.sha256, cohort_sha256=cohort.sha256, interval=interval, detail=detail
            )

        with _apriori_clients() as clients:
            result = await execute_spread_study(
                loaded,
                output,
                cohort=cohort,
                build=_panel_builder(loaded, cohort, clients, cache),
                environment=environment,
                power_dir=power_dir,
                null_dir=null_dir,
                journal=journal,
                confirm=confirm,
                progress=_progress,
            )
    _echo(result, "Spread study failed; see result.json for the reason")
