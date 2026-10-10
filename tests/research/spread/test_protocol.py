import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.spread.protocol import (
    SpreadCohort,
    SpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)


REPO = Path(__file__).resolve().parents[3]
COHORT_PATH = REPO / "config/research/spread/cohort-v1.json"
PROTOCOL_PATH = REPO / "config/research/spread/pairs-v1.json"


def _cohort(**overrides) -> dict:
    base = {
        "id": "spread-cohort",
        "version": 1,
        "survivorship": "test",
        "market": "SPY",
        "sectors": {"tech": ["AAPL", "MSFT", "NVDA"], "energy": ["XOM", "CVX"]},
    }
    return {**base, **overrides}


def test_cohort_pairs_are_same_sector_sorted_and_unique():
    cohort = SpreadCohort.model_validate(_cohort())
    assert cohort.symbols == ("AAPL", "CVX", "MSFT", "NVDA", "XOM")
    assert cohort.pairs() == (
        ("CVX", "XOM", "energy"),
        ("AAPL", "MSFT", "tech"),
        ("AAPL", "NVDA", "tech"),
        ("MSFT", "NVDA", "tech"),
    )


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"sectors": {"tech": ["AAPL"], "energy": ["XOM", "CVX"]}}, "at least two"),
        ({"sectors": {"tech": ["AAPL", "XOM"], "energy": ["XOM", "CVX"]}}, "more than one sector"),
        ({"market": "AAPL"}, "market"),
        ({"sectors": {"tech": ["aapl", "MSFT"]}}, "symbol"),
        ({"extra": 1}, "[Ee]xtra"),
    ],
)
def test_cohort_rejects_invalid_shapes(overrides, match):
    with pytest.raises(ValidationError, match=match):
        SpreadCohort.model_validate(_cohort(**overrides))


def test_frozen_files_load_and_pin_each_other():
    cohort = load_spread_cohort(COHORT_PATH)
    loaded = load_spread_protocol(PROTOCOL_PATH)
    assert cohort.sha256 == hashlib.sha256(COHORT_PATH.read_bytes()).hexdigest()
    assert loaded.protocol.cohort_sha256 == cohort.sha256
    assert loaded.protocol.cohort == "config/research/spread/cohort-v1.json"
    assert len(cohort.cohort.symbols) == 127 and len(cohort.cohort.sectors) == 11
    assert len(cohort.cohort.pairs()) == 927
    assert cohort.cohort.market == "SPY" and "SPY" not in cohort.cohort.symbols
    p = loaded.protocol
    assert p.windows.discovery == (date(2018, 1, 2), date(2023, 12, 29))
    assert p.windows.confirmation == (date(2024, 1, 2), date(2026, 7, 31))
    assert p.schedule.formation_sessions == 504 and p.schedule.trading_sessions == 126
    assert p.formation.top_pairs == 20 and p.formation.coint_max_lag == 1
    assert p.trading.z_entry == 2.0 and p.trading.z_exit == 0.5 and p.trading.z_stop == 4.0
    assert p.decision_cost_bps == 5.0 and p.decision_cost_bps in p.costs_bps_per_side
    assert p.pass_rules.min_closed_trades == 100 and p.confirmation_rules.min_closed_trades == 30


def _protocol_doc() -> dict:
    return json.loads(PROTOCOL_PATH.read_text())


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["windows"].update(confirmation=["2023-06-01", "2026-07-31"]), "confirmation"),
        (lambda d: d["windows"].update(discovery=["2015-01-05", "2023-12-29"]), "bars"),
        (lambda d: d["trading"].update(z_exit=2.5), "z_exit"),
        (lambda d: d["formation"].update(half_life_sessions=[42, 5]), "half_life"),
        (lambda d: d.update(decision_cost_bps=7.0), "costs_bps_per_side"),
        (lambda d: d["power"].update(min_pass=11), "min_pass"),
        (lambda d: d["null_check"].update(max_pass=10), "max_pass"),
        (lambda d: d.update(cohort_sha256="0" * 63), "cohort_sha256"),
    ],
)
def test_protocol_cross_field_rules(mutate, match):
    doc = _protocol_doc()
    mutate(doc)
    with pytest.raises(ValidationError, match=match):
        SpreadProtocol.model_validate(doc)


def test_loader_rejects_cohort_hash_mismatch(tmp_path):
    doc = _protocol_doc()
    doc["cohort_sha256"] = "0" * 64
    path = tmp_path / "p.json"
    path.write_text(json.dumps(doc))
    loaded = load_spread_protocol(path)
    cohort = load_spread_cohort(COHORT_PATH)
    assert loaded.protocol.cohort_sha256 != cohort.sha256  # the executor refuses this pair (Task 7)
