from __future__ import annotations

import asyncio
import logging

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.research import (
    AutoRetuner,
    ParameterGridOptimizer,
    export_candidate_to_config,
    format_candidate_as_yaml,
    format_optimization_report,
)


logger = logging.getLogger("copilot")


@click.command("optimize", help="Run quantitative parameter grid search and sensitivity analysis")
@click.option(
    "--symbol",
    type=str,
    default="SPY",
    help="Symbol to optimize against (default: 'SPY')",
)
@click.option(
    "--strategy",
    type=click.Choice(["trend_pullback", "squeeze_breakout"], case_sensitive=False),
    default="trend_pullback",
    help="Strategy to optimize (default: trend_pullback)",
)
@click.option(
    "--lookback",
    type=str,
    default="2y",
    help="Historical lookback period (e.g. 1y, 2y, 5y; default: 2y)",
)
@click.option(
    "--top-n",
    type=int,
    default=5,
    help="Number of top parameter combinations to display (default: 5)",
)
@click.option(
    "--export-config",
    type=str,
    default=None,
    flag_value="stdout",
    is_flag=False,
    help="Export top candidate parameters as YAML for config.yaml (specify optional destination file path)",
)
@click.option(
    "--walk-forward",
    is_flag=True,
    default=False,
    help="Run walk-forward out-of-sample cross-validation across rolling/expanding windows",
)
@click.option(
    "--splits",
    type=int,
    default=3,
    help="Number of walk-forward validation splits (default: 3)",
)
@coro
async def optimize(
    symbol: str,
    strategy: str,
    lookback: str,
    top_n: int,
    export_config: str | None,
    walk_forward: bool,
    splits: int,
) -> None:
    """Run quantitative parameter grid search and sensitivity analysis."""
    config = load_config()
    optimizer = ParameterGridOptimizer(config=config)
    logger.info(
        "Running parameter optimization for %s (strategy: %s, lookback: %s)...",
        symbol,
        strategy,
        lookback,
    )
    opt_res = await asyncio.to_thread(
        optimizer.run,
        symbol=symbol,
        strategy=strategy,
        lookback=lookback,
        walk_forward=walk_forward,
        splits=splits,
    )
    opt_report = format_optimization_report(opt_res, top_n=top_n)
    click.echo(opt_report)

    if export_config and opt_res.ranked_candidates:
        best_cand = opt_res.ranked_candidates[0]
        if export_config == "stdout":
            yaml_str = format_candidate_as_yaml(best_cand, strategy)
            click.echo("\n" + "=" * 60)
            click.echo("EXPORTED CONFIGURATION SNIPPET (Ready to paste into config.yaml):")
            click.echo("=" * 60)
            click.echo(yaml_str)
        else:
            export_candidate_to_config(best_cand, strategy, export_config)
            click.echo(f"\n[OK] Exported optimal {strategy} parameters to {export_config}")


@click.command("retune", help="Run quantitative parameter auto-recalibration across watchlists")
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma-separated symbols to recalibrate (default: configured watchlists)",
)
@click.option(
    "--strategy",
    type=click.Choice(["all", "trend_pullback", "squeeze_breakout"], case_sensitive=False),
    default="all",
    help="Strategy to recalibrate (default: all)",
)
@click.option(
    "--min-wfe",
    type=float,
    default=0.50,
    help="Minimum Walk-Forward Efficiency ratio threshold (default: 0.50)",
)
@click.option(
    "--min-sharpe",
    type=float,
    default=0.80,
    help="Minimum Out-of-Sample Sharpe ratio threshold (default: 0.80)",
)
@click.option(
    "--export-config",
    type=str,
    default=None,
    help="Target config file to export all updated parameters (e.g. config/config.yaml)",
)
@coro
async def retune(
    symbols: str | None,
    strategy: str,
    min_wfe: float,
    min_sharpe: float,
    export_config: str | None,
) -> None:
    """Run quantitative parameter auto-recalibration across watchlists."""
    config = load_config()
    retuner = AutoRetuner(config=config)
    syms = [s.strip() for s in symbols.split(",")] if symbols else None
    strats = [strategy] if strategy != "all" else ["trend_pullback", "squeeze_breakout"]
    logger.info("Executing parameter retuning (strategy: %s)...", strategy)
    retune_res = await asyncio.to_thread(
        retuner.run_retune,
        symbols=syms,
        strategies=strats,
        min_wfe=min_wfe,
        min_sharpe=min_sharpe,
    )
    clean_summary = (
        retune_res["summary_html"]
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("<code>", "")
        .replace("</code>", "")
        .replace("<i>", "")
        .replace("</i>", "")
    )
    click.echo(clean_summary)
    if export_config:
        count = retuner.export_all_to_config(export_config)
        click.echo(f"\n[OK] Exported {count} calibrated parameter sets to {export_config}")
