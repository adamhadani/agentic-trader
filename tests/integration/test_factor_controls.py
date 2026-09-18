"""Frozen factor hypotheses reuse retained artifacts and the durable research journal."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.factor_controls import compute_factor_controls
from agentic_trader.research.alpha.factor_controls_plan import FACTOR_MODELS, FactorControlsPlan
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_forecast import compute_panel_forecast
from agentic_trader.research.alpha.panel_forecast_plan import ForecastCohort, ForecastSelection, PanelForecastPlan
from agentic_trader.research.alpha.panel_study import PanelFold, PanelHypothesis
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService
from agentic_trader.research.alpha.retained_panel import RetainedPanelSource
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget
from agentic_trader.storage.models import DomainEventRecord


@pytest.fixture
def factor_parent_case():
    """Enough genuine causal history for production H20, 126/252 fits and HAC20."""
    clock = pd.date_range("2021-01-04", periods=460, freq="B", tz="America/New_York")
    equities = tuple(f"EQ{letter}" for letter in "ABCDEFGHIJKLMNOPQR")
    factors = tuple(f"ETF{letter}" for letter in "ABCDEFGHI")
    rng = np.random.default_rng(83412)
    factor_returns = rng.normal(0.0001, 0.004, (len(clock), len(factors)))
    exposures = rng.normal(0.15, 0.2, (len(factors), len(equities)))
    equity_returns = factor_returns @ exposures + rng.normal(
        0.0001, np.linspace(0.003, 0.012, len(equities)), (len(clock), len(equities))
    )
    close = 100 * np.cumprod(1 + np.column_stack((equity_returns, factor_returns)), axis=0)
    opening = np.vstack((np.full(len(equities) + len(factors), 100.0), close[:-1]))
    frames = {}
    for i, symbol in enumerate((*equities, *factors)):
        frames[symbol] = pd.DataFrame(
            {
                "open": opening[:, i],
                "high": np.maximum(opening[:, i], close[:, i]) * 1.001,
                "low": np.minimum(opening[:, i], close[:, i]) * 0.999,
                "close": close[:, i],
                "volume": 1000.0,
            },
            index=clock,
        )
        frames[symbol].attrs.update(feed="alpaca:iex", adjustment="all", timeframe="1d")
    plan = PanelForecastPlan(
        campaign_id="factor_integration_fixture",
        cohorts=(
            ForecastCohort("equities", equities, top_k=8, min_assets=16),
            ForecastCohort("sector_etfs", factors, top_k=3, min_assets=6),
        ),
        start=clock[0].date(),
        end=clock[-1].date(),
        folds=(PanelFold("evaluation", clock[380].date(), clock[-1].date()),),
        features=(
            PanelHypothesis("momentum60", "roc(close,60)"),
            PanelHypothesis("reversal5", "-roc(close,5)"),
            PanelHypothesis("volatility20", "realized_vol(returns,20)"),
        ),
        economic_features=("momentum60", "reversal5"),
        target=ForecastTarget("1d", 20, ForecastLabel.NEXT_OPEN_TO_CLOSE),
        costs_bps=(0.0, 1.0, 5.0),
        ic=ICPolicy(min_assets=16, min_observations=60, hac_lags=20, observations_per_year=252),
        selection=ForecastSelection("equities", "a" * 64, "b" * 64, "c" * 64, pd.Timestamp("2023-01-01T12:00Z")),
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    return SimpleNamespace(clock=clock, plan=plan, frames=frames, sessions=sessions, factors=factors, equities=equities)


def _hashes(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("fault", [None, "unknown_held_endpoint", "dataset_tamper", "parent_tamper"])
async def test_factor_study_retains_real_artifacts_causal_evidence_and_durable_charges(
    controls_repository, factor_parent_case, tmp_path, monkeypatch, fault
):
    repository, case = controls_repository, factor_parent_case
    parent_directory, output = tmp_path / "parent", tmp_path / "factor"
    parent_reads = []
    if fault == "unknown_held_endpoint":
        # The first basket is selected at 380; its unknown entry endpoint at381
        # cannot change that decision. Keep this observation in the original parent.
        for symbol in case.equities:
            case.frames[symbol].loc[case.clock[381], "volume"] = 0

    class SyntheticSource:
        def calendar(self, start, end):
            assert (start, end) == (case.plan.start, case.plan.end)
            parent_reads.append("calendar")
            return case.sessions

        def daily(self, symbol, start, end, feed, adjustment):
            assert (start, end, feed, adjustment) == (
                case.plan.start,
                case.plan.end,
                case.plan.feed,
                case.plan.adjustment,
            )
            parent_reads.append(symbol)
            return case.frames[symbol].copy(deep=True)

    acquisition = DailyAcquisitionConfig(min_request_interval_seconds=0)
    parent = await AlphaPanelService(
        repository, SyntheticSource(), acquisition=acquisition, compute=compute_panel_forecast
    ).run(case.plan, parent_directory, environment={"fixture": True}, as_of=pd.Timestamp("2023-01-02T00:00Z"))
    assert parent["status"] == "completed", parent
    assert parent_reads == ["calendar", *case.plan.acquisition_symbols]
    parent_inputs = json.loads((parent_directory / "inputs.json").read_bytes())
    plan = FactorControlsPlan(
        case.plan,
        "equities",
        hashlib.sha256((parent_directory / "result.json").read_bytes()).hexdigest(),
        case.factors,
    )
    assert case.plan.trial_count == 32 and plan.trial_count == 18
    if fault in ("dataset_tamper", "parent_tamper"):
        path = (
            parent_directory / parent_inputs["datasets"][case.equities[0]]["artifact"]
            if fault == "dataset_tamper"
            else parent_directory / "result.json"
        )
        with path.open("ab") as artifact:
            artifact.write(b"tampered fixture bytes")
    frozen_parent = _hashes(parent_directory)
    loop = asyncio.get_running_loop()
    retained_reads = []

    async def verify_reserved_before_first_read():
        assert (await repository.get("family/all"))["trial_count"] == 50
        async with repository.store.db.session_factory() as session:
            events = (await session.scalars(select(DomainEventRecord))).all()
        exclusions = [
            payload["value"]
            for event in events
            if (payload := json.loads(event.payload))["key"].startswith("external-observation/")
            and f"frozen plan {plan.identity}" in payload["value"]["reason"]
        ]
        assert len(exclusions) == 27
        assert {row["symbol"] for row in exclusions} == set(plan.acquisition_symbols)
        assert all(pd.Timestamp(row["start"]) == case.clock[0] for row in exclusions)
        assert all(pd.Timestamp(row["end"]) == case.clock[-1] + pd.DateOffset(days=1) for row in exclusions)
        assert json.loads((output / "manifest.json").read_bytes())["plan"] == plan.document()

    read_bytes = Path.read_bytes

    def guarded_read(path):
        if path.is_relative_to(parent_directory):
            if not retained_reads:
                asyncio.run_coroutine_threadsafe(verify_reserved_before_first_read(), loop).result(timeout=10)
            retained_reads.append(str(path.relative_to(parent_directory)))
        return read_bytes(path)

    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "read_bytes", guarded_read)
        source = RetainedPanelSource(parent_directory, plan)
        assert not retained_reads  # Construction cannot inspect parent artifacts.

        def compute(batch, clock, actual_plan, sessions):
            return compute_factor_controls(batch, clock, actual_plan, sessions, parent_result=source.parent_result)

        service = AlphaPanelService(repository, source, acquisition=acquisition, compute=compute)
        result = await service.run(plan, output, environment={"fixture": True, "input_origin": "retained_artifacts"})
    complete = fault not in ("dataset_tamper", "parent_tamper")
    assert result["status"] == ("completed" if complete else "failed"), result
    assert result["charged_trials"] == 18
    assert result["completed_comparisons"] == (18 if complete else 0)
    assert not result["authorizes_promotion"]
    assert retained_reads[0] == "result.json"
    assert _hashes(parent_directory) == frozen_parent
    assert parent_reads == ["calendar", *case.plan.acquisition_symbols]
    inputs = json.loads((output / "inputs.json").read_bytes())
    members = [json.loads((output / row["artifact"]).read_bytes()) for row in inputs["members"]]
    assert len(members) == 27 and {row["symbol"] for row in members} == set(plan.acquisition_symbols)
    for row in inputs["members"]:
        path = output / row["artifact"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        assert path.stat().st_mode & 0o777 == 0o600
    if complete:
        assert not inputs["failures"] and inputs["datasets"] == parent_inputs["datasets"]
        assert inputs["receipts"] != parent_inputs["receipts"]
        assert result["supervised_refitted_models"] == 0
        assert all(row["verified"] for row in result["parent_ridge_reproduced"])
        assert {trial["model"] for trial in result["trials"]} == set(FACTOR_MODELS)
        fit = next(
            row
            for row in result["factor_features"]["fits"]
            if row["symbol"] == case.equities[0] and row["return_bar"] == case.clock[127].isoformat()
        )
        assert fit["fit_status"] == "fitted" and fit["training_rows"] == fit["required_training_rows"] == 126
        assert fit["training_start"] == case.clock[1].isoformat()
        assert fit["training_end"] == case.clock[126].isoformat()
        assert fit["coefficient_names"] == ["intercept", *case.factors]
        assert len(fit["coefficients"]) == fit["rank"] == 10
        assert fit["residual"] is not None and fit["residual_reason"] is None
        assert len(fit["training_source_hash"]) == len(fit["evaluation_source_hash"]) == 64
        support = result["supports"][0]
        assert support["symbols"] == list(case.equities)
        assert all(support["common_eligible"][0])
        parent_ridge = next(t for t in parent["trials"] if t["model"] == "ridge" and t["cohort"] == "equities")
        ridge = next(t for t in result["trials"] if t["model"] == "ridge")
        assert ridge["predictions"]["values"][0] == parent_ridge["predictions"]["values"][0]
        for trial in result["trials"]:
            assert trial["outcome_scope"] == "positive_endpoint_volume"
            assert len(trial["baskets"]) == 3
            assert len(trial["ic"]["observations"]) == 60
            assert trial["baskets"][0]["status"] == ("unavailable" if fault else "observed")
            assert any(trial["baskets"][0]["weights"].values())
            for costs in trial["cost_summaries"]:
                assert costs["cost_bps"] in (1.0, 5.0)
                assert costs["curve_complete"] is (fault is None)
                if fault:
                    assert costs["missing_baskets"] >= 1 and costs["net_return"] is None
            if fault is None:
                assert trial["stage_counts"]["complete_ic_dates"] == 60
                assert trial["ic"]["folds"]["evaluation"]["hac_standard_error"] is not None
            else:
                assert trial["ic_missing_label_dates"][0] == case.clock[380].isoformat()
        for exposure in result["basket_factor_exposures"]:
            if exposure["decision_bar"] == case.clock[380].isoformat():
                assert exposure["status"] == "observed"
                assert not exposure["missing_held_symbols"]
                assert set(exposure["net_loadings"]) == set(case.factors)
        assert len(result["paired_comparisons"]) == 10
        for comparison in result["paired_comparisons"]:
            assert comparison["expected_baskets"] == len(comparison["observations"]) == 3
            assert comparison["complete"] is (fault is None)
            if fault:
                assert comparison["observations"][0]["difference"] is None
                assert comparison["missing_baskets"] >= 1
                assert comparison["complete_arithmetic_difference_sum"] is None
        assert len(result["paired_ic"]) == 5
        for comparison in result["paired_ic"]:
            assert comparison["statistics"]["expected"] == len(comparison["observations"]) == 60
            if fault:
                assert comparison["observations"][0]["difference"] is None
                assert comparison["statistics"]["inference_unavailable"] == "missing_observations"
    elif fault == "parent_tamper":
        assert retained_reads == ["result.json"]
        assert not inputs["datasets"]
        assert all(row["status"] == "not_attempted" for row in members)
    else:
        assert inputs["failures"][case.equities[0]]["error_type"] == "ValueError"
        assert len(inputs["datasets"]) == 26
    assert await repository.versions() == []
    registry = await repository.snapshot()
    assert registry.generation == 0 and not registry.active and not registry.shadow
    assert await repository.get(repository._variance_family_key("1d")) is None
    diagnostic = await repository.get(f"diagnostic/{result['run_id']}")
    assert diagnostic["artifact_hash"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest()
    accounting = await repository.get("family/all")
    assert accounting["trial_count"] == 50
    before = _hashes(output)
    await repository.rebuild()
    assert await repository.get(f"diagnostic/{result['run_id']}") == diagnostic
    assert await repository.get("family/all") == accounting
    assert await repository.snapshot() == registry
    with pytest.raises(FileExistsError):
        await service.run(plan, output, environment={"fixture": True})
    assert _hashes(output) == before and _hashes(parent_directory) == frozen_parent
    assert (await repository.get("family/all"))["trial_count"] == 50
