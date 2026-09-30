import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
