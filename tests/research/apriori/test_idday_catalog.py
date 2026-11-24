import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import catalog_entry_id, load_idday_entry


ENTRY = Path(__file__).parent / "fixtures" / "idday-v1-test.json"


def _spec(*, atr_window: int = 14, decisions=(date(2016, 8, 1), date(2026, 7, 31))):
    return SimpleNamespace(bracket=SimpleNamespace(atr_window=atr_window), decisions=decisions)


def _write(tmp_path, mutate):
    document = json.loads(ENTRY.read_text())
    mutate(document)
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(document))
    return path


def test_idday_entry_loads_fixture():
    loaded = load_idday_entry(ENTRY)
    assert loaded.entry.id == "idday"
    assert [c.name for c in loaded.entry.cohorts] == ["v3", "xu"]
    assert loaded.entry.event.max_hold_sessions == 63
    assert loaded.entry.placebo.planted_return == 0.0185
    assert loaded.sha256 == hashlib.sha256(ENTRY.read_bytes()).hexdigest()
    assert catalog_entry_id(ENTRY) == "idday"


def test_idday_entry_rejects_wrong_cohort_order(tmp_path):
    def mutate(document):
        document["cohorts"].reverse()

    with pytest.raises(ValidationError, match="cohorts"):
        load_idday_entry(_write(tmp_path, mutate))


def test_idday_entry_rejects_last_exit_before_decisions_end(tmp_path):
    def mutate(document):
        document["window"]["last_exit"] = "2026-06-29"

    with pytest.raises(ValidationError, match="last_exit"):
        load_idday_entry(_write(tmp_path, mutate))


def test_idday_entry_rejects_filings_ending_before_last_exit(tmp_path):
    def mutate(document):
        document["investor_days"]["filings_through"] = "2026-07-30"

    with pytest.raises(ValidationError, match="filings_through"):
        load_idday_entry(_write(tmp_path, mutate))


def test_idday_entry_rejects_decision_cost_outside_costs(tmp_path):
    def mutate(document):
        document["decision_cost_bps"] = 10

    with pytest.raises(ValidationError, match="decision_cost_bps"):
        load_idday_entry(_write(tmp_path, mutate))


def test_check_against_accepts_covering_spec_and_rejects_atr_mismatch():
    entry = load_idday_entry(ENTRY).entry
    entry.check_against(_spec())
    with pytest.raises(ValueError, match="atr_window"):
        entry.check_against(_spec(atr_window=20))


def test_check_against_rejects_window_outside_cube():
    entry = load_idday_entry(ENTRY).entry
    with pytest.raises(ValueError, match="window.decisions"):
        entry.check_against(_spec(decisions=(date(2017, 1, 3), date(2026, 7, 31))))
    with pytest.raises(ValueError, match="window.decisions"):
        entry.check_against(_spec(decisions=(date(2016, 8, 1), date(2026, 6, 1))))
