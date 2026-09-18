"""Causal panel forecasts with separate predictor, outcome and payoff support.

Current-cohort development only. Missing historical observations remain on the
clock; future outcomes never determine forecasts or basket membership.
"""

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, OHLCV, SessionSchedule
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.baselines import build_forecast_estimator, fitted_forecast_evidence, forecast_metrics
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.diagnostics import forecast_diagnostics
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.forecast_policy import BASIS_POINTS, DailyLongFlatPolicy
from agentic_trader.research.alpha.information import cross_sectional_ic
from agentic_trader.research.alpha.panel import DailyResearchPanel, align_daily_panel
from agentic_trader.research.alpha.panel_forecast_plan import MODEL_CONTROLS, PANEL_FORECAST_VERSION, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelStudyStatus, basket_weights
from agentic_trader.research.alpha.targets import forecast_labels


def _values(frame):
    return [[float(value) if np.isfinite(value) else None for value in row] for row in frame.to_numpy(dtype=float)]


def _matrix(frame):
    return {"dates": [t.isoformat() for t in frame.index], "symbols": list(frame.columns), "values": _values(frame)}


def _fit(features, eligible, labels, position, clock, symbols, plan):
    """Every training label becomes available strictly before this decision."""
    end = max(0, position - plan.target.horizon_bars)
    start = max(0, end - plan.train_sessions)
    mask = eligible.iloc[start:end].to_numpy() & np.isfinite(labels.iloc[start:end].to_numpy())
    date_positions, symbol_positions = np.nonzero(mask)
    dates = date_positions + start
    evidence = {
        "decision_bar": clock[position].isoformat(),
        "decision_at": (clock[position] + pd.DateOffset(days=1)).isoformat(),
        "training_window_start": clock[start].isoformat(),
        "training_end_exclusive": clock[end].isoformat(),
        "training_dates": len(np.unique(dates)),
        "training_rows": len(dates),
        "status": "unavailable",
        "reason": "insufficient_mature_training",
        "last_training_label_available_at": None,
    }
    if len(dates):
        last = dates.max() + plan.target.horizon_bars
        evidence["last_training_label_available_at"] = (clock[last] + pd.DateOffset(days=1)).isoformat()
    if len(dates) < plan.min_train_rows or len(np.unique(dates)) < plan.min_train_sessions:
        return None, np.nan, evidence
    index = pd.MultiIndex.from_arrays(
        [clock[dates], np.asarray(symbols)[symbol_positions]], names=["decision_bar", "symbol"]
    )
    training = pd.DataFrame({name: values[dates, symbol_positions] for name, values in features.items()}, index=index)
    target = pd.Series(labels.to_numpy()[dates, symbol_positions], index=index)
    # Ridge is deterministic; the shared factory's random seed only affects boosted models.
    estimator = build_forecast_estimator("ridge", {"regularization": plan.ridge_alpha}, seed=0)
    estimator.fit(training, target)
    evidence.update(
        status="fitted",
        reason=None,
        training_mean=float(target.mean()),
        fitted_model=fitted_forecast_evidence(estimator, "ridge", training, target),
    )
    return estimator, float(target.mean()), evidence


def _predictions(features, eligible, labels, positions, clock, cohort, plan):
    names = [feature.name for feature in plan.features]
    values = {name: features[name].loc[:, list(cohort.symbols)].to_numpy() for name in names}
    frames = {
        name: features[name].loc[clock[positions], list(cohort.symbols)].where(eligible.iloc[positions])
        for name in plan.economic_features
    }
    for model in MODEL_CONTROLS:
        frames[model] = pd.DataFrame(np.nan, index=clock[positions], columns=cohort.symbols)
    fits = []
    estimator, mean = None, np.nan
    for offset, position in enumerate(positions):
        if offset % plan.refit_sessions == 0:
            estimator, mean, evidence = _fit(values, eligible, labels, position, clock, cohort.symbols, plan)
            fits.append(evidence)
        available = eligible.iloc[position].to_numpy()
        if estimator is not None and available.any():
            observed = pd.DataFrame({name: values[name][position, available] for name in names})
            predicted = estimator.predict(observed)
            if not np.isfinite(predicted).all():
                raise ValueError("Nonfinite fitted forecasts cannot become missing market observations")
            frames["ridge"].iloc[offset, np.flatnonzero(available)] = predicted
            frames["training_mean"].iloc[offset, np.flatnonzero(available)] = mean
    return frames, fits


def evaluate_frozen_basket(weights: pd.Series, outcomes: pd.Series, *, costs_bps):
    """Evaluate immutable holdings; unavailable held outcomes never become cash."""
    costs_bps = DailyLongFlatPolicy(costs_bps).costs_bps
    if (
        not weights.index.is_unique
        or not weights.index.equals(outcomes.index)
        or not np.isfinite(weights.to_numpy()).all()
        or np.isinf(outcomes.to_numpy()).any()
        or (weights.abs().sum() > 1 and not np.isclose(weights.abs().sum(), 1))
    ):
        raise ValueError("Aligned finite frozen weights with bounded gross and explicit outcomes required")
    held = weights.ne(0)
    missing = list(outcomes.index[held & ~np.isfinite(outcomes)])
    known = not missing
    gross = float(weights.loc[held] @ outcomes.loc[held]) if known else None
    entered = float(weights.abs().sum())
    exited = float(weights.loc[held].abs() @ (1 + outcomes.loc[held])) if known else None
    turnover = entered + exited if exited is not None else None
    return {
        "missing_held_symbols": missing,
        "gross_return": gross,
        "entry_gross": entered,
        "exit_gross": exited,
        "turnover": turnover,
        "costs": [
            {
                "cost_bps": cost,
                "fees": cost / BASIS_POINTS * turnover if turnover is not None else None,
                "net_return": gross - cost / BASIS_POINTS * turnover if turnover is not None else None,
            }
            for cost in costs_bps
        ],
    }


def _baskets(scores, labels, cohort, plan, weight_builder=None):
    dates = scores.index
    horizon = plan.target.horizon_bars
    observations = []
    for offset in range(0, len(dates) - horizon, horizon):
        observed = scores.iloc[offset]
        available = observed[np.isfinite(observed)]
        weights = pd.Series(0.0, index=cohort.symbols)
        enough = len(available) >= cohort.min_assets
        if enough:
            chosen = basket_weights(available, cohort.top_k) if weight_builder is None else weight_builder(available)
            if (
                not isinstance(chosen, pd.Series)
                or not chosen.index.equals(available.index)
                or not np.isfinite(chosen.to_numpy()).all()
                or (chosen.abs().sum() > 1 and not np.isclose(chosen.abs().sum(), 1))
            ):
                raise ValueError("Basket policy must preserve finite symbol weights and unit gross bounds")
            weights.loc[available.index] = chosen
        # Only after immutable weights exist do outcome values enter evaluation.
        payoff = evaluate_frozen_basket(weights, labels.iloc[offset], costs_bps=plan.costs_bps)
        known = not payoff["missing_held_symbols"]
        observations.append(
            {
                "decision_bar": dates[offset].isoformat(),
                "assumed_decision_at": (dates[offset] + pd.DateOffset(days=1)).isoformat(),
                "entry_bar": dates[offset + 1].isoformat(),
                "exit_bar": dates[offset + horizon].isoformat(),
                "eligible_symbols": list(available.index),
                "weights": weights.to_dict(),
                "status": "unavailable" if not known else "observed" if enough else "abstained",
                "reason": "missing_held_outcome" if not known else None if enough else "insufficient_forecast_breadth",
                **payoff,
            }
        )
    return observations


def _cost_summaries(baskets, costs):
    summaries = []
    known = [row for row in baskets if row["gross_return"] is not None]
    turnover = sum(row["turnover"] for row in known)
    complete = len(known) == len(baskets) and bool(baskets)
    break_even = (
        sum(row["gross_return"] for row in known) / turnover * BASIS_POINTS if complete and turnover > 0 else None
    )
    for cost in costs:
        rows = [next(c for c in row["costs"] if c["cost_bps"] == cost) for row in known]
        returns = np.array([row["net_return"] for row in rows])
        valid = complete and bool((returns > -1).all())
        total, drawdown = None, None
        if valid:
            log_wealth = np.log1p(returns).cumsum()
            wealth = np.exp(log_wealth)
            if not np.isfinite(wealth).all():
                raise ValueError("Nonfinite basket wealth cannot be reported")
            total = float(wealth[-1] - 1)
            drawdown = float(np.max(1 - wealth / np.maximum.accumulate(np.r_[1, wealth])[1:]))
        summaries.append(
            {
                "cost_bps": cost,
                "baskets": len(baskets),
                "known_baskets": len(known),
                "missing_baskets": len(baskets) - len(known),
                "abstentions": sum(row["status"] == "abstained" for row in baskets),
                "curve_complete": valid,
                "net_return": total,
                "marked_boundary_drawdown": drawdown,
                "known_arithmetic_return_sum": float(returns.sum()),
                "known_fees_sum": float(sum(row["fees"] for row in rows)),
                "known_turnover_sum": float(turnover),
                "arithmetic_break_even_bps": break_even,
                "curve_unavailable_reason": None
                if valid
                else "missing_held_outcomes"
                if not complete
                else "nonpositive_capital",
            }
        )
    return summaries


def evaluate_forecast_trial(scores, labels, cohort, fold, plan, *, weight_builder=None):
    """Evaluate a frozen score panel using one shared IC and basket accounting contract."""
    horizon = plan.target.horizon_bars
    mature = scores.index[:-horizon]
    predicted, target = scores.loc[mature], labels.loc[mature]
    available = np.isfinite(predicted)
    missing_labels = available & ~np.isfinite(target)
    missing_dates = missing_labels.any(axis=1)
    ic_scores = predicted.copy()
    ic_scores.loc[missing_dates] = np.nan  # Never infer a complete IC from outcome-selected membership.
    ic = cross_sectional_ic(
        ic_scores,
        target,
        pd.Series(fold.name, index=mature),
        replace(plan.ic, min_assets=cohort.min_assets),
        expected_index=mature,
        target=plan.target,
    )
    baskets = _baskets(scores, labels, cohort, plan, weight_builder)
    return {
        "cohort": cohort.name,
        "fold": fold.name,
        "predictions": _matrix(scores),
        "ic": ic.document(),
        "ic_missing_label_dates": [t.isoformat() for t in mature[missing_dates]],
        "stage_counts": {
            "expected_symbol_decisions": scores.size,
            "available_predictions": int(np.isfinite(scores.to_numpy()).sum()),
            "matured_symbol_decisions": predicted.size,
            "matured_predictions": int(available.to_numpy().sum()),
            "paired_labels": int((available & np.isfinite(target)).to_numpy().sum()),
            "predicted_missing_labels": int(missing_labels.to_numpy().sum()),
            "complete_ic_dates": sum(observation.ic is not None for observation in ic.observations),
        },
        "baskets": baskets,
        "cost_summaries": _cost_summaries(baskets, plan.costs_bps),
        "forecast_metrics": None,
        "forecast_diagnostics": None,
    }


@dataclass(frozen=True)
class ForecastPanelInputs:
    panel: DailyResearchPanel
    features: dict[str, pd.DataFrame]
    observed: pd.DataFrame
    history: pd.DataFrame
    finite: pd.DataFrame
    eligible: pd.DataFrame
    labels: pd.DataFrame


def prepare_forecast_inputs(batch: DailyStudyInputs, clock, plan: PanelForecastPlan, sessions):
    """Validate native sources and derive the shared causal feature/label support."""
    SessionSchedule(plan.start, plan.end, sessions, "observed_exchange_calendar")
    expected = pd.DatetimeIndex([pd.Timestamp(s.date, tz=ET_TZ) for s in sessions])
    if not isinstance(clock, pd.DatetimeIndex) or not clock.equals(expected):
        raise ValueError("Exact observed native daily session clock required")
    frames = batch.require_complete(plan.acquisition_symbols)
    if any(frame.attrs.get("bar_layout", FIXED_BAR_LAYOUT) != FIXED_BAR_LAYOUT for frame in frames.values()):
        raise ValueError("Explicit native daily source layout required")
    panel = align_daily_panel(frames, clock, feed=plan.feed, adjustment=plan.adjustment)
    evaluator = AlphaExpressionEvaluator()
    features = {
        feature.name: pd.DataFrame(
            {symbol: evaluator.evaluate(feature.expression, frame) for symbol, frame in panel.frames.items()}
        )
        for feature in plan.features
    }
    observed = pd.DataFrame(
        {symbol: frame.loc[:, list(OHLCV)].notna().all(axis=1) for symbol, frame in panel.frames.items()}
    )
    history = observed.rolling(plan.history_sessions, min_periods=plan.history_sessions).sum().eq(plan.history_sessions)
    finite = pd.DataFrame(True, index=clock, columns=list(panel.frames))
    for frame in features.values():
        finite &= np.isfinite(frame)
    eligible = history & finite
    all_labels = pd.DataFrame({symbol: forecast_labels(frame, plan.target) for symbol, frame in panel.frames.items()})
    return ForecastPanelInputs(panel, features, observed, history, finite, eligible, all_labels)


def compute_panel_forecast(batch: DailyStudyInputs, clock, plan: PanelForecastPlan, sessions):
    """Run a complete declared matrix, retaining unavailable support within each cell."""
    inputs = prepare_forecast_inputs(batch, clock, plan, sessions)
    panel, features, eligible, all_labels = inputs.panel, inputs.features, inputs.eligible, inputs.labels
    observed, history, finite = inputs.observed, inputs.history, inputs.finite
    supports, trials = [], []
    for cohort in plan.cohorts:
        columns = list(cohort.symbols)
        support, labels = eligible[columns], all_labels[columns]
        for fold in plan.folds:
            positions = np.flatnonzero((clock.date >= fold.start) & (clock.date <= fold.end))
            if len(positions) <= plan.target.horizon_bars:
                raise ValueError("Fold has no matured decision dates")
            dates = clock[positions]
            predicted, fits = _predictions(features, support, labels, positions, clock, cohort, plan)
            outcomes = labels.loc[dates].copy()
            outcomes.iloc[-plan.target.horizon_bars :] = np.nan
            selected_support = support.loc[dates]
            supports.append(
                {
                    "cohort": cohort.name,
                    "fold": fold.name,
                    "dates": [t.isoformat() for t in dates],
                    "symbols": columns,
                    "eligible": selected_support.to_numpy().tolist(),
                    "targets": _values(outcomes),
                    "unmatured_tail_dates": [t.isoformat() for t in dates[-plan.target.horizon_bars :]],
                    "fits": fits,
                    "stage_counts": {
                        "expected_symbol_decisions": len(dates) * len(columns),
                        "observed_bars": int(observed.loc[dates, columns].to_numpy().sum()),
                        "complete_history": int(history.loc[dates, columns].to_numpy().sum()),
                        "finite_features": int(finite.loc[dates, columns].to_numpy().sum()),
                        "eligible_symbol_decisions": int(selected_support.to_numpy().sum()),
                        "matured_decision_dates": len(dates) - plan.target.horizon_bars,
                        "fitted_refits": sum(f["status"] == "fitted" for f in fits),
                        "expected_refits": len(fits),
                    },
                }
            )
            for model, scores in predicted.items():
                result = evaluate_forecast_trial(scores, outcomes, cohort, fold, plan)
                result["model"] = model
                if model in MODEL_CONTROLS:
                    mature = dates[: -plan.target.horizon_bars]
                    paired = pd.DataFrame(
                        {
                            "prediction": scores.loc[mature].to_numpy().ravel(),
                            "target": outcomes.loc[mature].to_numpy().ravel(),
                            "training_mean": predicted["training_mean"].loc[mature].to_numpy().ravel(),
                        },
                        index=pd.MultiIndex.from_product([mature, columns], names=["decision_bar", "symbol"]),
                    )
                    result.update(
                        forecast_metrics=forecast_metrics(paired), forecast_diagnostics=forecast_diagnostics(paired)
                    )
                trials.append(result)
    return {
        "version": PANEL_FORECAST_VERSION,
        "status": PanelStudyStatus.COMPLETED,
        "coverage": panel.coverage,
        "supports": supports,
        "trials": trials,
        "charged_trials": plan.trial_count,
        "authorizes_promotion": False,
        "limitations": [
            "Current membership/liquidity conditions this historical development cohort.",
            "Adjusted native daily outcomes are research proxies, not fills or borrow evidence.",
            "Missing held outcomes withhold total P&L; available paired diagnostics are not complete coverage.",
            "Synthetic control power is mechanical verification, not calibrated false-positive probability.",
        ],
    }
