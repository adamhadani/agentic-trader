"""A complete frozen current cohort is screened without inventing historical membership."""

import copy
import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.equity_universe import EquityUniversePlan, build_snapshot, document_hash
from agentic_trader.research.alpha.liquidity import EquityLiquidityPlan, compute_liquidity_study


def _snapshot(symbols):
    assets = [
        {
            "id": str(UUID(int=i + 1)),
            "symbol": symbol,
            "class": "us_equity",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": True,
        }
        for i, symbol in enumerate(symbols)
    ]
    directories = (
        {
            "name": "nasdaqlisted",
            "file_date": "2026-09-17",
            "rows": [
                {"Symbol": symbol, "ETF": "N", "Test Issue": "N", "NextShares": "N", "Financial Status": "N"}
                for symbol in symbols
            ],
        },
        {"name": "otherlisted", "file_date": "2026-09-17", "rows": []},
    )
    return build_snapshot(
        EquityUniversePlan("fixture", target_size=len(symbols)),
        assets,
        directories,
        observed_at=pd.Timestamp("2026-09-17T17:32:00Z"),
    )


@pytest.fixture
def liquidity_case():
    snapshot = _snapshot(("AAA", "BBB", "CCC"))
    clock = pd.DatetimeIndex(
        ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"], tz="America/New_York"
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    frames = {}
    for symbol, close, volume in (("AAA", 10.0, 100.0), ("BBB", 20.0, 200.0), ("CCC", 10.0, 400.0)):
        frames[symbol] = pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume}, index=clock
        )
        frames[symbol].attrs.update(feed="alpaca:iex", timeframe="1d", adjustment="raw")
    plan = EquityLiquidityPlan.from_snapshot(
        snapshot,
        campaign_id="fixture",
        start=clock[0].date(),
        end=clock[-1].date(),
        feed="alpaca:iex",
        lookback=3,
        selection_size=2,
        min_positive_volume_sessions=3,
    )
    return SimpleNamespace(snapshot=snapshot, clock=clock, sessions=sessions, frames=frames, plan=plan)


def _compute(case, *, failures=None):
    return compute_liquidity_study(
        DailyStudyInputs(frames=case.frames, failures=failures or {}), case.clock, case.plan, case.sessions
    )


def test_exact_snapshot_binding_and_deterministic_ranked_selection(liquidity_case):
    c = liquidity_case
    assert EquityLiquidityPlan.from_document(json.loads(json.dumps(c.plan.document())), c.snapshot) == c.plan
    assert c.plan.trial_count == 1 and c.plan.acquisition_symbols == ("AAA", "BBB", "CCC")
    result = _compute(c)
    assert result["status"] == "completed" and result["error_type"] is None
    assert [m["symbol"] for m in result["selected"]] == ["BBB", "CCC"]
    assert result["selection_available"] and result["shortfall"] == 0
    assert result["eligible_count"] == 3 and len(result["members"]) == 3
    assert not result["authorizes_promotion"] and not result["point_in_time_historical_membership"]
    assert result["volume_scope"] == "single_venue_iex"
    assert all(m["instrument_subtype"] == "unknown" for m in result["members"])
    c.frames = dict(reversed(tuple(c.frames.items())))
    assert _compute(c) == result


@pytest.mark.parametrize("fault", ["snapshot_hash", "wrong_snapshot", "subset", "extra_policy", "uuid_symbol"])
def test_cohort_cannot_be_replaced_or_partially_consumed(liquidity_case, fault):
    c = liquidity_case
    document, snapshot = copy.deepcopy(c.plan.document()), copy.deepcopy(c.snapshot)
    if fault == "snapshot_hash":
        snapshot["selected"][0]["symbol"] = "WRONG"
    elif fault == "wrong_snapshot":
        document["snapshot_id"] = "0" * 64
    elif fault == "subset":
        document["symbols"] = ["AAA"]
    elif fault == "extra_policy":
        document["look_ahead"] = True
    else:
        snapshot["selected"][0]["symbol"] = "WRONG"
        snapshot["snapshot_id"] = document_hash({k: v for k, v in snapshot.items() if k != "snapshot_id"})
        document["snapshot_id"] = snapshot["snapshot_id"]
    with pytest.raises(ValueError):
        EquityLiquidityPlan.from_document(document, snapshot)


@pytest.mark.parametrize("field", ["symbol", "asset_id", "snapshot_observed_at"])
def test_frozen_identity_covers_member_content_and_actual_receipt(liquidity_case, field):
    c = liquidity_case
    if field == "snapshot_observed_at":
        changed = replace(c.plan, snapshot_observed_at=c.plan.snapshot_observed_at + pd.Timedelta(seconds=1))
    else:
        member = replace(c.plan.members[0], **{field: "ZZZ" if field == "symbol" else str(UUID(int=999))})
        members = tuple(sorted((member, *c.plan.members[1:]), key=lambda row: row.asset_id))
        changed = replace(c.plan, members=members)
        assert len(changed.members) == len(c.plan.members)
    assert changed.identity != c.plan.identity
    with pytest.raises(ValueError):
        EquityLiquidityPlan.from_document(changed.document(), c.snapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lookback", 1),
        ("lookback", 61),
        ("lookback", True),
        ("selection_size", 65),
        ("selection_size", 0),
        ("price_floor", 0),
        ("price_floor", np.inf),
        ("min_median_dollar_volume", -1),
        ("min_positive_volume_sessions", 4),
        ("feed", "yahoo"),
        ("end", date(2027, 1, 1)),
    ],
)
def test_policy_is_bounded_and_immutable(liquidity_case, field, value):
    with pytest.raises(ValueError):
        replace(liquidity_case.plan, **{field: value})


@pytest.mark.parametrize("now", ["2026-09-17T17:31:59Z", "2026-09-16T23:59:59Z", "2026-09-18"])
def test_screen_cannot_backdate_membership_or_accept_naive_clock(liquidity_case, now):
    with pytest.raises(ValueError):
        liquidity_case.plan.validate_as_of(pd.Timestamp(now))


def test_incomplete_current_daily_bar_is_refused(liquidity_case):
    c = liquidity_case
    c.plan.validate_as_of(pd.Timestamp("2026-09-17T17:32:00Z"))
    with pytest.raises(ValueError, match="elapsed"):
        replace(c.plan, end=date(2026, 9, 17)).validate_as_of(pd.Timestamp("2026-09-17T23:00:00Z"))


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "missing_window_sessions"),
        ("empty", "missing_window_sessions"),
        ("zero", "insufficient_positive_volume"),
        ("price", "price_below_floor"),
        ("liquidity", "median_dollar_volume_below_floor"),
    ],
)
def test_known_ineligibility_is_retained_without_imputation(liquidity_case, fault, reason):
    c = liquidity_case
    if fault == "missing":
        c.frames["BBB"] = c.frames["BBB"].drop(c.clock[-1])
    elif fault == "empty":
        c.frames["BBB"] = c.frames["BBB"].iloc[:0]
    elif fault == "zero":
        c.frames["BBB"].loc[c.clock[-1], "volume"] = 0
    elif fault == "price":
        c.plan = replace(c.plan, price_floor=21)
    else:
        c.plan = replace(c.plan, min_median_dollar_volume=5000)
    result = _compute(c)
    row = next(m for m in result["members"] if m["symbol"] == "BBB")
    assert reason in row["reasons"] and not row["eligible"]
    assert result["selection_available"] and len(result["members"]) == 3
    assert result["selected_count"] + result["shortfall"] == c.plan.selection_size
    if fault in ("empty", "missing"):
        assert row["median_dollar_volume"] is None and row["missing_window_dates"]


@pytest.mark.parametrize("fault", ["failed", "absent", "negative", "nan", "feed", "adjustment", "clock", "future"])
def test_unknown_or_invalid_evidence_withholds_entire_selection(liquidity_case, fault):
    c = liquidity_case
    failures = {}
    if fault in ("failed", "absent"):
        del c.frames["BBB"]
        if fault == "failed":
            failures["BBB"] = {"error_type": "ConnectionError", "evidence": {"capture_id": "fixture"}}
    elif fault in ("negative", "nan"):
        c.frames["BBB"].loc[c.clock[-1], "volume"] = -1 if fault == "negative" else np.nan
    elif fault in ("feed", "adjustment", "clock"):
        c.frames["BBB"].attrs[{"clock": "bar_layout"}.get(fault, fault)] = "other"
    else:
        c.frames["BBB"].loc[c.clock[-1] + pd.Timedelta(days=1)] = c.frames["BBB"].iloc[-1]
    result = _compute(c, failures=failures)
    assert result["status"] == "failed" and result["error_type"] == "DailyAcquisitionError"
    assert not result["selection_available"] and result["selected"] == []
    assert result["shortfall"] == c.plan.selection_size and result["eligible_count"] == 2
    assert next(m for m in result["members"] if m["symbol"] == "AAA")["median_dollar_volume"] == 1000


def test_latest_window_uses_only_preceding_complete_dates(liquidity_case):
    c = liquidity_case
    result = _compute(c)
    for frame in c.frames.values():
        frame.loc[c.clock[:2], "volume"] *= 1e9
    assert _compute(c)["members"] == result["members"]
    assert result["window_dates"] == [t.date().isoformat() for t in c.clock[-3:]]


def test_missing_earlier_dates_are_retained_without_changing_trailing_eligibility(liquidity_case):
    c = liquidity_case
    c.frames["BBB"] = c.frames["BBB"].drop(c.clock[0])
    result = _compute(c)
    row = next(m for m in result["members"] if m["symbol"] == "BBB")
    assert row["missing_dates"] == [c.clock[0].date().isoformat()]
    assert row["missing_window_dates"] == [] and row["eligible"]
    assert row["median_dollar_volume"] == 4000


def test_uuid_tie_break_is_independent_of_symbol_order(liquidity_case):
    c = liquidity_case
    c.plan = EquityLiquidityPlan.from_snapshot(
        _snapshot(("CCC", "BBB", "AAA")),
        campaign_id="tiebreak",
        start=c.plan.start,
        end=c.plan.end,
        feed=c.plan.feed,
        lookback=3,
        selection_size=2,
        min_positive_volume_sessions=3,
    )
    for frame in c.frames.values():
        frame["volume"] = 4000 / frame.close
    assert [row["symbol"] for row in _compute(c)["selected"]] == ["CCC", "BBB"]


@pytest.mark.parametrize("fault", ["calendar", "extra_symbol", "frame_and_failure", "short_calendar"])
def test_clock_and_batch_contract_cannot_be_silently_intersected(liquidity_case, fault):
    c = liquidity_case
    failures = {}
    if fault == "calendar":
        c.sessions = c.sessions[:-1]
    elif fault == "extra_symbol":
        c.frames["OTHER"] = c.frames["AAA"]
    elif fault == "frame_and_failure":
        failures["AAA"] = {"error_type": "Error"}
    else:
        c.clock, c.sessions = c.clock[:2], c.sessions[:2]
    with pytest.raises(ValueError):
        _compute(c, failures=failures)


def test_full_500_candidate_bound_does_not_shrink_to_panel_limit(liquidity_case):
    snapshot = _snapshot(tuple(f"A{i:03d}" for i in range(500)))
    plan = EquityLiquidityPlan.from_snapshot(
        snapshot,
        campaign_id="full",
        start=liquidity_case.plan.start,
        end=liquidity_case.plan.end,
        feed="alpaca:sip",
        lookback=3,
        min_positive_volume_sessions=3,
        selection_size=64,
    )
    assert len(plan.acquisition_symbols) == 500 and plan.trial_count == 1
    assert EquityLiquidityPlan.from_document(plan.document(), snapshot).identity == plan.identity
    frame = liquidity_case.frames["AAA"].copy(deep=True)
    frame.attrs["feed"] = "alpaca:sip"
    result = compute_liquidity_study(
        DailyStudyInputs(frames={symbol: frame for symbol in plan.acquisition_symbols}),
        liquidity_case.clock,
        plan,
        liquidity_case.sessions,
    )
    assert len(result["members"]) == 500 and result["selected_count"] == 64
    assert result["volume_scope"] == "consolidated_sip"


@pytest.mark.parametrize("fault", [None, "empty", "failed", "invalid_protocol"])
def test_cli_retains_charged_outcomes_without_promotion(liquidity_case, tmp_path, monkeypatch, fault):
    c = liquidity_case
    calls = []
    output = tmp_path / "screen"

    def daily(symbol, *_):
        assert (output / "manifest.json").exists()
        calls.append(symbol)
        if fault == "failed" and symbol == "BBB":
            raise ConnectionError("Fixture acquisition refusal")
        frame = c.frames[symbol].copy(deep=True)
        return frame.iloc[:0] if fault == "empty" and symbol in ("BBB", "CCC") else frame

    source = SimpleNamespace(calendar=lambda *_: c.sessions, daily=daily)

    def compose(*_):
        calls.append("composed")
        return nullcontext(source)

    monkeypatch.setattr("agentic_trader.cli.commands.alpha_liquidity.session_source", compose)
    monkeypatch.setattr(
        "agentic_trader.research.alpha.panel_workflow.datetime",
        SimpleNamespace(now=lambda _: pd.Timestamp("2026-09-18T00:00:00Z").to_pydatetime()),
    )
    protocol = tmp_path / "protocol.json"
    document = {
        "plan": c.plan.document(),
        "acquisition": DailyAcquisitionConfig(min_request_interval_seconds=0).model_dump(),
    }
    if fault == "invalid_protocol":
        document["plan"]["symbols"] = ["AAA"]
    protocol.write_text(json.dumps(document))
    universe = tmp_path / "snapshot.json"
    universe.write_text(json.dumps(c.snapshot))
    result = CliRunner().invoke(
        cli, ["alpha", "liquidity-study", str(protocol), "--universe", str(universe), "--output", str(output)]
    )
    assert (result.exit_code == 0) == (fault is None), result.output
    status = CliRunner().invoke(cli, ["alpha", "status"])
    assert status.exit_code == 0, status.output
    state = json.loads(status.output)
    assert state["generation"] == 0 and state["active"] == 0
    assert (state["research_family"] or {}).get("trial_count", 0) == (fault != "invalid_protocol")
    if fault == "invalid_protocol":
        assert not calls and not output.exists()
        return
    assert calls == ["composed", "AAA", "BBB", "CCC"]
    saved = json.loads((output / "result.json").read_text())
    assert saved["charged_trials"] == 1 and not saved["authorizes_promotion"]
    assert saved["status"] == ("failed" if fault == "failed" else "completed")
    assert saved["completed_comparisons"] == (fault != "failed")
    assert len(saved["members"]) == 3 and len(list((output / "members").glob("*.json"))) == 3
    assert saved["shortfall"] == {None: 0, "empty": 1, "failed": 2}[fault]
    assert (output / "screen.json").exists()
