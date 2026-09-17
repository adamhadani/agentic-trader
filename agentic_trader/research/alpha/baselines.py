"""Causal forecast-component benchmarks, independent of trade execution policy.

These diagnostics cannot qualify a strategy. Every feature/model parameter choice
is charged as a trial; forecasts and their matured labels stay inside discovery.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy, PolicyScenario, evaluate_daily_policy
from agentic_trader.research.alpha.targets import ForecastTarget, forecast_labels
from agentic_trader.research.alpha.validation import ValidationPolicy, frame_digest, purged_folds, validate_sampling


FORECAST_BENCHMARK_VERSION = "forecast_components_v2"
MAX_FORECAST_FEATURES = 32
MAX_BENCHMARK_TRIALS = 100
MIN_TRAINING_OBSERVATIONS = 30
BENCHMARK_METHODS = ("single", "ridge", "boosted")
ECONOMIC_FEATURES = (
    "roc(close,5)",
    "roc(close,20)",
    "realized_vol(returns,20)",
    "zscore(close,20)",
    "ts_rank(close*volume,20)",
    "open_gap",
    "ts_residual(close,20)/close",
)


@dataclass(frozen=True)
class ForecastBenchmarkPlan:
    target: ForecastTarget
    method: str = "ridge"
    budget: int = 5
    seed: int = 20260917
    validation: ValidationPolicy = field(default_factory=ValidationPolicy)
    features: tuple[str, ...] = ECONOMIC_FEATURES
    execution: DailyLongFlatPolicy | None = None

    def __post_init__(self):
        if self.method not in BENCHMARK_METHODS:
            raise ValueError("Invalid forecast benchmark method")
        if not isinstance(self.features, tuple) or not 1 <= len(self.features) <= MAX_FORECAST_FEATURES:
            raise ValueError("A bounded immutable feature tuple is required")
        canonical = tuple(ast.unparse(compile_expression(expr).tree) for expr in self.features)
        if len(set(canonical)) != len(canonical):
            raise ValueError("Duplicate forecast features")
        object.__setattr__(self, "features", canonical)
        if self.execution is not None and (self.target.timeframe != "1d" or self.target.horizon_bars != 1):
            raise ValueError("Execution proxy requires a daily one-bar horizon")
        maximum = len(self.features) if self.method == "single" else MAX_BENCHMARK_TRIALS
        if type(self.budget) is not int or not 1 <= self.budget <= maximum:
            raise ValueError("Invalid forecast benchmark budget")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("Invalid forecast benchmark seed")

    @property
    def trial_count(self):
        return self.budget * (1 + (len(self.execution.costs_bps) if self.execution is not None else 0))

    def document(self):
        return {
            "version": FORECAST_BENCHMARK_VERSION,
            "target": self.target.document(),
            "method": self.method,
            "budget": self.budget,
            "seed": self.seed,
            "validation": asdict(self.validation),
            "features": list(self.features),
            "execution": self.execution.document() if self.execution is not None else None,
            "charged_trials": self.trial_count,
            "minimum_training_observations": MIN_TRAINING_OBSERVATIONS,
            "trials": [_parameters(self, trial) for trial in range(self.budget)],
        }

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True).encode()).hexdigest()


@dataclass
class ForecastTrial:
    trial: int
    parameters: dict
    predictions: pd.DataFrame
    folds: list[dict]
    metrics: dict
    execution: list[PolicyScenario] = field(default_factory=list)

    def document(self):
        return {
            "trial": self.trial,
            "parameters": self.parameters,
            "folds": self.folds,
            "metrics": self.metrics,
            "prediction_hash": frame_digest(self.predictions),
            "execution": [scenario.document() for scenario in self.execution],
        }


@dataclass
class ForecastBenchmark:
    plan: ForecastBenchmarkPlan
    discovery_hash: str
    holdout_start: int
    trials: list[ForecastTrial]

    def document(self):
        return {
            "plan": self.plan.document(),
            "plan_id": self.plan.identity,
            "target": self.plan.target.document(),
            "trial_count": self.plan.trial_count,
            "model_trial_count": len(self.trials),
            "discovery_hash": self.discovery_hash,
            "holdout_start": self.holdout_start,
            "trials": [trial.document() for trial in self.trials],
            "authorizes_promotion": False,
        }


def forecast_metrics(observations: pd.DataFrame) -> dict:
    """Paired forecast/baseline comparison; undefined statistics stay unavailable.

    Rank IC here is pooled time-series Spearman correlation, not cross-sectional
    IC or an independence-adjusted significance test. Fold evidence is separate.
    """
    paired = observations[["prediction", "target", "training_mean"]].replace([np.inf, -np.inf], np.nan).dropna()
    if paired.empty:
        return {"observations": 0, "rank_ic": None, "rmse": None, "baseline_rmse": None, "skill_vs_training_mean": None}
    errors = paired.prediction - paired.target
    baseline_errors = paired.training_mean - paired.target
    mse, baseline_mse = float((errors**2).mean()), float((baseline_errors**2).mean())
    rank_ic = None
    if len(paired) > 1 and paired.prediction.nunique() > 1 and paired.target.nunique() > 1:
        rank_ic = float(paired.prediction.corr(paired.target, method="spearman"))
    return {
        "observations": len(paired),
        "rank_ic": rank_ic,
        "rmse": float(np.sqrt(mse)),
        "baseline_rmse": float(np.sqrt(baseline_mse)),
        "skill_vs_training_mean": 1 - mse / baseline_mse if baseline_mse > 0 else None,
    }


def _parameters(plan: ForecastBenchmarkPlan, trial: int):
    if plan.method == "single":
        return {"features": [plan.features[trial]]}
    regularization = float(np.logspace(-3, 3, plan.budget)[trial])
    parameters: dict[str, Any] = {"features": list(plan.features), "regularization": regularization}
    if plan.method == "boosted":
        parameters.update(max_iter=50 + 10 * (trial % 5), max_leaf_nodes=3 + 2 * (trial // 5), learning_rate=0.05)
    return parameters


def _estimator(plan: ForecastBenchmarkPlan, parameters: dict):
    if plan.method == "single":
        return make_pipeline(StandardScaler(), LinearRegression())
    if plan.method == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=parameters["regularization"]))
    return HistGradientBoostingRegressor(
        max_iter=parameters["max_iter"],
        max_leaf_nodes=parameters["max_leaf_nodes"],
        learning_rate=parameters["learning_rate"],
        l2_regularization=parameters["regularization"],
        random_state=plan.seed,
        early_stopping=False,
    )


def benchmark_models(bars: pd.DataFrame, plan: ForecastBenchmarkPlan) -> ForecastBenchmark:
    validate_sampling(bars, plan.target.timeframe)
    horizon = plan.target.horizon_bars
    # The target, not a bracket simulator or another strategy's label horizon,
    # determines the purge. Holdout remains exactly the predeclared fraction.
    folds = purged_folds(len(bars), replace(plan.validation, label_horizon=horizon))
    holdout = int(len(bars) * (1 - plan.validation.holdout_fraction))
    discovery = bars.iloc[:holdout]
    evaluator = AlphaExpressionEvaluator()
    features = pd.DataFrame({expr: evaluator.evaluate(expr, discovery) for expr in plan.features})
    features = features.replace([np.inf, -np.inf], np.nan)
    labels = forecast_labels(discovery, plan.target)
    trials = []
    for trial in range(plan.budget):
        parameters = _parameters(plan, trial)
        selected = features[parameters["features"]]
        predictions, evidence = [], []
        for fold_number, fold in enumerate(folds):
            train = selected.iloc[: fold.train_end]
            target = labels.iloc[: fold.train_end]
            mask = train.notna().all(axis=1) & target.notna()
            if mask.sum() < MIN_TRAINING_OBSERVATIONS:
                raise ValueError("Insufficient complete training observations")
            estimator = _estimator(plan, parameters)
            estimator.fit(train.loc[mask], target.loc[mask])
            # Keep every eligible validation row; do not fill holes or lose the
            # coverage denominator. Labels must mature within this same fold.
            end = fold.validation_end - horizon
            if end <= fold.validation_start:
                raise ValueError("Forecast horizon leaves no matured validation labels")
            validation = selected.iloc[fold.validation_start : end]
            available = validation.notna().all(axis=1)
            predicted = pd.Series(np.nan, index=validation.index)
            if available.any():
                predicted.loc[available] = estimator.predict(validation.loc[available])
            observations = pd.DataFrame(
                {
                    "prediction": predicted,
                    "target": labels.reindex(validation.index),
                    "training_mean": float(target.loc[mask].mean()),
                    "fold": fold_number,
                }
            )
            last_training = int(np.flatnonzero(mask.to_numpy())[-1])
            scored = np.flatnonzero(observations.notna().all(axis=1).to_numpy())
            evidence.append(
                {
                    **asdict(fold),
                    "training_observations": int(mask.sum()),
                    "last_training_feature_position": last_training,
                    "last_training_label_position": last_training + horizon,
                    "last_scored_label_position": int(fold.validation_start + scored[-1] + horizon)
                    if len(scored)
                    else None,
                    "validation_rows": len(validation),
                    "available_predictions": int(available.sum()),
                    "metrics": forecast_metrics(observations),
                }
            )
            predictions.append(observations)
        combined = pd.concat(predictions)
        policy_results = (
            evaluate_daily_policy(discovery, combined, evidence, plan.execution) if plan.execution is not None else []
        )
        trials.append(ForecastTrial(trial, parameters, combined, evidence, forecast_metrics(combined), policy_results))
    return ForecastBenchmark(plan, frame_digest(discovery), holdout, trials)
