import hashlib
import json
from datetime import date, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import (
    ExdivEntry,
    PeadEntry,
    catalog_entry_id,
    load_exdiv_entry,
    load_pead_entry,
)


ENTRY = Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json"
EXDIV = Path(__file__).resolve().parents[3] / "config/research/apriori/exdiv-v1.json"


def test_frozen_entry_loads_with_the_spec_values():
    loaded = load_pead_entry(ENTRY)
    entry = loaded.entry
    assert loaded.sha256 == hashlib.sha256(ENTRY.read_bytes()).hexdigest()
    assert (entry.id, entry.version) == ("pead", 1)
    assert entry.window.decisions == (date(2016, 3, 1), date(2026, 7, 31))
    assert entry.window.bars_through == date(2026, 9, 1) and entry.window.recent_from == date(2023, 1, 1)
    assert (entry.event.surprise_pct, entry.event.reaction_sigma, entry.event.vol_window) == (5.0, 1.0, 20)
    assert (entry.event.min_abs_forecast, entry.event.min_estimates, entry.event.benchmark) == (0.05, 1, "SPY")
    assert (entry.universe.min_price, entry.universe.static_percentile) == (10.0, 0.25)
    assert entry.trade.decision_time() == time(10, 35)
    assert (entry.trade.stop_atr_multiple, entry.trade.target_r, entry.trade.atr_window) == (2.0, 3.0, 14)
    assert (entry.trade.max_hold_sessions, entry.trade.secondary_hold_sessions) == (20, 60)
    assert entry.costs_bps_per_side == (0.0, 5.0) and entry.decision_cost_bps == 5.0
    assert (entry.bootstrap.block_mean, entry.bootstrap.draws, entry.bootstrap.seed) == (10, 2000, 20260925)
    assert (entry.pass_rule.min_events, entry.pass_rule.min_recent_events, entry.pass_rule.trim_fraction) == (
        300,
        100,
        0.01,
    )
    assert entry.max_failed_calendar_fraction == 0.02 and entry.calendar_request_interval_seconds == 1.0
    assert entry.universe.liquidity_adjustment == "raw"
    assert entry.max_empty_session_fraction_per_year == 0.10


def _document() -> dict:
    return json.loads(ENTRY.read_text())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unexpected=1),
        lambda d: d["window"].update(decisions=["2026-07-31", "2016-03-01"]),
        lambda d: d["window"].update(recent_from="2015-01-01"),
        lambda d: d["window"].update(bars_through="2026-08-15"),
        lambda d: d.update(decision_cost_bps=7.0),
        lambda d: d["trade"].update(decision_time_et="25:00"),
        lambda d: d["trade"].update(secondary_hold_sessions=10),
        lambda d: d["universe"].update(dollar_volume_window=30),
        lambda d: d["pass_rule"].update(trim_fraction=0.6),
        lambda d: d["universe"].update(liquidity_adjustment="all"),
        lambda d: d.update(max_empty_session_fraction_per_year=1.0),
    ],
)
def test_invalid_entries_are_rejected(mutate):
    document = _document()
    mutate(document)
    with pytest.raises(ValidationError):
        PeadEntry.model_validate(document)


def test_exdiv_entry_loads_with_the_spec_values():
    loaded = load_exdiv_entry(EXDIV)
    entry = loaded.entry
    assert loaded.sha256 == hashlib.sha256(EXDIV.read_bytes()).hexdigest()
    assert (entry.id, entry.version) == ("exdiv", 1)
    assert entry.window.decisions == (date(2016, 8, 1), date(2026, 7, 31))
    assert entry.window.bars_through == date(2026, 10, 1) and entry.window.recent_from == date(2023, 1, 1)
    assert entry.actions_window == (date(2016, 1, 1), date(2026, 12, 1))
    assert entry.cohort == "config/research/spread/cohort-v1.json"
    assert (entry.event.min_gap_days, entry.event.max_gap_days, entry.event.structural_exclusion_days) == (77, 105, 10)
    assert entry.event.exclude_special is True
    assert (entry.universe.min_price, entry.universe.static_percentile, entry.universe.liquidity_adjustment) == (
        10.0,
        0.25,
        "raw",
    )
    assert entry.trade.decision_time() == time(10, 35)
    assert (entry.trade.entry_lead_sessions, entry.trade.control_offset_sessions) == (5, 32)
    assert entry.trade.max_hold_sessions == 5 and entry.trade.secondary_hold_sessions == 10
    assert (entry.trade.stop_atr_multiple, entry.trade.atr_window, entry.trade.target_r) == (2.0, 14, 3.0)
    assert entry.costs_bps_per_side == (0.0, 5.0, 10.0) and entry.decision_cost_bps == 5.0
    assert (entry.bootstrap.block_mean, entry.bootstrap.draws, entry.bootstrap.seed) == (10, 2000, 20261012)
    assert (entry.pass_rule.min_events, entry.pass_rule.min_recent_events, entry.pass_rule.trim_fraction) == (
        300,
        100,
        0.01,
    )
    assert catalog_entry_id(EXDIV) == "exdiv" and catalog_entry_id(ENTRY) == "pead"


def _exdiv_document() -> dict:
    return json.loads(EXDIV.read_text())


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["trade"].__setitem__("secondary_hold_sessions", 5), "secondary_hold_sessions"),
        (lambda d: d["trade"].__setitem__("control_offset_sessions", 6), "control_offset_sessions"),
        (lambda d: d["event"].__setitem__("max_gap_days", 70), "gap"),
        (lambda d: d.__setitem__("actions_window", ["2016-09-01", "2026-12-01"]), "actions_window"),
        (lambda d: d.__setitem__("actions_window", ["2016-01-01", "2026-09-01"]), "actions_window"),
        (lambda d: d.__setitem__("decision_cost_bps", 7.5), "decision_cost_bps"),
        (lambda d: d.__setitem__("id", "pead"), "id"),
        (lambda d: d["trade"].__setitem__("max_hold_sessions", 5), "[Ee]xtra"),
    ],
)
def test_exdiv_entry_rejects_inconsistent_documents(mutate, message):
    document = _exdiv_document()
    mutate(document)
    with pytest.raises(ValidationError, match=message):
        ExdivEntry.model_validate(document)


def test_catalog_entry_id_requires_a_string_id(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"version": 1}))
    with pytest.raises(ValueError, match="id"):
        catalog_entry_id(path)
