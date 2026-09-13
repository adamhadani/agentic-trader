"""CLI command for Cointegration & Statistical Pairs Trading Screener."""

from __future__ import annotations

import asyncio
import json
import logging

import click

from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.pairs import PairsScreener, format_pairs_report


logger = logging.getLogger("copilot")


@click.command(
    "pairs",
    help="Screen cross-asset pairs for cointegration, mean-reverting spread Z-scores, and statistical arbitrage signals",
)
@click.option(
    "--pair",
    type=str,
    default=None,
    help="Single pair to evaluate, formatted as ASSET_Y/ASSET_X or ASSET_Y:ASSET_X (e.g. SPY/QQQ)",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma-separated symbols to screen all pairwise combinations (e.g. SPY,QQQ,IWM,GLD)",
)
@click.option(
    "--lookback",
    type=int,
    default=None,
    help="Lookback period in trading days for cointegration test (default: 252)",
)
@click.option(
    "--p-value",
    "p_value",
    type=float,
    default=None,
    help="ADF p-value threshold for cointegration significance (default: 0.05)",
)
@click.option(
    "--z-entry",
    type=float,
    default=None,
    help="Z-score threshold to trigger entry signal (default: 2.0)",
)
@click.option(
    "--z-exit",
    type=float,
    default=None,
    help="Z-score threshold to trigger mean-reversion exit signal (default: 0.5)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output raw JSON array of pair evaluations instead of ASCII table",
)
@coro
async def pairs(
    pair: str | None,
    symbols: str | None,
    lookback: int | None,
    p_value: float | None,
    z_entry: float | None,
    z_exit: float | None,
    output_json: bool,
) -> None:
    """Screen cross-asset pairs for cointegration, mean-reverting spread Z-scores, and statistical arbitrage signals."""
    config = load_config()
    pairs_cfg = config.pairs

    if p_value is not None:
        pairs_cfg.p_value_threshold = p_value
    if z_entry is not None:
        pairs_cfg.z_entry_threshold = z_entry
    if z_exit is not None:
        pairs_cfg.z_exit_threshold = z_exit

    screener = PairsScreener(config=pairs_cfg)

    # Determine candidate pairs
    candidate_pairs: list[tuple[str, str]] | None = None
    if pair:
        clean_pair = pair.replace(":", "/").strip()
        parts = clean_pair.split("/")
        if len(parts) != 2:
            click.echo("Error: --pair must be formatted as ASSET_Y/ASSET_X or ASSET_Y:ASSET_X (e.g. SPY/QQQ)")
            return
        candidate_pairs = [(parts[0].strip().upper(), parts[1].strip().upper())]
    elif symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if len(symbol_list) < 2:
            click.echo("Error: --symbols must include at least 2 distinct symbols to form pairs")
            return
        candidate_pairs = screener.generate_pairwise_combinations(symbol_list)

    lookback_days = lookback or pairs_cfg.lookback_days
    click.echo(
        f"Scanning cross-asset pairs (lookback: {lookback_days} days, ADF p-val: {pairs_cfg.p_value_threshold})..."
    )

    try:
        results = await asyncio.to_thread(
            screener.scan_pairs,
            pairs=candidate_pairs,
            lookback_days=lookback_days,
        )

        if output_json:
            json_data = [res.model_dump(mode="json") for res in results]
            click.echo(json.dumps(json_data, indent=2))
        else:
            click.echo(format_pairs_report(results))

    except Exception as e:
        logger.error("Failed to run pairs screener: %s", e)
        click.echo(f"Error evaluating pairs: {e}")
