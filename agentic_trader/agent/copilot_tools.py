"""Quantitative desk tools exposed to LangGraph Conversational Copilot."""

from __future__ import annotations

import asyncio
import html
import re
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

from agentic_trader.constants import DEFAULT_RESEARCH_SYMBOL, ExecutionMode
from agentic_trader.research.alpha.catalog import AlphaCatalog
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
            return _clean_html(await copilot.get_positions_summary_html())
        except Exception as e:
            return f"Error retrieving open positions: {e}"

    @tool
    async def get_portfolio_status() -> str:
        """Fetch portfolio capital, cash balances, open notional exposure, margin utilization, and execution mode."""
        try:
            return _clean_html(await copilot.get_status_text_html())
        except Exception as e:
            return f"Error retrieving portfolio status: {e}"

    @tool
    async def get_macro_intelligence() -> str:
        """Fetch comprehensive multi-asset macro intelligence: US Treasury yield curve structure (3M, 2Y, 5Y, 10Y, 30Y),
        10Y-2Y and 10Y-3M slopes, High Yield OAS credit spreads, breakeven inflation rates, volatility regime and combined trading filters."""
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
        symbol: str = DEFAULT_RESEARCH_SYMBOL,
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
    async def get_gex_surface(symbol: str = DEFAULT_RESEARCH_SYMBOL) -> str:
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
        """Read the journal-backed alpha registry and formula catalog."""
        snapshot = await copilot.alpha_repository.snapshot()
        lines = [
            f"Formulaic Alpha Intelligence: generation {snapshot.generation}",
            f"Active: {len(snapshot.active)}; shadow: {len(snapshot.shadow)}",
            "Catalog Library:",
        ]
        lines.extend(f"{a.alpha_id}: {a.expression}" for a in AlphaCatalog().list_alphas())
        return "\n".join(lines)

    @tool
    async def promote_alpha(version_id: str, expected_generation: int) -> str:
        """Promote an exact qualified immutable alpha version using the observed registry generation."""
        try:
            generation = await copilot.alpha_repository.promote(
                version_id, actor="copilot_chat", expected_generation=expected_generation
            )
            return f"Promoted {version_id}; registry generation {generation}. Effective next scan."
        except ValueError as exc:
            return f"Promotion rejected: {exc}"

    @tool
    async def demote_alpha(version_id: str, expected_generation: int) -> str:
        """Deactivate an exact alpha version. Existing positions retain their protection."""
        try:
            generation = await copilot.alpha_repository.demote(
                version_id, actor="copilot_chat", expected_generation=expected_generation
            )
            return f"Demoted {version_id}; registry generation {generation}. Existing positions remain protected."
        except ValueError as exc:
            return f"Demotion rejected: {exc}"

    return [
        get_open_positions,
        get_portfolio_status,
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
