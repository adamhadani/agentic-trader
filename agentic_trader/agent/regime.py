import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import yfinance as yf

from agentic_trader.agent.macro import (
    MacroIntelligenceEngine,
    MacroIntelligenceReport,
)
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import (
    DEFAULT_MIN_RISK_REWARD_RATIO,
    DXY_TICKER,
    TNX_TICKER,
    VIX_TICKER,
    VolatilityRegime,
)


logger = logging.getLogger(__name__)


@dataclass
class RegimeSnapshot:
    """Quantitative representation of current market volatility and macro regime."""

    vix: float
    vix_regime: VolatilityRegime
    tnx: float | None
    dxy: float | None
    breakout_allowed: bool
    min_rr_threshold: float
    timestamp: datetime
    summary_text: str
    macro_report: MacroIntelligenceReport | None = None
    risk_multiplier: float = 1.0


def classify_vix_level(vix: float, config: RegimeConfig | None = None) -> VolatilityRegime:
    """Classify VIX value into VolatilityRegime enum based on configured thresholds."""
    cfg = config or RegimeConfig()
    if vix < cfg.vix_compressed_threshold:
        return VolatilityRegime.COMPRESSED
    elif vix <= cfg.vix_elevated_threshold:
        return VolatilityRegime.NORMAL
    elif vix <= cfg.vix_extreme_threshold:
        return VolatilityRegime.ELEVATED
    else:
        return VolatilityRegime.EXTREME


def _fetch_ticker_sync(ticker: str) -> float | None:
    """Synchronously fetch the most recent closing price for a macro ticker."""
    try:
        data = yf.Ticker(ticker)
        hist = data.history(period="5d")
        if not hist.empty and "Close" in hist:
            val = float(hist["Close"].dropna().iloc[-1])
            return round(val, 2)
    except Exception as e:
        logger.debug("Failed to fetch macro ticker %s: %s", ticker, e)
    return None


class RegimeDetector:
    """
    Macro and Volatility Regime Filter.
    Monitors ^VIX, ^TNX, and DX-Y.NYB and orchestrates MacroIntelligenceEngine
    to classify market conditions, adjust risk invariants, and suppress vulnerable breakout strategies.
    """

    def __init__(
        self,
        config: RegimeConfig | None = None,
        macro_engine: MacroIntelligenceEngine | None = None,
    ):
        self.config = config or RegimeConfig()
        self.macro_engine = macro_engine or MacroIntelligenceEngine(config=self.config)
        self._cached_snapshot: RegimeSnapshot | None = None
        self._last_fetch_time: datetime | None = None

    async def get_regime(self, force_refresh: bool = False) -> RegimeSnapshot:
        """Fetch real-time macro indicators and return the current market regime snapshot."""
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._cached_snapshot is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self.config.cache_ttl_seconds
        ):
            return self._cached_snapshot

        # Fetch macro tickers concurrently in worker threads
        vix_task = asyncio.to_thread(_fetch_ticker_sync, VIX_TICKER)
        tnx_task = asyncio.to_thread(_fetch_ticker_sync, TNX_TICKER)
        dxy_task = asyncio.to_thread(_fetch_ticker_sync, DXY_TICKER)

        results = await asyncio.gather(vix_task, tnx_task, dxy_task, return_exceptions=True)

        vix_res = results[0] if not isinstance(results[0], Exception) else None
        tnx_res = results[1] if not isinstance(results[1], Exception) else None
        dxy_res = results[2] if not isinstance(results[2], Exception) else None

        # Fallback to standard baseline if VIX query fails
        if vix_res is None or not isinstance(vix_res, float | int) or vix_res <= 0:
            logger.warning(
                "Could not retrieve live VIX data from yfinance. Falling back to default baseline (18.0 NORMAL).",
                extra={"vix_ticker": VIX_TICKER},
            )
            vix = 18.0
        else:
            vix = float(vix_res)

        tnx = float(tnx_res) if isinstance(tnx_res, float | int) else None
        dxy = float(dxy_res) if isinstance(dxy_res, float | int) else None

        regime = classify_vix_level(vix, self.config)

        # In extreme volatility panic regimes, suppress breakout expansions to prevent false whipsaws
        breakout_allowed = regime != VolatilityRegime.EXTREME

        # Modulate minimum required reward-to-risk ratio
        if regime == VolatilityRegime.EXTREME:
            min_rr = 2.5
        elif regime == VolatilityRegime.ELEVATED:
            min_rr = 2.2
        else:
            min_rr = DEFAULT_MIN_RISK_REWARD_RATIO

        risk_multiplier = 1.0
        macro_report: MacroIntelligenceReport | None = None

        # Integrate enriched macro intelligence if enabled
        if getattr(self.config, "yield_curve_enabled", True):
            try:
                macro_report = await self.macro_engine.get_macro_report(force_refresh=force_refresh)
                risk_multiplier = macro_report.stress.risk_multiplier
                if not macro_report.stress.squeeze_breakout_allowed:
                    breakout_allowed = False
                min_rr = max(min_rr, macro_report.stress.min_rr_threshold)
            except Exception as e:
                logger.debug("Failed to incorporate macro intelligence report: %s", e)

        tnx_str = f"{tnx:.2f}%" if tnx is not None else "N/A"
        dxy_str = f"{dxy:.2f}" if dxy is not None else "N/A"
        breakout_str = "Allowed" if breakout_allowed else "Suppressed"
        summary = f"VIX: {vix:.2f} ({regime.value}) | 10Y Yield: {tnx_str} | Dollar (DXY): {dxy_str} | Breakouts: {breakout_str}"

        snapshot = RegimeSnapshot(
            vix=vix,
            vix_regime=regime,
            tnx=tnx,
            dxy=dxy,
            breakout_allowed=breakout_allowed,
            min_rr_threshold=min_rr,
            timestamp=now,
            summary_text=summary,
            macro_report=macro_report,
            risk_multiplier=risk_multiplier,
        )

        self._cached_snapshot = snapshot
        self._last_fetch_time = now

        logger.info(
            "Market volatility regime evaluated: %s (VIX=%.2f, RiskMultiplier=%.2fx)",
            regime.value,
            vix,
            risk_multiplier,
            extra={
                "vix": vix,
                "regime": regime.value,
                "breakout_allowed": breakout_allowed,
                "tnx": tnx,
                "dxy": dxy,
                "risk_multiplier": risk_multiplier,
            },
        )
        return snapshot

    def get_prompt_context(self, regime: RegimeSnapshot | None = None) -> str:
        """Format regime snapshot for LLM trade evaluation prompts."""
        reg = regime or self._cached_snapshot
        if reg is None:
            return "Market Volatility & Macro Regime: NORMAL (Orderly market conditions, VIX ~ 18.0 baseline)."

        status = (
            "ALLOWED" if reg.breakout_allowed else "SUPPRESSED (Extreme Volatility Chop / High False Breakout Risk)"
        )
        tnx_str = f"{reg.tnx:.2f}%" if reg.tnx is not None else "N/A"
        dxy_str = f"{reg.dxy:.2f}" if reg.dxy is not None else "N/A"

        lines = [
            "Market Volatility & Macro Regime Context:",
            f"• Volatility Regime: {reg.vix_regime.value} (CBOE VIX: {reg.vix:.2f})",
            f"• 10-Year US Treasury Yield (^TNX): {tnx_str}",
            f"• US Dollar Index (DXY): {dxy_str}",
        ]

        if reg.macro_report:
            m = reg.macro_report
            lines.extend(
                [
                    f"• US Treasury Yield Curve: {m.spreads.regime.value} (10Y-2Y Spread: {m.spreads.slope_10y_2y_bps:+.1f} bps, 10Y-3M Spread: {m.spreads.slope_10y_3m_bps:+.1f} bps)",
                    f"• Credit Risk (High Yield OAS): {m.credit.high_yield_oas_bps:.0f} bps ({m.credit.regime.value})",
                    f"• 10Y Breakeven Inflation: {m.inflation.breakeven_10y:.2f}% ({m.inflation.regime.value})",
                    f"• Compound Macro Stress: {m.stress.level.value} (Risk Multiplier: {m.stress.risk_multiplier:.2f}x)",
                ]
            )

        lines.append(
            f"• Strategy Adaptation: Squeeze Breakouts {status}; Minimum required Reward-to-Risk: {reg.min_rr_threshold:.1f}:1."
        )
        return "\n".join(lines)
