import json
from pathlib import Path

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    EventsCampaignProtocol,
    IntradayCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
POOLED = REPO / "config/research/pooled"
COHORT3_SHA256 = "8c0d807aa47b19d391b3967c97e35715caf974faa9ed4418075d794766fbe8d1"  # from the controller's dispatch
ACTIONS_SHA256 = "8a547b0694d9f171fa1fa7e39f492749f6468393426cf1a69de8a0bde0386c8e"  # cohort-v3 corporate-actions content_sha256, from the dispatch
PAIRS = (
    ("cohort3-events-v1.json", "events-v1.json", EventsCampaignProtocol),
    ("cohort3-daily-v1.json", "campaign-v4.json", CampaignProtocol),
    ("cohort3-intraday-v1.json", "intraday-v1.json", IntradayCampaignProtocol),
)
DECLARED = {
    "cohort",
    "cohort_sha256",
    "universe",
    "decision_cost_bps",
    "version",
    "title",
    "rationale",
}


def test_cohort_v3_is_the_band_without_config_groups():
    loaded = load_cohort(POOLED / "cohort-v3.json")
    cohort = loaded.cohort
    assert loaded.sha256 == COHORT3_SHA256
    assert cohort.version == 3
    assert [source.kind for source in cohort.sources] == ["liquidity_screen"]
    screen = cohort.sources[0].screen
    assert screen.rank_from == 401 and screen.top == 1400
    assert screen.snapshot_id.startswith("00a9a363b02a6f49")
    assert 900 <= len(cohort.symbols) <= 1000
    v2 = load_cohort(POOLED / "cohort-v2.json").cohort
    screened_v2 = next(s for s in v2.sources if s.kind == "liquidity_screen").symbols
    assert not set(cohort.symbols) & set(screened_v2)  # the band starts where v2's top cut ends


def test_cohort3_protocols_equal_their_parents_except_the_declared_keys():
    for child_name, parent_name, cls in PAIRS:
        child = load_campaign_protocol(POOLED / child_name)
        assert isinstance(child.protocol, cls)
        c, p = json.loads((POOLED / child_name).read_text()), json.loads((POOLED / parent_name).read_text())
        differing = {k for k in set(c) | set(p) if c.get(k) != p.get(k)}
        if cls is EventsCampaignProtocol:
            differing -= {"event_inputs"}
            assert c["event_inputs"]["corporate_actions"]["content_sha256"] == ACTIONS_SHA256
            assert c["event_inputs"]["earnings"] == p["event_inputs"]["earnings"]
        assert differing <= DECLARED, (child_name, differing - DECLARED)
        assert c["cohort"] == "config/research/pooled/cohort-v3.json" and c["cohort_sha256"] == COHORT3_SHA256
        assert c["universe"] == {"min_price": 10.0, "min_median_dollar_volume": 5000000.0, "min_eligible_names": 30}
        assert c["decision_cost_bps"] == 15.0 and c["version"] == 1
        assert c["families"] == p["families"] and c["search_mode"] == "fixed_set"
        assert (
            child.protocol.cube_spec().identity
            == load_campaign_protocol(POOLED / "cohort3-daily-v1.json").protocol.cube_spec().identity
        )
        assert not child.protocol.cube_spec().identity.startswith("f1c835a3")
