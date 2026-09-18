"""Frozen native-day campaign clocks do not move with acquisition success."""

import copy
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_plan import DailyComparisonPlan, DailyPanelPlan


@pytest.fixture
def daily_plan():
    return DailyPanelPlan(
        campaign_id="daily-fixture",
        symbols=tuple(f"A{i:02}" for i in range(20)),
        factor_symbols=tuple(f"F{i}" for i in range(9)),
        start_date=date(2026, 10, 30),
        end_date=date(2027, 10, 29),
        history_start=date(2021, 1, 1),
        selection_hash="a" * 64,
        parent_plan_id="b" * 64,
    )


@pytest.fixture
def daily_sessions():
    clock = pd.bdate_range("2026-10-30", periods=65, tz="America/New_York")
    return tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )


def test_exact_protocol_roundtrip_and_refuses_semantic_edits(daily_plan):
    doc = daily_plan.document()
    assert DailyPanelPlan.from_document(doc) == daily_plan
    assert len(daily_plan.acquisition_symbols) == 29
    for field in ("authorizes_promotion", "models", "training", "outcome_policy"):
        changed = copy.deepcopy(doc)
        changed[field] = "altered"
        with pytest.raises(ValueError):
            DailyPanelPlan.from_document(changed)


def test_native_day_deadline_and_fixed_h20_anchor_across_dst(daily_plan, daily_sessions):
    first = daily_plan.decision_window(daily_sessions[0].date, daily_sessions)
    assert first.native_closed_at == pd.Timestamp("2026-10-31T04:00Z")
    assert first.available_at == pd.Timestamp("2026-10-31T04:30Z")
    assert first.expires_at == pd.Timestamp("2026-10-31T07:00Z")
    assert first.entry_open == pd.Timestamp("2026-11-02T14:30Z")
    assert first.exit_date == daily_sessions[20].date
    assert first.economic_scheduled
    assert not daily_plan.decision_window(daily_sessions[19].date, daily_sessions).economic_scheduled
    assert daily_plan.decision_window(daily_sessions[20].date, daily_sessions).economic_scheduled
    assert first.document()["entry_date"] == "2026-11-02"


def test_early_close_is_not_native_daily_finality(daily_plan, daily_sessions):
    early = replace(daily_sessions[0], close=pd.Timestamp("2026-10-30T17:00Z"))
    window = daily_plan.decision_window(early.date, (early, *daily_sessions[1:]))
    assert window.available_at == pd.Timestamp("2026-10-31T04:30Z")


@pytest.mark.parametrize(
    "change",
    [
        {"feed": "alpaca:sip"},
        {"symbols": ("A",)},
        {"factor_symbols": ("F",)},
        {"train_sessions": 0},
        {"train_sessions": 504.0},
        {"top_k": 8.0},
        {"ridge_alpha": float("nan")},
        {"selection_hash": "unknown"},
        {"end_date": date(2026, 10, 1)},
    ],
)
def test_invalid_protocol_is_rejected(daily_plan, change):
    with pytest.raises(ValueError):
        replace(daily_plan, **change)


def test_calendar_must_include_anchor_and_future_outcome(daily_plan, daily_sessions):
    for sessions in (daily_sessions[1:], daily_sessions[:20], tuple(reversed(daily_sessions))):
        with pytest.raises(ValueError):
            daily_plan.decision_window(daily_sessions[1].date, sessions)


@pytest.fixture
def comparison_plan(daily_plan):
    return DailyComparisonPlan("baseline", daily_plan.campaign_id, daily_plan.identity, daily_plan.start_date)


def test_companion_roundtrip_binds_parent_without_changing_primary(daily_plan, comparison_plan):
    original = daily_plan.document()
    assert daily_plan.identity == "2bd897e95052fc1b08faf27ea2a02873a1ff3a16e581e248a28d8520fa7808e4"
    comparison_plan.validate_parent(daily_plan)
    document = comparison_plan.document()
    assert DailyComparisonPlan.from_document(document) == comparison_plan
    assert document["models"] == ["rank_blend", "volatility20", "ridge"]
    assert document["support"] == "baseline_common_v1"
    assert document["charged_trials"] == 3 and document["authorizes_promotion"] is False
    assert daily_plan.document() == original
    assert comparison_plan.identity != daily_plan.identity


@pytest.mark.parametrize(
    "change",
    [
        {"comparison_id": ""},
        {"campaign_id": "unsafe/path"},
        {"parent_protocol_hash": "unknown"},
        {"first_decision_date": "2026-10-30"},
    ],
)
def test_companion_rejects_malformed_identity(comparison_plan, change):
    with pytest.raises(ValueError):
        replace(comparison_plan, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"campaign_id": "other"},
        {"parent_protocol_hash": "c" * 64},
        {"first_decision_date": date(2026, 10, 29)},
        {"first_decision_date": date(2027, 10, 30)},
    ],
)
def test_companion_rejects_unbound_or_out_of_campaign_plan(daily_plan, comparison_plan, change):
    with pytest.raises(ValueError):
        replace(comparison_plan, **change).validate_parent(daily_plan)


@pytest.mark.parametrize(
    "field,value",
    [
        ("charged_trials", 0),
        ("models", ["ridge"]),
        ("support", "future_complete"),
        ("authorizes_promotion", True),
        ("version", "unfrozen"),
        ("extra", "unknown"),
    ],
)
def test_companion_document_must_match_exact_contract(comparison_plan, field, value):
    document = comparison_plan.document()
    document[field] = value
    with pytest.raises(ValueError):
        DailyComparisonPlan.from_document(document)
