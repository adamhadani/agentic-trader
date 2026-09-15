from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AlphaOrigin(StrEnum):
    """Origin source of the formulaic alpha."""

    WORLDQUANT_101 = "worldquant_101"
    MINED = "mined"
    MANUAL = "manual"
    FACTOR_LIBRARY = "factor_library"


class AlphaStatus(StrEnum):
    """Lifecycle status of a formulaic alpha."""

    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    DEMOTED = "demoted"
    RETIRED = "retired"


@dataclass
class AlphaDefinition:
    """Core definition and hyperparameters of a formulaic alpha expression."""

    alpha_id: str
    name: str
    expression: str
    description: str = ""
    direction: str = "bi_directional"  # "long", "short", "bi_directional"
    entry_threshold: float = 1.5  # z-score or normalized trigger value
    exit_threshold: float = 0.0  # signal decay exit point
    timeframe: str = "4h"
    origin: str = AlphaOrigin.MINED
    eligible_symbols: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["origin"] = str(self.origin.value if hasattr(self.origin, "value") else self.origin)
        d["eligible_symbols"] = list(self.eligible_symbols) if self.eligible_symbols else None
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlphaDefinition:
        symbols_raw = data.get("eligible_symbols")
        eligible_symbols = [str(s).upper() for s in symbols_raw] if symbols_raw else None
        return cls(
            alpha_id=str(data["alpha_id"]),
            name=str(data.get("name", data["alpha_id"])),
            expression=str(data["expression"]),
            description=str(data.get("description", "")),
            direction=str(data.get("direction", "bi_directional")),
            entry_threshold=float(data.get("entry_threshold", 1.5)),
            exit_threshold=float(data.get("exit_threshold", 0.0)),
            timeframe=str(data.get("timeframe", "4h")),
            origin=str(data.get("origin", AlphaOrigin.MINED)),
            eligible_symbols=eligible_symbols,
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
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    annualized_return_pct: float = 0.0

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
            profit_factor=float(data.get("profit_factor", 0.0)),
            max_drawdown_pct=float(data.get("max_drawdown_pct", 0.0)),
            total_trades=int(data.get("total_trades", 0)),
            annualized_return_pct=float(data.get("annualized_return_pct", 0.0)),
        )


@dataclass
class AlphaCandidate:
    """Discovered or tested alpha candidate with evaluation metrics and correlation profile."""

    definition: AlphaDefinition
    metrics: AlphaEvaluationMetrics
    correlations: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition": self.definition.to_dict(),
            "metrics": self.metrics.to_dict(),
            "correlations": dict(self.correlations),
        }


@dataclass
class PromotedAlphaRecord:
    """Audit record for a formulaic alpha promoted to production desk execution."""

    alpha_id: str
    definition: AlphaDefinition
    metrics: AlphaEvaluationMetrics | None = None
    promoted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    promoted_by: str = "cli_operator"
    allocation_weight: float = 0.10
    status: str = AlphaStatus.PROMOTED
    notes: str = ""
    eligible_symbols: list[str] | None = None

    def __post_init__(self) -> None:
        if self.eligible_symbols and not self.definition.eligible_symbols:
            self.definition.eligible_symbols = list(self.eligible_symbols)
        elif self.definition.eligible_symbols and not self.eligible_symbols:
            self.eligible_symbols = list(self.definition.eligible_symbols)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "definition": self.definition.to_dict(),
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "promoted_at": self.promoted_at,
            "promoted_by": self.promoted_by,
            "allocation_weight": self.allocation_weight,
            "status": str(self.status.value if hasattr(self.status, "value") else self.status),
            "notes": self.notes,
            "eligible_symbols": list(self.eligible_symbols) if self.eligible_symbols else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromotedAlphaRecord:
        metrics_raw = data.get("metrics")
        metrics = AlphaEvaluationMetrics.from_dict(metrics_raw) if metrics_raw else None
        defn = AlphaDefinition.from_dict(data["definition"])
        symbols_raw = data.get("eligible_symbols") or defn.eligible_symbols
        eligible_symbols = [str(s).upper() for s in symbols_raw] if symbols_raw else None
        return cls(
            alpha_id=str(data["alpha_id"]),
            definition=defn,
            metrics=metrics,
            promoted_at=str(data.get("promoted_at", datetime.now(UTC).isoformat())),
            promoted_by=str(data.get("promoted_by", "cli_operator")),
            allocation_weight=float(data.get("allocation_weight", 0.10)),
            status=str(data.get("status", AlphaStatus.PROMOTED)),
            notes=str(data.get("notes", "")),
            eligible_symbols=eligible_symbols,
        )
