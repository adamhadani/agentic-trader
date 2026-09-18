"""Fixed factor scores composed through retained forecasts and shared evaluation kernels."""

from dataclasses import replace

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.factor_controls_plan import (
    FACTOR_BENCHMARK,
    FACTOR_CONTROLS_VERSION,
    FACTOR_MODELS,
    FACTOR_OUTCOME_SCOPE,
    FactorControlsPlan,
)
from agentic_trader.research.alpha.factor_features import residual_momentum_features
from agentic_trader.research.alpha.forecast_controls_plan import CONTROL_EXPRESSIONS
from agentic_trader.research.alpha.panel_forecast import evaluate_forecast_trial, prepare_forecast_inputs
from agentic_trader.research.alpha.panel_study import PanelStudyStatus
from agentic_trader.research.alpha.retained_forecasts import (
    paired_basket_comparison,
    paired_ic_comparison,
    require_retained_ridge_match,
    validate_retained_forecasts,
)


def _basket_exposures(trial, features, factors):
    rows = []
    for basket in trial["baskets"]:
        timestamp = pd.Timestamp(basket["decision_bar"])
        weights = pd.Series(basket["weights"], dtype=float)
        held = weights[weights.ne(0)]
        loadings = pd.DataFrame(
            {symbol: features.loadings[symbol].loc[timestamp, list(factors)] for symbol in held.index}
        ).T
        missing = list(loadings.index[~np.isfinite(loadings).all(axis=1)])
        observed = not missing
        net = {factor: float(held @ loadings[factor]) if len(held) else 0.0 for factor in factors} if observed else None
        absolute = (
            {factor: float(held.abs() @ loadings[factor].abs()) if len(held) else 0.0 for factor in factors}
            if observed
            else None
        )
        rows.append(
            {
                "cohort": trial["cohort"],
                "fold": trial["fold"],
                "model": trial["model"],
                "decision_bar": basket["decision_bar"],
                "loading_fit_end_exclusive": basket["decision_bar"],
                "status": "unavailable" if not observed else "observed" if len(held) else "abstained",
                "missing_held_symbols": missing,
                "net_loadings": net,
                "absolute_weighted_loadings": absolute,
                "scope": "Past-only proxy exposures, not realized risk, portfolio neutrality or outcome-based eligibility",
            }
        )
    return rows


def compute_factor_controls(batch, clock, plan: FactorControlsPlan, sessions, *, parent_result: dict):
    """Keep the original forecasts, match all six arms before inspecting strict outcomes."""
    parent = plan.parent
    inputs = prepare_forecast_inputs(batch, clock, parent, sessions)
    parent_trials, retained_scores = validate_retained_forecasts(parent_result, parent, inputs, clock)
    cohort = next(c for c in parent.cohorts if c.name == plan.cohort_name)
    symbols = list(cohort.symbols)
    evaluation_plan = replace(parent, costs_bps=plan.costs_bps)
    reproduction = []
    for fold in parent.folds:
        ridge = retained_scores[cohort.name, fold.name, "ridge"]
        outcomes = inputs.labels.loc[ridge.index, symbols].copy()
        outcomes.iloc[-parent.target.horizon_bars :] = np.nan
        original = evaluate_forecast_trial(ridge, outcomes, cohort, fold, evaluation_plan)
        require_retained_ridge_match(original, parent_trials[cohort.name, fold.name, "ridge"], plan.costs_bps)
        reproduction.append({"cohort": cohort.name, "fold": fold.name, "verified": True})

    closes = pd.DataFrame({symbol: frame.close for symbol, frame in inputs.panel.frames.items()})
    volumes = pd.DataFrame({symbol: frame.volume for symbol, frame in inputs.panel.frames.items()})
    features = residual_momentum_features(
        closes, volumes, symbols=cohort.symbols, factors=plan.factor_symbols, feed=plan.feed
    )
    evaluator = AlphaExpressionEvaluator()
    controls = {
        name: pd.DataFrame({symbol: evaluator.evaluate(expression, inputs.panel.frames[symbol]) for symbol in symbols})
        for name, expression in CONTROL_EXPRESSIONS.items()
    }
    endpoint_volume = volumes.shift(-1).gt(0) & volumes.shift(-parent.target.horizon_bars).gt(0)
    supports, trials, comparisons, paired_ic, exposures = [], [], [], [], []
    for fold in parent.folds:
        ridge = retained_scores[cohort.name, fold.name, "ridge"]
        dates = ridge.index
        parent_eligible = inputs.eligible.loc[dates, symbols]
        ridge_available = parent_eligible & np.isfinite(ridge)
        raw = features.raw_momentum.loc[dates, symbols]
        residual = features.residual_momentum.loc[dates, symbols]
        common = ridge_available & np.isfinite(raw) & np.isfinite(residual)
        for values in controls.values():
            common &= np.isfinite(values.loc[dates, symbols])
        scores = {
            "ridge": ridge.where(common),
            "volatility20": controls["volatility20"].loc[dates, symbols].where(common),
            "skipped_month_momentum": raw.where(common),
            "residual_momentum": residual.where(common),
        }
        ranks = [
            values.loc[dates, symbols].where(common).rank(axis=1, method="average", pct=True)
            for values in controls.values()
        ]
        scores["rank_blend"] = sum(ranks) / len(ranks)
        scores["residual_rank_blend"] = (
            scores["rank_blend"].rank(axis=1, method="average", pct=True)
            + scores["residual_momentum"].rank(axis=1, method="average", pct=True)
        ) / 2
        labels = inputs.labels.loc[dates, symbols].where(endpoint_volume.loc[dates, symbols])
        labels.iloc[-parent.target.horizon_bars :] = np.nan
        supports.append(
            {
                "cohort": cohort.name,
                "fold": fold.name,
                "dates": [t.isoformat() for t in dates],
                "symbols": symbols,
                "parent_eligible": parent_eligible.to_numpy().tolist(),
                "frozen_ridge_available": ridge_available.to_numpy().tolist(),
                "raw_momentum_available": np.isfinite(raw).to_numpy().tolist(),
                "residual_momentum_available": np.isfinite(residual).to_numpy().tolist(),
                "common_eligible": common.to_numpy().tolist(),
                "eligible_breadth": common.sum(axis=1).tolist(),
                "unmatured_tail_dates": [t.isoformat() for t in dates[-parent.target.horizon_bars :]],
                "stage_counts": {
                    "expected_symbol_decisions": common.size,
                    "parent_eligible_symbol_decisions": int(parent_eligible.to_numpy().sum()),
                    "frozen_ridge_symbol_decisions": int(ridge_available.to_numpy().sum()),
                    "raw_momentum_symbol_decisions": int(np.isfinite(raw).to_numpy().sum()),
                    "residual_momentum_symbol_decisions": int(np.isfinite(residual).to_numpy().sum()),
                    "common_symbol_decisions": int(common.to_numpy().sum()),
                    "lost_ridge_symbol_decisions": int((ridge_available & ~common).to_numpy().sum()),
                    "insufficient_breadth_dates": int(common.sum(axis=1).lt(cohort.min_assets).sum()),
                },
            }
        )
        evaluated = {}
        for model in FACTOR_MODELS:
            current = evaluate_forecast_trial(scores[model], labels, cohort, fold, evaluation_plan)
            current.update(model=model, outcome_scope=FACTOR_OUTCOME_SCOPE)
            trials.append(current)
            evaluated[model] = current
            exposures.extend(_basket_exposures(current, features, plan.factor_symbols))
        benchmark = evaluated[FACTOR_BENCHMARK]
        for model in FACTOR_MODELS:
            if model == FACTOR_BENCHMARK:
                continue
            paired_ic.append(paired_ic_comparison(evaluated[model], benchmark, parent.ic))
            for cost in plan.costs_bps:
                comparison = paired_basket_comparison(evaluated[model], benchmark, cost, control_label="benchmark")
                comparison.update(model=model, benchmark=benchmark["model"])
                comparisons.append(comparison)
    return {
        "version": FACTOR_CONTROLS_VERSION,
        "status": PanelStudyStatus.COMPLETED,
        "coverage": inputs.panel.coverage,
        "supports": supports,
        "factor_features": features.document(),
        "parent_ridge_reproduced": reproduction,
        "trials": trials,
        "paired_ic": paired_ic,
        "paired_comparisons": comparisons,
        "basket_factor_exposures": exposures,
        "charged_trials": plan.trial_count,
        "completed_comparisons": len(trials) * (1 + len(plan.costs_bps)),
        "supervised_refitted_models": 0,
        "factor_regression_fits": sum(row["fit_status"] == "fitted" for row in features.fits),
        "authorizes_promotion": False,
        "limitations": [
            "Original finite-price Ridge training is unchanged; strict endpoints mask evaluation outcomes only.",
            "Common past-only support changes the historical cohort; these curves are not directly comparable with the original wider-cohort curves.",
            "Past ETF loadings explain return proxies; residual-score baskets are not guaranteed factor-neutral or equally risky.",
            "Positive endpoint volume is source evidence, not borrow availability, liquidity capacity or executable fills.",
            "Missing expected dates and held outcomes remain unknown; complete inference and curves are withheld.",
            "This is inspected current-cohort development with no adaptive-selection correction or promotion authority.",
        ],
    }
