import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import catalog_entry_id, load_runup_entry
from agentic_trader.research.pooled.campaign import load_campaign_protocol


REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "config/research/apriori/runup-v1.json"
PROTOCOL = REPO / "config/research/pooled/cohort3-events-v1.json"


def _spec(*, atr_window: int = 14, decisions=(date(2016, 8, 1), date(2026, 7, 31))):
    return SimpleNamespace(bracket=SimpleNamespace(atr_window=atr_window), decisions=decisions)


def _write(tmp_path, mutate):
    document = json.loads(ENTRY.read_text())
    mutate(document)
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(document))
    return path


def test_shipped_runup_entry_loads_and_records_its_digest():
    loaded = load_runup_entry(ENTRY)
    assert loaded.entry.id == "runup"
    assert loaded.entry.event.lead_sessions == 10
    assert loaded.entry.decision_cost_bps == 15
    assert loaded.sha256 == hashlib.sha256(ENTRY.read_bytes()).hexdigest()
    assert catalog_entry_id(ENTRY) == "runup"


def test_shipped_entry_states_the_amended_rule():
    entry = load_runup_entry(ENTRY).entry
    assert entry.control.quiet_before_sessions == 30 and entry.control.coverage_sessions == 130
    assert "P1-P5" in entry.decision_rule
    for phrase in ("hold_outside_window", "[D-30, D+10]", "P5:", "duplicate_session", "minimum detectable effect"):
        assert phrase in entry.hypotheses, phrase


@pytest.mark.parametrize("value", [None, -1])
def test_quiet_before_sessions_is_required_and_not_negative(tmp_path, value):
    def mutate(document):
        if value is None:
            del document["control"]["quiet_before_sessions"]
        else:
            document["control"]["quiet_before_sessions"] = value

    with pytest.raises(ValidationError, match="quiet_before_sessions"):
        load_runup_entry(_write(tmp_path, mutate))


def test_entry_pins_shipped_protocol():
    entry = load_runup_entry(ENTRY).entry
    assert hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() == entry.pooled_protocol_sha256


def test_trade_has_no_target():
    assert "target_r" not in load_runup_entry(ENTRY).entry.trade.model_dump()


def test_unknown_key_refused(tmp_path):
    path = _write(tmp_path, lambda d: d["event"].update(surprise=1))
    with pytest.raises(ValidationError, match="surprise"):
        load_runup_entry(path)


def test_decision_cost_must_be_a_reported_cost(tmp_path):
    path = _write(tmp_path, lambda d: d.update(decision_cost_bps=10))
    with pytest.raises(ValidationError, match="decision_cost_bps"):
        load_runup_entry(path)


def test_bars_through_must_mature_the_last_decision(tmp_path):
    path = _write(tmp_path, lambda d: d["window"].update(bars_through="2026-08-10"))
    with pytest.raises(ValidationError, match="bars_through"):
        load_runup_entry(path)


def test_check_against_accepts_the_shipped_geometry():
    load_runup_entry(ENTRY).entry.check_against(_spec())


def test_check_against_refuses_uncovered_window():
    entry = load_runup_entry(ENTRY).entry
    with pytest.raises(ValueError, match="decisions"):
        entry.check_against(_spec(decisions=(date(2017, 1, 1), date(2026, 7, 31))))
    with pytest.raises(ValueError, match="decisions"):
        entry.check_against(_spec(decisions=(date(2016, 8, 1), date(2026, 7, 16))))


def test_check_against_refuses_other_atr_window():
    with pytest.raises(ValueError, match="atr_window"):
        load_runup_entry(ENTRY).entry.check_against(_spec(atr_window=20))


def test_check_against_accepts_the_pinned_events_protocol_cube():
    spec = load_campaign_protocol(PROTOCOL).protocol.cube_spec()
    load_runup_entry(ENTRY).entry.check_against(spec)
