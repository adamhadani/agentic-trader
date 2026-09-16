from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.alpha.strategy import NORMALIZATION_WINDOW, TIMEFRAME_FIELDS, AlphaExecutionPolicy


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
            or self.semantics_version != 2
        ):
            raise ValueError("Invalid normalization or semantics version")
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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlphaDefinition:
        symbols_raw = data.get("eligible_symbols")
        eligible_symbols = tuple(str(s).upper() for s in symbols_raw) if symbols_raw else None
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
            execution=AlphaExecutionPolicy(**data.get("execution", {})),
            semantics_version=int(data.get("semantics_version", 2)),
            data_feed=str(data.get("data_feed", "unverified")),
            adjustment=str(data.get("adjustment", "raw")),
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
    annualized_return_pct: float = 0.0
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
            annualized_return_pct=float(data.get("annualized_return_pct", 0.0)),
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
