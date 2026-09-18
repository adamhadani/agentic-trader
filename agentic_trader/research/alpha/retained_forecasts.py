"""Shared immutable parent validation and paired retained-forecast comparisons."""

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.information import summarize_expected_values
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


def validate_retained_forecasts(parent_result, parent, inputs, clock):
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


def require_retained_ridge_match(current, original, costs):
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


def paired_basket_comparison(strategy, control, cost, *, strategy_label="strategy", control_label="control"):
    rows = []
    if len(strategy["baskets"]) != len(control["baskets"]):
        raise ValueError("Matched strategies require identical basket clocks")
    for first, second in zip(strategy["baskets"], control["baskets"], strict=True):
        if any(first[name] != second[name] for name in ("decision_bar", "entry_bar", "exit_bar")):
            raise ValueError("Paired returns require identical decision and outcome clocks")
        left = next(row["net_return"] for row in first["costs"] if row["cost_bps"] == cost)
        right = next(row["net_return"] for row in second["costs"] if row["cost_bps"] == cost)
        known = left is not None and right is not None
        rows.append(
            {
                "decision_bar": first["decision_bar"],
                f"{strategy_label}_return": left,
                f"{control_label}_return": right,
                "difference": left - right if known else None,
                "status": "paired" if known else "unavailable",
            }
        )
    values = np.array([row["difference"] for row in rows if row["difference"] is not None], dtype=float)
    complete = len(values) == len(rows) and bool(rows)
    absolute_sum = float(np.abs(values).sum())
    return {
        "cohort": strategy["cohort"],
        "fold": strategy["fold"],
        "outcome_scope": strategy["outcome_scope"],
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


def paired_ic_comparison(strategy, benchmark, policy):
    left = strategy["ic"]["observations"]
    right = benchmark["ic"]["observations"]
    if len(left) != len(right):
        raise ValueError("Paired IC requires identical expected clocks")
    observations = []
    for first, second in zip(left, right, strict=True):
        if any(first[key] != second[key] for key in ("timestamp", "fold")):
            raise ValueError("Paired IC requires identical expected clocks")
        known = first["ic"] is not None and second["ic"] is not None
        observations.append(
            {
                "timestamp": first["timestamp"],
                "strategy_ic": first["ic"],
                "benchmark_ic": second["ic"],
                "difference": first["ic"] - second["ic"] if known else None,
                "status": "paired" if known else "unavailable",
            }
        )
    return {
        "cohort": strategy["cohort"],
        "fold": strategy["fold"],
        "model": strategy["model"],
        "benchmark": benchmark["model"],
        "observations": observations,
        "statistics": summarize_expected_values([row["difference"] for row in observations], policy),
        "scope": "Within-fold expected-date paired IC differences; HAC is not adaptive-selection correction",
    }
