from pathlib import Path

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
V2 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v2.json")
V1 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol
WINDOWS = {
    "high52": (126, 189, 252),
    "reversal": (3, 5, 10, 21),
    "max_lottery": (5, 10, 21, 42),
    "momentum_12_1": (21, 63, 105, 126, 189, 252),
    "momentum_12_7": (84, 105, 126, 147, 168),
    "range_location": (10, 20, 40, 60, 120),
    "trend_slope": (10, 20, 40, 60, 120),
    "abnormal_volume": (5, 10, 20, 50, 100),
    "overnight_intraday": (5, 10, 21, 42),
    "price_volume_corr": (5, 10, 21, 42),
    "signed_volume": (5, 10, 20, 40),
    "hl_spread": (5, 10, 21, 42),
}


def test_campaign_v2_pins_cohort_v2_and_is_campaign_ready():
    protocol = V2.protocol
    cohort = load_cohort(REPO / protocol.cohort)
    assert protocol.version == 2 and protocol.cohort == "config/research/pooled/cohort-v2.json"
    assert protocol.cohort_sha256 == cohort.sha256
    protocol.require_campaign_ready()
    assert {f.id: f.windows for f in protocol.families} == WINDOWS
    assert protocol.power_search.seed == 20261002
    assert protocol.null_check.model_dump() == {"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}


def test_campaign_v2_changes_nothing_else_from_v1():
    changed = {"version", "title", "cohort", "cohort_sha256", "families", "power_search", "null_check"}
    keep = set(type(V1).model_fields) - changed
    assert {k: getattr(V2.protocol, k) for k in keep} == {k: getattr(V1, k) for k in keep}
    assert [f.model_copy(update={"windows": None}) for f in V2.protocol.families] == list(V1.families)
    assert V2.protocol.power_search.model_copy(update={"seed": None}) == V1.power_search


def test_cohort_v2_is_the_screen_union():
    cohort = load_cohort(REPO / "config/research/pooled/cohort-v2.json").cohort
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert cohort.version == 2 and len(screened.symbols) == screened.screen.top == 400
    assert screened.screen.window_end.isoformat() == "2023-12-29"
    assert {source.kind for source in cohort.sources} == {"config_groups", "liquidity_screen"}
