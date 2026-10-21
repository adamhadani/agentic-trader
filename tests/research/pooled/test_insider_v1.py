import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.pooled.campaign import InsiderCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.campaign_run import frozen_document
from agentic_trader.research.pooled.formula import evaluate_prepared, prepare_daily, require_dimensionless
from agentic_trader.research.pooled.insider_fields import INSIDER_FIELDS
from agentic_trader.research.pooled.scoring import ScoreBook
from tests.research.pooled.mini_world import ADJUSTED, BAR_DAYS, INSIDER_FRAMES, NAMES, SESSIONS
from tests.research.pooled.test_short_v1 import COHORT3_SHORT_V1, SHORT_V1


REPO = Path(__file__).resolve().parents[3]
INSIDER_V1 = REPO / "config/research/pooled/insider-v1.json"
COHORT3_INSIDER_V1 = REPO / "config/research/pooled/cohort3-insider-v1.json"
INSIDER_V1_SHA256 = "37bb463b"  # recorded prefix of the insider-v1 file's SHA-256 (docs/alpha-pooled-mining.md)
COHORT3_INSIDER_V1_SHA256 = "41e10194"  # recorded prefix of the cohort3-insider-v1 file's SHA-256
FILES_SHA256 = "67a262c0aa9acca12f4d1c67db1236d41771fda793cccabcb6eb185925cd66d9"
EXPECTED_SCORES = {
    "net_buying": "insider_net_ratio",
    "buy_pressure": "insider_buy_rel / (insider_buy_rel > 0)",
    "opportunistic_buying": "insider_opp_buy_rel / (insider_opp_buy_rel > 0)",
    "cluster_buying": "insider_buy_rel / (insider_buyers > 1)",
    "fresh_purchase": "-insider_buy_age",
    "recent_buy_pressure": "insider_buy_rel / (insider_buy_age <= 10)",
    "buying_into_weakness": "insider_buy_rel / (ts_sum(returns, 20) < 0)",
    "light_selling": "-insider_sell_rel / (insider_sell_rel > 0)",
}
SHARED = (
    "cohort_sha256",
    "windows",
    "selection",
    "bootstrap",
    "discovery_gate",
    "selection_gate",
    "confirmation_gate",
    "power",
    "null_check",
    "search",
    "coverage",
    "universe",
    "bars_from",
    "bars_through",
    "decision_cost_bps",
)
PINNED_FILINGS = {
    "source": "sec-form345-datasets",
    "files_sha256": FILES_SHA256,
    "quarters_from": "2015q1",
    "quarters_through": "2026q3",
    "buy_window": 63,
    "net_window": 126,
    "owner_roles": ["Officer", "Director"],
    "exclude_10b5_1": False,
    "max_trade_rel": 10,
}
PARAMS = [
    (
        INSIDER_V1,
        INSIDER_V1_SHA256,
        SHORT_V1,
        "776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344",
    ),
    (
        COHORT3_INSIDER_V1,
        COHORT3_INSIDER_V1_SHA256,
        COHORT3_SHORT_V1,
        "8a547b0694d9f171fa1fa7e39f492749f6468393426cf1a69de8a0bde0386c8e",
    ),
]
SHORT_FAMILIES = (
    "light_shorting",
    "light_shorting_week",
    "shorts_retreat",
    "shorts_retreat_week",
    "shorts_retreat_volume",
    "unopposed_strength",
    "squeeze",
    "quiet_tape",
)


@pytest.mark.parametrize(("path", "prefix", "parent", "actions_sha"), PARAMS)
def test_insider_protocols_are_fixed_sets_sharing_the_parent_cube_with_pinned_sources(
    path, prefix, parent, actions_sha
):
    loaded = load_campaign_protocol(path)
    assert loaded.sha256.startswith(prefix)
    protocol = loaded.protocol
    assert isinstance(protocol, InsiderCampaignProtocol)
    assert protocol.version == 1 and protocol.search_mode == "fixed_set" and protocol.score_inputs == "daily"
    sibling = load_campaign_protocol(parent).protocol
    assert protocol.cube_spec().identity == sibling.cube_spec().identity
    for name in SHARED:
        assert getattr(protocol, name) == getattr(sibling, name), name
    assert {f.id: f.seeds[0] for f in protocol.families} == EXPECTED_SCORES
    assert [f.id for f in protocol.families] == list(EXPECTED_SCORES)
    assert [tuple(f.seeds) for f in protocol.families] == [(s,) for s in EXPECTED_SCORES.values()]
    assert protocol.formula_budget == 8 and all(not f.mutation_operators for f in protocol.families)
    for seed in protocol.seed_expressions:
        require_dimensionless(seed)
    i = protocol.insider_inputs
    assert i.fields_version == "insider_fields_v1"
    assert i.insider_filings.model_dump(mode="json") == PINNED_FILINGS
    assert i.corporate_actions.content_sha256 == actions_sha
    assert tuple(d.isoformat() for d in i.corporate_actions.window) == ("2016-01-01", "2027-03-31")
    assert i.corporate_actions.trailing_sessions == 252
    assert protocol.power_search is None


def test_insider_v1_runs_on_the_cohort_v2_cube():
    protocol = load_campaign_protocol(INSIDER_V1).protocol
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")


@pytest.mark.parametrize("path", [INSIDER_V1, COHORT3_INSIDER_V1])
def test_insider_protocols_exclude_every_earlier_family(path):
    excluded = {entry["id"] for entry in json.loads(path.read_text())["excluded_families"]}
    cohort3_short = json.loads(COHORT3_SHORT_V1.read_text())
    short = json.loads(SHORT_V1.read_text())
    assert {entry["id"] for entry in cohort3_short["excluded_families"]} <= excluded
    assert {entry["id"] for entry in short["excluded_families"]} <= excluded
    assert {family["id"] for family in short["families"]} == set(SHORT_FAMILIES) <= excluded
    assert not excluded & set(EXPECTED_SCORES)


@pytest.mark.parametrize("path", [INSIDER_V1, COHORT3_INSIDER_V1])
def test_an_insider_formula_never_shares_an_identity_with_the_same_text_under_short_volume(path):
    insider = load_campaign_protocol(path).protocol
    short = load_campaign_protocol(SHORT_V1).protocol
    text = "insider_net_ratio"
    assert frozen_document(text, insider)["identity"] != frozen_document(text, short)["identity"]


def test_every_insider_v1_seed_evaluates_on_a_frame_with_insider_columns():
    keyed = prepare_daily(ADJUSTED)[NAMES[0]].join(INSIDER_FRAMES[NAMES[0]], how="left")
    evaluator = AlphaExpressionEvaluator()
    for key, expression in EXPECTED_SCORES.items():
        series = evaluator.evaluate(expression, keyed)
        assert isinstance(series, pd.Series) and series.index.equals(keyed.index)
        if key != "light_selling":  # the mini world has no sales: the condition is never met
            assert np.isfinite(series.to_numpy(float)).any(), expression


def test_conditional_formulas_are_nan_out_of_condition_so_the_cell_is_ineligible():
    """``x / (cond)``: safe_div gives NaN where the 0/1 condition is 0, and evaluate_prepared keeps it."""
    book = ScoreBook.with_fields(ADJUSTED, INSIDER_FRAMES, INSIDER_FIELDS, BAR_DAYS, SESSIONS, NAMES)
    buy = book.panel("insider_buy_rel")
    buyers = book.panel("insider_buyers")
    age = book.panel("insider_buy_age")
    week = book.panel("ts_sum(returns, 20)")
    with np.errstate(invalid="ignore"):
        cases = {
            "buy_pressure": buy > 0,
            "cluster_buying": buyers > 1,
            "recent_buy_pressure": age <= 10,
            "buying_into_weakness": np.isfinite(week) & (week < 0),
        }
    keyed = {name: prepare_daily(ADJUSTED)[name].join(INSIDER_FRAMES[name], how="left") for name in NAMES}
    for key, inside in cases.items():
        window = book.panel(EXPECTED_SCORES[key])
        known = np.isfinite(buy)
        outside = known & ~inside
        assert (inside & known).any() and outside.any(), key
        assert np.isnan(window[outside]).all(), key
        assert np.array_equal(window[inside & known], buy[inside & known], equal_nan=True), key
        direct = evaluate_prepared(EXPECTED_SCORES[key], keyed, BAR_DAYS, SESSIONS, NAMES)
        assert np.array_equal(direct, window, equal_nan=True), key
