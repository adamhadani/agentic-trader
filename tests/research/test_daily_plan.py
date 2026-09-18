"""Frozen native-day campaign clocks do not move with acquisition success."""

import copy
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.daily_plan import DailyPanelPlan


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
