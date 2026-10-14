import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.alpha.search import WINDOWS
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    IntradayCampaignProtocol,
    ScoreRead,
    Selection,
    load_campaign_protocol,
)
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


def test_campaign_v3_loads_with_its_frozen_hash():
    loaded = load_campaign_protocol(PROTOCOL.with_name("campaign-v3.json"))
    assert loaded.sha256 == "d713b575792b62d233e0ad652e5a0a840e4dec80392beeb9cce98dcd531051f2"


def test_the_v2_fields_validate_and_make_the_protocol_campaign_ready(tmp_path):
    protocol = load_campaign_protocol(_mutated(tmp_path, _v2)).protocol
    protocol.require_campaign_ready()
    assert (protocol.null_check.replicates, protocol.null_check.max_false_acceptances) == (40, 2)
    assert protocol.power_search.seed == 20261002
    assert {f.id: f.constant_windows for f in protocol.families} == {k: tuple(v) for k, v in V2_WINDOWS.items()}


def test_search_mode_defaults_to_genetic_with_three_gates_and_rejects_unknown_modes(tmp_path):
    protocol = load_campaign_protocol(PROTOCOL).protocol
    assert protocol.search_mode == "genetic" and protocol.requires_search_power is True
    assert [name for name, _ in protocol.gates] == ["power", "search_power", "null_check"]

    def unknown(doc):
        doc["search_mode"] = "random"

    with pytest.raises(ValidationError, match="search_mode"):
        load_campaign_protocol(_mutated(tmp_path, unknown))


def test_a_genetic_protocol_needs_a_power_search_block_even_in_v1(tmp_path):
    def mutate(doc):
        del doc["power_search"]

    with pytest.raises(ValidationError, match="genetic protocol needs power_search"):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_a_genetic_protocol_without_the_check_b_seed_is_not_campaign_ready(tmp_path):
    def mutate(doc):
        _v2(doc)
        del doc["power_search"]["seed"]

    protocol = load_campaign_protocol(_mutated(tmp_path, mutate)).protocol
    assert protocol.null_check is not None and protocol.power_search is not None
    with pytest.raises(ValueError, match="checks B and C"):
        protocol.require_campaign_ready()


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


@pytest.mark.parametrize(
    "selection",
    [
        {"rule": "top_k"},
        {"rule": "top_k", "k": 3, "fraction": 0.1},
        {"rule": "top_fraction"},
        {"rule": "top_fraction", "fraction": 1.5},
        {"rule": "top_fraction", "fraction": 0.1, "k": 3},
    ],
)
def test_a_selection_carries_exactly_its_own_parameter(selection):
    with pytest.raises(ValidationError):
        Selection(**selection)


def test_a_protocol_has_exactly_one_of_k_or_selection(tmp_path):
    def both(doc):
        doc["selection"] = {"rule": "top_fraction", "fraction": 0.1}

    def neither(doc):
        del doc["k"]

    for mutate in (both, neither):
        with pytest.raises(ValidationError, match="exactly one of k"):
            load_campaign_protocol(_mutated(tmp_path, mutate))


def test_v1_protocols_select_top_k_with_hold_skipping():
    protocol = load_campaign_protocol(PROTOCOL).protocol
    assert protocol.selection_rule == Selection(rule="top_k", k=3)


V4_PATH = REPO / "config/research/pooled/campaign-v4.json"
V4_SHA256 = "a72f2e65"  # recorded prefix of the v4 file's SHA-256 (docs/alpha-pooled-checks-v4-2026-10-07.md)


def _intraday_document() -> dict:
    doc = json.loads(V4_PATH.read_text())
    doc["id"] = "pooled-intraday-campaign"
    doc["version"] = 1
    doc["score_inputs"] = "hourly"
    doc["score_read"] = {"bars_before_decision": 1}
    return doc


def test_v4_identity_is_unchanged_by_the_dispatching_loader():
    loaded = load_campaign_protocol(V4_PATH)
    assert type(loaded.protocol) is CampaignProtocol
    assert loaded.sha256.startswith(V4_SHA256)
    assert hashlib.sha256(V4_PATH.read_bytes()).hexdigest() == loaded.sha256
    assert loaded.protocol.score_inputs == "daily"
    assert "score_inputs" not in loaded.protocol.model_dump(mode="json")


def test_intraday_protocol_is_a_subclass_with_its_three_fields(tmp_path):
    path = tmp_path / "intraday.json"
    path.write_text(json.dumps(_intraday_document()))
    loaded = load_campaign_protocol(path)
    protocol = loaded.protocol
    assert isinstance(protocol, IntradayCampaignProtocol) and isinstance(protocol, CampaignProtocol)
    assert protocol.score_inputs == "hourly" and protocol.score_read == ScoreRead(bars_before_decision=1)
    assert protocol.cube_spec().identity == CampaignProtocol(**json.loads(V4_PATH.read_text())).cube_spec().identity
    assert protocol.gates == (("power", "power_a"), ("null_check", "null_check"))
    dumped = protocol.model_dump(mode="json")
    assert dumped["id"] == "pooled-intraday-campaign" and dumped["score_read"] == {"bars_before_decision": 1}
    assert dumped["score_inputs"] == "hourly"


def test_intraday_protocol_refuses_genetic_mode_and_bad_reads():
    doc = _intraday_document()
    with pytest.raises(ValidationError, match="fixed_set"):
        IntradayCampaignProtocol(
            **{
                **doc,
                "search_mode": "genetic",
                "power_search": {"families": ["reversal_5"], "seeds": 10, "delta": 0.1, "min_recovered": 5, "seed": 1},
            }
        )
    with pytest.raises(ValidationError):
        IntradayCampaignProtocol(**{**doc, "score_read": {"bars_before_decision": 0}})
    with pytest.raises(ValidationError):
        IntradayCampaignProtocol(**{**doc, "score_inputs": "daily"})
    with pytest.raises(ValidationError):
        CampaignProtocol(**doc)  # the base refuses the intraday id and the extra fields


def test_loader_refuses_an_unknown_id(tmp_path):
    doc = _intraday_document()
    doc["id"] = "pooled-something"
    path = tmp_path / "x.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="id"):
        load_campaign_protocol(path)


@pytest.mark.parametrize("document", ["[]", "3", '"pooled-campaign"', "null"])
def test_loader_refuses_a_document_that_is_not_an_object(tmp_path, document):
    path = tmp_path / "x.json"
    path.write_text(document)
    with pytest.raises(ValueError, match="JSON object"):
        load_campaign_protocol(path)


def test_loader_refuses_a_missing_or_non_string_id(tmp_path):
    missing = _intraday_document()
    del missing["id"]
    unhashable = {**_intraday_document(), "id": ["pooled-campaign"]}
    numeric = {**_intraday_document(), "id": 4}
    for index, doc in enumerate((missing, unhashable, numeric)):
        path = tmp_path / f"x{index}.json"
        path.write_text(json.dumps(doc))
        with pytest.raises(ValueError, match="id"):
            load_campaign_protocol(path)
