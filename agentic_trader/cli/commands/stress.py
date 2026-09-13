from __future__ import annotations

import asyncio

import click

from agentic_trader.backtest import (
    CrisisReplayEngine,
    format_instantaneous_shock_report,
    format_stress_test_report,
)
from agentic_trader.cli.utils import coro, get_copilot_and_config


@click.command("stress", help="Run portfolio stress testing against historical macro crises and shocks")
@click.option(
    "--scenario",
    type=click.Choice(["all", "2008_gfc", "2020_covid", "2022_inflation", "shock"], case_sensitive=False),
    default="all",
    help="Crisis scenario to replay or 'shock' for instantaneous factor stress (default: all)",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma-separated symbols to test (default: scenario defaults)",
)
@click.option(
    "--strategy",
    type=click.Choice(["all", "trend_pullback", "squeeze_breakout"], case_sensitive=False),
    default="all",
    help="Strategy to evaluate (default: all)",
)
@click.option(
    "--cash",
    type=float,
    default=None,
    help="Starting cash amount in dollars (default: config.portfolio.cash)",
)
@coro
async def stress(
    scenario: str,
    symbols: str | None,
    strategy: str,
    cash: float | None,
) -> None:
    """Run portfolio stress testing against historical macro crises and shocks."""
    copilot, config = get_copilot_and_config()
    stress_engine = CrisisReplayEngine(config=config)
    syms = [s.strip() for s in symbols.split(",")] if symbols else None

    if scenario == "shock":
        positions = await copilot.db.get_active_positions()
        shock_res = stress_engine.simulate_instantaneous_shock(
            active_positions=positions,
            cash=cash or config.portfolio.cash,
        )
        click.echo(format_instantaneous_shock_report(shock_res))
    elif scenario == "all":
        results = await asyncio.to_thread(
            stress_engine.replay_all_crises,
            symbols=syms,
            strategy_filter=strategy,
            initial_cash=cash,
        )
        click.echo(format_stress_test_report(results))
    else:
        scenario_key = scenario.upper()
        single_stress_res = await asyncio.to_thread(
            stress_engine.replay_scenario,
            scenario_id=scenario_key,
            symbols=syms,
            strategy_filter=strategy,
            initial_cash=cash,
        )
        click.echo(format_stress_test_report([single_stress_res]))
