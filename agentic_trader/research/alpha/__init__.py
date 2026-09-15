from __future__ import annotations

from agentic_trader.research.alpha.catalog import (
    INSTITUTIONAL_ALPHA_CATALOG,
    AlphaCatalog,
)
from agentic_trader.research.alpha.dsl import (
    AlphaDSLSyntaxError,
    AlphaExpressionEvaluator,
)
from agentic_trader.research.alpha.metrics import (
    calculate_cross_strategy_correlations,
    calculate_deflated_sharpe_ratio,
    calculate_rank_ic,
    simulate_alpha_performance,
)
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import (
    AlphaCandidate,
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaOrigin,
    AlphaStatus,
    PromotedAlphaRecord,
)
from agentic_trader.research.alpha.operators import ALPHA_OPERATORS
from agentic_trader.research.alpha.optimizer import (
    ConvexAlphaPortfolioOptimizer,
    PortfolioOptimizationResult,
)
from agentic_trader.research.alpha.orthogonalization import (
    build_factor_annihilator,
    evaluate_residual_predictive_power,
    factor_neutralize,
    gram_schmidt_orthogonalize,
)
from agentic_trader.research.alpha.promotion import (
    DEFAULT_PROMOTED_ALPHAS_PATH,
    AlphaPromotionManager,
)


__all__ = [
    "ALPHA_OPERATORS",
    "DEFAULT_PROMOTED_ALPHAS_PATH",
    "INSTITUTIONAL_ALPHA_CATALOG",
    "AlphaCandidate",
    "AlphaCatalog",
    "AlphaDSLSyntaxError",
    "AlphaDefinition",
    "AlphaEvaluationMetrics",
    "AlphaExpressionEvaluator",
    "AlphaMiner",
    "AlphaOrigin",
    "AlphaPromotionManager",
    "AlphaStatus",
    "ConvexAlphaPortfolioOptimizer",
    "PortfolioOptimizationResult",
    "PromotedAlphaRecord",
    "build_factor_annihilator",
    "calculate_cross_strategy_correlations",
    "calculate_deflated_sharpe_ratio",
    "calculate_rank_ic",
    "evaluate_residual_predictive_power",
    "factor_neutralize",
    "gram_schmidt_orthogonalize",
    "simulate_alpha_performance",
]
