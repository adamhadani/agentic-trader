"""Quantitative desk tools exposed to LangGraph Conversational Copilot."""

from __future__ import annotations

import asyncio
import html
import re
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

from agentic_trader.constants import ExecutionMode
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.promotion import AlphaPromotionManager
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.indicators import (
    calculate_atr,
    calculate_ema,
    calculate_rsi,
)


if TYPE_CHECKING:
    from agentic_trader.agent.copilot import TradingCopilot


def _clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape entities to provide clean text to LLM."""
    clean = re.sub(r"<[^>]+>", "", raw_html)
    return html.unescape(clean).strip()


def make_copilot_tools(copilot: TradingCopilot) -> list[BaseTool]:
    """Create and bind quantitative desk tools to the provided TradingCopilot instance."""

    @tool
    async def get_open_positions() -> str:
        """Fetch all currently open trading positions, entry prices, quantities, unrealized PnL, stops, and targets."""
        try:
            positions = await copilot.db.get_active_positions()
            if not positions:
                return "No open positions in portfolio. All capital is unallocated."

            lines = ["Current Open Positions:"]
            for pos in positions:
                sym = pos.get("symbol", "UNKNOWN")
                qty = pos.get("quantity", 0.0)
                side = pos.get("side", "buy").upper()
                entry = pos.get("entry_price", 0.0)
                curr = pos.get("current_price") or entry
                pnl = pos.get("unrealized_pnl", 0.0)
                sl = pos.get("stop_loss", 0.0)
                tp = pos.get("take_profit", 0.0)
                trailing = pos.get("trailing_stop")
                trailing_info = f", Trailing Stop: ${trailing:.2f}" if trailing else ""
                lines.append(
                    f"- {sym} ({side}): {qty:g} units @ ${entry:,.2f} | Current: ${curr:,.2f} | "
                    f"PnL: ${pnl:+,.2f} | Stop: ${sl:,.2f} | Target: ${tp:,.2f}{trailing_info}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error retrieving open positions: {e}"

    @tool
    async def get_portfolio_status() -> str:
        """Fetch portfolio capital, cash balances, open notional exposure, margin utilization, and execution mode."""
        try:
            cash = getattr(copilot.config.portfolio, "cash", 100_000.0)
            mode = getattr(copilot.config, "execution_mode", ExecutionMode.PAPER).upper()
            positions = await copilot.db.get_active_positions()
            active_count = len(positions)
            notional = sum(p.get("entry_price", 0.0) * p.get("quantity", 0.0) for p in positions)
            leverage = (notional / cash) if cash > 0 else 0.0
            return (
                f"Portfolio Status Summary:\n"
                f"- Base Cash: ${cash:,.2f}\n"
                f"- Execution Mode: {mode}\n"
                f"- Open Position Count: {active_count}\n"
                f"- Active Notional Exposure: ${notional:,.2f}\n"
                f"- Effective Leverage: {leverage:.2f}x\n"
                f"- System Halted: {copilot.is_halted}"
            )
        except Exception as e:
            return f"Error retrieving portfolio status: {e}"

    @tool
    async def get_market_regime() -> str:
        """Fetch the current market regime, volatility level (VIX), 10Y Treasury yield (TNX), and macro blackout status."""
        try:
            raw_html = await copilot.get_regime_summary_html()
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error retrieving market regime: {e}"

    @tool
    async def get_macro_intelligence() -> str:
        """Fetch comprehensive multi-asset macro intelligence: US Treasury yield curve structure (3M, 2Y, 5Y, 10Y, 30Y),
        10Y-2Y and 10Y-3M slopes, High Yield OAS credit spreads, breakeven inflation rates, and compound macro stress index."""
        try:
            raw_html = await copilot.get_macro_summary_html()
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error retrieving macro intelligence: {e}"

    @tool
    async def trigger_market_scan(
        asset_class: str = "equities",
        strategy: str = "trend_pullback",
    ) -> str:
        """Trigger an on-demand market scan across equities or futures for technical setups."""
        try:
            raw_html = await copilot.run_scan_summary_html()
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error executing market scan: {e}"

    @tool
    async def run_backtest(
        symbol: str = "SPY",
        strategy: str = "trend_pullback",
        lookback_days: int = 60,
    ) -> str:
        """Run a vectorized backtest on a specific symbol with historical lookback."""
        try:
            raw_html = await copilot.run_backtest_summary_html(symbol=symbol, lookback=f"{lookback_days}d")
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error running backtest for {symbol}: {e}"

    @tool
    async def get_gex_surface(symbol: str = "SPY") -> str:
        """Fetch Gamma Exposure (GEX) surface, Call/Put walls, and Net GEX for a given index or ETF."""
        try:
            raw_html = await copilot.run_gex_summary_html(symbol)
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error retrieving GEX surface for {symbol}: {e}"

    @tool
    async def get_pairs_cointegration() -> str:
        """Fetch statistical pairs cointegration and spread z-scores across tracked pairs."""
        try:
            raw_html = await copilot.run_pairs_summary_html()
            return _clean_html(raw_html)
        except Exception as e:
            return f"Error retrieving pairs cointegration: {e}"

    @tool
    async def get_technical_summary(symbol: str) -> str:
        """Calculate technical indicators (RSI, EMA 20/50, ATR) for a given symbol based on recent daily bars."""
        try:
            df = await asyncio.to_thread(copilot.data_fetcher.provider.fetch_bars, symbol, "1d", period="60d")

            if df is None or getattr(df, "empty", True):
                return f"No price bar data available for symbol {symbol}."

            close_col = "close" if "close" in df.columns else "Close"
            high_col = "high" if "high" in df.columns else "High"
            low_col = "low" if "low" in df.columns else "Low"

            if close_col not in df.columns:
                return f"Price column missing for symbol {symbol}."

            close = df[close_col]
            high = df[high_col] if high_col in df.columns else close
            low = df[low_col] if low_col in df.columns else close

            rsi_series = calculate_rsi(close, period=14)
            ema20_series = calculate_ema(close, span=20)
            ema50_series = calculate_ema(close, span=50)
            atr_series = calculate_atr(high, low, close, period=14)

            last_close = float(close.iloc[-1])
            last_rsi = float(rsi_series.iloc[-1])
            last_ema20 = float(ema20_series.iloc[-1])
            last_ema50 = float(ema50_series.iloc[-1])
            last_atr = float(atr_series.iloc[-1])

            trend = (
                "BULLISH"
                if last_close > last_ema20 > last_ema50
                else "BEARISH"
                if last_close < last_ema20 < last_ema50
                else "NEUTRAL"
            )

            return (
                f"Technical Analysis Summary for {symbol.upper()}:\n"
                f"- Last Price: ${last_close:,.2f}\n"
                f"- RSI(14): {last_rsi:.1f} ({'Overbought' if last_rsi > 70 else 'Oversold' if last_rsi < 30 else 'Neutral'})\n"
                f"- EMA(20): ${last_ema20:,.2f}\n"
                f"- EMA(50): ${last_ema50:,.2f}\n"
                f"- ATR(14): ${last_atr:,.2f}\n"
                f"- Structure: {trend}"
            )
        except Exception as e:
            return f"Error calculating technical indicators for {symbol}: {e}"

    @tool
    async def get_system_health() -> str:
        """Fetch desk operational health, emergency kill-switch status, and uptime."""
        try:
            status = "Halted" if copilot.is_halted else "Operational / Active"
            reason = f" (Reason: {copilot.halt_reason})" if copilot.is_halted and copilot.halt_reason else ""
            mode = getattr(copilot.config, "execution_mode", ExecutionMode.PAPER).upper()
            return (
                f"System Operational Health:\n"
                f"- Status: {status}{reason}\n"
                f"- Halted: {copilot.is_halted}\n"
                f"- Mode: {mode}\n"
                f"- Emergency Kill-Switch: {'ENGAGED' if copilot.is_halted else 'DISENGAGED'}"
            )
        except Exception as e:
            return f"Error retrieving system health: {e}"

    @tool
    async def get_alpha_catalog() -> str:
        """Fetch the formulaic alpha catalog, including institutional WorldQuant 101 formulas and active promoted production alphas."""
        try:
            mgr = AlphaPromotionManager()
            promoted = mgr.list_active_alphas()
            catalog = AlphaCatalog()
            catalog_alphas = catalog.list_alphas()

            lines = ["🧪 Formulaic Alpha Intelligence:"]
            lines.append(f"\nActive Production Alphas ({len(promoted)}):")
            if not promoted:
                lines.append("  - None currently active in production desk.")
            else:
                for a in promoted:
                    m = a.metrics
                    sr_str = f"{m.sharpe_oos:.2f}" if m else "N/A"
                    dsr_str = f"{m.dsr:.2f}" if m else "N/A"
                    lines.append(
                        f"  - [{a.alpha_id}] {a.definition.name} (Alloc: {a.allocation_weight * 100:.0f}%, "
                        f"OOS Sharpe: {sr_str}, DSR: {dsr_str})\n"
                        f"    Expression: {a.definition.expression}"
                    )

            lines.append(f"\nCatalog Library ({len(catalog_alphas)} Institutional Formulas):")
            lines.extend(f"  - [{ca.alpha_id}] {ca.name}: {ca.expression}" for ca in catalog_alphas[:10])
            if len(catalog_alphas) > 10:
                lines.append(f"  ... and {len(catalog_alphas) - 10} more formulas.")

            return "\n".join(lines)
        except Exception as e:
            return f"Error retrieving alpha catalog: {e}"

    @tool
    async def promote_alpha(alpha_id: str, allocation_weight: float = 0.10, notes: str = "") -> str:
        """Promote an institutional or candidate formulaic alpha into production desk trading.

        Args:
            alpha_id: Identifier of the alpha (e.g., 'alpha_wq_006', 'alpha_wq_054').
            allocation_weight: Capital allocation weight (e.g. 0.10 for 10%, default 0.10).
            notes: Optional operator rationale or audit notes.
        """
        try:
            mgr = AlphaPromotionManager()
            catalog = AlphaCatalog()
            defn = catalog.get(alpha_id)
            if not defn:
                return f"Alpha '{alpha_id}' not found in catalog. Use get_alpha_catalog to list available alphas."

            rec = mgr.promote(
                alpha=defn,
                promoted_by="copilot_chat",
                allocation_weight=allocation_weight,
                notes=notes or f"Promoted via conversational copilot: {defn.name}",
            )

            # Dynamically register in Copilot's active strategy engine if available
            if hasattr(copilot, "strategy_engine") and hasattr(copilot.strategy_engine, "registry"):
                copilot.strategy_engine.registry.register(FormulaicAlphaStrategy(definition=defn))

            return (
                f"✅ Successfully promoted '{rec.alpha_id}' ({defn.name}) into production desk!\n"
                f"- Allocation: {rec.allocation_weight * 100:.0f}%\n"
                f"- Expression: {defn.expression}\n"
                f"- Audit Trail: Stored in config/promoted_alphas.yaml"
            )
        except Exception as e:
            return f"Error promoting alpha '{alpha_id}': {e}"

    @tool
    async def demote_alpha(alpha_id: str, notes: str = "") -> str:
        """Demote and deactivate a formulaic alpha from production trading.

        Args:
            alpha_id: Identifier of the promoted alpha to demote (e.g., 'alpha_wq_006').
            notes: Optional reason for deactivation / demotion.
        """
        try:
            mgr = AlphaPromotionManager()
            success = mgr.demote(alpha_id=alpha_id, reason=notes)
            if not success:
                return f"No active promoted alpha found with ID '{alpha_id}'."

            if hasattr(copilot, "strategy_engine") and hasattr(copilot.strategy_engine, "registry"):
                copilot.strategy_engine.registry.unregister(alpha_id)

            return (
                f"🛑 Successfully demoted '{alpha_id}' from production desk.\n"
                f"- Reason: {notes or 'Deactivated via copilot'}"
            )
        except Exception as e:
            return f"Error demoting alpha '{alpha_id}': {e}"

    return [
        get_open_positions,
        get_portfolio_status,
        get_market_regime,
        get_macro_intelligence,
        trigger_market_scan,
        run_backtest,
        get_gex_surface,
        get_pairs_cointegration,
        get_technical_summary,
        get_system_health,
        get_alpha_catalog,
        promote_alpha,
        demote_alpha,
    ]
