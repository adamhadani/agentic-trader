from dataclasses import dataclass, field
from typing import Any


@dataclass
class WalkForwardFold:
    """Out-of-sample walk-forward evaluation fold."""

    fold_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_parameters: dict[str, Any]
    train_return_pct: float
    test_return_pct: float
    train_sharpe: float
    test_sharpe: float
    wfe_ratio: float


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
    is_return_pct: float | None = None
    oos_return_pct: float | None = None
    is_sharpe: float | None = None
    oos_sharpe: float | None = None
    wfe_ratio: float | None = None


@dataclass
class OptimizationResult:
    """Aggregated optimization output containing tested candidates ranked by risk-adjusted return."""

    symbol: str
    strategy: str
    lookback: str
    engine_used: str
    total_combinations_tested: int
    ranked_candidates: list[ParameterCandidate] = field(default_factory=list)
    is_walk_forward: bool = False
    walk_forward_folds: list[WalkForwardFold] = field(default_factory=list)
    avg_wfe_ratio: float | None = None
