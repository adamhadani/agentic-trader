import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.spread.protocol import (
    SpreadCohort,
    SpreadProtocol,
    TradingRule,
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


def test_trading_rule_defaults_reproduce_v1():
    rule = TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open")
    assert rule.entry == "threshold"
    assert rule.z_entry_max is None
    assert rule.reenter_after_stop is True


def test_trading_rule_v2_fields_load():
    rule = TradingRule(
        z_entry=2.0,
        z_exit=0.5,
        z_stop=4.0,
        fill="next_open",
        entry="crossing",
        z_entry_max=4.0,
        reenter_after_stop=False,
    )
    assert rule.entry == "crossing" and rule.z_entry_max == 4.0 and rule.reenter_after_stop is False


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"z_entry_max": 2.0}, "z_entry_max"),  # must exceed z_entry
        ({"z_entry_max": 1.5}, "z_entry_max"),
        ({"z_entry_max": 4.5}, "z_entry_max"),  # must not exceed z_stop
        ({"entry": "momentum"}, "entry"),
        ({"band": 3.0}, "extra"),
    ],
)
def test_trading_rule_entry_band_validation(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open", **kwargs)


def test_protocol_second_look_defaults_false_and_v1_hash_is_frozen():
    loaded = load_spread_protocol(PROTOCOL_PATH)
    assert loaded.protocol.second_look is False
    assert loaded.sha256 == "88dc4945751bfdbcb45b21dca9ad84f3266239ab0b839c9a01f44986bcaaf352"
    assert loaded.protocol.second_look_note == ""
    doc = _protocol_doc()
    doc["second_look"] = True
    doc["second_look_note"] = "declared"
    assert SpreadProtocol.model_validate(doc).second_look is True


PROTOCOL_V2_PATH = REPO / "config/research/spread/pairs-v2.json"


def test_pairs_v2_is_v1_with_the_entry_rule_and_a_declared_second_look():
    v1 = load_spread_protocol(PROTOCOL_PATH)
    v2 = load_spread_protocol(PROTOCOL_V2_PATH)
    assert v2.sha256 != v1.sha256
    assert v2.protocol.version == 2 and v2.protocol.second_look is True
    assert v2.protocol.cohort_sha256 == v1.protocol.cohort_sha256
    t = v2.protocol.trading
    assert (t.entry, t.z_entry_max, t.reenter_after_stop) == ("crossing", 4.0, False)
    assert (t.z_entry, t.z_exit, t.z_stop, t.fill) == (2.0, 0.5, 4.0, "next_open")
    same = (
        "cohort",
        "feed",
        "adjustment",
        "bars",
        "windows",
        "schedule",
        "formation",
        "costs_bps_per_side",
        "decision_cost_bps",
        "bootstrap",
        "pass_rules",
        "confirmation_rules",
        "power",
        "null_check",
        "references",
    )
    for field in same:
        assert getattr(v2.protocol, field) == getattr(v1.protocol, field), field
    assert (
        "crossing" in v2.protocol.hypotheses
        and "second" in json.loads(PROTOCOL_V2_PATH.read_text())["second_look_note"].lower()
    )
