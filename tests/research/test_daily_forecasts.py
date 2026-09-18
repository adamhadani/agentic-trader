"""Receipt-aware daily forecasts remain immutable before their H20 outcomes arrive."""

import copy
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import agentic_trader.research.alpha.daily_forecasts as daily_module
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_forecasts import (
    compute_daily_forecast_bundle,
    compute_daily_forecasts,
    evaluate_daily_outcome_bundle,
    evaluate_daily_outcomes,
)
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.daily_plan import DailyComparisonPlan, DailyPanelPlan
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.factor_features import (
    ResidualMomentumSpec,
    factor_residuals_at,
    residual_momentum_features,
)
from agentic_trader.research.alpha.panel import align_daily_panel
from agentic_trader.research.alpha.panel_forecast import evaluate_frozen_basket
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


@pytest.fixture(scope="module")
def residual_case():
    rng = np.random.default_rng(319)
    clock = pd.date_range("2023-01-02", periods=140, freq="B", tz="America/New_York")
    factors = tuple(f"F{i}" for i in range(9))
    closes = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.004, (len(clock), 10)), axis=0), index=clock, columns=("AAA", *factors)
    )
    volumes = pd.DataFrame(1000.0, index=clock, columns=closes.columns)
    return closes, volumes, factors


def test_single_residual_fit_exactly_matches_historical_kernel_and_ignores_future(residual_case):
    closes, volumes, factors = residual_case
    full = residual_momentum_features(closes, volumes, symbols=("AAA",), factors=factors, feed="alpaca:iex")
    current = factor_residuals_at(closes, volumes, symbols=("AAA",), factors=factors, feed="alpaca:iex", position=132)
    assert current.fits == [full.fits[132]]
    pd.testing.assert_series_equal(current.residuals, full.residuals.iloc[132], check_names=False)
    pd.testing.assert_series_equal(current.loadings.loc["AAA"], full.loadings["AAA"].iloc[132], check_names=False)
    changed = closes.copy()
    changed.iloc[133:] *= 7
    revised = factor_residuals_at(changed, volumes, symbols=("AAA",), factors=factors, feed="alpaca:iex", position=132)
    assert revised.fits == current.fits


@pytest.mark.parametrize("missing_held", [False, True])
def test_immutable_basket_evaluation_preserves_unknown_outcomes_and_exact_fees(missing_held):
    weights = pd.Series({"AAA": 0.5, "BBB": -0.5, "CCC": 0.0})
    outcomes = pd.Series({"AAA": 0.1, "BBB": -0.1, "CCC": np.nan})
    if missing_held:
        outcomes.BBB = np.nan
    result = evaluate_frozen_basket(weights, outcomes, costs_bps=(1.0, 5.0))
    assert result["missing_held_symbols"] == (["BBB"] if missing_held else [])
    assert result["entry_gross"] == 1.0
    if missing_held:
        assert result["gross_return"] is None and result["turnover"] is None
        assert all(row["net_return"] is None for row in result["costs"])
    else:
        assert result["gross_return"] == pytest.approx(0.1)
        assert result["turnover"] == pytest.approx(2.0)
        assert result["costs"][1]["net_return"] == pytest.approx(0.099)


@pytest.fixture(scope="module")
def daily_case():
    rng = np.random.default_rng(932)
    clock = pd.date_range("2021-01-04", periods=750, freq="B", tz="America/New_York")
    symbols, factors = tuple(f"A{i:02d}" for i in range(18)), tuple(f"F{i}" for i in range(9))
    factor_returns = rng.normal(0, 0.005, (len(clock), 9))
    stock_returns = factor_returns @ rng.normal(0, 0.25, (9, 18)) + rng.normal(0.0002, 0.008, (len(clock), 18))
    values = np.column_stack([stock_returns, factor_returns])
    closes = 100 * np.cumprod(1 + values, axis=0)
    opens = np.vstack([np.full(27, 100), closes[:-1]])
    frames = {}
    for number, symbol in enumerate((*symbols, *factors)):
        frame = pd.DataFrame(
            {
                "open": opens[:, number],
                "close": closes[:, number],
                "volume": 1000.0,
                "high": np.maximum(opens[:, number], closes[:, number]) * 1.001,
                "low": np.minimum(opens[:, number], closes[:, number]) * 0.999,
            },
            index=clock,
        )
        frame.attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
        frames[symbol] = frame
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )

    def decision_window(day, observed):
        dates = [session.date for session in observed]
        position = dates.index(day)
        native_end = pd.Timestamp(day + timedelta(days=1), tz="America/New_York")
        exit_date = dates[position + 20]
        outcome_end = pd.Timestamp(exit_date + timedelta(days=1), tz="America/New_York")
        return SimpleNamespace(
            decision_date=day,
            native_closed_at=native_end,
            available_at=native_end + pd.Timedelta(minutes=30),
            expires_at=native_end + pd.Timedelta(hours=3),
            entry_date=dates[position + 1],
            entry_open=observed[position + 1].open,
            exit_date=exit_date,
            outcome_available_at=outcome_end + pd.Timedelta(minutes=30),
            outcome_expires_at=outcome_end + pd.Timedelta(hours=3),
            economic_scheduled=(position - 680) % 20 == 0,
        )

    plan = SimpleNamespace(
        campaign_id="daily-fixture",
        end_date=clock[-1].date(),
        start_date=clock[680].date(),
        symbols=symbols,
        factor_symbols=factors,
        acquisition_symbols=tuple(sorted((*symbols, *factors))),
        feed="alpaca:iex",
        adjustment="all",
        target=ForecastTarget("1d", 20, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        top_k=8,
        min_assets=16,
        costs_bps=(1.0, 5.0),
        ridge_alpha=100.0,
        train_sessions=504,
        min_train_sessions=126,
        min_train_rows=500,
        history_sessions=61,
        identity="a" * 64,
        residual_spec=ResidualMomentumSpec(),
        decision_window=decision_window,
        ridge_features={
            "momentum60": "roc(close,60)",
            "reversal5": "-roc(close,5)",
            "volatility20": "realized_vol(returns,20)",
        },
    )
    return SimpleNamespace(clock=clock, frames=frames, sessions=sessions, plan=plan)


def capture(case, position, *, frames=None):
    frames = case.frames if frames is None else frames
    day = case.clock[position].date()
    window = case.plan.decision_window(day, case.sessions)
    requested, received = window.available_at + pd.Timedelta(seconds=1), window.available_at + pd.Timedelta(seconds=2)
    return (
        DailyStudyInputs({s: f.iloc[: position + 1].copy() for s, f in frames.items()}),
        {s: {"requested_at": requested.isoformat(), "received_at": received.isoformat()} for s in frames},
        received + pd.Timedelta(seconds=1),
    )


def forecast(case, position, previous=None, *, frames=None):
    batch, receipts, cutoff = capture(case, position, frames=frames)
    return compute_daily_forecasts(
        batch,
        case.sessions,
        case.plan,
        decision_date=case.clock[position].date(),
        fit_cutoff=cutoff,
        receipts=receipts,
        previous_residual_state=previous,
    )


@pytest.fixture(scope="module")
def initial_forecast(daily_case):
    return forecast(daily_case, 699)


@pytest.fixture(scope="module")
def current_forecast(daily_case, initial_forecast):
    return forecast(daily_case, 700, initial_forecast["residual_state"])


def test_first_receipt_bootstraps_history_without_backdating_or_forward_credit(daily_case, initial_forecast):
    state = initial_forecast["residual_state"]
    assert len(state["rows"]) == 252
    for row in state["rows"]:
        if row["date"] < daily_case.plan.start_date.isoformat():
            assert row["kind"] == "historical_bootstrap"
            assert row["observed_at"] == initial_forecast["latest_received_at"]
        elif row["date"] == initial_forecast["decision_date"]:
            assert row["kind"] == "prospective"
            assert row["observed_at"] == initial_forecast["latest_received_at"]
        else:
            assert row["kind"] == "missed"
            assert all(value is None for value in row["values"].values())
            assert row["observed_at"] is row["source_hash"] is None
    assert initial_forecast["residual_updates"]["bootstrap"] is True
    assert initial_forecast["authorizes_promotion"] is False


def test_late_first_capture_never_backfills_campaign_innovations(daily_case):
    result = forecast(daily_case, 703)
    rows = result["residual_state"]["rows"]
    missed = [row for row in rows if daily_case.plan.start_date.isoformat() <= row["date"] < result["decision_date"]]
    assert len(missed) == 23
    assert all(row["kind"] == "missed" and all(value is None for value in row["values"].values()) for row in missed)
    assert result["status"] == "unavailable"
    assert not any(result["common_support"].values())
    stages = result["stage_support"]
    assert stages["finite_residual"]["count"] == stages["common"]["count"] == 0
    assert all(
        stages[name]["count"] == 18
        for name in ("feature_eligible", "finite_ridge", "finite_reversal", "finite_volatility")
    )
    assert all(len(stage["mask"]) == 18 and sum(stage["mask"].values()) == stage["count"] for stage in stages.values())
    accepted_dates = {row["return_bar"][:10] for row in result["residual_updates"]["fits"]}
    assert accepted_dates.isdisjoint({row["date"] for row in missed})


def test_all_arms_share_preceding_only_support_and_train_cutoff(daily_case, current_forecast):
    result = current_forecast
    assert result["economic_scheduled"] is True
    assert {row["model"] for row in result["arms"]} == {"rank_blend", "volatility20", "residual_rank_blend", "ridge"}
    assert len(result["common_support"]) == 18 and all(result["common_support"].values())
    for arm in result["arms"]:
        assert set(arm["scores"]) == set(arm["weights"]) == set(daily_case.plan.symbols)
        assert sum(arm["weights"].values()) == pytest.approx(0)
        assert sum(abs(w) for w in arm["weights"].values()) == pytest.approx(1)
    fit = result["ridge_fit"]
    assert fit["training_sessions"] == 504 and fit["training_rows"] == 504 * 18
    assert fit["last_label_date"] == daily_case.clock[699].date().isoformat()
    assert pd.Timestamp(fit["last_label_received_at"]) < pd.Timestamp(result["fit_cutoff"])
    assert len(result["residual_updates"]["fits"]) == 18 and not result["residual_updates"]["bootstrap"]
    assert result["status"] == "scored"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("fault", ["equal_cutoff", "future_receipt", "before_native_end", "late_fit"])
def test_invalid_receipt_or_cutoff_cannot_generate_forecast(daily_case, initial_forecast, fault):
    c = daily_case
    batch, receipts, cutoff = capture(c, 700)
    symbol = c.plan.symbols[0]
    if fault == "equal_cutoff":
        receipts[symbol]["received_at"] = cutoff.isoformat()
    elif fault == "future_receipt":
        receipts[symbol]["received_at"] = (cutoff + pd.Timedelta(seconds=1)).isoformat()
    elif fault == "before_native_end":
        receipts[symbol]["requested_at"] = c.clock[700].isoformat()
    else:
        cutoff = c.plan.decision_window(c.clock[700].date(), c.sessions).expires_at
    with pytest.raises(ValueError):
        compute_daily_forecasts(
            batch,
            c.sessions,
            c.plan,
            decision_date=c.clock[700].date(),
            fit_cutoff=cutoff,
            receipts=receipts,
            previous_residual_state=initial_forecast["residual_state"],
        )


def test_current_label_cannot_leak_into_ridge_fit(daily_case, initial_forecast, current_forecast):
    c = daily_case
    frames = {s: f.copy() for s, f in c.frames.items()}
    for symbol in c.plan.symbols:
        frames[symbol].loc[c.clock[700], ["open", "high", "low", "close"]] *= 2
    changed = forecast(c, 700, initial_forecast["residual_state"], frames=frames)
    assert changed["ridge_fit"] == current_forecast["ridge_fit"]


def test_zero_volume_training_endpoint_is_excluded_without_removing_its_session(
    daily_case, initial_forecast, current_forecast
):
    c = daily_case
    frames = {s: f.copy() for s, f in c.frames.items()}
    frames[c.plan.symbols[0]].loc[c.clock[699], "volume"] = 0
    changed = forecast(c, 700, initial_forecast["residual_state"], frames=frames)
    assert changed["ridge_fit"]["training_rows"] == current_forecast["ridge_fit"]["training_rows"] - 1
    assert changed["ridge_fit"]["expected_training_sessions"] == 504


def test_missing_history_withholds_common_forecasts_and_never_falls_back_to_other_arms(daily_case, initial_forecast):
    c = daily_case
    batch, receipts, cutoff = capture(c, 700)
    for symbol in c.plan.symbols:
        batch.frames[symbol] = batch.frames[symbol].iloc[-50:]
    result = compute_daily_forecasts(
        batch,
        c.sessions,
        c.plan,
        decision_date=c.clock[700].date(),
        fit_cutoff=cutoff,
        receipts=receipts,
        previous_residual_state=initial_forecast["residual_state"],
    )
    assert result["status"] == "unavailable" and not any(result["common_support"].values())
    assert result["ridge_fit"]["status"] == "unavailable"
    assert all(arm["status"] == "abstained" and not any(arm["weights"].values()) for arm in result["arms"])


def test_prior_residuals_survive_revisions_and_missed_dates_are_never_backfilled(daily_case, initial_forecast):
    c = daily_case
    frames = {s: f.copy() for s, f in c.frames.items()}
    for symbol in c.plan.symbols:
        frames[symbol].loc[c.clock[600], ["open", "high", "low", "close"]] *= 1.1
    original = copy.deepcopy(initial_forecast["residual_state"])
    changed = forecast(c, 703, original, frames=frames)
    previous = {row["date"]: row for row in original["rows"]}
    for row in changed["residual_state"]["rows"]:
        if row["date"] in previous:
            assert row == previous[row["date"]]
        elif row["date"] < c.clock[703].date().isoformat():
            assert row["kind"] == "missed" and all(value is None for value in row["values"].values())
    assert original == initial_forecast["residual_state"]


def outcomes(case, decision, *, frames=None, position=720):
    batch, receipts, observed = capture(case, position, frames=frames)
    return evaluate_daily_outcomes(decision, batch, case.sessions, case.plan, receipts=receipts, observed_at=observed)


def test_outcomes_use_one_new_adjustment_vintage_and_never_rewrite_forecast(daily_case, current_forecast):
    c = daily_case
    frames = {s: f.copy() for s, f in c.frames.items()}
    symbol = c.plan.symbols[0]
    frames[symbol].loc[: c.clock[701], ["open", "high", "low", "close"]] *= 0.5
    before = copy.deepcopy(current_forecast)
    result = outcomes(c, current_forecast, frames=frames)
    expected = frames[symbol].close.iloc[720] / frames[symbol].open.iloc[701] - 1
    assert result["labels"][symbol]["value"] == pytest.approx(expected)
    assert current_forecast == before
    assert result["input_hashes"][symbol] != current_forecast["input_hashes"][symbol]
    assert result["authorizes_promotion"] is False
    assert result["status"] == "complete"
    json.dumps(result, allow_nan=False)


def test_missing_held_endpoint_withholds_payoff_and_whole_date_ic(daily_case, current_forecast):
    c = daily_case
    frames = {s: f.copy() for s, f in c.frames.items()}
    held = next(symbol for symbol, weight in current_forecast["arms"][0]["weights"].items() if weight)
    frames[held].loc[c.clock[720], "volume"] = 0
    result = outcomes(c, current_forecast, frames=frames)
    arm = next(row for row in result["arms"] if row["model"] == current_forecast["arms"][0]["model"])
    assert result["labels"][held]["value"] is None
    assert arm["basket"]["gross_return"] is None and arm["ic"] is None
    assert held in arm["missing_label_symbols"]
    assert result["status"] == "unavailable"


def test_unscheduled_day_observes_ic_without_claiming_overlapping_basket_economics(daily_case, initial_forecast):
    result = outcomes(daily_case, initial_forecast, position=719)
    assert not result["economic_scheduled"] and result["status"] == "complete"
    assert all(arm["basket"] is None and arm["ic"] is not None for arm in result["arms"])


def test_residual_state_tamper_cannot_be_accepted(daily_case, initial_forecast):
    state = copy.deepcopy(initial_forecast["residual_state"])
    state["rows"][0]["values"][daily_case.plan.symbols[0]] = 999
    with pytest.raises(ValueError, match="Immutable"):
        forecast(daily_case, 700, state)


def test_immature_outcomes_and_modified_decision_fail_closed(daily_case, current_forecast):
    with pytest.raises(ValueError):
        outcomes(daily_case, current_forecast, position=719)
    changed = copy.deepcopy(current_forecast)
    changed["arms"][0]["weights"][daily_case.plan.symbols[0]] += 0.1
    with pytest.raises(ValueError, match="forecast"):
        outcomes(daily_case, changed)


@pytest.mark.parametrize("observed", [0, 2])
def test_sdk_object_ohlcv_is_canonical_numeric_without_fabricating_coverage(observed):
    clock = pd.date_range("2026-01-05", periods=3, freq="B", tz="America/New_York")
    frame = pd.DataFrame(
        {
            "Open": [100.0] * observed,
            "High": [101.0] * observed,
            "Low": [99.0] * observed,
            "Close": [100.0] * observed,
            "Volume": [1000.0] * observed,
        },
        index=clock[:observed],
        dtype=object,
    )
    frame.attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d", source_marker="preserved")
    original = frame.copy()
    panel = align_daily_panel({"AAA": frame}, clock, feed="alpaca:iex", adjustment="all")
    aligned = panel.frames["AAA"]
    assert all(dtype == np.dtype(float) for dtype in aligned.dtypes)
    assert aligned.index.equals(clock) and aligned.attrs == frame.attrs
    assert aligned.iloc[observed:].isna().all().all()
    assert panel.coverage["AAA"]["observed"] == observed
    assert panel.coverage["AAA"]["missing_dates"] == [t.date().isoformat() for t in clock[observed:]]
    pd.testing.assert_frame_equal(frame, original)


@pytest.fixture(scope="module")
def comparison_case(daily_case):
    clock = pd.bdate_range(daily_case.clock[0], periods=800)
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    comparison = DailyComparisonPlan(
        "baselines", daily_case.plan.campaign_id, daily_case.plan.identity, daily_case.clock[700].date()
    )
    return SimpleNamespace(**{**daily_case.__dict__, "sessions": sessions}, comparison=comparison)


def forecast_bundle(case, position, previous=None, *, frames=None, comparisons=None):
    batch, receipts, cutoff = capture(case, position, frames=frames)
    return compute_daily_forecast_bundle(
        batch,
        case.sessions,
        case.plan,
        decision_date=case.clock[position].date(),
        fit_cutoff=cutoff,
        receipts=receipts,
        previous_residual_state=previous,
        comparisons=(case.comparison,) if comparisons is None else comparisons,
    )


@pytest.fixture(scope="module")
def baseline_bundle(comparison_case, initial_forecast):
    return forecast_bundle(comparison_case, 720, initial_forecast["residual_state"])


def test_baselines_score_on_their_own_support_when_primary_residuals_are_missing(comparison_case, baseline_bundle):
    primary = baseline_bundle["primary"]
    companion = baseline_bundle["companions"]["baselines"]
    assert primary["status"] == "unavailable" and not any(primary["common_support"].values())
    assert companion["status"] == "scored" and sum(companion["common_support"].values()) == 18
    assert [a["model"] for a in companion["arms"]] == ["rank_blend", "volatility20", "ridge"]
    assert companion["comparison_plan"] == comparison_case.comparison.document()
    assert companion["comparison_plan_id"] == comparison_case.comparison.identity
    assert companion["primary_forecast_id"] == primary["forecast_id"]
    for field in ("receipts", "vintage_id", "ridge_fit", "entry_date", "exit_date", "fit_cutoff"):
        assert companion[field] == primary[field]
    assert "residual_state" not in companion
    assert all(
        a["status"] == "scored" and sum(abs(w) for w in a["weights"].values()) == pytest.approx(1)
        for a in companion["arms"]
    )
    assert not companion["authorizes_promotion"]
    json.dumps(baseline_bundle, allow_nan=False)


def test_bundle_preserves_primary_forecast_and_computes_expensive_inputs_once(
    comparison_case, initial_forecast, monkeypatch
):
    c = comparison_case
    expected = forecast(c, 720, initial_forecast["residual_state"])
    calls = {}
    for name in ("build_forecast_estimator", "_features", "_residual_state"):
        original = getattr(daily_module, name)

        def tracked(*args, _original=original, _name=name, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(daily_module, name, tracked)
    actual = forecast_bundle(c, 720, initial_forecast["residual_state"])
    assert actual["primary"] == expected
    assert calls == dict.fromkeys(calls, 1) and len(calls) == 3


@pytest.mark.parametrize("failure", ["before_first_date", "duplicate", "too_many", "wrong_parent"])
def test_bundle_refuses_unbound_comparisons_before_computation(comparison_case, initial_forecast, failure, monkeypatch):
    c = comparison_case
    comparisons = (c.comparison,)
    if failure == "before_first_date":
        comparisons = (replace(c.comparison, first_decision_date=c.clock[721].date()),)
    elif failure == "duplicate":
        comparisons = (c.comparison, c.comparison)
    elif failure == "too_many":
        comparisons = tuple(replace(c.comparison, comparison_id=f"b{i}") for i in range(9))
    else:
        comparisons = (replace(c.comparison, parent_protocol_hash="b" * 64),)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid comparison reached fitting")

    monkeypatch.setattr(daily_module, "build_forecast_estimator", forbidden)
    with pytest.raises(ValueError):
        forecast_bundle(c, 720, initial_forecast["residual_state"], comparisons=comparisons)


def test_companion_scores_rerank_on_baseline_support_and_ignore_future_prices(
    comparison_case, initial_forecast, baseline_bundle
):
    c = comparison_case
    companion = baseline_bundle["companions"]["baselines"]
    scores = {row["model"]: pd.Series(row["scores"]) for row in companion["arms"]}
    reversal = pd.Series(
        {
            symbol: -((c.frames[symbol].close.iloc[720] / c.frames[symbol].close.iloc[660]) - 1)
            for symbol in c.plan.symbols
        }
    )
    expected = (reversal.rank(method="average", pct=True) + scores["volatility20"].rank(method="average", pct=True)) / 2
    pd.testing.assert_series_equal(scores["rank_blend"], expected, check_names=False)
    frames = {s: f.copy() for s, f in c.frames.items()}
    for frame in frames.values():
        frame.loc[c.clock[721] :, ["open", "high", "low", "close"]] *= 8
    assert forecast_bundle(c, 720, initial_forecast["residual_state"], frames=frames) == baseline_bundle


@pytest.mark.parametrize("missing", [None, "held", "unheld"])
def test_one_outcome_measurement_preserves_companion_unknowns_and_primary_abstention(
    comparison_case, baseline_bundle, missing, monkeypatch
):
    c = comparison_case
    companion = baseline_bundle["companions"]["baselines"]
    arm = next(a for a in companion["arms"] if a["model"] == "volatility20")
    frames = {s: f.copy() for s, f in c.frames.items()}
    affected = None
    if missing:
        affected = next(s for s, w in arm["weights"].items() if bool(w) == (missing == "held"))
        frames[affected].loc[c.clock[740], "volume"] = 0
    batch, receipts, observed = capture(c, 740, frames=frames)
    count = []
    original = daily_module._panel

    def measured(*args, **kwargs):
        count.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(daily_module, "_panel", measured)
    result = evaluate_daily_outcome_bundle(
        baseline_bundle["primary"],
        baseline_bundle["companions"],
        batch,
        c.sessions,
        c.plan,
        receipts=receipts,
        observed_at=observed,
    )
    assert len(count) == 1
    assert result["primary"]["status"] == "unavailable"
    assert all(row["basket"] is None for row in result["primary"]["arms"])
    outcome = result["companions"]["baselines"]
    assert outcome["status"] == ("unavailable" if missing else "complete")
    row = next(a for a in outcome["arms"] if a["model"] == "volatility20")
    assert (row["ic"] is None) == bool(missing)
    assert (row["basket"]["gross_return"] is None) == (missing == "held")
    assert row["missing_label_symbols"] == ([affected] if missing else [])
    assert outcome["labels"] == result["primary"]["labels"]


def test_unscored_companion_never_claims_cash_payoff(comparison_case, initial_forecast):
    c = comparison_case
    frames = {s: f.iloc[:721].iloc[-50:].copy() for s, f in c.frames.items()}
    bundle = forecast_bundle(c, 720, initial_forecast["residual_state"], frames=frames)
    assert bundle["companions"]["baselines"]["status"] == "unavailable"
    batch, receipts, observed = capture(c, 740)
    result = evaluate_daily_outcome_bundle(
        bundle["primary"], bundle["companions"], batch, c.sessions, c.plan, receipts=receipts, observed_at=observed
    )
    assert all(a["basket"] is None for a in result["companions"]["baselines"]["arms"])


@pytest.mark.parametrize(
    "field,value", [("primary_forecast_id", "b" * 64), ("comparison_plan_id", "c" * 64), ("comparison_id", "other")]
)
def test_companion_outcome_rejects_detached_binding_even_with_resealed_document(
    comparison_case, baseline_bundle, field, value
):
    c = comparison_case
    companions = copy.deepcopy(baseline_bundle["companions"])
    companions["baselines"][field] = value
    document = companions["baselines"]
    document["forecast_id"] = document_hash({k: v for k, v in document.items() if k != "forecast_id"})
    batch, receipts, observed = capture(c, 740)
    with pytest.raises(ValueError):
        evaluate_daily_outcome_bundle(
            baseline_bundle["primary"], companions, batch, c.sessions, c.plan, receipts=receipts, observed_at=observed
        )


@pytest.mark.parametrize("position,primary_scored", [(420, True), (421, False), (651, False), (652, True)])
def test_one_missed_innovation_has_exact_231_decision_effect_without_disabling_baselines(
    daily_case, position, primary_scored
):
    original_case = daily_case
    plan = DailyPanelPlan(
        "gap-boundary",
        original_case.plan.symbols,
        original_case.plan.factor_symbols,
        original_case.clock[400].date(),
        original_case.clock[729].date(),
        original_case.clock[0].date(),
        "a" * 64,
        "b" * 64,
    )
    c = SimpleNamespace(
        **{**original_case.__dict__, "plan": plan},
        comparison=DailyComparisonPlan("baseline", plan.campaign_id, plan.identity, plan.start_date),
    )
    prior_clock = c.clock[:position][-252:]
    received = c.plan.decision_window(c.clock[position - 1].date(), c.sessions).available_at.isoformat()
    rows = [
        {
            "date": t.date().isoformat(),
            "values": {
                s: None if t == c.clock[400] else float(np.sin(i / 7 + j)) for j, s in enumerate(c.plan.symbols)
            },
            "kind": "missed"
            if t == c.clock[400]
            else "historical_bootstrap"
            if t.date() < plan.start_date
            else "prospective",
            "observed_at": None if t == c.clock[400] else received,
            "source_hash": None if t == c.clock[400] else "a" * 64,
        }
        for i, t in enumerate(prior_clock)
    ]
    state = {
        "version": daily_module.DAILY_RESIDUAL_VERSION,
        "plan_id": plan.identity,
        "symbols": list(plan.symbols),
        "through_date": prior_clock[-1].date().isoformat(),
        "rows": rows,
        "authorizes_promotion": False,
    }
    state["state_id"] = document_hash(state)
    original = copy.deepcopy(state)
    result = forecast_bundle(c, position, state)
    assert (result["primary"]["status"] == "scored") == primary_scored
    assert result["companions"]["baseline"]["status"] == "scored"
    assert state == original
    retained = {row["date"]: row for row in result["primary"]["residual_state"]["rows"]}
    missing_date = c.clock[400].date().isoformat()
    if position <= 651:
        assert retained[missing_date] == next(row for row in rows if row["date"] == missing_date)
    else:
        assert missing_date not in retained


@pytest.mark.parametrize("changed_first_date", [False, True])
def test_public_companion_validator_requires_authoritative_enrollment_pin(
    comparison_case, baseline_bundle, changed_first_date
):
    c = comparison_case
    companion = copy.deepcopy(baseline_bundle["companions"]["baselines"])
    if changed_first_date:
        changed = replace(c.comparison, first_decision_date=c.clock[701].date())
        companion["comparison_plan"] = changed.document()
        companion["comparison_plan_id"] = changed.identity
        companion["forecast_id"] = document_hash({k: v for k, v in companion.items() if k != "forecast_id"})
        with pytest.raises(ValueError, match="pinned"):
            daily_module.validate_daily_comparison_forecast(companion, baseline_bundle["primary"], c.comparison, c.plan)
    else:
        assert (
            daily_module.validate_daily_comparison_forecast(companion, baseline_bundle["primary"], c.comparison, c.plan)
            is None
        )
