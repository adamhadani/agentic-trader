import json
from pathlib import Path

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    IntradayCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.formula import require_dimensionless


REPO = Path(__file__).resolve().parents[3]
V4 = REPO / "config/research/pooled/campaign-v4.json"
V1 = REPO / "config/research/pooled/intraday-v1.json"
EXPECTED_SCORES = {
    "first_hour_momentum": "oc_spread",
    "overnight_persistence": "open_gap",
    "overnight_vs_intraday": "open_gap - oc_spread",
    "relative_volume": "volume / ts_mean(volume, 140)",
    "range_expansion": "hl_spread / ts_mean(hl_spread, 140)",
    "range_location": "(close - low) / (high - low)",
    "vwap_strength": "(close - vwap) / close",
    "intraday_high_proximity": "close / ts_max(high, 140)",
}


def test_intraday_v1_is_a_fixed_set_on_v4s_cube():
    loaded = load_campaign_protocol(V1)
    protocol = loaded.protocol
    assert isinstance(protocol, IntradayCampaignProtocol)
    assert protocol.version == 1 and protocol.search_mode == "fixed_set"
    assert protocol.score_inputs == "hourly" and protocol.score_read.bars_before_decision == 1
    v4 = CampaignProtocol(**json.loads(V4.read_text()))
    assert protocol.cube_spec().identity == v4.cube_spec().identity
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")
    assert protocol.cohort_sha256 == v4.cohort_sha256 and protocol.windows == v4.windows
    assert protocol.selection == v4.selection and protocol.bootstrap == v4.bootstrap
    assert protocol.discovery_gate == v4.discovery_gate
    assert protocol.confirmation_gate == v4.confirmation_gate
    assert protocol.power == v4.power and protocol.null_check == v4.null_check
    assert {f.id: f.seeds[0] for f in protocol.families} == EXPECTED_SCORES
    assert [f.id for f in protocol.families] == list(EXPECTED_SCORES)
    assert protocol.formula_budget == 8 and all(not f.mutation_operators for f in protocol.families)
    for seed in protocol.seed_expressions:
        require_dimensionless(seed)
    assert set(protocol.search.forbidden_operators) == {"realized_vol", "ts_std", "ts_mad"}
    assert protocol.power_search is None


def test_intraday_v1_excludes_every_v4_family():
    raw = json.loads(V1.read_text())
    v4 = json.loads(V4.read_text())
    excluded = {entry["id"] for entry in raw["excluded_families"]}
    assert {entry["id"] for entry in v4["excluded_families"]} <= excluded
    assert {family["id"] for family in v4["families"]} <= excluded
    assert not excluded & set(EXPECTED_SCORES)
