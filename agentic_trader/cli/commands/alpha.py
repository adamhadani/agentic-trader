from __future__ import annotations

import asyncio
import functools
import logging

import click
import numpy as np
import pandas as pd
import yfinance as yf

from agentic_trader.cli.utils import coro, get_copilot_and_config
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
from agentic_trader.research.alpha.orthogonalization import (
    evaluate_residual_predictive_power,
    gram_schmidt_orthogonalize,
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

    click.echo("\n" + "=" * 115)
    click.echo(f"🚀 PRODUCTION PROMOTED ALPHAS ({len(records)} Records)")
    click.echo("=" * 115)
    if not records:
        click.echo("No promoted alphas found in configuration.")
        click.echo("Use 'copilot alpha mine --auto-promote' or 'copilot alpha promote <id>' to promote alphas.")
        click.echo("=" * 115 + "\n")
        return

    click.echo(
        f"{'ALPHA ID':<20} | {'STATUS':<10} | {'TF':<5} | {'ALLOC':<6} | {'OOS SR':<7} | {'DSR':<6} | {'ELIGIBLE SYMBOLS':<18} | {'PROMOTED BY':<14} | {'PROMOTED AT'}"
    )
    click.echo("-" * 122)
    for r in records:
        m = r.metrics
        sr_str = f"{m.sharpe_oos:.2f}" if m else "N/A"
        dsr_str = f"{m.dsr:.2f}" if m else "N/A"
        alloc_pct = f"{r.allocation_weight * 100:.0f}%"
        prom_ts = r.promoted_at[:19].replace("T", " ")
        syms_str = ", ".join(r.eligible_symbols) if r.eligible_symbols else "ALL"
        tf_str = getattr(r.definition, "timeframe", "4h").upper()
        click.echo(
            f"{r.alpha_id:<20} | {r.status:<10} | {tf_str:<5} | {alloc_pct:<6} | {sr_str:>7} | {dsr_str:>6} | {syms_str:<18} | {r.promoted_by[:14]:<14} | {prom_ts}"
        )
    click.echo("=" * 122 + "\n")


@alpha_group.command("mine", help="Mine and discover formulaic alphas with DSR overfitting control")
@click.option("--symbol", type=str, default="SPY", help="Symbol to mine across (default: SPY)")
@click.option(
    "--symbols",
    type=str,
    default="",
    help="Comma-separated symbols for multi-asset matrix & orthogonalization check (e.g. NVDA,AMD,AAPL,MSFT,QQQ,SPY)",
)
@click.option("--lookback", type=str, default="2y", help="Historical lookback (e.g. 6m, 1y, 2y; default: 2y)")
@click.option("--interval", type=str, default="1d", help="Bar interval (e.g. 1h, 1d; default: 1d)")
@click.option("--iterations", type=int, default=15, help="Number of genetic search iterations (default: 15)")
@click.option("--min-sharpe", type=float, default=1.0, help="Minimum OOS Sharpe threshold (default: 1.0)")
@click.option("--min-dsr", type=float, default=0.85, help="Minimum Deflated Sharpe Ratio (default: 0.85)")
@click.option(
    "--auto-promote", is_flag=True, default=False, help="Automatically promote top qualifying alpha to production"
)
@coro
async def alpha_mine_cmd(
    symbol: str,
    symbols: str,
    lookback: str,
    interval: str,
    iterations: int,
    min_sharpe: float,
    min_dsr: float,
    auto_promote: bool,
) -> None:
    """Execute formulaic alpha mining on market data."""
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else [symbol.strip().upper()]
    primary_sym = symbol_list[0]

    click.echo(
        f"\n🔍 Fetching historical data for {', '.join(symbol_list)} (lookback: {lookback}, interval: {interval})..."
    )

    # Ingest data via yfinance
    loop = asyncio.get_running_loop()
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbol_list:
        download_func = functools.partial(yf.download, sym, period=lookback, interval=interval, progress=False)
        df: pd.DataFrame = await loop.run_in_executor(None, download_func)
        if not df.empty and len(df) >= 50:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            dfs[sym] = df

    if primary_sym not in dfs:
        click.echo(f"⚠️  Insufficient data retrieved for primary symbol {primary_sym}. Mining aborted.")
        return

    primary_df = dfs[primary_sym]
    click.echo(
        f"🧠 Mining alpha expressions across {len(primary_df)} bars on {primary_sym} ({iterations} iterations + institutional catalog)..."
    )
    miner = AlphaMiner()
    candidates = await loop.run_in_executor(
        None,
        lambda: miner.mine(
            df=primary_df,
            iterations=iterations,
            include_catalog=True,
            min_sharpe=min_sharpe,
            min_dsr=min_dsr,
        ),
    )

    if not candidates:
        click.echo("\nNo alpha candidates met the minimum statistical gating filters.")
        click.echo("💡 Tip: Try relaxing --min-sharpe or increasing --iterations.\n")
        return

    click.echo("\n" + format_mined_alphas_table(candidates))

    # Cross-Asset Qualification & Orthogonalization Check
    mgr = AlphaPromotionManager()
    active_alphas = mgr.list_active_alphas()
    evaluator = AlphaExpressionEvaluator()

    primary_returns = primary_df["Close"].pct_change().dropna().to_numpy()
    incumbent_signals: list[np.ndarray] = []
    if active_alphas:
        for act in active_alphas:
            try:
                sig = evaluator.evaluate(act.definition.expression, primary_df)
                aligned = sig.iloc[1:].fillna(0.0).to_numpy()
                if len(aligned) == len(primary_returns):
                    incumbent_signals.append(aligned)
            except Exception:
                pass

    candidate_qualified_syms: dict[str, list[str]] = {}

    if len(symbol_list) > 1 or active_alphas:
        click.echo("\n" + "=" * 118)
        click.echo("🌐 MULTI-ASSET QUALIFICATION & SIGNAL ORTHOGONALIZATION MATRIX")
        click.echo("=" * 118)
        click.echo(
            f"{'ALPHA ID':<20} | {'SYMBOL':<6} | {'OOS SR':<7} | {'DSR':<6} | {'RANK IC':<8} | {'RESIDUAL IC':<11} | {'ORTHO STATUS':<16} | {'QUALIFIED?'}"
        )
        click.echo("-" * 118)

        for c in candidates:
            c_defn = c.definition
            candidate_qualified_syms[c_defn.alpha_id] = []

            # Orthogonalization check on primary dataset
            residual_ic_str = "N/A"
            ortho_status = "BASELINE"
            if incumbent_signals:
                try:
                    c_sig = evaluator.evaluate(c_defn.expression, primary_df)
                    c_aligned = c_sig.iloc[1:].fillna(0.0).to_numpy()
                    if len(c_aligned) == len(primary_returns):
                        c_ortho, _, _ = gram_schmidt_orthogonalize(c_aligned, incumbent_signals)
                        is_novel, res_ic, _ = evaluate_residual_predictive_power(c_ortho, primary_returns)
                        residual_ic_str = f"{res_ic:+.3f}"
                        ortho_status = "✅ ORTHOGONAL" if is_novel else "⚠️ REDUNDANT"
                except Exception as e:
                    logger.debug("Orthogonalization failed: %s", e)
                    ortho_status = "ERROR"
            elif not active_alphas:
                ortho_status = "✅ PIONEER"

            for sym in symbol_list:
                sym_df = dfs.get(sym)
                if sym_df is None:
                    continue
                eval_func = functools.partial(miner.evaluate_alpha, c_defn, sym_df)
                sym_cand = await loop.run_in_executor(None, eval_func)
                if sym_cand:
                    m = sym_cand.metrics
                    qual = m.sharpe_oos >= min_sharpe and m.dsr >= min_dsr
                    if qual and "REDUNDANT" not in ortho_status:
                        candidate_qualified_syms[c_defn.alpha_id].append(sym)
                    qual_str = "✅ YES" if qual else "❌ NO"
                    click.echo(
                        f"{c_defn.alpha_id:<20} | {sym:<6} | {m.sharpe_oos:>7.2f} | {m.dsr:>6.2f} | {m.rank_ic_mean:>+8.3f} | {residual_ic_str:>11} | {ortho_status:<16} | {qual_str}"
                    )
        click.echo("=" * 118 + "\n")

    if candidates and auto_promote:
        top_candidate = candidates[0]
        top_id = top_candidate.definition.alpha_id
        eligible_for_top = candidate_qualified_syms.get(top_id) or ([primary_sym] if primary_sym in dfs else None)
        rec = mgr.promote(
            alpha=top_candidate,
            promoted_by="auto_pipeline",
            allocation_weight=0.10,
            notes=f"Auto-promoted from {primary_sym} mining (OOS SR: {top_candidate.metrics.sharpe_oos:.2f}, DSR: {top_candidate.metrics.dsr:.2f})",
            eligible_symbols=eligible_for_top,
        )
        syms_display = ", ".join(rec.eligible_symbols) if rec.eligible_symbols else "ALL"
        click.echo(
            f"\n✅ AUTO-PROMOTED: '{rec.alpha_id}' registered into production desk (Alloc: {rec.allocation_weight * 100:.0f}%, Universe: {syms_display})\n"
        )


@alpha_group.command("inspect", help="Display detailed quantitative tearsheet for an alpha")
@click.argument("alpha_id", type=str)
@click.option("--symbol", type=str, default="SPY", help="Benchmark symbol (default: SPY)")
@click.option("--lookback", type=str, default="2y", help="Historical lookback (default: 2y)")
@click.option("--interval", type=str, default="1d", help="Bar interval (e.g. 1h, 1d; default: 1d)")
@coro
async def alpha_inspect_cmd(alpha_id: str, symbol: str, lookback: str, interval: str) -> None:
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

    click.echo(
        f"\n🔬 Evaluating {definition.alpha_id} across {symbol} historical bars ({lookback}, interval: {interval})..."
    )
    loop = asyncio.get_running_loop()
    df: pd.DataFrame = await loop.run_in_executor(
        None, lambda: yf.download(symbol, period=lookback, interval=interval, progress=False)
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
@click.option(
    "--symbols",
    type=str,
    default="",
    help="Comma-separated eligible symbols (e.g. NVDA,AMD; default: all)",
)
@click.option(
    "--timeframe",
    type=str,
    default=None,
    help="Candle timeframe override (e.g. '15m', '1h', '4h', '1d')",
)
@click.option("--notes", type=str, default="", help="Operational audit notes")
def alpha_promote_cmd(alpha_id: str, allocation: float, symbols: str, timeframe: str | None, notes: str) -> None:
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

    if timeframe:
        defn.timeframe = timeframe.strip().lower()

    symbols_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None

    mgr = AlphaPromotionManager()
    rec = mgr.promote(
        alpha=defn,
        promoted_by="cli_operator",
        allocation_weight=allocation,
        notes=notes or "Manually promoted via CLI",
        eligible_symbols=symbols_list,
    )
    click.echo(f"\n✅ SUCCESS: Promoted '{rec.alpha_id}' to production desk.")
    click.echo(f"Allocation Weight: {rec.allocation_weight * 100:.1f}%")
    syms_display = ", ".join(rec.eligible_symbols) if rec.eligible_symbols else "ALL"
    click.echo(f"Eligible Symbols:  {syms_display}")
    click.echo(f"Stored in: {mgr.config_path}\n")


@alpha_group.command("demote", help="Demote/retire an active production alpha")
@click.argument("alpha_id", type=str)
@click.option("--reason", type=str, default="", help="Reason for demotion")
@click.option(
    "--liquidate-positions",
    is_flag=True,
    default=False,
    help="Immediately liquidate any active positions attributed to this alpha",
)
@coro
async def alpha_demote_cmd(alpha_id: str, reason: str, liquidate_positions: bool) -> None:
    """Demote an active alpha and safeguard against zombie positions."""
    mgr = AlphaPromotionManager()
    success = mgr.demote(alpha_id, reason=reason)
    if not success:
        click.echo(f"\n❌ Alpha '{alpha_id}' not found in promoted records.\n")
        return

    click.echo(f"\n🛑 SUCCESS: Demoted '{alpha_id}' from production desk.")

    # Check for active positions attributed to this alpha
    try:
        copilot, _config = get_copilot_and_config()
        active_positions = await copilot.db.get_active_positions()
        matched_positions = [p for p in active_positions if (p.get("strategy") or "").lower() == alpha_id.lower()]

        if matched_positions:
            click.echo(f"\n⚠️  FOUND {len(matched_positions)} ACTIVE POSITION(S) ATTRIBUTED TO '{alpha_id}':")
            for p in matched_positions:
                click.echo(
                    f"   • Signal #{p['id']}: {p['symbol']} ({p['direction']}, {p.get('quantity', 1)} units @ ${p['entry_price']:.2f})"
                )

            if liquidate_positions:
                click.echo("\n⚡ --liquidate-positions specified: Closing positions immediately...")
                await copilot.broker.connect()
                for p in matched_positions:
                    res = await copilot.close_position_manual(p["id"])
                    clean_res = res.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
                    click.echo(f"   {clean_res}")
                click.echo("✅ All attributed positions have been closed.\n")
            else:
                click.echo(
                    "\n🛡️  ACTION NOTICE: Positions remain open under orphan status. Existing stop losses and trailing stops"
                )
                click.echo(
                    "   will manage trade risk until natural exit. To liquidate immediately, rerun with '--liquidate-positions'"
                )
                click.echo("   or use 'copilot close <signal_id> --price <price>'.\n")
        else:
            click.echo(f"ℹ️  No active positions are currently attributed to '{alpha_id}'. Clean retirement.\n")
    except Exception as e:
        click.echo(f"⚠️  Could not inspect active positions: {e}\n")


@alpha_group.command("test", help="Test an ad-hoc formulaic alpha expression")
@click.argument("expression", type=str)
@click.option("--symbol", type=str, default="SPY", help="Benchmark symbol (default: SPY)")
@click.option("--lookback", type=str, default="2y", help="Historical lookback (default: 2y)")
@click.option("--interval", type=str, default="1d", help="Bar interval (e.g. 1h, 1d; default: 1d)")
@coro
async def alpha_test_cmd(expression: str, symbol: str, lookback: str, interval: str) -> None:
    """Validate and test an ad-hoc expression."""
    evaluator = AlphaExpressionEvaluator()
    if not evaluator.validate(expression):
        click.echo(f"❌ Invalid expression syntax: '{expression}'")
        return

    click.echo(f"\n🧪 Testing expression across {symbol} ({lookback}, interval: {interval})...")
    loop = asyncio.get_running_loop()
    df: pd.DataFrame = await loop.run_in_executor(
        None, lambda: yf.download(symbol, period=lookback, interval=interval, progress=False)
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
