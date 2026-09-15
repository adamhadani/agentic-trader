import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx
import yfinance as yf

from agentic_trader.config import RegimeConfig
from agentic_trader.constants import (
    DEFAULT_MIN_RISK_REWARD_RATIO,
    DXY_TICKER,
    FRED_CSV_BASE_URL,
    FRED_HY_OAS_SERIES,
    FRED_T5YIE_SERIES,
    FRED_T10YIE_SERIES,
    FVX_TICKER,
    IRX_TICKER,
    TNX_TICKER,
    TWO_YEAR_TICKER,
    TYX_TICKER,
    VIX_TICKER,
)


logger = logging.getLogger(__name__)


class YieldCurveRegime(StrEnum):
    """Classification of the US Treasury yield curve structure."""

    NORMAL_STEEP = "NORMAL_STEEP"  # Standard upward sloping curve (25-150 bps)
    FLAT = "FLAT"  # Compressed spread (0-25 bps)
    INVERTED = "INVERTED"  # Inverted spread (< 0 bps)
    STEEP = "STEEP"  # Widely steepened (> 150 bps)


class CreditStressRegime(StrEnum):
    """Credit market stress regime based on High Yield OAS spreads."""

    BENIGN = "BENIGN"  # OAS < 3.50% (< 350 bps)
    ELEVATED = "ELEVATED"  # 3.50% <= OAS <= 5.00% (350-500 bps)
    CRITICAL = "CRITICAL"  # OAS > 5.00% (> 500 bps)


class InflationRegime(StrEnum):
    """Inflation expectations regime based on 10Y and 5Y breakeven rates."""

    ANCHORED = "ANCHORED"  # 2.0% - 2.6%
    DEFLECTION_LOW = "DEFLECTION_LOW"  # < 2.0%
    ELEVATED = "ELEVATED"  # > 2.6%


class MacroStressLevel(StrEnum):
    """Compound institutional macro stress level."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class TreasuryYields:
    """Benchmark US Treasury constant maturity yields in percentage points."""

    yield_3m: float
    yield_2y: float
    yield_5y: float
    yield_10y: float
    yield_30y: float


@dataclass
class YieldCurveSpreads:
    """Calculated term structure slope and butterfly curvature."""

    slope_10y_2y_pct: float
    slope_10y_2y_bps: float
    slope_10y_3m_bps: float
    curvature_butterfly_bps: float
    regime: YieldCurveRegime


@dataclass
class CreditSpreads:
    """ICE BofA US High Yield Option-Adjusted Spread."""

    high_yield_oas_pct: float
    high_yield_oas_bps: float
    regime: CreditStressRegime


@dataclass
class InflationExpectations:
    """Breakeven inflation expectations from TIPS spreads."""

    breakeven_10y: float
    breakeven_5y: float
    regime: InflationRegime


@dataclass
class MacroStressAssessment:
    """Synthesized compound macro stress evaluation and risk budget scaling."""

    level: MacroStressLevel
    risk_multiplier: float  # Multiplier applied to position sizing risk dollars (e.g. 1.0, 0.8, 0.5, 0.25)
    squeeze_breakout_allowed: bool
    min_rr_threshold: float
    key_drivers: list[str] = field(default_factory=list)


@dataclass
class MacroIntelligenceReport:
    """Comprehensive institutional macro intelligence snapshot."""

    yields: TreasuryYields
    spreads: YieldCurveSpreads
    credit: CreditSpreads
    inflation: InflationExpectations
    vix: float
    dxy: float
    stress: MacroStressAssessment
    timestamp: datetime
    summary_text: str


def calculate_yield_curve_spreads(yields: TreasuryYields) -> YieldCurveSpreads:
    """
    Compute 10Y-2Y slope, 10Y-3M slope, butterfly curvature, and curve regime.
    Curvature butterfly: 2 * 5Y - (2Y + 10Y) in basis points.
    """
    slope_10y_2y_pct = yields.yield_10y - yields.yield_2y
    slope_10y_2y_bps = slope_10y_2y_pct * 100.0

    slope_10y_3m_pct = yields.yield_10y - yields.yield_3m
    slope_10y_3m_bps = slope_10y_3m_pct * 100.0

    curvature_pct = (2.0 * yields.yield_5y) - (yields.yield_2y + yields.yield_10y)
    curvature_bps = curvature_pct * 100.0

    # Classify regime based on 10Y-2Y and 10Y-3M slopes
    if slope_10y_2y_bps < 0.0 or slope_10y_3m_bps < 0.0:
        regime = YieldCurveRegime.INVERTED
    elif slope_10y_2y_bps < 25.0:
        regime = YieldCurveRegime.FLAT
    elif slope_10y_2y_bps <= 150.0:
        regime = YieldCurveRegime.NORMAL_STEEP
    else:
        regime = YieldCurveRegime.STEEP

    return YieldCurveSpreads(
        slope_10y_2y_pct=round(slope_10y_2y_pct, 4),
        slope_10y_2y_bps=round(slope_10y_2y_bps, 2),
        slope_10y_3m_bps=round(slope_10y_3m_bps, 2),
        curvature_butterfly_bps=round(curvature_bps, 2),
        regime=regime,
    )


def classify_credit_stress(
    oas_pct: float,
    elevated_threshold: float = 3.50,
    critical_threshold: float = 5.00,
) -> CreditSpreads:
    """Classify ICE BofA High Yield OAS into benign, elevated, or critical regimes."""
    oas_bps = oas_pct * 100.0
    if oas_pct < elevated_threshold:
        regime = CreditStressRegime.BENIGN
    elif oas_pct <= critical_threshold:
        regime = CreditStressRegime.ELEVATED
    else:
        regime = CreditStressRegime.CRITICAL

    return CreditSpreads(
        high_yield_oas_pct=round(oas_pct, 3),
        high_yield_oas_bps=round(oas_bps, 1),
        regime=regime,
    )


def classify_inflation_expectations(
    breakeven_10y: float,
    breakeven_5y: float,
) -> InflationExpectations:
    """Classify TIPS breakeven inflation rates into anchored, low, or elevated regimes."""
    if breakeven_10y < 2.0:
        regime = InflationRegime.DEFLECTION_LOW
    elif breakeven_10y <= 2.6:
        regime = InflationRegime.ANCHORED
    else:
        regime = InflationRegime.ELEVATED

    return InflationExpectations(
        breakeven_10y=round(breakeven_10y, 2),
        breakeven_5y=round(breakeven_5y, 2),
        regime=regime,
    )


def compute_compound_macro_stress(
    vix: float,
    yield_spreads: YieldCurveSpreads,
    credit: CreditSpreads,
    inflation: InflationExpectations,
) -> MacroStressAssessment:
    """
    Synthesize VIX, yield curve inversion, and credit spreads into a compound macro stress index.
    Determines risk multiplier (1.0x to 0.25x), breakout suppression, and required R:R.
    """
    drivers: list[str] = []
    score = 0

    # 1. Volatility scoring
    if vix > 30.0:
        score += 3
        drivers.append(f"VIX Extreme ({vix:.1f})")
    elif vix > 22.0:
        score += 2
        drivers.append(f"VIX Elevated ({vix:.1f})")
    elif vix > 18.0:
        score += 1

    # 2. Yield curve structure scoring
    if yield_spreads.regime == YieldCurveRegime.INVERTED:
        score += 2
        drivers.append(f"Yield Curve Inverted (10Y-2Y: {yield_spreads.slope_10y_2y_bps:.0f} bps)")
    elif yield_spreads.regime == YieldCurveRegime.FLAT:
        score += 1
        drivers.append("Yield Curve Flat (< 25 bps)")

    # 3. Credit spread scoring
    if credit.regime == CreditStressRegime.CRITICAL:
        score += 3
        drivers.append(f"High Yield OAS Critical ({credit.high_yield_oas_bps:.0f} bps)")
    elif credit.regime == CreditStressRegime.ELEVATED:
        score += 2
        drivers.append(f"High Yield OAS Elevated ({credit.high_yield_oas_bps:.0f} bps)")

    # 4. Determine Level and Invariants
    # Fast escalation override for extreme panic
    if vix > 30.0 or credit.regime == CreditStressRegime.CRITICAL or score >= 7:
        level = MacroStressLevel.EXTREME
        multiplier = 0.25
        breakout_allowed = False
        min_rr = 2.5
    elif yield_spreads.regime == YieldCurveRegime.INVERTED or score >= 4:
        level = MacroStressLevel.HIGH
        multiplier = 0.50
        breakout_allowed = False
        min_rr = 2.2
    elif score >= 2:
        level = MacroStressLevel.MODERATE
        multiplier = 0.80
        breakout_allowed = True
        min_rr = DEFAULT_MIN_RISK_REWARD_RATIO
    else:
        level = MacroStressLevel.LOW
        multiplier = 1.00
        breakout_allowed = True
        min_rr = DEFAULT_MIN_RISK_REWARD_RATIO

    if not drivers:
        drivers.append("Benign macro conditions across all indicators")

    return MacroStressAssessment(
        level=level,
        risk_multiplier=multiplier,
        squeeze_breakout_allowed=breakout_allowed,
        min_rr_threshold=min_rr,
        key_drivers=drivers,
    )


def _fetch_ticker_sync(ticker: str) -> float | None:
    """Synchronously fetch the most recent closing price for a macro ticker."""
    try:
        data = yf.Ticker(ticker)
        hist = data.history(period="5d")
        if not hist.empty and "Close" in hist:
            val = float(hist["Close"].dropna().iloc[-1])
            return round(val, 3)
    except Exception as e:
        logger.debug("Failed to fetch macro ticker %s: %s", ticker, e)
    return None


class FredDataClient:
    """
    Lightweight, zero-auth HTTP client fetching economic time series from FRED.
    Uses public fredgraph.csv endpoints with robust fallback and in-memory TTL caching.
    """

    def __init__(self, cache_ttl_seconds: int = 900, timeout_seconds: float = 5.0):
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, datetime]] = {}

    async def _fetch_csv_text(self, series_id: str) -> str:
        """Fetch raw CSV text from FRED."""
        url = f"{FRED_CSV_BASE_URL}?id={series_id}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def fetch_latest_value(
        self,
        series_id: str,
        fallback_default: float | None = None,
        force_refresh: bool = False,
    ) -> float | None:
        """Fetch the most recent valid observation for a given FRED series."""
        now = datetime.now(UTC)
        if not force_refresh and series_id in self._cache:
            cached_val, cached_time = self._cache[series_id]
            if (now - cached_time).total_seconds() < self.cache_ttl_seconds:
                return cached_val

        try:
            csv_text = await self._fetch_csv_text(series_id)
            lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
            # Find the most recent line with valid numeric value (skipping '.' missing marks)
            latest_val: float | None = None
            for line in reversed(lines):
                parts = line.split(",")
                if len(parts) == 2 and parts[1] != "." and parts[0] != "DATE":
                    with contextlib.suppress(ValueError):
                        latest_val = float(parts[1])
                        break

            if latest_val is not None:
                self._cache[series_id] = (latest_val, now)
                return latest_val

        except Exception as e:
            logger.warning("Failed to fetch FRED series %s: %s. Using fallback.", series_id, e)

        if fallback_default is not None:
            return fallback_default
        return self._cache.get(series_id, (None, None))[0]


class MacroIntelligenceEngine:
    """
    Institutional Multi-Asset Macro Intelligence Engine.
    Ingests US Treasury curve (3M, 2Y, 5Y, 10Y, 30Y), Credit OAS, and TIPS Breakevens,
    calculating term structure spreads, macro stress index, and dynamic risk budget multipliers.
    """

    def __init__(
        self,
        config: RegimeConfig | None = None,
        fred_client: FredDataClient | None = None,
    ):
        self.config = config or RegimeConfig()
        self.fred_client = fred_client or FredDataClient(cache_ttl_seconds=self.config.cache_ttl_seconds)
        self._cached_report: MacroIntelligenceReport | None = None
        self._last_fetch_time: datetime | None = None

    async def get_macro_report(self, force_refresh: bool = False) -> MacroIntelligenceReport:
        """Fetch all macro feeds concurrently and build a comprehensive MacroIntelligenceReport."""
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._cached_report is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self.config.cache_ttl_seconds
        ):
            return self._cached_report

        # 1. Concurrently fetch tickers via threadpool
        ticker_tasks = {
            "3m": asyncio.to_thread(_fetch_ticker_sync, IRX_TICKER),
            "2y": asyncio.to_thread(_fetch_ticker_sync, TWO_YEAR_TICKER),
            "5y": asyncio.to_thread(_fetch_ticker_sync, FVX_TICKER),
            "10y": asyncio.to_thread(_fetch_ticker_sync, TNX_TICKER),
            "30y": asyncio.to_thread(_fetch_ticker_sync, TYX_TICKER),
            "vix": asyncio.to_thread(_fetch_ticker_sync, VIX_TICKER),
            "dxy": asyncio.to_thread(_fetch_ticker_sync, DXY_TICKER),
        }

        # 2. Concurrently fetch FRED series
        fred_tasks = {
            "hy_oas": self.fred_client.fetch_latest_value(FRED_HY_OAS_SERIES, fallback_default=3.20),
            "t10yie": self.fred_client.fetch_latest_value(FRED_T10YIE_SERIES, fallback_default=2.35),
            "t5yie": self.fred_client.fetch_latest_value(FRED_T5YIE_SERIES, fallback_default=2.40),
        }

        all_keys = list(ticker_tasks.keys()) + list(fred_tasks.keys())
        all_coroutines = list(ticker_tasks.values()) + list(fred_tasks.values())

        raw_results = await asyncio.gather(*all_coroutines, return_exceptions=True)
        res_map: dict[str, Any] = {}
        for key, res in zip(all_keys, raw_results, strict=False):
            if isinstance(res, BaseException) or res is None:
                res_map[key] = None
            else:
                res_map[key] = float(res)

        # 3. Robust Yield Curve Resolution with sensible baselines
        y_10y = res_map["10y"] or 4.50
        y_2y = res_map["2y"] or (y_10y - 0.40)
        y_5y = res_map["5y"] or ((y_2y + y_10y) / 2.0)
        y_30y = res_map["30y"] or (y_10y + 0.35)
        y_3m = res_map["3m"] or 4.00

        yields = TreasuryYields(
            yield_3m=y_3m,
            yield_2y=y_2y,
            yield_5y=y_5y,
            yield_10y=y_10y,
            yield_30y=y_30y,
        )

        spreads = calculate_yield_curve_spreads(yields)

        # 4. Credit OAS Resolution
        hy_oas = res_map["hy_oas"] or 3.20
        credit = classify_credit_stress(
            hy_oas,
            elevated_threshold=getattr(self.config, "hy_oas_elevated_threshold", 3.50),
            critical_threshold=getattr(self.config, "hy_oas_critical_threshold", 5.00),
        )

        # 5. Inflation Breakeven Resolution
        t10yie = res_map["t10yie"] or 2.35
        t5yie = res_map["t5yie"] or 2.40
        inflation = classify_inflation_expectations(t10yie, t5yie)

        # 6. Volatility & Dollar
        vix = res_map["vix"] or 18.0
        dxy = res_map["dxy"] or 103.5

        # 7. Compound Macro Stress Assessment
        stress = compute_compound_macro_stress(vix, spreads, credit, inflation)

        # 8. Summary Text
        summary = (
            f"Macro Stress: {stress.level.value} (Risk Multiplier: {stress.risk_multiplier:.2f}x) | "
            f"Curve: {spreads.regime.value} (10Y-2Y: {spreads.slope_10y_2y_bps:+.1f} bps) | "
            f"Credit OAS: {credit.high_yield_oas_bps:.0f} bps ({credit.regime.value}) | "
            f"10Y Inflation: {inflation.breakeven_10y:.2f}% | VIX: {vix:.2f}"
        )

        report = MacroIntelligenceReport(
            yields=yields,
            spreads=spreads,
            credit=credit,
            inflation=inflation,
            vix=vix,
            dxy=dxy,
            stress=stress,
            timestamp=now,
            summary_text=summary,
        )

        self._cached_report = report
        self._last_fetch_time = now

        logger.info(
            "Evaluated multi-asset macro intelligence: Stress=%s, Curve=%s (10Y-2Y=%.1f bps), OAS=%.0f bps",
            stress.level.value,
            spreads.regime.value,
            spreads.slope_10y_2y_bps,
            credit.high_yield_oas_bps,
        )
        return report
