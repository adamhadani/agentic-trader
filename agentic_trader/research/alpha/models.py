from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agentic_trader.execution.lifetime_policy import daily_entry_lifetime
from agentic_trader.market.bars import (
    FIXED_DAILY_CLOCK_LAYOUT,
    SESSION_BAR_LAYOUT,
    FixedDailyClockPolicy,
    SessionClockPolicy,
)
from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.alpha.strategy import (
    NORMALIZATION_WINDOW,
    TIMEFRAME_FIELDS,
    AlphaExecutionPolicy,
    TimedAlphaExecutionPolicy,
    execution_policy_from_dict,
)


DAILY_SESSION_SEMANTICS_VERSION = 5


class DecisionStatus(StrEnum):
    CLAIMED = "claimed"
    SCORED = "scored"
    UNAVAILABLE = "unavailable"
    MISSED = "missed"
    SUPERSEDED = "superseded"
    INTERRUPTED = "interrupted"


class AlphaOrigin(StrEnum):
    """Origin source of the formulaic alpha."""

    WORLDQUANT_101 = "worldquant_101"
    MINED = "mined"
    MANUAL = "manual"
    FACTOR_LIBRARY = "factor_library"


@dataclass(frozen=True)
class AlphaDefinition:
    """Core definition and hyperparameters of a formulaic alpha expression."""

    alpha_id: str
    name: str
    expression: str
    description: str = ""
    direction: str = "bi_directional"  # "long", "short", "bi_directional"
    entry_threshold: float = 1.5  # z-score or normalized trigger value
    timeframe: str = "4h"
    origin: str = AlphaOrigin.MINED
    eligible_symbols: tuple[str, ...] | None = None
    normalization_window: int = NORMALIZATION_WINDOW
    execution: AlphaExecutionPolicy = field(default_factory=AlphaExecutionPolicy)
    semantics_version: int = 2
    data_feed: str = "unverified"
    adjustment: str = "raw"
    clock: SessionClockPolicy | FixedDailyClockPolicy | None = None

    def __post_init__(self):
        compile_expression(self.expression)
        object.__setattr__(self, "entry_threshold", float(self.entry_threshold))
        if self.timeframe not in TIMEFRAME_FIELDS or self.direction not in ("long", "short", "bi_directional"):
            raise ValueError("Unsupported timeframe/direction")
        if not math.isfinite(self.entry_threshold) or self.entry_threshold <= 0:
            raise ValueError("Invalid alpha thresholds")
        if (
            type(self.normalization_window) is not int
            or not 2 <= self.normalization_window <= 252
            or type(self.semantics_version) is not int
            or self.semantics_version not in (2, 3, 4, DAILY_SESSION_SEMANTICS_VERSION)
        ):
            raise ValueError("Invalid normalization or semantics version")
        if (
            (self.semantics_version == 2 and self.clock is not None)
            or (
                self.semantics_version == 3
                and (
                    not isinstance(self.clock, SessionClockPolicy)
                    or self.data_feed not in ("alpaca:iex", "alpaca:sip")
                    or self.adjustment != "raw"
                )
            )
            or (
                self.semantics_version == 4
                and (
                    not isinstance(self.clock, FixedDailyClockPolicy)
                    or self.timeframe != "1d"
                    or self.data_feed != "synthetic"
                    or self.adjustment != "raw"
                )
            )
        ):
            raise ValueError(
                "Version 3 requires an explicit raw Alpaca session clock; "
                "version 4 requires an explicit raw synthetic fixed-daily clock; version 2 is fixed-duration"
            )
        if self.semantics_version == DAILY_SESSION_SEMANTICS_VERSION and (
            self.timeframe != "1d"
            or self.clock is not None
            or not isinstance(self.execution, TimedAlphaExecutionPolicy)
            or self.execution.lifetime != daily_entry_lifetime()
        ):
            raise ValueError(
                "Version 5 requires an unclocked native daily definition with the fixed one-session entry lifetime"
            )
        if (
            isinstance(self.execution, TimedAlphaExecutionPolicy)
            and self.clock is None
            and self.semantics_version != DAILY_SESSION_SEMANTICS_VERSION
        ):
            raise ValueError("Timed execution requires a versioned session clock")
        if self.eligible_symbols is not None:
            object.__setattr__(
                self, "eligible_symbols", tuple(sorted({s.strip().upper() for s in self.eligible_symbols if s.strip()}))
            )

    @property
    def version_id(self) -> str:
        payload = self.to_dict()
        payload["expression"] = ast.dump(compile_expression(self.expression).tree, include_attributes=False)
        for descriptive in ("name", "description", "origin"):
            payload.pop(descriptive)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["origin"] = str(self.origin.value if hasattr(self.origin, "value") else self.origin)
        d["eligible_symbols"] = list(self.eligible_symbols) if self.eligible_symbols else None
        # Version 2's persisted document and hash are immutable financial identity.
        if self.semantics_version == 2:
            d.pop("clock")
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlphaDefinition:
        symbols_raw = data.get("eligible_symbols")
        eligible_symbols = tuple(str(s).upper() for s in symbols_raw) if symbols_raw else None
        clock_data = data.get("clock")
        clock: SessionClockPolicy | FixedDailyClockPolicy | None
        if clock_data is None:
            clock = None
        elif clock_data.get("bar_layout", SESSION_BAR_LAYOUT) == SESSION_BAR_LAYOUT:
            clock = SessionClockPolicy(**clock_data)
        elif clock_data.get("bar_layout") == FIXED_DAILY_CLOCK_LAYOUT:
            clock = FixedDailyClockPolicy(**clock_data)
        else:
            raise ValueError("Unsupported alpha clock layout")
        return cls(
            alpha_id=str(data["alpha_id"]),
            name=str(data.get("name", data["alpha_id"])),
            expression=str(data["expression"]),
            description=str(data.get("description", "")),
            direction=str(data.get("direction", "bi_directional")),
            entry_threshold=float(data.get("entry_threshold", 1.5)),
            timeframe=str(data.get("timeframe", "4h")),
            origin=str(data.get("origin", AlphaOrigin.MINED)),
            eligible_symbols=eligible_symbols,
            normalization_window=data.get("normalization_window", NORMALIZATION_WINDOW),
            execution=execution_policy_from_dict(data.get("execution", {})),
            semantics_version=data.get("semantics_version", 2),
            data_feed=str(data.get("data_feed", "unverified")),
            adjustment=str(data.get("adjustment", "raw")),
            clock=clock,
        )


@dataclass
class AlphaEvaluationMetrics:
    """Quantitative performance and statistical metrics for an alpha candidate."""

    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    rank_ic_ir: float = 0.0
    sharpe_is: float = 0.0
    sharpe_oos: float = 0.0
    dsr: float = 0.0  # Deflated Sharpe Ratio (0.0 to 1.0)
    win_rate: float = 0.0
    profit_factor: float | None = None
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    annualized_return_pct: float | None = 0.0
    per_bar_sharpe: float = 0.0
    sample_length: int = 0
    skewness: float = 0.0
    kurtosis: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlphaEvaluationMetrics:
        return cls(
            rank_ic_mean=float(data.get("rank_ic_mean", 0.0)),
            rank_ic_std=float(data.get("rank_ic_std", 0.0)),
            rank_ic_ir=float(data.get("rank_ic_ir", 0.0)),
            sharpe_is=float(data.get("sharpe_is", 0.0)),
            sharpe_oos=float(data.get("sharpe_oos", 0.0)),
            dsr=float(data.get("dsr", 0.0)),
            win_rate=float(data.get("win_rate", 0.0)),
            profit_factor=float(data["profit_factor"]) if data.get("profit_factor") is not None else None,
            max_drawdown_pct=float(data.get("max_drawdown_pct", 0.0)),
            total_trades=int(data.get("total_trades", 0)),
            annualized_return_pct=float(data["annualized_return_pct"])
            if data.get("annualized_return_pct") is not None
            else None,
            per_bar_sharpe=float(data.get("per_bar_sharpe", 0)),
            sample_length=int(data.get("sample_length", 0)),
            skewness=float(data.get("skewness", 0)),
            kurtosis=float(data.get("kurtosis", 3)),
        )


@dataclass
class AlphaCandidate:
    """Discovered or tested alpha candidate with evaluation metrics and correlation profile."""

    definition: AlphaDefinition
    metrics: AlphaEvaluationMetrics
    correlations: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition": self.definition.to_dict(),
            "metrics": self.metrics.to_dict(),
            "correlations": dict(self.correlations),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RegistrySnapshot:
    generation: int
    active: tuple[AlphaDefinition, ...]
    shadow: tuple[AlphaDefinition, ...]
    probe: tuple[AlphaDefinition, ...] = ()
