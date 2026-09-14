"""Quantitative desk tools exposed to LangGraph Conversational Copilot."""

from __future__ import annotations

import asyncio
import html
import inspect
import re
from typing import TYPE_CHECKING

import pandas as pd
from langchain_core.tools import BaseTool, tool

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
            mode = getattr(copilot.config, "execution_mode", "paper").upper()
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
            df: pd.DataFrame | None = None
            fetcher = getattr(copilot, "data_fetcher", None)
            if fetcher:
                provider = getattr(fetcher, "provider", None)
                if provider and hasattr(provider, "fetch_bars"):
                    raw = await asyncio.to_thread(lambda: provider.fetch_bars(symbol, "1d", period="60d"))
                    if isinstance(raw, pd.DataFrame):
                        df = raw
                if df is None and hasattr(fetcher, "fetch_daily_bars"):
                    raw = fetcher.fetch_daily_bars(symbol, limit=50)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    if isinstance(raw, pd.DataFrame):
                        df = raw

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
            mode = getattr(copilot.config, "execution_mode", "paper").upper()
            return (
                f"System Operational Health:\n"
                f"- Status: {status}{reason}\n"
                f"- Halted: {copilot.is_halted}\n"
                f"- Mode: {mode}\n"
                f"- Emergency Kill-Switch: {'ENGAGED' if copilot.is_halted else 'DISENGAGED'}"
            )
        except Exception as e:
            return f"Error retrieving system health: {e}"

    return [
        get_open_positions,
        get_portfolio_status,
        get_market_regime,
        trigger_market_scan,
        run_backtest,
        get_gex_surface,
        get_pairs_cointegration,
        get_technical_summary,
        get_system_health,
    ]
