import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentic_trader.agent.macro import (
    MacroInput,
    MacroIntelligenceEngine,
    MacroIntelligenceReport,
    MacroStressAssessment,
    MacroStressLevel,
    missing_input_names,
)
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import (
    DEFAULT_MIN_RISK_REWARD_RATIO,
    VolatilityRegime,
)
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.ranker import finite_or_none


logger = logging.getLogger(__name__)

# With yield-curve enrichment disabled the regime reads only these (VIX decides; the 10Y and DXY are display).
_VIX_ONLY_INPUTS: tuple[str, ...] = ("vix", "10y", "dxy")
_ABSENT = object()


class RegimeUnavailable(Exception):
    """VIX is missing, so no regime can be evaluated.

    ``inputs`` holds every input read; ``missing`` names the missing ones in canonical order
    (decision inputs first). The message names the missing inputs only, never a provider's text.
    """

    def __init__(self, inputs: Mapping[str, MacroInput]):
        self.inputs: Mapping[str, MacroInput] = dict(inputs)
        self.missing: tuple[str, ...] = missing_input_names(inputs)
        super().__init__(f"Market regime unavailable: missing {', '.join(self.missing)}")

    def __reduce__(self) -> tuple[type[RegimeUnavailable], tuple[dict[str, MacroInput]]]:
        # ``args`` is the message, so the default reduction would rebuild ``cls(message)``; copy,
        # deepcopy and pickle rebuild from the inputs instead.
        return (type(self), (dict(self.inputs),))


@dataclass
class RegimeSnapshot:
    """Quantitative representation of current market volatility and macro regime.

    ``macro_stress`` is the measured or ``UNKNOWN`` assessment the policy applied, None only when
    yield-curve enrichment is disabled; ``macro_report`` is None unless every decision input was
    observed. ``macro_inputs`` holds every input read for this snapshot.
    """

    vix: float
    vix_regime: VolatilityRegime
    tnx: float | None
    dxy: float | None
    breakout_allowed: bool
    min_rr_threshold: float
    timestamp: datetime
    summary_text: str
    risk_multiplier: float
    macro_report: MacroIntelligenceReport | None = None
    macro_unavailable_reason: str | None = None
    macro_stress: MacroStressAssessment | None = None
    macro_inputs: Mapping[str, MacroInput] = field(default_factory=dict)


def _breakout_status(reg: RegimeSnapshot) -> str:
    """The prompt's breakout status, naming the cause of a suppression.

    Only an EXTREME VIX is volatility chop; a suppression by macro stress names the stress level
    (and, for ``UNKNOWN``, the missing decision inputs). A suppression with no recorded cause says so
    without inventing one.
    """
    if reg.breakout_allowed:
        return "ALLOWED"
    if reg.vix_regime is VolatilityRegime.EXTREME:
        return "SUPPRESSED (Extreme Volatility Chop / High False Breakout Risk)"
    stress = reg.macro_stress or (reg.macro_report.stress if reg.macro_report is not None else None)
    if stress is not None and not stress.squeeze_breakout_allowed:
        if stress.level is MacroStressLevel.UNKNOWN:
            missing = ", ".join(missing_input_names(reg.macro_inputs, decision_only=True))
            return f"SUPPRESSED (Macro Stress UNKNOWN: missing {missing})"
        return f"SUPPRESSED (Macro Stress {stress.level.value})"
    return "SUPPRESSED"


def _unknown_macro_text(stress: MacroStressAssessment, inputs: Mapping[str, MacroInput]) -> str:
    breakouts = "allowed" if stress.squeeze_breakout_allowed else "suppressed"
    missing = ", ".join(missing_input_names(inputs, decision_only=True))
    return f"Macro: UNKNOWN (missing {missing}; ×{stress.risk_multiplier:.2f}, breakouts {breakouts})"


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


REGIME_BLOCK_KEYS: tuple[str, ...] = (
    "vix",
    "vix_regime",
    "breakout_allowed",
    "risk_multiplier",
    "min_rr_threshold",
    "macro_stress",
    "observed_at",
    "macro_enabled",
    "inputs",
    "missing",
    "missing_decision",
    "clock_regime",
)


def _number(snapshot: Any, name: str) -> float | None:
    value = getattr(snapshot, name, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return finite_or_none(float(value))


def _input_record(item: MacroInput) -> dict[str, Any]:
    return {
        "value": finite_or_none(item.value),
        "source": item.source,
        "observed_on": item.observed_on.isoformat() if item.observed_on is not None else None,
        "decision": item.decision,
        "error": item.error,
    }


def _inputs_fields(inputs: Any) -> dict[str, Any]:
    """The ``inputs``, ``missing`` and ``missing_decision`` keys; None each without an inputs mapping."""
    if not isinstance(inputs, Mapping):
        return dict.fromkeys(("inputs", "missing", "missing_decision"))
    return {
        "inputs": {name: _input_record(item) for name, item in inputs.items()},
        "missing": list(missing_input_names(inputs)),
        "missing_decision": list(missing_input_names(inputs, decision_only=True)),
    }


def _clock_regime(timestamp: Any) -> str | None:
    """``EDT`` or ``EST``: New York's clock at an aware timestamp."""
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(ET_TZ).tzname()


def regime_journal_block(snapshot: Any) -> dict[str, Any]:
    """JSON-safe record of the regime a scan observed; every key present, missing values None.

    Scan-level evidence for ``scan_candidates_ranked`` and ``decision_provenance``: what the desk
    saw and gated on (the VIX bucket, breakout flag, risk multiplier, required reward/risk, macro
    stress) and every raw input with its source and observation date cannot be rebuilt from bars
    later, so it is journaled; the tape itself is derived by the outcomes report. ``macro_enabled``
    is ``macro_stress is not None`` (False under the VIX-only configuration); ``missing_decision``
    is the missing inputs with ``decision: true``. Never raises: a detector stub or a partial
    snapshot yields None fields.
    """
    try:
        regime = getattr(snapshot, "vix_regime", None)
        stress = getattr(snapshot, "macro_stress", _ABSENT)
        level = getattr(stress, "level", None) if stress is not _ABSENT else None
        allowed = getattr(snapshot, "breakout_allowed", None)
        timestamp = getattr(snapshot, "timestamp", None)
        return {
            "vix": _number(snapshot, "vix"),
            "vix_regime": str(getattr(regime, "value", regime)) if regime is not None else None,
            "breakout_allowed": bool(allowed) if allowed is not None else None,
            "risk_multiplier": _number(snapshot, "risk_multiplier"),
            "min_rr_threshold": _number(snapshot, "min_rr_threshold"),
            "macro_stress": str(getattr(level, "value", level)) if level is not None else None,
            "observed_at": timestamp.isoformat() if isinstance(timestamp, datetime) else None,
            "macro_enabled": stress is not None if stress is not _ABSENT else None,
            **_inputs_fields(getattr(snapshot, "macro_inputs", None)),
            "clock_regime": _clock_regime(timestamp),
        }
    except Exception as exc:
        logger.debug("Regime journal block unavailable: %s", type(exc).__name__)
        return dict.fromkeys(REGIME_BLOCK_KEYS)


def unavailable_regime_block(exc: BaseException, *, at: datetime, macro_enabled: bool) -> dict[str, Any]:
    """The regime block of a scan whose regime read raised: every key present, the snapshot fields None.

    ``inputs`` and the missing names come from a ``RegimeUnavailable``; any other exception has none.
    """
    inputs: Mapping[str, MacroInput] = exc.inputs if isinstance(exc, RegimeUnavailable) else {}
    return {
        **dict.fromkeys(REGIME_BLOCK_KEYS),
        "observed_at": at.isoformat(),
        "macro_enabled": macro_enabled,
        **_inputs_fields(inputs),
        "clock_regime": _clock_regime(at),
    }


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
        """Fetch real-time macro indicators and return the current market regime snapshot.

        A missing decision input applies the ``UNKNOWN`` stress policy; a missing VIX raises
        ``RegimeUnavailable``. Every snapshot, ``UNKNOWN`` included, is cached for ``cache_ttl_seconds``.
        """
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._cached_snapshot is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self.config.cache_ttl_seconds
        ):
            return self._cached_snapshot

        stress: MacroStressAssessment | None = None
        macro_report: MacroIntelligenceReport | None = None
        macro_unavailable_reason: str | None
        inputs: Mapping[str, MacroInput]
        if self.config.yield_curve_enabled:
            evaluation = await self.macro_engine.evaluate(force_refresh=force_refresh)
            inputs, stress, macro_report = evaluation.inputs, evaluation.stress, evaluation.report
            macro_unavailable_reason = (
                f"Missing decision inputs: {', '.join(evaluation.missing_decision)}"
                if evaluation.missing_decision
                else None
            )
        else:
            inputs = await self.macro_engine.read_inputs(_VIX_ONLY_INPUTS, force_refresh=force_refresh)
            macro_unavailable_reason = "Yield-curve enrichment is disabled in configuration"

        vix = inputs["vix"].value
        if vix is None:
            logger.warning(
                "Market regime unavailable: VIX missing",
                extra={
                    "event": "regime_unavailable",
                    "error": inputs["vix"].error,
                    "missing": list(missing_input_names(inputs)),
                },
            )
            raise RegimeUnavailable(inputs)
        tnx, dxy = inputs["10y"].value, inputs["dxy"].value

        regime = classify_vix_level(vix, self.config)

        # In extreme volatility panic regimes, suppress breakout expansions to prevent false whipsaws
        breakout_allowed = regime != VolatilityRegime.EXTREME

        # Modulate minimum required reward-to-risk ratio
        if regime == VolatilityRegime.EXTREME:
            min_rr = self.config.extreme_min_rr
        elif regime == VolatilityRegime.ELEVATED:
            min_rr = self.config.elevated_min_rr
        else:
            min_rr = DEFAULT_MIN_RISK_REWARD_RATIO

        # The measured or UNKNOWN stress applies the same way; only the VIX-only configuration is 1.0x.
        risk_multiplier = 1.0
        if stress is not None:
            risk_multiplier = stress.risk_multiplier
            breakout_allowed = breakout_allowed and stress.squeeze_breakout_allowed
            min_rr = max(min_rr, stress.min_rr_threshold)

        tnx_str = f"{tnx:.2f}%" if tnx is not None else "N/A"
        dxy_str = f"{dxy:.2f}" if dxy is not None else "N/A"
        breakout_str = "Allowed" if breakout_allowed else "Suppressed"
        summary = f"VIX: {vix:.2f} ({regime.value}) | 10Y Yield: {tnx_str} | Dollar (DXY): {dxy_str} | Breakouts: {breakout_str}"
        if stress is not None and stress.level is MacroStressLevel.UNKNOWN:
            summary += f" | {_unknown_macro_text(stress, inputs)}"

        snapshot = RegimeSnapshot(
            vix=vix,
            vix_regime=regime,
            tnx=tnx,
            dxy=dxy,
            breakout_allowed=breakout_allowed,
            min_rr_threshold=min_rr,
            timestamp=now,
            summary_text=summary,
            risk_multiplier=risk_multiplier,
            macro_report=macro_report,
            macro_unavailable_reason=macro_unavailable_reason,
            macro_stress=stress,
            macro_inputs=dict(inputs),
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
                "macro_stress": stress.level.value if stress is not None else None,
                "missing": list(missing_input_names(inputs)),
            },
        )
        return snapshot

    def get_prompt_context(self, regime: RegimeSnapshot | None = None) -> str:
        """Format regime snapshot for LLM trade evaluation prompts."""
        reg = regime or self._cached_snapshot
        if reg is None:
            return "Market Volatility & Macro Regime: unavailable; no observation has been loaded."

        status = _breakout_status(reg)
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
            inflation_str = (
                f"{m.inflation.breakeven_10y:.2f}% ({m.inflation.regime.value})" if m.inflation is not None else "N/A"
            )
            lines.extend(
                [
                    f"• US Treasury Yield Curve: {m.spreads.regime.value} (10Y-2Y Spread: {m.spreads.slope_10y_2y_bps:+.1f} bps, 10Y-3M Spread: {m.spreads.slope_10y_3m_bps:+.1f} bps)",
                    f"• Credit Risk (High Yield OAS): {m.credit.high_yield_oas_bps:.0f} bps ({m.credit.regime.value})",
                    f"• 10Y Breakeven Inflation: {inflation_str}",
                    f"• Compound Macro Stress: {m.stress.level.value} (Risk Multiplier: {m.stress.risk_multiplier:.2f}x)",
                ]
            )
        elif reg.macro_stress is not None and reg.macro_stress.level is MacroStressLevel.UNKNOWN:
            missing = ", ".join(missing_input_names(reg.macro_inputs, decision_only=True))
            lines.append(
                f"• Compound Macro Stress: UNKNOWN (missing {missing}; Risk Multiplier: {reg.macro_stress.risk_multiplier:.2f}x)"
            )

        lines.append(
            f"• Strategy Adaptation: Squeeze Breakouts {status}; Minimum required Reward-to-Risk: {reg.min_rr_threshold:.1f}:1."
        )
        return "\n".join(lines)
