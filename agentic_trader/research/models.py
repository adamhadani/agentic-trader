from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParameterCandidate:
    """Represents a specific hyperparameter configuration and its evaluated performance."""

    parameters: dict[str, Any]
    total_return_pct: float
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int


@dataclass
class OptimizationResult:
    """Aggregated optimization output containing tested candidates ranked by risk-adjusted return."""

    symbol: str
    strategy: str
    lookback: str
    engine_used: str
    total_combinations_tested: int
    ranked_candidates: list[ParameterCandidate] = field(default_factory=list)
