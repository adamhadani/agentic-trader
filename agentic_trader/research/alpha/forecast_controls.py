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
from agentic_trader.research.alpha.panel_forecast_plan import MODEL_CONTROLS, PANEL_FORECAST_VERSION
from agentic_trader.research.alpha.panel_study import PanelStudyStatus


def _indexed(rows, names, expected):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Complete retained forecast matrix required")
    try:
        keys = [tuple(row[name] for name in names) for row in rows]
        if len(set(keys)) != len(keys) or set(keys) != expected:
            raise ValueError("Retained forecast matrix has duplicate or unexpected cells")
        return dict(zip(keys, rows, strict=True))
    except (KeyError, TypeError) as exc:
        raise ValueError("Named retained forecast matrix required") from exc


def _matrix(values, dates, symbols, *, boolean=False):
    if (
        not isinstance(values, list)
        or len(values) != len(dates)
        or any(not isinstance(row, list) or len(row) != len(symbols) for row in values)
    ):
        raise ValueError("Exact retained forecast dimensions required")
    for row in values:
        for value in row:
            if boolean:
                valid = type(value) is bool
            else:
                valid = value is None or (type(value) in (int, float) and np.isfinite(value))
            if not valid:
                raise ValueError("Retained forecast values must be typed, finite observations or explicit nulls")
    return pd.DataFrame(values, index=dates, columns=symbols, dtype=bool if boolean else float)


def _validate_parent(parent_result, parent, inputs, clock):
    try:
        if (
            parent_result["version"] != PANEL_FORECAST_VERSION
            or parent_result["status"] != PanelStudyStatus.COMPLETED
            or parent_result["authorizes_promotion"] is not False
            or parent_result["charged_trials"] != parent.trial_count
        ):
            raise ValueError("Completed research-only parent forecast result required")
        support_keys = {(cohort.name, fold.name) for cohort in parent.cohorts for fold in parent.folds}
        models = (*parent.economic_features, *MODEL_CONTROLS)
        trial_keys = {(*key, model) for key in support_keys for model in models}
        supports = _indexed(parent_result["supports"], ("cohort", "fold"), support_keys)
        trials = _indexed(parent_result["trials"], ("cohort", "fold", "model"), trial_keys)
        scores = {}
        for cohort in parent.cohorts:
            symbols = list(cohort.symbols)
            for fold in parent.folds:
                dates = clock[(clock.date >= fold.start) & (clock.date <= fold.end)]
                expected_dates = [t.isoformat() for t in dates]
                support = supports[cohort.name, fold.name]
                if support["dates"] != expected_dates or support["symbols"] != symbols:
                    raise ValueError("Retained support axes differ from frozen cohort/calendar")
                eligible = _matrix(support["eligible"], dates, symbols, boolean=True)
                target = _matrix(support["targets"], dates, symbols)
                expected_target = inputs.labels.loc[dates, symbols].copy()
                expected_target.iloc[-parent.target.horizon_bars :] = np.nan
                if not eligible.equals(inputs.eligible.loc[dates, symbols]) or not target.equals(expected_target):
                    raise ValueError("Retained eligibility or targets differ from original source observations")
                if support["unmatured_tail_dates"] != expected_dates[-parent.target.horizon_bars :]:
                    raise ValueError("Retained forecast maturity differs from frozen target")
                for model in models:
                    matrix = trials[cohort.name, fold.name, model]["predictions"]
                    if matrix["dates"] != expected_dates or matrix["symbols"] != symbols:
                        raise ValueError("Retained forecast axes differ from frozen cohort/calendar")
                    values = _matrix(matrix["values"], dates, symbols)
                    if (np.isfinite(values) & ~eligible).to_numpy().any():
                        raise ValueError("Retained forecast cannot exist outside causal eligibility")
                    scores[cohort.name, fold.name, model] = values
        return trials, scores
    except (KeyError, TypeError) as exc:
        raise ValueError("Complete retained forecast evidence required") from exc


def _long_only(scores):
    return pd.Series(1 / len(scores), index=scores.index)


def _require_parent_ridge_match(current, original, costs):
    """Retained forecasts must reproduce their own frozen price-scope accounting."""
    try:
        original_baskets = [
            {**row, "costs": [cost for cost in row["costs"] if cost["cost_bps"] in costs]}
            for row in original["baskets"]
        ]
        original_costs = [cost for cost in original["cost_summaries"] if cost["cost_bps"] in costs]
        if (
            current["ic"] != original["ic"]
            or current["baskets"] != original_baskets
            or current["cost_summaries"] != original_costs
        ):
            raise ValueError("Retained Ridge accounting differs from its frozen forecast/price observations")
    except (KeyError, TypeError) as exc:
        raise ValueError("Complete retained Ridge accounting required") from exc


def _paired(ridge, control, cost):
    rows = []
    if len(ridge["baskets"]) != len(control["baskets"]):
        raise ValueError("Matched strategies require identical basket clocks")
    for first, second in zip(ridge["baskets"], control["baskets"], strict=True):
        if any(first[name] != second[name] for name in ("decision_bar", "entry_bar", "exit_bar")):
            raise ValueError("Paired returns require identical decision and outcome clocks")
        left = next(row["net_return"] for row in first["costs"] if row["cost_bps"] == cost)
        right = next(row["net_return"] for row in second["costs"] if row["cost_bps"] == cost)
        known = left is not None and right is not None
        rows.append(
            {
                "decision_bar": first["decision_bar"],
                "ridge_return": left,
                "control_return": right,
                "difference": left - right if known else None,
                "status": "paired" if known else "unavailable",
            }
        )
    values = np.array([row["difference"] for row in rows if row["difference"] is not None], dtype=float)
    complete = len(values) == len(rows) and bool(rows)
    absolute_sum = float(np.abs(values).sum())
    return {
        "cohort": ridge["cohort"],
        "fold": ridge["fold"],
        "outcome_scope": ridge["outcome_scope"],
        "control": control["model"],
        "cost_bps": cost,
        "expected_baskets": len(rows),
        "paired_baskets": len(values),
        "missing_baskets": len(rows) - len(values),
        "observations": rows,
        "complete": complete,
        "complete_arithmetic_difference_sum": float(values.sum()) if complete else None,
        "known_arithmetic_difference_sum": float(values.sum()),
        "known_mean_difference": float(values.mean()) if len(values) else None,
        "known_sample_std_difference": float(values.std(ddof=1)) if len(values) > 1 else None,
        "known_minimum_difference": float(values.min()) if len(values) else None,
        "known_maximum_difference": float(values.max()) if len(values) else None,
        "known_positive_differences": int((values > 0).sum()),
        "largest_absolute_difference_share": float(np.abs(values).max() / absolute_sum) if absolute_sum else None,
        "scope": "Paired descriptive basket differences; differences are not compounded or treated as a traded return.",
    }


def compute_forecast_controls(batch, clock, plan: ForecastControlsPlan, sessions, *, parent_result: dict):
    """Compare fixed controls on parent decision support; outcome scopes never select holdings."""
    parent = plan.parent
    inputs = prepare_forecast_inputs(batch, clock, parent, sessions)
    parent_trials, retained_scores = _validate_parent(parent_result, parent, inputs, clock)
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
                    _require_parent_ridge_match(current, parent_trials[cohort.name, fold.name, "ridge"], plan.costs_bps)
                evaluated[model] = current
                trials.append(current)
            for model in CONTROL_MODELS[1:]:
                comparisons.extend(_paired(evaluated["ridge"], evaluated[model], cost) for cost in plan.costs_bps)
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
