import hashlib
import json
from datetime import date, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import PeadEntry, load_pead_entry


ENTRY = Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v1.json"


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
