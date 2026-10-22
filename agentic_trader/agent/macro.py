import asyncio
import contextlib
import dataclasses
import logging
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType

import httpx
import pandas as pd
import yfinance as yf

from agentic_trader.config import RegimeConfig
from agentic_trader.constants import (
    DEFAULT_MIN_RISK_REWARD_RATIO,
    DXY_TICKER,
    FRED_CSV_BASE_URL,
    FRED_HY_OAS_SERIES,
    FRED_T5YIE_SERIES,
    FRED_T10YIE_SERIES,
    FRED_TWO_YEAR_SERIES,
    FVX_TICKER,
    IRX_TICKER,
    TNX_TICKER,
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
    # A decision input is missing: the configured regime.unknown_* policy, never a measured level.
    UNKNOWN = "UNKNOWN"


# Decision inputs are exactly what the stress score and the curve regime read; context inputs
# (butterfly curvature, inflation regime, display) never move the stress assessment.
DECISION_INPUTS: tuple[str, ...] = ("vix", "3m", "2y", "10y", "hy_oas")
CONTEXT_INPUTS: tuple[str, ...] = ("5y", "30y", "dxy", "t10yie", "t5yie")
# Every input's provider and symbol, in canonical order (decision inputs first).
MACRO_INPUT_SOURCES: Mapping[str, str] = MappingProxyType(
    {
        "vix": f"yfinance:{VIX_TICKER}",
        "3m": f"yfinance:{IRX_TICKER}",
        "2y": f"fred:{FRED_TWO_YEAR_SERIES}",
        "10y": f"yfinance:{TNX_TICKER}",
        "hy_oas": f"fred:{FRED_HY_OAS_SERIES}",
        "5y": f"yfinance:{FVX_TICKER}",
        "30y": f"yfinance:{TYX_TICKER}",
        "dxy": f"yfinance:{DXY_TICKER}",
        "t10yie": f"fred:{FRED_T10YIE_SERIES}",
        "t5yie": f"fred:{FRED_T5YIE_SERIES}",
    }
)


@dataclass(frozen=True)
class MacroInput:
    """One macro observation as read: its value or why it is missing, its source and observation date.

    ``error`` is None exactly when ``value`` is present. Otherwise it is an exception type name,
    ``"no_observation"`` (the source answered without a usable value) or ``"unexpected_payload"``
    (the body was not the expected format, or the value was not finite). ``decision`` marks the
    names in ``DECISION_INPUTS``.
    """

    name: str
    value: float | None
    source: str
    observed_on: date | None
    decision: bool
    error: str | None


def _missing_input(name: str, source: str, *, decision: bool, error: str) -> MacroInput:
    return MacroInput(name=name, value=None, source=source, observed_on=None, decision=decision, error=error)


def missing_input_names(inputs: Mapping[str, MacroInput], *, decision_only: bool = False) -> tuple[str, ...]:
    """The names without a value, in canonical order (decision inputs first); optionally only decision inputs."""
    order = [
        *(name for name in MACRO_INPUT_SOURCES if name in inputs),
        *(n for n in inputs if n not in MACRO_INPUT_SOURCES),
    ]
    return tuple(name for name in order if inputs[name].value is None and (inputs[name].decision or not decision_only))


@dataclass
class TreasuryYields:
    """Benchmark US Treasury constant maturity yields in percentage points (5Y and 30Y are context, None when missing)."""

    yield_3m: float
    yield_2y: float
    yield_5y: float | None
    yield_10y: float
    yield_30y: float | None


@dataclass
class YieldCurveSpreads:
    """Calculated term structure slope and butterfly curvature."""

    slope_10y_2y_pct: float
    slope_10y_2y_bps: float
    slope_10y_3m_bps: float
    curvature_butterfly_bps: float | None  # None without a 5Y observation
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
    inflation: InflationExpectations | None  # None when either breakeven is missing
    vix: float
    dxy: float | None
    stress: MacroStressAssessment
    timestamp: datetime
    summary_text: str
    observation_dates: dict[str, str] = field(default_factory=dict)


def calculate_yield_curve_spreads(yields: TreasuryYields) -> YieldCurveSpreads:
    """
    Compute 10Y-2Y slope, 10Y-3M slope, butterfly curvature, and curve regime.
    Curvature butterfly: 2 * 5Y - (2Y + 10Y) in basis points; None without a 5Y observation.
    The regime reads only the 3M, 2Y and 10Y.
    """
    slope_10y_2y_pct = yields.yield_10y - yields.yield_2y
    slope_10y_2y_bps = slope_10y_2y_pct * 100.0

    slope_10y_3m_pct = yields.yield_10y - yields.yield_3m
    slope_10y_3m_bps = slope_10y_3m_pct * 100.0

    curvature_bps: float | None = None
    if yields.yield_5y is not None:
        curvature_bps = round(((2.0 * yields.yield_5y) - (yields.yield_2y + yields.yield_10y)) * 100.0, 2)

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
        curvature_butterfly_bps=curvature_bps,
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
    config: RegimeConfig | None = None,
) -> MacroStressAssessment:
    """
    Synthesize VIX, yield curve inversion, and credit spreads into a compound macro stress index.
    Determines risk multiplier (1.0x to 0.25x), breakout suppression, and required R:R.
    Reads only the decision inputs: VIX, the curve regime (3M, 2Y, 10Y) and HY OAS.
    """
    cfg = config or RegimeConfig()
    drivers: list[str] = []
    score = 0

    # 1. Volatility scoring
    if vix > cfg.vix_extreme_threshold:
        score += 3
        drivers.append(f"VIX Extreme ({vix:.1f})")
    elif vix > cfg.vix_elevated_threshold:
        score += 2
        drivers.append(f"VIX Elevated ({vix:.1f})")
    elif vix > cfg.vix_watch_threshold:
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
    if vix > cfg.vix_extreme_threshold or credit.regime == CreditStressRegime.CRITICAL or score >= 7:
        level = MacroStressLevel.EXTREME
        multiplier = 0.25
        breakout_allowed = False
        min_rr = cfg.extreme_min_rr
    elif yield_spreads.regime == YieldCurveRegime.INVERTED or score >= 4:
        level = MacroStressLevel.HIGH
        multiplier = 0.50
        breakout_allowed = False
        min_rr = cfg.elevated_min_rr
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


def unknown_macro_stress(config: RegimeConfig, missing: Sequence[str]) -> MacroStressAssessment:
    """The ``UNKNOWN`` stress policy for missing decision inputs: the configured multiplier and breakout flag.

    Its reward/risk is the default, so the VIX-derived ratio governs.
    """
    return MacroStressAssessment(
        level=MacroStressLevel.UNKNOWN,
        risk_multiplier=config.unknown_risk_multiplier,
        squeeze_breakout_allowed=config.unknown_breakout_allowed,
        min_rr_threshold=DEFAULT_MIN_RISK_REWARD_RATIO,
        key_drivers=[f"Missing decision inputs: {', '.join(missing)}"],
    )


def _yahoo_input_from_history(name: str, ticker: str, history: pd.DataFrame, *, decision: bool) -> MacroInput:
    """The last non-null Close of a yfinance history frame, rounded to 3 dp, dated by its bar."""
    source = f"yfinance:{ticker}"
    closes = history["Close"].dropna() if "Close" in history else None
    if closes is None or closes.empty:
        return _missing_input(name, source, decision=decision, error="no_observation")
    value = float(closes.iloc[-1])
    stamp = closes.index[-1]
    if not math.isfinite(value) or not isinstance(stamp, datetime):
        return _missing_input(name, source, decision=decision, error="unexpected_payload")
    return MacroInput(
        name=name, value=round(value, 3), source=source, observed_on=stamp.date(), decision=decision, error=None
    )


def fetch_yahoo_input(name: str, ticker: str, *, decision: bool) -> MacroInput:
    """Blocking read of a Yahoo Finance ticker's latest close; call it through ``asyncio.to_thread``."""
    try:
        return _yahoo_input_from_history(name, ticker, yf.Ticker(ticker).history(period="5d"), decision=decision)
    except Exception as exc:
        logger.warning(
            "Macro ticker %s unavailable",
            ticker,
            extra={"event": "macro_input_unavailable", "input": name, "error": type(exc).__name__},
        )
        return _missing_input(name, f"yfinance:{ticker}", decision=decision, error=type(exc).__name__)


_FRED_DATE_HEADERS = frozenset({"date", "observation_date"})


def _fred_input_from_csv(name: str, series_id: str, text: str, *, decision: bool) -> MacroInput:
    """The most recent finite observation of a fredgraph.csv body.

    A body whose first non-empty line is not a two-field ``date``/``observation_date`` header (for
    example an HTML maintenance page served with HTTP 200) is ``unexpected_payload``; a CSV without
    a finite dated value (only ``.`` missing marks) is ``no_observation``.
    """
    source = f"fred:{series_id}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header = lines[0].split(",") if lines else []
    if len(header) != 2 or header[0].strip().lower() not in _FRED_DATE_HEADERS:
        return _missing_input(name, source, decision=decision, error="unexpected_payload")
    for line in reversed(lines[1:]):
        parts = line.split(",")
        if len(parts) != 2:
            continue
        with contextlib.suppress(ValueError):
            value = float(parts[1])
            observed_on = date.fromisoformat(parts[0].strip())
            if math.isfinite(value):
                return MacroInput(
                    name=name, value=value, source=source, observed_on=observed_on, decision=decision, error=None
                )
    return _missing_input(name, source, decision=decision, error="no_observation")


class FredDataClient:
    """
    Lightweight, zero-auth HTTP client fetching economic time series from FRED.
    Uses public fredgraph.csv endpoints with in-memory TTL caching of successful observations;
    missing/expired observations remain unavailable.
    """

    def __init__(self, cache_ttl_seconds: int = 900, timeout_seconds: float = 5.0):
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, date, datetime]] = {}

    async def _fetch_csv_text(self, series_id: str) -> str:
        """Fetch raw CSV text from FRED."""
        url = f"{FRED_CSV_BASE_URL}?id={series_id}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def fetch_input(
        self,
        name: str,
        series_id: str,
        *,
        decision: bool,
        force_refresh: bool = False,
    ) -> MacroInput:
        """The most recent valid observation of a FRED series as a typed input; never raises for a failed read."""
        source = f"fred:{series_id}"
        now = datetime.now(UTC)
        cached = self._cache.get(series_id)
        if not force_refresh and cached is not None and (now - cached[2]).total_seconds() < self.cache_ttl_seconds:
            value, observed_on, _ = cached
            return MacroInput(
                name=name, value=value, source=source, observed_on=observed_on, decision=decision, error=None
            )

        try:
            text = await self._fetch_csv_text(series_id)
        except Exception as exc:
            result = _missing_input(name, source, decision=decision, error=type(exc).__name__)
        else:
            result = _fred_input_from_csv(name, series_id, text, decision=decision)

        if result.value is not None and result.observed_on is not None:
            self._cache[series_id] = (result.value, result.observed_on, now)
        else:
            logger.warning(
                "FRED series %s unavailable",
                series_id,
                extra={"event": "macro_input_unavailable", "input": name, "error": result.error},
            )
        return result


@dataclass(frozen=True)
class MacroEvaluation:
    """Every input read, the missing names (canonical order) and either a measured report or ``UNKNOWN`` stress.

    ``report`` is None exactly when a decision input is missing (``missing_decision``); ``stress`` is
    then ``unknown_macro_stress``. Missing context inputs leave the report's optional fields None.
    """

    inputs: Mapping[str, MacroInput]
    missing: tuple[str, ...]
    missing_decision: tuple[str, ...]
    report: MacroIntelligenceReport | None
    stress: MacroStressAssessment


def _decision_value(inputs: Mapping[str, MacroInput], name: str) -> float:
    value = inputs[name].value
    if value is None:  # evaluate() builds a report only when every decision input is present
        raise ValueError(f"Decision input {name} is missing")
    return value


class MacroIntelligenceEngine:
    """
    Institutional Multi-Asset Macro Intelligence Engine.
    Ingests US Treasury curve (3M, 2Y, 5Y, 10Y, 30Y), Credit OAS, and TIPS Breakevens,
    calculating term structure spreads, macro stress index, and dynamic risk budget multipliers.
    Yahoo reads use the injected blocking ``yahoo_fetch`` through ``asyncio.to_thread``; FRED reads
    use ``fred_client``.
    """

    def __init__(
        self,
        config: RegimeConfig | None = None,
        fred_client: FredDataClient | None = None,
        yahoo_fetch: Callable[..., MacroInput] = fetch_yahoo_input,
    ):
        self.config = config or RegimeConfig()
        self.fred_client = fred_client or FredDataClient(cache_ttl_seconds=self.config.cache_ttl_seconds)
        self.yahoo_fetch = yahoo_fetch
        self._cached_evaluation: MacroEvaluation | None = None
        self._last_fetch_time: datetime | None = None

    async def _read_input(self, name: str, *, force_refresh: bool) -> MacroInput:
        source = MACRO_INPUT_SOURCES[name]
        provider, _, symbol = source.partition(":")
        decision = name in DECISION_INPUTS
        try:
            if provider == "fred":
                result = await self.fred_client.fetch_input(
                    name, symbol, decision=decision, force_refresh=force_refresh
                )
            else:
                result = await asyncio.to_thread(self.yahoo_fetch, name, symbol, decision=decision)
        except Exception as exc:
            logger.warning(
                "Macro input %s unavailable",
                name,
                extra={"event": "macro_input_unavailable", "input": name, "error": type(exc).__name__},
            )
            return _missing_input(name, source, decision=decision, error=type(exc).__name__)
        if result.value is None:
            return result if result.error else dataclasses.replace(result, error="no_observation")
        # A non-finite value, or a non-positive VIX, is not an observation.
        if not math.isfinite(result.value) or (name == "vix" and result.value <= 0):
            return _missing_input(name, source, decision=decision, error="unexpected_payload")
        return result

    async def read_inputs(self, names: Collection[str], *, force_refresh: bool = False) -> dict[str, MacroInput]:
        """Read the named inputs concurrently, in canonical order; a failed read is a missing input, never raised."""
        unknown = sorted(set(names) - set(MACRO_INPUT_SOURCES))
        if unknown:
            raise ValueError(f"Unknown macro inputs: {', '.join(unknown)}")
        ordered = [name for name in MACRO_INPUT_SOURCES if name in names]
        results = await asyncio.gather(*(self._read_input(name, force_refresh=force_refresh) for name in ordered))
        return dict(zip(ordered, results, strict=True))

    async def evaluate(self, force_refresh: bool = False) -> MacroEvaluation:
        """Read all ten inputs and evaluate the compound stress; never raises for missing data.

        A missing decision input gives ``UNKNOWN`` stress and no report. Only a complete evaluation
        (all ten inputs present) is cached, so a recovered feed is measured on the next call.
        """
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._cached_evaluation is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self.config.cache_ttl_seconds
        ):
            return self._cached_evaluation

        inputs = await self.read_inputs(MACRO_INPUT_SOURCES.keys(), force_refresh=force_refresh)
        missing = missing_input_names(inputs)
        missing_decision = missing_input_names(inputs, decision_only=True)
        if missing_decision:
            logger.warning(
                "Macro stress UNKNOWN: missing decision inputs %s",
                ", ".join(missing_decision),
                extra={
                    "event": "macro_stress_unknown",
                    "missing": list(missing),
                    "missing_decision": list(missing_decision),
                },
            )
            return MacroEvaluation(
                inputs=inputs,
                missing=missing,
                missing_decision=missing_decision,
                report=None,
                stress=unknown_macro_stress(self.config, missing_decision),
            )

        report = self._build_report(inputs, now)
        evaluation = MacroEvaluation(
            inputs=inputs,
            missing=missing,
            missing_decision=(),
            report=report,
            stress=report.stress,
        )
        if not missing:
            self._cached_evaluation = evaluation
            self._last_fetch_time = now

        logger.info(
            "Evaluated multi-asset macro intelligence: Stress=%s, Curve=%s (10Y-2Y=%.1f bps), OAS=%.0f bps",
            report.stress.level.value,
            report.spreads.regime.value,
            report.spreads.slope_10y_2y_bps,
            report.credit.high_yield_oas_bps,
            extra={"event": "macro_evaluated", "missing": list(missing)},
        )
        return evaluation

    def _build_report(self, inputs: Mapping[str, MacroInput], now: datetime) -> MacroIntelligenceReport:
        yields = TreasuryYields(
            yield_3m=_decision_value(inputs, "3m"),
            yield_2y=_decision_value(inputs, "2y"),
            yield_5y=inputs["5y"].value,
            yield_10y=_decision_value(inputs, "10y"),
            yield_30y=inputs["30y"].value,
        )
        spreads = calculate_yield_curve_spreads(yields)
        credit = classify_credit_stress(
            _decision_value(inputs, "hy_oas"),
            elevated_threshold=self.config.hy_oas_elevated_threshold,
            critical_threshold=self.config.hy_oas_critical_threshold,
        )
        t10yie, t5yie = inputs["t10yie"].value, inputs["t5yie"].value
        inflation = classify_inflation_expectations(t10yie, t5yie) if t10yie is not None and t5yie is not None else None
        vix = _decision_value(inputs, "vix")
        stress = compute_compound_macro_stress(vix, spreads, credit, self.config)

        inflation_text = f"{inflation.breakeven_10y:.2f}%" if inflation is not None else "N/A"
        summary = (
            f"Macro Stress: {stress.level.value} (Risk Multiplier: {stress.risk_multiplier:.2f}x) | "
            f"Curve: {spreads.regime.value} (10Y-2Y: {spreads.slope_10y_2y_bps:+.1f} bps) | "
            f"Credit OAS: {credit.high_yield_oas_bps:.0f} bps ({credit.regime.value}) | "
            f"10Y Inflation: {inflation_text} | VIX: {vix:.2f}"
        )
        return MacroIntelligenceReport(
            yields=yields,
            spreads=spreads,
            credit=credit,
            inflation=inflation,
            vix=vix,
            dxy=inputs["dxy"].value,
            stress=stress,
            timestamp=now,
            summary_text=summary,
            observation_dates={
                item.source.removeprefix("fred:"): item.observed_on.isoformat()
                for item in inputs.values()
                if item.source.startswith("fred:") and item.observed_on is not None
            },
        )
