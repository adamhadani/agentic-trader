from __future__ import annotations

import asyncio
import logging

import click
import pandas as pd
import yfinance as yf

from agentic_trader.cli.utils import coro
from agentic_trader.presentation.formatters import (
    format_alpha_inspection_report,
    format_mined_alphas_table,
)
from agentic_trader.research.alpha import (
    AlphaCatalog,
    AlphaDefinition,
    AlphaExpressionEvaluator,
    AlphaMiner,
    AlphaPromotionManager,
)


logger = logging.getLogger("copilot")


@click.group("alpha", help="Formulaic Alpha Mining, Expression DSL & Auto-Promotion Engine")
def alpha_group() -> None:
    """Quantitative formulaic alpha mining and research command suite."""


@alpha_group.command("catalog", help="List pre-cataloged institutional alphas (WorldQuant 101, etc.)")
def alpha_catalog_cmd() -> None:
    """Display the pre-cataloged institutional alpha library."""
    catalog = AlphaCatalog()
    alphas = catalog.list_alphas()

    click.echo("\n" + "=" * 95)
    click.echo(f"🏛️  INSTITUTIONAL FORMULAIC ALPHA CATALOG ({len(alphas)} Formulas)")
    click.echo("=" * 95)
    click.echo(f"{'ALPHA ID':<22} | {'ORIGIN':<15} | {'DIR':<6} | {'EXPRESSION'}")
    click.echo("-" * 95)
    for a in alphas:
        click.echo(f"{a.alpha_id:<22} | {a.origin:<15} | {a.direction[:5]:<6} | {a.expression}")
    click.echo("=" * 95 + "\n")


@alpha_group.command("list", help="List all production-promoted and candidate alphas")
def alpha_list_cmd() -> None:
    """Display promoted production alphas and their status."""
    mgr = AlphaPromotionManager()
    records = mgr.load_records()

    click.echo("\n" + "=" * 105)
    click.echo(f"🚀 PRODUCTION PROMOTED ALPHAS ({len(records)} Records)")
    click.echo("=" * 105)
    if not records:
        click.echo("No promoted alphas found in configuration.")
        click.echo("Use 'copilot alpha mine --auto-promote' or 'copilot alpha promote <id>' to promote alphas.")
        click.echo("=" * 105 + "\n")
        return

    click.echo(
        f"{'ALPHA ID':<20} | {'STATUS':<10} | {'ALLOC':<6} | {'OOS SR':<7} | {'DSR':<6} | {'PROMOTED BY':<16} | {'PROMOTED AT'}"
    )
    click.echo("-" * 105)
    for r in records:
        m = r.metrics
        sr_str = f"{m.sharpe_oos:.2f}" if m else "N/A"
        dsr_str = f"{m.dsr:.2f}" if m else "N/A"
        alloc_pct = f"{r.allocation_weight * 100:.0f}%"
        prom_ts = r.promoted_at[:19].replace("T", " ")
        click.echo(
            f"{r.alpha_id:<20} | {r.status:<10} | {alloc_pct:<6} | {sr_str:>7} | {dsr_str:>6} | {r.promoted_by[:15]:<16} | {prom_ts}"
        )
    click.echo("=" * 105 + "\n")


@alpha_group.command("mine", help="Mine and discover formulaic alphas with DSR overfitting control")
@click.option("--symbol", type=str, default="SPY", help="Symbol to mine across (default: SPY)")
@click.option("--lookback", type=str, default="1y", help="Historical lookback (e.g. 6m, 1y, 2y; default: 1y)")
@click.option("--iterations", type=int, default=15, help="Number of genetic search iterations (default: 15)")
@click.option("--min-sharpe", type=float, default=1.0, help="Minimum OOS Sharpe threshold (default: 1.0)")
@click.option("--min-dsr", type=float, default=0.85, help="Minimum Deflated Sharpe Ratio (default: 0.85)")
@click.option(
    "--auto-promote", is_flag=True, default=False, help="Automatically promote top qualifying alpha to production"
)
@coro
async def alpha_mine_cmd(
    symbol: str,
    lookback: str,
    iterations: int,
    min_sharpe: float,
    min_dsr: float,
    auto_promote: bool,
) -> None:
    """Execute formulaic alpha mining on market data."""
    click.echo(f"\n🔍 Fetching historical data for {symbol} (lookback: {lookback})...")

    # Ingest data via yfinance
    loop = asyncio.get_running_loop()
    df: pd.DataFrame = await loop.run_in_executor(
        None, lambda: yf.download(symbol, period=lookback, interval="1h", progress=False)
    )

    if df.empty or len(df) < 50:
        click.echo(f"⚠️  Insufficient data retrieved for {symbol}. Mining aborted.")
        return

    # Flatten MultiIndex if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    click.echo(
        f"🧠 Mining alpha expressions across {len(df)} bars ({iterations} iterations + institutional catalog)..."
    )
    miner = AlphaMiner()
    candidates = await loop.run_in_executor(
        None,
        lambda: miner.mine(
            df=df,
            iterations=iterations,
            include_catalog=True,
            min_sharpe=min_sharpe,
            min_dsr=min_dsr,
        ),
    )

    click.echo("\n" + format_mined_alphas_table(candidates))

    if candidates and auto_promote:
        top_candidate = candidates[0]
        mgr = AlphaPromotionManager()
        rec = mgr.promote(
            alpha=top_candidate,
            promoted_by="auto_pipeline",
            allocation_weight=0.10,
            notes=f"Auto-promoted from {symbol} mining (OOS SR: {top_candidate.metrics.sharpe_oos:.2f}, DSR: {top_candidate.metrics.dsr:.2f})",
        )
        click.echo(
            f"\n✅ AUTO-PROMOTED: '{rec.alpha_id}' registered into production desk (Alloc: {rec.allocation_weight * 100:.0f}%)\n"
        )
    elif not candidates:
        click.echo("\n💡 Tip: Try relaxing --min-sharpe or increasing --iterations.\n")


@alpha_group.command("inspect", help="Display detailed quantitative tearsheet for an alpha")
@click.argument("alpha_id", type=str)
@click.option("--symbol", type=str, default="SPY", help="Benchmark symbol (default: SPY)")
@click.option("--lookback", type=str, default="1y", help="Historical lookback (default: 1y)")
@coro
async def alpha_inspect_cmd(alpha_id: str, symbol: str, lookback: str) -> None:
    """Inspect and evaluate an alpha expression in detail."""
    # Check catalog first, then promoted records
    catalog = AlphaCatalog()
    definition = catalog.get(alpha_id)
    if not definition:
        mgr = AlphaPromotionManager()
        rec = mgr.get_record(alpha_id)
        if rec:
            definition = rec.definition

    if not definition:
        click.echo(f"❌ Alpha '{alpha_id}' not found in catalog or promoted records.")
        return

    click.echo(f"\n🔬 Evaluating {definition.alpha_id} across {symbol} historical bars ({lookback})...")
    loop = asyncio.get_running_loop()
    df: pd.DataFrame = await loop.run_in_executor(
        None, lambda: yf.download(symbol, period=lookback, interval="1h", progress=False)
    )

    if df.empty or len(df) < 50:
        click.echo("⚠️  Insufficient data retrieved.")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    miner = AlphaMiner()
    candidate = await loop.run_in_executor(None, lambda: miner.evaluate_alpha(definition, df))

    if not candidate:
        click.echo("❌ Evaluation failed on the given market dataset.")
        return

    click.echo("\n" + format_alpha_inspection_report(candidate) + "\n")


@alpha_group.command("promote", help="Manually promote an alpha to production paper trading")
@click.argument("alpha_id", type=str)
@click.option("--allocation", type=float, default=0.10, help="Portfolio risk allocation weight (default: 0.10)")
@click.option("--notes", type=str, default="", help="Operational audit notes")
def alpha_promote_cmd(alpha_id: str, allocation: float, notes: str) -> None:
    """Promote an alpha by ID to production."""
    catalog = AlphaCatalog()
    defn = catalog.get(alpha_id)
    if not defn:
        mgr = AlphaPromotionManager()
        rec = mgr.get_record(alpha_id)
        if rec:
            defn = rec.definition

    if not defn:
        click.echo(f"❌ Alpha '{alpha_id}' not found. Cannot promote.")
        return

    mgr = AlphaPromotionManager()
    rec = mgr.promote(
        alpha=defn,
        promoted_by="cli_operator",
        allocation_weight=allocation,
        notes=notes or "Manually promoted via CLI",
    )
    click.echo(f"\n✅ SUCCESS: Promoted '{rec.alpha_id}' to production desk.")
    click.echo(f"Allocation Weight: {rec.allocation_weight * 100:.1f}%")
    click.echo(f"Stored in: {mgr.config_path}\n")


@alpha_group.command("demote", help="Demote/retire an active production alpha")
@click.argument("alpha_id", type=str)
@click.option("--reason", type=str, default="", help="Reason for demotion")
def alpha_demote_cmd(alpha_id: str, reason: str) -> None:
    """Demote an active alpha."""
    mgr = AlphaPromotionManager()
    success = mgr.demote(alpha_id, reason=reason)
    if success:
        click.echo(f"\n🛑 SUCCESS: Demoted '{alpha_id}' from production desk.\n")
    else:
        click.echo(f"\n❌ Alpha '{alpha_id}' not found in promoted records.\n")


@alpha_group.command("test", help="Test an ad-hoc formulaic alpha expression")
@click.argument("expression", type=str)
@click.option("--symbol", type=str, default="SPY", help="Benchmark symbol (default: SPY)")
@click.option("--lookback", type=str, default="1y", help="Historical lookback (default: 1y)")
@coro
async def alpha_test_cmd(expression: str, symbol: str, lookback: str) -> None:
    """Validate and test an ad-hoc expression."""
    evaluator = AlphaExpressionEvaluator()
    if not evaluator.validate(expression):
        click.echo(f"❌ Invalid expression syntax: '{expression}'")
        return

    click.echo(f"\n🧪 Testing expression across {symbol} ({lookback})...")
    loop = asyncio.get_running_loop()
    df: pd.DataFrame = await loop.run_in_executor(
        None, lambda: yf.download(symbol, period=lookback, interval="1h", progress=False)
    )

    if df.empty or len(df) < 50:
        click.echo("⚠️  Insufficient data.")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    defn = AlphaDefinition(
        alpha_id="alpha_custom_test",
        name="Custom Ad-Hoc Expression",
        expression=expression,
    )
    miner = AlphaMiner()
    candidate = await loop.run_in_executor(None, lambda: miner.evaluate_alpha(defn, df))

    if candidate:
        click.echo("\n" + format_alpha_inspection_report(candidate) + "\n")
    else:
        click.echo("❌ Evaluation returned empty results.")
