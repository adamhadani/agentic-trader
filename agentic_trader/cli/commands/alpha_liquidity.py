"""Composition for a frozen current-cohort liquidity diagnostic."""

import asyncio
import hashlib
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import alpha_repository, research_environment
from agentic_trader.cli.utils import coro, session_source
from agentic_trader.config import DailyAcquisitionConfig, load_config
from agentic_trader.research.alpha.liquidity import EquityLiquidityPlan, compute_liquidity_study
from agentic_trader.research.alpha.panel_study import PanelStudyStatus
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.storage.artifacts import save_json_report


@click.command("liquidity-study")
@click.argument("protocol", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--universe", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def liquidity_study_cmd(protocol: Path, universe: Path, output: Path):
    """Screen every frozen equity candidate; retain evidence without promotion or orders."""
    document = await asyncio.to_thread(lambda: json.loads(protocol.read_text()))
    snapshot = await asyncio.to_thread(lambda: json.loads(universe.read_text()))
    if set(document) != {"plan", "acquisition"}:
        raise click.ClickException("Exact frozen plan and acquisition policy required")
    plan = EquityLiquidityPlan.from_document(document["plan"], snapshot)
    acquisition = DailyAcquisitionConfig.model_validate(document["acquisition"])
    if acquisition.model_dump(mode="json") != document["acquisition"]:
        raise click.ClickException("Explicit complete acquisition policy required")
    config = await asyncio.to_thread(load_config)
    async with alpha_repository() as repository:
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            result = await AlphaPanelService(
                repository, source, acquisition=acquisition, compute=compute_liquidity_study
            ).run(plan, output, environment=await asyncio.to_thread(research_environment))
    report = {
        name: result.get(name)
        for name in (
            "status",
            "plan_id",
            "charged_trials",
            "completed_comparisons",
            "selection_available",
            "selected_count",
            "eligible_count",
            "shortfall",
            "authorizes_promotion",
        )
    }
    report["result_hash"] = await asyncio.to_thread(
        lambda: hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    )
    await asyncio.to_thread(save_json_report, report, output / "screen.json")
    click.echo(json.dumps(report, indent=2))
    if result["status"] != PanelStudyStatus.COMPLETED:
        raise click.ClickException("Liquidity selection unavailable; inspect retained member checkpoints and evidence")
    if result["shortfall"]:
        raise click.ClickException("Screen completed with fewer eligible candidates than the frozen target")
