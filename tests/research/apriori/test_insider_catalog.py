import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from agentic_trader.research.apriori.catalog import catalog_entry_id, load_insider_entry
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.insider_fields import plausible_transactions


REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "config/research/apriori/insider-v1.json"
PROTOCOL = REPO / "config/research/pooled/cohort3-insider-v1.json"


def _spec(*, max_hold: int = 20, decisions=(date(2016, 1, 1), date(2026, 12, 31))):
    return SimpleNamespace(bracket=SimpleNamespace(max_hold_sessions=max_hold), decisions=decisions)


def _with(**event):
    document = json.loads(ENTRY.read_text())
    document["event"].update(event)
    return document


def test_shipped_insider_entry_loads():
    loaded = load_insider_entry(ENTRY)
    assert loaded.entry.id == "insider"
    assert loaded.entry.event.min_buyers == 2
    assert loaded.sha256 == hashlib.sha256(ENTRY.read_bytes()).hexdigest()


def test_entry_pins_shipped_protocol():
    entry = load_insider_entry(ENTRY).entry
    assert hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() == entry.pooled_protocol_sha256


def test_min_buyers_below_two_refused(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(_with(min_buyers=1)))
    with pytest.raises(ValidationError, match="min_buyers"):
        load_insider_entry(path)


def test_check_against_accepts_the_shipped_geometry():
    load_insider_entry(ENTRY).entry.check_against(_spec())


def test_check_against_refuses_short_cooldown(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(_with(cooldown_sessions=20)))
    with pytest.raises(ValueError, match="cooldown"):
        load_insider_entry(path).entry.check_against(_spec(max_hold=20))


def test_check_against_refuses_window_outside_cube():
    entry = load_insider_entry(ENTRY).entry
    with pytest.raises(ValueError, match="decisions"):
        entry.check_against(_spec(decisions=(date(2017, 1, 1), date(2026, 12, 31))))


def test_catalog_entry_id_reads_insider():
    assert catalog_entry_id(ENTRY) == "insider"


def _tx(filing: date, dollars: float, owner: int = 7) -> InsiderTransaction:
    return InsiderTransaction(
        symbol="AAA", filing_date=filing, trans_date=filing, code="P", dollars=dollars, owner_cik=owner
    )


def test_plausible_transactions_drops_oversized_and_later_filings_in_order():
    days = [date(2017, 3, 1).toordinal() + i for i in range(30)]
    ordinals = np.array(days, dtype=np.int64)
    dv = np.full(30, 1e6)
    first, huge, second, late = (
        _tx(date.fromordinal(days[3]), 5e5, owner=1),
        _tx(date.fromordinal(days[4]), 2e7, owner=2),  # > 10 x DV
        _tx(date.fromordinal(days[5]), 1e7, owner=3),  # exactly 10 x DV is kept
        _tx(date.fromordinal(days[-1] + 1), 1.0, owner=4),  # after the last session
    )
    kept, implausible = plausible_transactions([first, huge, second, late], ordinals, dv, 10.0)
    assert kept == [first, second]
    assert implausible == 1
