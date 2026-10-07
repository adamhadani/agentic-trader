from pathlib import Path

import pytest

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import CampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.formula import require_dimensionless


REPO = Path(__file__).resolve().parents[3]
V3 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v3.json")
V4 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v4.json")

FIXED_SET = {
    "reversal_5": "-1.0 * roc(close, 5)",
    "high_126": "close / ts_max(high, 126)",
    "overnight_intraday": "ts_sum(open_gap, 21) - ts_sum(oc_spread, 21)",
    "abnormal_volume": "volume / ts_mean(volume, 50)",
    "momentum_12_1": "delay(close, 21) / delay(close, 252) - 1.0",
    "price_volume_corr": "-1.0 * ts_corr(returns, volume, 21)",
    "illiquidity": "ts_mean(hl_spread, 21)",
    "reversal_x_volume": "-1.0 * roc(close, 5) * (volume / ts_mean(volume, 50))",
}
LITERATURE_SCORES = ("close / ts_max(high, 252)", "-1.0 * roc(close, 21)")


def test_earlier_protocols_are_byte_identical_and_genetic():
    for name, sha in (("campaign-v1.json", None), ("campaign-v2.json", "6470ca97"), ("campaign-v3.json", "d713b575")):
        loaded = load_campaign_protocol(REPO / "config/research/pooled" / name)
        assert loaded.protocol.search_mode == "genetic"
        if sha:
            assert loaded.sha256.startswith(sha)
    assert V3.protocol.gates == (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))
    assert V3.protocol.requires_search_power is True


def test_v4_is_v3_with_the_fixed_set():
    p3, p4 = V3.protocol, V4.protocol
    assert p4.version == 4 and p4.search_mode == "fixed_set"
    for field in (
        "cohort",
        "cohort_sha256",
        "feed",
        "bars_from",
        "bars_through",
        "windows",
        "selection",
        "bracket",
        "universe",
        "decision_cost_bps",
        "coverage",
        "search",
        "complexity_penalty_per_node",
        "dedupe_jaccard",
        "bootstrap",
        "discovery_gate",
        "selection_gate",
        "confirmation_gate",
        "power",
        "null_check",
        "excluded_families",
    ):
        assert getattr(p4, field) == getattr(p3, field), field
    assert p4.power_search is None and p4.k is None
    assert p4.formula_budget == 8 and len(p4.families) == 8
    assert {f.id: f.seeds[0] for f in p4.families} == FIXED_SET
    assert all(f.mutation_operators == () and f.windows is None for f in p4.families)
    assert p4.gates == (("power", "power_a"), ("null_check", "null_check"))
    assert p4.requires_search_power is False
    assert p4.family_budgets() == {f.id: 1 for f in p4.families}
    p4.require_campaign_ready()  # fixed-set needs null_check only
    assert p4.cube_spec().identity == p3.cube_spec().identity  # same cached cube


def test_v4_seeds_are_valid_unique_and_distinct_from_the_literature_entries():
    seeds = V4.protocol.seed_expressions
    assert len(seeds) == 8 and len(set(seeds)) == 8
    for expression in FIXED_SET.values():
        require_dimensionless(expression)
    literature = {canonical_expression(s) for s in LITERATURE_SCORES}
    assert not (set(seeds) & literature)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            {
                "families": [
                    {
                        "id": "reversal_5",
                        "rationale": "r",
                        "seeds": ["-1.0 * roc(close, 5)"],
                        "mutation_operators": ["ts_mean"],
                    }
                ],
                "formula_budget": 1,
            },
            "mutation_operators",
        ),
        ({"formula_budget": 9}, "formula_budget"),
        (
            {"power_search": {"families": ["reversal_5"], "seeds": 1, "delta": 0.15, "min_recovered": 1, "seed": 1}},
            "power_search",
        ),
        ({"null_check": None}, "null_check"),
        (
            {
                "families": [
                    {"id": "a", "rationale": "r", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": []},
                    {"id": "b", "rationale": "r", "seeds": ["(-1.0)*roc(close,5)"], "mutation_operators": []},
                ],
                "formula_budget": 2,
            },
            "unique",
        ),
        (
            {
                "families": [
                    {
                        "id": "a",
                        "rationale": "r",
                        "seeds": ["-1.0 * roc(close, 5)", "(-1.0)*roc(close,5)"],
                        "mutation_operators": [],
                    }
                ],
                "formula_budget": 2,
            },
            "unique",
        ),
        (
            {
                "families": [
                    {
                        "id": "a",
                        "rationale": "r",
                        "seeds": ["-1.0 * roc(close, 5)"],
                        "mutation_operators": [],
                        "windows": [5, 10],
                    }
                ],
                "formula_budget": 1,
            },
            "windows",
        ),
    ],
)
def test_fixed_set_validators(patch, message):
    document = {**V4.protocol.model_dump(mode="json"), **patch}
    with pytest.raises(ValueError, match=message):
        CampaignProtocol.model_validate(document)


def test_genetic_mode_still_requires_power_search():
    document = {**V3.protocol.model_dump(mode="json"), "power_search": None}
    with pytest.raises(ValueError, match="power_search"):
        CampaignProtocol.model_validate(document)
