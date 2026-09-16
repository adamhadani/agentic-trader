"""Training-only statistical/ML baselines under the discovery split contract.

These are comparison experiments, not deployable DSL versions. Each parameter
choice consumes one trial. Transform fitting and model selection never see holdout.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio, calculate_rank_ic
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import return_statistics, simulate_strategy
from agentic_trader.research.alpha.strategy import normalize_scores
from agentic_trader.research.alpha.validation import ValidationPolicy, frame_digest, purged_folds, validate_sampling


ECONOMIC_FEATURES = (
    "roc(close,5)",
    "roc(close,20)",
    "realized_vol(returns,20)",
    "zscore(close,20)",
    "ts_rank(close*volume,20)",
    "open_gap",
    "ts_residual(close,20)/close",
)


def benchmark_models(
    bars: pd.DataFrame, *, timeframe: str, method: str, seed: int, budget: int, policy: ValidationPolicy | None = None
):
    if method not in ("ridge", "boosted") or not 1 <= budget <= 100:
        raise ValueError("Invalid baseline method/budget")
    policy = policy or ValidationPolicy()
    validate_sampling(bars, timeframe)
    folds = purged_folds(len(bars), policy)
    holdout = int(len(bars) * (1 - policy.holdout_fraction))
    discovery = bars.iloc[:holdout]
    evaluator = AlphaExpressionEvaluator()
    features = pd.DataFrame({expr: evaluator.evaluate(expr, discovery) for expr in ECONOMIC_FEATURES})
    close = discovery.rename(columns=str.lower).close
    labels = close.shift(-policy.label_horizon) / close - 1
    trials: list[dict[str, Any]] = []
    for trial in range(budget):
        alpha = float(np.logspace(-3, 3, budget)[trial])
        estimator = (
            make_pipeline(StandardScaler(), Ridge(alpha=alpha))
            if method == "ridge"
            else HistGradientBoostingRegressor(
                max_iter=50 + 10 * (trial % 5),
                max_leaf_nodes=3 + 2 * (trial // 5),
                learning_rate=0.05,
                l2_regularization=alpha,
                random_state=seed,
                early_stopping=False,
            )
        )
        simulations = []
        forecast_parts = []
        label_parts = []
        for fold in folds:
            train = features.iloc[: fold.train_end]
            target = labels.iloc[: fold.train_end]
            mask = train.notna().all(axis=1) & target.notna()
            if mask.sum() < 30:
                raise ValueError("Insufficient training observations")
            estimator.fit(train.loc[mask], target.loc[mask])
            # Warm up normalization using training predictions from this same
            # frozen model; evaluate only subsequent validation observations.
            available = features.notna().all(axis=1)
            predictions = pd.Series(np.nan, index=features.index)
            predictions.loc[available] = estimator.predict(features.loc[available])
            scores = normalize_scores(predictions)
            definition = AlphaDefinition(
                f"alpha_baseline_{method}_{trial}", "Research-only model baseline", "close", timeframe=timeframe
            )
            simulations.append(
                simulate_strategy(
                    definition, discovery, start=fold.validation_start, end=fold.validation_end, scores=scores
                )
            )
            forecast_parts.append(predictions.iloc[fold.validation_start : fold.validation_end - policy.label_horizon])
            label_parts.append(labels.iloc[fold.validation_start : fold.validation_end - policy.label_horizon])
        returns = pd.concat([simulation["net_returns"] for simulation in simulations])
        metrics = return_statistics(returns, [t for sim in simulations for t in sim["trades"]])
        ic, _, _ = calculate_rank_ic(pd.concat(forecast_parts), pd.concat(label_parts))
        trials.append(
            {
                "method": method,
                "trial": trial,
                "regularization": alpha,
                "status": "research_only_model",
                "metrics": {k: v for k, v in metrics.items() if k not in ("net_returns", "trades", "entries")},
                "rank_ic": ic,
            }
        )
    variance = float(np.var([t["metrics"]["per_bar_sharpe"] for t in trials], ddof=1)) if budget > 1 else 0
    for trial_result in trials:
        metrics = trial_result["metrics"]
        metrics["dsr"] = calculate_deflated_sharpe_ratio(
            metrics["per_bar_sharpe"],
            budget,
            variance,
            metrics["sample_length"],
            metrics["skewness"],
            metrics["kurtosis"],
        )
    return {
        "method": method,
        "seed": seed,
        "trial_count": budget,
        "discovery_hash": frame_digest(discovery),
        "holdout_start": holdout,
        "trials": trials,
        "deployable": False,
    }
