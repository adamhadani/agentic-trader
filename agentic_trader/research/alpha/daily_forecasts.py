"""Pure receipt-aware prospective panel forecasts and separately observed outcomes."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from numbers import Real

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, OHLCV, SessionSchedule, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.baselines import build_forecast_estimator, fitted_forecast_evidence
from agentic_trader.research.alpha.daily_plan import (
    DAILY_BASELINE_MODELS,
    DAILY_MODELS,
    MAX_DAILY_COMPARISONS,
    DailyComparisonPlan,
)
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.factor_features import factor_residuals_at, residual_momentum_features
from agentic_trader.research.alpha.forecast_controls_plan import CONTROL_EXPRESSIONS
from agentic_trader.research.alpha.information import ICPolicy, cross_sectional_ic
from agentic_trader.research.alpha.panel import align_daily_panel
from agentic_trader.research.alpha.panel_forecast import evaluate_frozen_basket
from agentic_trader.research.alpha.panel_study import basket_weights
from agentic_trader.research.alpha.targets import forecast_labels
from agentic_trader.research.alpha.validation import frame_digest


DAILY_FORECAST_VERSION = "prospective_daily_panel_forecast_v1"
DAILY_OUTCOME_VERSION = "prospective_daily_panel_outcome_v1"
DAILY_RESIDUAL_VERSION = "prospective_daily_residual_state_v1"
DAILY_COMPARISON_FORECAST_VERSION = "daily_baseline_comparison_forecast_v1"
DAILY_COMPARISON_OUTCOME_VERSION = "daily_baseline_comparison_outcome_v1"
COMPARISON_SHARED_FIELDS = (
    "plan_id",
    "decision_date",
    "fit_cutoff",
    "latest_received_at",
    "receipts",
    "input_hashes",
    "vintage_id",
    "coverage",
    "entry_date",
    "exit_date",
    "economic_scheduled",
    "ridge_fit",
    "authorizes_promotion",
)


def _nullable(series):
    return {symbol: float(value) if np.isfinite(value) else None for symbol, value in series.items()}


def _seal(document, key):
    return {**document, key: document_hash(document)}


def _verify(document, key, version, plan):
    if (
        not isinstance(document, dict)
        or document.get("version") != version
        or document.get("plan_id") != plan.identity
        or document.get("authorizes_promotion") is not False
        or document.get(key) != document_hash({name: value for name, value in document.items() if name != key})
    ):
        raise ValueError(f"Immutable {version} evidence does not match the forecast contract")


def _panel(batch, sessions, plan, last_date):
    sessions = tuple(sessions)
    if not sessions:
        raise ValueError("Observed exchange calendar required")
    SessionSchedule(sessions[0].date, sessions[-1].date, sessions, "observed_exchange_calendar")
    dates = [session.date for session in sessions if session.date <= last_date]
    if not dates or dates[-1] != last_date:
        raise ValueError("Observed final native daily session required")
    clock = pd.DatetimeIndex([pd.Timestamp(day, tz=ET_TZ) for day in dates])
    frames = batch.require_complete(plan.acquisition_symbols)
    if any(frame.attrs.get("bar_layout", FIXED_BAR_LAYOUT) != FIXED_BAR_LAYOUT for frame in frames.values()):
        raise ValueError("Native daily source layout required")
    return align_daily_panel(frames, clock, feed=plan.feed, adjustment=plan.adjustment), clock


def _receipts(receipts, symbols, *, earliest, cutoff, strict):
    if not isinstance(receipts, dict) or set(receipts) != set(symbols):
        raise ValueError("Every declared daily member requires an actual receipt")
    retained = {}
    for symbol in symbols:
        try:
            requested = utc_timestamp(receipts[symbol]["requested_at"])
            received = utc_timestamp(receipts[symbol]["received_at"])
        except (KeyError, TypeError) as exc:
            raise ValueError("Complete actual daily receipt required") from exc
        if not earliest <= requested <= received or not (received < cutoff if strict else received <= cutoff):
            raise ValueError("Daily source receipt falls outside the frozen availability cutoff")
        retained[symbol] = {"requested_at": requested.isoformat(), "received_at": received.isoformat()}
    return retained


def _residual_state(panel, clock, plan, receipts, previous, vintage_id, fit_cutoff):
    closes = pd.DataFrame({s: frame.close for s, frame in panel.frames.items()})
    volumes = pd.DataFrame({s: frame.volume for s, frame in panel.frames.items()})
    latest = max(row["received_at"] for row in receipts.values())
    expected = clock[-plan.residual_spec.lookback_sessions :]
    symbols = list(plan.symbols)
    bootstrap = previous is None
    if bootstrap:
        measured = residual_momentum_features(
            closes, volumes, symbols=plan.symbols, factors=plan.factor_symbols, feed=plan.feed, spec=plan.residual_spec
        )
        rows = []
        accepted_dates = set()
        for timestamp in expected:
            day = timestamp.date()
            if day < plan.start_date or timestamp == clock[-1]:
                rows.append(
                    {
                        "date": day.isoformat(),
                        "values": _nullable(measured.residuals.loc[timestamp]),
                        "kind": "historical_bootstrap" if day < plan.start_date else "prospective",
                        "observed_at": latest,
                        "source_hash": vintage_id,
                    }
                )
                accepted_dates.add(day.isoformat())
            else:
                rows.append(
                    {
                        "date": day.isoformat(),
                        "values": dict.fromkeys(symbols),
                        "kind": "missed",
                        "observed_at": None,
                        "source_hash": None,
                    }
                )
        fits = [row for row in measured.fits if row["return_bar"][:10] in accepted_dates]
    else:
        _verify(previous, "state_id", DAILY_RESIDUAL_VERSION, plan)
        if previous.get("symbols") != symbols:
            raise ValueError("Residual-state symbols differ from the campaign")
        prior_rows = previous.get("rows")
        if not isinstance(prior_rows, list) or not 1 <= len(prior_rows) <= plan.residual_spec.lookback_sessions:
            raise ValueError("Bounded preceding residual history required")
        dates = [row["date"] for row in prior_rows]
        if (
            dates != sorted(set(dates))
            or previous.get("through_date") != dates[-1]
            or dates[-1] >= clock[-1].date().isoformat()
        ):
            raise ValueError("Residual history must precede this new daily decision")
        through = pd.Timestamp(previous["through_date"], tz=ET_TZ)
        if through not in clock:
            raise ValueError("Previously observed residual calendar changed")
        expected_previous = clock[clock <= through][-plan.residual_spec.lookback_sessions :]
        if dates != [timestamp.date().isoformat() for timestamp in expected_previous]:
            raise ValueError("Residual history cannot silently omit observed exchange dates")
        for row in prior_rows:
            if (
                set(row["values"]) != set(symbols)
                or row["kind"] not in ("historical_bootstrap", "prospective", "missed")
                or any(
                    value is not None
                    and (isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(float(value)))
                    for value in row["values"].values()
                )
                or (row["kind"] == "missed" and any(value is not None for value in row["values"].values()))
                or (row["kind"] != "missed" and not utc_timestamp(row["observed_at"]) < fit_cutoff)
            ):
                raise ValueError("Invalid immutable residual innovation evidence")
        observed = {row["date"]: row for row in prior_rows}
        current = factor_residuals_at(
            closes,
            volumes,
            symbols=plan.symbols,
            factors=plan.factor_symbols,
            feed=plan.feed,
            position=len(clock) - 1,
            spec=plan.residual_spec,
        )
        rows = []
        for timestamp in expected:
            day = timestamp.date().isoformat()
            if day in observed:
                rows.append(deepcopy(observed[day]))
            elif timestamp == clock[-1]:
                rows.append(
                    {
                        "date": day,
                        "values": _nullable(current.residuals),
                        "kind": "prospective",
                        "observed_at": latest,
                        "source_hash": vintage_id,
                    }
                )
            else:
                rows.append(
                    {
                        "date": day,
                        "values": dict.fromkeys(symbols),
                        "kind": "missed",
                        "observed_at": None,
                        "source_hash": None,
                    }
                )
        fits = current.fits
    state = _seal(
        {
            "version": DAILY_RESIDUAL_VERSION,
            "plan_id": plan.identity,
            "symbols": symbols,
            "through_date": clock[-1].date().isoformat(),
            "rows": rows,
            "authorizes_promotion": False,
        },
        "state_id",
    )
    history = pd.DataFrame([row["values"] for row in rows], index=expected, columns=symbols, dtype=float)
    width = plan.residual_spec.lookback_sessions - plan.residual_spec.skip_sessions
    window = history.iloc[-plan.residual_spec.lookback_sessions : -plan.residual_spec.skip_sessions]
    standard = window.std(ddof=plan.residual_spec.ddof)
    momentum = (window.mean() / standard).where(
        window.notna().sum().eq(width) & np.isfinite(standard) & standard.gt(plan.residual_spec.min_residual_std)
    )
    return state, momentum, {"bootstrap": bootstrap, "fits": fits, "forward_credit": False}


def _features(panel, clock, plan):
    evaluator = AlphaExpressionEvaluator()
    features = {
        name: pd.DataFrame({s: evaluator.evaluate(expression, panel.frames[s]) for s in plan.symbols})
        for name, expression in plan.ridge_features.items()
    }
    observed = pd.DataFrame({s: panel.frames[s].loc[:, list(OHLCV)].notna().all(axis=1) for s in plan.symbols})
    eligible = (
        observed.rolling(plan.history_sessions, min_periods=plan.history_sessions).sum().eq(plan.history_sessions)
    )
    for frame in features.values():
        eligible &= np.isfinite(frame)
    return features, eligible


def _ridge(panel, clock, plan, features, eligible, receipts, fit_cutoff):
    position, horizon = len(clock) - 1, plan.target.horizon_bars
    end = max(0, position - horizon)
    start = max(0, end - plan.train_sessions)
    labels = pd.DataFrame({s: forecast_labels(panel.frames[s], plan.target) for s in plan.symbols})
    volume = pd.DataFrame({s: panel.frames[s].volume for s in plan.symbols})
    qualified = volume.shift(-1).gt(0) & volume.shift(-horizon).gt(0)
    mask = (
        eligible.iloc[start:end].to_numpy()
        & qualified.iloc[start:end].to_numpy()
        & np.isfinite(labels.iloc[start:end].to_numpy())
    )
    relative, column = np.nonzero(mask)
    positions = relative + start
    symbols = np.asarray(plan.symbols)[column]
    evidence = {
        "status": "unavailable",
        "reason": "insufficient_mature_training",
        "fit_cutoff": fit_cutoff.isoformat(),
        "training_window_start": clock[start].date().isoformat(),
        "training_end_exclusive": clock[end].date().isoformat(),
        "expected_training_sessions": end - start,
        "training_sessions": len(np.unique(positions)),
        "training_rows": len(positions),
        "last_label_date": None,
        "last_label_received_at": None,
    }
    if len(positions):
        evidence.update(
            last_label_date=clock[positions.max() + horizon].date().isoformat(),
            last_label_received_at=max(receipts[s]["received_at"] for s in set(symbols)),
        )
    predicted = pd.Series(np.nan, index=plan.symbols)
    if len(positions) < plan.min_train_rows or len(np.unique(positions)) < plan.min_train_sessions:
        return predicted, evidence
    index = pd.MultiIndex.from_arrays([clock[positions], symbols], names=["decision_bar", "symbol"])
    training = pd.DataFrame(
        {name: frame.to_numpy()[positions, column] for name, frame in features.items()}, index=index
    )
    target = pd.Series(labels.to_numpy()[positions, column], index=index)
    estimator = build_forecast_estimator("ridge", {"regularization": plan.ridge_alpha}, seed=0)
    estimator.fit(training, target)
    available = eligible.iloc[-1]
    if available.any():
        values = pd.DataFrame({name: frame.iloc[-1][available] for name, frame in features.items()})
        predictions = estimator.predict(values)
        if not np.isfinite(predictions).all():
            raise ValueError("Nonfinite fitted daily forecasts are not missing market observations")
        predicted.loc[available] = predictions
    evidence.update(
        status="fitted", reason=None, fitted_model=fitted_forecast_evidence(estimator, "ridge", training, target)
    )
    return predicted, evidence


def _forecast_calculation(
    batch, sessions, plan, *, decision_date: date, fit_cutoff, receipts, previous_residual_state=None
):
    """Freeze all four arms from actually received prior data, independently of future labels."""
    fit_cutoff = utc_timestamp(fit_cutoff)
    window = plan.decision_window(decision_date, sessions)
    if not window.available_at <= fit_cutoff < min(window.expires_at, window.entry_open):
        raise ValueError("Daily forecast fit cutoff is outside the pre-entry decision window")
    retained_receipts = _receipts(
        receipts, plan.acquisition_symbols, earliest=window.available_at, cutoff=fit_cutoff, strict=True
    )
    panel, clock = _panel(batch, sessions, plan, decision_date)
    hashes = {symbol: frame_digest(frame) for symbol, frame in panel.frames.items()}
    vintage_id = document_hash({"input_hashes": hashes, "receipts": retained_receipts})
    state, residual_momentum, updates = _residual_state(
        panel, clock, plan, retained_receipts, previous_residual_state, vintage_id, fit_cutoff
    )
    features, eligible = _features(panel, clock, plan)
    ridge, fit = _ridge(panel, clock, plan, features, eligible, retained_receipts, fit_cutoff)
    evaluator = AlphaExpressionEvaluator()
    reversal = pd.Series(
        {s: evaluator.evaluate(CONTROL_EXPRESSIONS["reversal60"], panel.frames[s]).iloc[-1] for s in plan.symbols}
    )
    volatility = features["volatility20"].iloc[-1]
    stages = {
        "feature_eligible": eligible.iloc[-1],
        "finite_ridge": np.isfinite(ridge),
        "finite_residual": np.isfinite(residual_momentum),
        "finite_reversal": np.isfinite(reversal),
        "finite_volatility": np.isfinite(volatility),
    }
    common = pd.concat(stages.values(), axis=1).all(axis=1)
    stages["common"] = common
    masked = {"ridge": ridge.where(common), "volatility20": volatility.where(common)}
    masked["rank_blend"] = (
        reversal.where(common).rank(method="average", pct=True)
        + volatility.where(common).rank(method="average", pct=True)
    ) / 2
    masked["residual_rank_blend"] = (
        masked["rank_blend"].rank(method="average", pct=True)
        + residual_momentum.where(common).rank(method="average", pct=True)
    ) / 2
    arms = _forecast_arms(masked, common, plan, DAILY_MODELS)
    enough = int(common.sum()) >= plan.min_assets
    primary = _seal(
        {
            "version": DAILY_FORECAST_VERSION,
            "plan_id": plan.identity,
            "decision_date": decision_date.isoformat(),
            "fit_cutoff": fit_cutoff.isoformat(),
            "latest_received_at": max(row["received_at"] for row in retained_receipts.values()),
            "receipts": retained_receipts,
            "input_hashes": hashes,
            "vintage_id": vintage_id,
            "coverage": panel.coverage,
            "entry_date": window.entry_date.isoformat(),
            "exit_date": window.exit_date.isoformat(),
            "economic_scheduled": window.economic_scheduled,
            "common_support": common.to_dict(),
            "stage_support": {
                name: {"count": int(mask.sum()), "mask": mask.to_dict()} for name, mask in stages.items()
            },
            "status": "scored" if enough else "unavailable",
            "reason": None if enough else "insufficient_common_breadth",
            "arms": arms,
            "ridge_fit": fit,
            "residual_state": state,
            "residual_updates": updates,
            "authorizes_promotion": False,
        },
        "forecast_id",
    )

    return primary, {"ridge": ridge, "volatility20": volatility, "reversal60": reversal}


def _forecast_arms(masked, support, plan, models):
    enough = int(support.sum()) >= plan.min_assets
    arms = []
    for model in models:
        scores = masked[model]
        weights = pd.Series(0.0, index=plan.symbols)
        if enough:
            weights.loc[support] = basket_weights(scores.loc[support], plan.top_k)
        arms.append(
            {
                "model": model,
                "scores": _nullable(scores),
                "weights": weights.to_dict(),
                "status": "scored" if enough else "abstained",
            }
        )
    return arms


def compute_daily_forecasts(
    batch, sessions, plan, *, decision_date: date, fit_cutoff, receipts, previous_residual_state=None
):
    """Canonical four-arm primary forecast, independent of any companion enrollment."""
    primary, _ = _forecast_calculation(
        batch,
        sessions,
        plan,
        decision_date=decision_date,
        fit_cutoff=fit_cutoff,
        receipts=receipts,
        previous_residual_state=previous_residual_state,
    )
    return primary


def _validate_comparisons(comparisons, plan, decision_date):
    if (
        not isinstance(comparisons, tuple)
        or len(comparisons) > MAX_DAILY_COMPARISONS
        or any(not isinstance(comparison, DailyComparisonPlan) for comparison in comparisons)
        or len({comparison.comparison_id for comparison in comparisons}) != len(comparisons)
    ):
        raise ValueError("Bounded unique frozen daily comparisons required")
    for comparison in comparisons:
        comparison.validate_parent(plan)
        if decision_date < comparison.first_decision_date:
            raise ValueError("Daily comparison cannot create a forecast before its first declared date")
    return tuple(sorted(comparisons, key=lambda comparison: comparison.comparison_id))


def _comparison_binding(comparison, primary):
    return {
        "comparison_id": comparison.comparison_id,
        "comparison_plan_id": comparison.identity,
        "comparison_plan": comparison.document(),
        "primary_forecast_id": primary["forecast_id"],
    }


def _companion_forecast(primary, scores, comparison, plan):
    stages = {
        name: pd.Series(evidence["mask"])
        for name, evidence in primary["stage_support"].items()
        if name not in ("finite_residual", "common")
    }
    support = pd.concat(stages.values(), axis=1).all(axis=1)
    stages["common"] = support
    masked = {"ridge": scores["ridge"].where(support), "volatility20": scores["volatility20"].where(support)}
    masked["rank_blend"] = (
        scores["reversal60"].where(support).rank(method="average", pct=True)
        + masked["volatility20"].rank(method="average", pct=True)
    ) / 2
    enough = int(support.sum()) >= plan.min_assets
    return _seal(
        {
            "version": DAILY_COMPARISON_FORECAST_VERSION,
            **{key: deepcopy(primary[key]) for key in COMPARISON_SHARED_FIELDS},
            **_comparison_binding(comparison, primary),
            "common_support": support.to_dict(),
            "stage_support": {
                name: {"count": int(mask.sum()), "mask": mask.to_dict()} for name, mask in stages.items()
            },
            "status": "scored" if enough else "unavailable",
            "reason": None if enough else "insufficient_common_breadth",
            "arms": _forecast_arms(masked, support, plan, DAILY_BASELINE_MODELS),
        },
        "forecast_id",
    )


def compute_daily_forecast_bundle(
    batch, sessions, plan, *, decision_date: date, fit_cutoff, receipts, previous_residual_state=None, comparisons=()
):
    """One fit/vintage/state update; independently sealed, prospectively pinned comparisons."""
    comparisons = _validate_comparisons(comparisons, plan, decision_date)
    primary, scores = _forecast_calculation(
        batch,
        sessions,
        plan,
        decision_date=decision_date,
        fit_cutoff=fit_cutoff,
        receipts=receipts,
        previous_residual_state=previous_residual_state,
    )
    return {
        "primary": primary,
        "companions": {
            comparison.comparison_id: _companion_forecast(primary, scores, comparison, plan)
            for comparison in comparisons
        },
    }


def _outcome_observations(forecast, batch, sessions, plan, *, receipts, observed_at):
    observed_at = utc_timestamp(observed_at)
    decision_date = date.fromisoformat(forecast["decision_date"])
    window = plan.decision_window(decision_date, sessions)
    if not window.outcome_available_at <= observed_at < window.outcome_expires_at:
        raise ValueError("Daily forecast outcome is immature or outside its observation window")
    if (forecast["entry_date"], forecast["exit_date"], forecast["economic_scheduled"]) != (
        window.entry_date.isoformat(),
        window.exit_date.isoformat(),
        window.economic_scheduled,
    ):
        raise ValueError("Observed calendar no longer matches the immutable forecast endpoints")
    retained_receipts = _receipts(
        receipts, plan.acquisition_symbols, earliest=window.outcome_available_at, cutoff=observed_at, strict=False
    )
    panel, _clock = _panel(batch, sessions, plan, window.exit_date)
    hashes = {symbol: frame_digest(frame) for symbol, frame in panel.frames.items()}
    labels = {}
    entry, exit_ = pd.Timestamp(window.entry_date, tz=ET_TZ), pd.Timestamp(window.exit_date, tz=ET_TZ)
    for symbol in plan.symbols:
        frame = panel.frames[symbol]
        opening, closing = frame.at[entry, "open"], frame.at[exit_, "close"]
        volumes = frame.loc[[entry, exit_], "volume"]
        valid = (
            np.isfinite([opening, closing]).all()
            and opening > 0
            and closing > 0
            and np.isfinite(volumes).all()
            and volumes.gt(0).all()
        )
        labels[symbol] = {
            "value": float(closing / opening - 1) if valid else None,
            "status": "observed" if valid else "unavailable",
            "entry_date": window.entry_date.isoformat(),
            "exit_date": window.exit_date.isoformat(),
            "received_at": retained_receipts[symbol]["received_at"],
            "source_hash": hashes[symbol],
        }
    return window, observed_at, retained_receipts, hashes, labels


def _evaluate_outcome(forecast, observations, plan, *, models, version, binding=None):
    window, observed_at, retained_receipts, hashes, labels = observations
    decision_date = date.fromisoformat(forecast["decision_date"])
    returns = pd.Series({s: row["value"] for s, row in labels.items()}, dtype=float)
    timestamp = pd.Timestamp(decision_date, tz=ET_TZ)
    index = pd.DatetimeIndex([timestamp])
    evaluated = []
    if [arm["model"] for arm in forecast["arms"]] != list(models):
        raise ValueError("Complete immutable daily forecast arms required")
    for arm in forecast["arms"]:
        scores = pd.Series(arm["scores"], index=plan.symbols, dtype=float)
        weights = pd.Series(arm["weights"], index=plan.symbols, dtype=float)
        known = np.isfinite(scores)
        expected = pd.Series(0.0, index=plan.symbols)
        if int(known.sum()) >= plan.min_assets:
            expected.loc[known] = basket_weights(scores.loc[known], plan.top_k)
        if not weights.equals(expected):
            raise ValueError("Immutable daily forecast weights differ from the declared score policy")
        missing = list(returns.index[known & ~np.isfinite(returns)])
        ic_scores = scores if not missing else scores * np.nan
        report = cross_sectional_ic(
            pd.DataFrame([ic_scores], index=index),
            pd.DataFrame([returns], index=index),
            pd.Series("prospective", index=index),
            ICPolicy(min_assets=plan.min_assets, hac_lags=plan.target.horizon_bars),
            expected_index=index,
            target=plan.target,
        )
        observation = report.observations[0]
        evaluated.append(
            {
                "model": arm["model"],
                "ic": observation.ic,
                "ic_reason": observation.reason,
                "ic_pairs": observation.pairs,
                "missing_label_symbols": missing,
                "basket": evaluate_frozen_basket(weights, returns, costs_bps=plan.costs_bps)
                if window.economic_scheduled and forecast["status"] == "scored"
                else None,
            }
        )
    predicted = [symbol for symbol, available in forecast["common_support"].items() if available]
    complete = (
        bool(predicted)
        and forecast["status"] == "scored"
        and all(labels[symbol]["value"] is not None for symbol in predicted)
    )
    return _seal(
        {
            "version": version,
            **(binding or {}),
            "plan_id": plan.identity,
            "forecast_id": forecast["forecast_id"],
            "decision_date": forecast["decision_date"],
            "observed_at": observed_at.isoformat(),
            "status": "complete" if complete else "unavailable",
            "reason": None
            if complete
            else "source_qualified_labels_unavailable"
            if forecast["status"] == "scored"
            else "forecast_unavailable",
            "economic_scheduled": window.economic_scheduled,
            "receipts": retained_receipts,
            "input_hashes": hashes,
            "vintage_id": document_hash({"input_hashes": hashes, "receipts": retained_receipts}),
            "labels": labels,
            "arms": evaluated,
            "authorizes_promotion": False,
        },
        "outcome_id",
    )


def evaluate_daily_outcomes(forecast, batch, sessions, plan, *, receipts, observed_at):
    """Canonical primary outcome; unscored primary forecasts have no basket payoff."""
    _verify(forecast, "forecast_id", DAILY_FORECAST_VERSION, plan)
    observations = _outcome_observations(forecast, batch, sessions, plan, receipts=receipts, observed_at=observed_at)
    return _evaluate_outcome(forecast, observations, plan, models=DAILY_MODELS, version=DAILY_OUTCOME_VERSION)


def _verify_companion(companion, primary, comparison_id, plan):
    _verify(companion, "forecast_id", DAILY_COMPARISON_FORECAST_VERSION, plan)
    comparison = DailyComparisonPlan.from_document(companion.get("comparison_plan"))
    _validate_comparisons((comparison,), plan, date.fromisoformat(primary["decision_date"]))
    expected = _comparison_binding(comparison, primary)
    if (
        comparison_id != comparison.comparison_id
        or any(companion.get(key) != value for key, value in expected.items())
        or any(companion.get(key) != primary[key] for key in COMPARISON_SHARED_FIELDS)
    ):
        raise ValueError("Daily companion does not match its frozen primary forecast and comparison")
    return comparison


def validate_daily_comparison_forecast(companion, primary, comparison: DailyComparisonPlan, parentplan) -> None:
    """Validate immutable evidence against the caller's authoritative enrollment pin."""
    _verify(primary, "forecast_id", DAILY_FORECAST_VERSION, parentplan)
    _validate_comparisons((comparison,), parentplan, date.fromisoformat(primary["decision_date"]))
    embedded = _verify_companion(companion, primary, comparison.comparison_id, parentplan)
    if embedded.identity != comparison.identity:
        raise ValueError("Daily companion differs from the pinned comparison protocol")


def evaluate_daily_outcome_bundle(primary, companions, batch, sessions, plan, *, receipts, observed_at):
    """One observed label vintage evaluates each previously frozen comparison independently."""
    _verify(primary, "forecast_id", DAILY_FORECAST_VERSION, plan)
    if not isinstance(companions, dict) or len(companions) > MAX_DAILY_COMPARISONS:
        raise ValueError("Bounded sealed daily companion mapping required")
    comparisons = {}
    for key, companion in companions.items():
        if not isinstance(companion, dict):
            raise TypeError("Sealed daily companion document required")
        comparison = DailyComparisonPlan.from_document(companion.get("comparison_plan"))
        if key != comparison.comparison_id:
            raise ValueError("Daily companion mapping differs from its declared comparison identity")
        validate_daily_comparison_forecast(companion, primary, comparison, plan)
        comparisons[key] = comparison
    observations = _outcome_observations(primary, batch, sessions, plan, receipts=receipts, observed_at=observed_at)
    return {
        "primary": _evaluate_outcome(primary, observations, plan, models=DAILY_MODELS, version=DAILY_OUTCOME_VERSION),
        "companions": {
            key: _evaluate_outcome(
                companions[key],
                observations,
                plan,
                models=DAILY_BASELINE_MODELS,
                version=DAILY_COMPARISON_OUTCOME_VERSION,
                binding=_comparison_binding(comparison, primary),
            )
            for key, comparison in sorted(comparisons.items())
        },
    }
