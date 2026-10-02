import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.alpha.search import WINDOWS
from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = REPO / "config/research/pooled/campaign-v1.json"


def _mutated(tmp_path, mutate) -> Path:
    doc = json.loads(PROTOCOL.read_text())
    mutate(doc)
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(doc))
    return path


def test_committed_protocol_is_valid_and_pins_the_committed_cohort():
    loaded = load_campaign_protocol(PROTOCOL)
    protocol = loaded.protocol
    assert protocol.cohort_sha256 == load_cohort(REPO / protocol.cohort).sha256
    assert len(protocol.families) == 12
    assert protocol.formula_budget == 200
    assert protocol.cube_spec().decisions == (protocol.windows.discovery[0], protocol.windows.confirmation[1])
    assert {f.id for f in protocol.excluded_families} >= {"vol_20", "sector_relative_momentum"}


def test_windows_must_be_ordered_and_disjoint(tmp_path):
    def overlap(doc):
        doc["windows"]["selection"][0] = doc["windows"]["discovery"][1]

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, overlap))


def test_seeds_must_be_dimensionless_and_avoid_forbidden_operators(tmp_path):
    def price_seed(doc):
        doc["families"][0]["seeds"].append("ts_slope(close, 20)")

    def forbidden(doc):
        doc["families"][0]["seeds"].append("-ts_std(returns, 20)")

    for mutate in (price_seed, forbidden):
        with pytest.raises(ValidationError):
            load_campaign_protocol(_mutated(tmp_path, mutate))


def test_mutation_operators_must_be_known_and_allowed(tmp_path):
    def unknown(doc):
        doc["families"][0]["mutation_operators"].append("realized_vol")

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, unknown))


def test_power_blocks_reference_declared_values(tmp_path):
    def bad_delta(doc):
        doc["power"]["detection_delta"] = 0.2

    def bad_family(doc):
        doc["power_search"]["families"] = ["nope"]

    for mutate in (bad_delta, bad_family):
        with pytest.raises(ValidationError):
            load_campaign_protocol(_mutated(tmp_path, mutate))


V2_WINDOWS = {
    "high52": [126, 189, 252],
    "reversal": [3, 5, 10, 21],
    "max_lottery": [5, 10, 21, 42],
    "momentum_12_1": [21, 63, 105, 126, 189, 252],
    "momentum_12_7": [84, 105, 126, 147, 168],
    "range_location": [10, 20, 40, 60, 120],
    "trend_slope": [10, 20, 40, 60, 120],
    "abnormal_volume": [5, 10, 20, 50, 100],
    "overnight_intraday": [5, 10, 21, 42],
    "price_volume_corr": [5, 10, 21, 42],
    "signed_volume": [5, 10, 20, 40],
    "hl_spread": [5, 10, 21, 42],
}


def _v2(doc):
    for family in doc["families"]:
        family["windows"] = V2_WINDOWS[family["id"]]
    doc["power_search"]["seed"] = 20261002
    doc["null_check"] = {"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}


def test_campaign_v1_still_loads_with_its_frozen_hash_and_cannot_run_a_campaign():
    loaded = load_campaign_protocol(PROTOCOL)
    assert loaded.sha256 == "897fd8e59d912975de9377389a75d8954a747ab49dfa6ae368a30879059ca5ba"
    assert loaded.protocol.null_check is None and loaded.protocol.power_search.seed is None
    assert all(f.windows is None and f.constant_windows == WINDOWS for f in loaded.protocol.families)
    with pytest.raises(ValueError, match="checks B and C"):
        loaded.protocol.require_campaign_ready()


def test_the_v2_fields_validate_and_make_the_protocol_campaign_ready(tmp_path):
    protocol = load_campaign_protocol(_mutated(tmp_path, _v2)).protocol
    protocol.require_campaign_ready()
    assert (protocol.null_check.replicates, protocol.null_check.max_false_acceptances) == (40, 2)
    assert protocol.power_search.seed == 20261002
    assert {f.id: f.constant_windows for f in protocol.families} == {k: tuple(v) for k, v in V2_WINDOWS.items()}


def test_the_formula_budget_splits_evenly_with_the_remainder_to_the_first_families():
    protocol = load_campaign_protocol(PROTOCOL).protocol
    budgets = protocol.family_budgets()
    assert list(budgets) == [f.id for f in protocol.families]
    assert list(budgets.values()) == [17] * 8 + [16] * 4
    assert sum(budgets.values()) == protocol.formula_budget == 200


@pytest.mark.parametrize("windows", [[], [20, 10], [5, 5], [1, 5]])
def test_family_windows_must_be_sorted_unique_and_at_least_two(tmp_path, windows):
    def mutate(doc):
        _v2(doc)
        doc["families"][0]["windows"] = windows

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_null_check_cannot_allow_more_false_acceptances_than_replicates(tmp_path):
    def mutate(doc):
        _v2(doc)
        doc["null_check"]["max_false_acceptances"] = 41

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_forbidden_operators_are_matched_in_any_letter_case(tmp_path):
    def mutate(doc):
        doc["search"]["forbidden_operators"] = ["REALIZED_VOL", "TS_STD", "TS_MAD"]
        doc["families"][0]["mutation_operators"].append("ts_std")

    with pytest.raises(ValidationError, match="forbidden"):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_a_forbidden_operator_must_name_a_dsl_operator(tmp_path):
    def mutate(doc):
        doc["search"]["forbidden_operators"].append("ts_stdev")

    with pytest.raises(ValidationError, match="unknown forbidden operators"):
        load_campaign_protocol(_mutated(tmp_path, mutate))
