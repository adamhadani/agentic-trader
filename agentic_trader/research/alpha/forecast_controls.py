"""Matched, descriptive controls over retained causal forecasts; never refit or trade."""

from dataclasses import replace

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.forecast_controls_plan import (
    CONTROL_EXPRESSIONS,
    CONTROL_MODELS,
    ENDPOINT_SCOPES,
    FORECAST_CONTROLS_VERSION,
    ForecastControlsPlan,
)
from agentic_trader.research.alpha.panel_forecast import evaluate_forecast_trial, prepare_forecast_inputs
from agentic_trader.research.alpha.panel_study import PanelStudyStatus
from agentic_trader.research.alpha.retained_forecasts import (
    paired_basket_comparison,
    require_retained_ridge_match,
    validate_retained_forecasts,
)


def _long_only(scores):
    return pd.Series(1 / len(scores), index=scores.index)


def compute_forecast_controls(batch, clock, plan: ForecastControlsPlan, sessions, *, parent_result: dict):
    """Compare fixed controls on parent decision support; outcome scopes never select holdings."""
    parent = plan.parent
    inputs = prepare_forecast_inputs(batch, clock, parent, sessions)
    parent_trials, retained_scores = validate_retained_forecasts(parent_result, parent, inputs, clock)
    cohort = next(cohort for cohort in parent.cohorts if cohort.name == plan.cohort_name)
    symbols = list(cohort.symbols)
    evaluator = AlphaExpressionEvaluator()
    control_features = {
        name: pd.DataFrame({symbol: evaluator.evaluate(expression, inputs.panel.frames[symbol]) for symbol in symbols})
        for name, expression in CONTROL_EXPRESSIONS.items()
    }
    volume = pd.DataFrame({symbol: inputs.panel.frames[symbol].volume for symbol in symbols})
    endpoint_volume = volume.shift(-1).gt(0) & volume.shift(-parent.target.horizon_bars).gt(0)
    evaluation_plan = replace(parent, costs_bps=plan.costs_bps)
    supports, trials = [], []
    comparisons: list[dict] = []
    for fold in parent.folds:
        ridge = retained_scores[cohort.name, fold.name, "ridge"]
        dates = ridge.index
        matched = inputs.eligible.loc[dates, symbols] & np.isfinite(ridge)
        scores = {"ridge": ridge}
        for name, features in control_features.items():
            scores[name] = features.loc[dates, symbols].where(matched)
            if (matched & ~np.isfinite(scores[name])).to_numpy().any():
                raise ValueError("A declared control is unavailable on matched Ridge support")
        ranks = [scores[name].rank(axis=1, method="average", pct=True) for name in CONTROL_EXPRESSIONS]
        scores["rank_blend"] = sum(ranks) / len(ranks)
        scores["equal_weight_long_only"] = pd.DataFrame(1.0, index=dates, columns=symbols).where(matched)
        outcomes = inputs.labels.loc[dates, symbols].copy()
        outcomes.iloc[-parent.target.horizon_bars :] = np.nan
        scopes = dict(
            zip(ENDPOINT_SCOPES, (outcomes, outcomes.where(endpoint_volume.loc[dates, symbols])), strict=True)
        )
        supports.append(
            {
                "cohort": cohort.name,
                "fold": fold.name,
                "dates": [t.isoformat() for t in dates],
                "symbols": symbols,
                "parent_eligible": inputs.eligible.loc[dates, symbols].to_numpy().tolist(),
                "matched_eligible": matched.to_numpy().tolist(),
                "parent_eligible_symbol_decisions": int(inputs.eligible.loc[dates, symbols].to_numpy().sum()),
                "matched_symbol_decisions": int(matched.to_numpy().sum()),
                "refitted_models": 0,
            }
        )
        for scope, labels in scopes.items():
            evaluated = {}
            for model in CONTROL_MODELS:
                current = evaluate_forecast_trial(
                    scores[model],
                    labels,
                    cohort,
                    fold,
                    evaluation_plan,
                    weight_builder=_long_only if model == "equal_weight_long_only" else None,
                )
                current.update(model=model, outcome_scope=scope)
                if model == "ridge" and scope == ENDPOINT_SCOPES[0]:
                    require_retained_ridge_match(
                        current, parent_trials[cohort.name, fold.name, "ridge"], plan.costs_bps
                    )
                evaluated[model] = current
                trials.append(current)
            for model in CONTROL_MODELS[1:]:
                comparisons.extend(
                    paired_basket_comparison(evaluated["ridge"], evaluated[model], cost, strategy_label="ridge")
                    for cost in plan.costs_bps
                )
    return {
        "version": FORECAST_CONTROLS_VERSION,
        "status": PanelStudyStatus.COMPLETED,
        "coverage": inputs.panel.coverage,
        "supports": supports,
        "trials": trials,
        "paired_comparisons": comparisons,
        "charged_trials": plan.trial_count,
        "completed_comparisons": len(trials) * (1 + len(plan.costs_bps)),
        "refitted_models": 0,
        "authorizes_promotion": False,
        "limitations": [
            "Original finite-price training and fixed Ridge forecasts are unchanged; endpoint volume is an outcome sensitivity only.",
            "Matched support and gross notional do not match beta, sector, volatility, borrow or execution risk.",
            "Long-only equal-weight is directional context, not a risk-matched long/short competitor.",
            "Basket differences retain missing dates and are descriptive; no compounded spread or economic significance claim.",
            "The retained current cohort is survivor/liquidity conditioned; this is post-selection development, not validation.",
        ],
    }
