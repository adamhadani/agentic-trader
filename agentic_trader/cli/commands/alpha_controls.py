"""Compose immutable retained-data controls through the existing daily journal."""

import asyncio
import hashlib
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import alpha_repository, research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.research.alpha.forecast_controls import compute_forecast_controls
from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.panel_study import PanelStudyStatus
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.retained_panel import RetainedPanelSource


def _read_protocol(protocol):
    document = json.loads(protocol.read_bytes())
    if set(document) != {"plan", "acquisition"}:
        raise ValueError("Exact frozen plan and acquisition policy required")
    plan = ForecastControlsPlan.from_document(document["plan"])
    acquisition = DailyAcquisitionConfig.model_validate(document["acquisition"])
    if acquisition.model_dump(mode="json") != document["acquisition"]:
        raise ValueError("Explicit complete acquisition policy required")
    return plan, acquisition


@click.command("forecast-controls")
@click.argument("protocol", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--parent",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Completed immutable forecast-study artifact directory.",
)
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@coro
async def forecast_controls_cmd(protocol: Path, parent: Path, output: Path):
    """Compare frozen forecasts/styles and endpoint evidence; no provider calls or promotion."""
    try:
        plan, acquisition = await asyncio.to_thread(_read_protocol, protocol)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(f"Invalid retained controls protocol: {exc}") from exc
    source = RetainedPanelSource(parent, plan)
    environment = await asyncio.to_thread(research_environment)
    environment["input_origin"] = {
        "kind": "retained_artifacts",
        "parent_result_sha256": plan.parent_result_sha256,
        "receipts": "Current artifact reads; original provider receipts remain in the parent artifact chain",
    }
    async with alpha_repository() as repository:
        result = await AlphaPanelService(
            repository,
            source,
            acquisition=acquisition,
            compute=lambda batch, clock, policy, sessions: compute_forecast_controls(
                batch, clock, policy, sessions, parent_result=source.parent_result
            ),
        ).run(plan, output, environment=environment)
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
        raise click.ClickException("Retained controls unavailable; inspect the charged attempt and checkpoints")
