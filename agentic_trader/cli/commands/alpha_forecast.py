"""Compose a screened-cohort forecast study using the shared research workflow."""

import asyncio
import hashlib
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import alpha_repository, research_environment
from agentic_trader.cli.utils import coro, session_source
from agentic_trader.config import DailyAcquisitionConfig, load_config
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelStudyStatus
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService


def _read_protocol(protocol: Path, selection: Path):
    document = json.loads(protocol.read_bytes())
    if set(document) != {"plan", "acquisition"}:
        raise ValueError("Exact frozen plan and acquisition policy required")
    plan = PanelForecastPlan.from_document(document["plan"])
    plan.validate_selection(
        *((selection / name).read_bytes() for name in ("result.json", "manifest.json", "inputs.json"))
    )
    acquisition = DailyAcquisitionConfig.model_validate(document["acquisition"])
    if acquisition.model_dump(mode="json") != document["acquisition"]:
        raise ValueError("Explicit complete acquisition policy required")
    return plan, acquisition


@click.command("forecast-study")
@click.argument("protocol", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--selection",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Completed liquidity-study artifact directory.",
)
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def forecast_study_cmd(protocol: Path, selection: Path, output: Path):
    """Compare economic/Ridge forecasts; retain every result without promotion or orders."""
    try:
        plan, acquisition = await asyncio.to_thread(_read_protocol, protocol, selection)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(f"Invalid forecast protocol or selection evidence: {exc}") from exc
    config = await asyncio.to_thread(load_config)
    async with alpha_repository() as repository:
        with session_source(config, plan.feed.removeprefix("alpaca:")) as source:
            result = await AlphaPanelService(
                repository, source, acquisition=acquisition, compute=compute_panel_forecast
            ).run(plan, output, environment=await asyncio.to_thread(research_environment))
    summary = {
        name: result.get(name)
        for name in (
            "status",
            "run_id",
            "plan_id",
            "charged_trials",
            "completed_comparisons",
            "authorizes_promotion",
            "error_type",
        )
    }
    summary["result_hash"] = await asyncio.to_thread(
        lambda: hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    )
    click.echo(json.dumps(summary, indent=2))
    if result["status"] != PanelStudyStatus.COMPLETED:
        raise click.ClickException("Forecast study unavailable; inspect retained acquisition checkpoints and result")
