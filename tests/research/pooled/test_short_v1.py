import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.pooled.campaign import ShortVolumeCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.campaign_run import frozen_document
from agentic_trader.research.pooled.formula import evaluate_prepared, prepare_daily, require_dimensionless
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.pooled.short_fields import SHORT_FIELDS
from tests.research.pooled.mini_world import ADJUSTED, BAR_DAYS, NAMES, SESSIONS, SHORT_FRAMES
from tests.research.pooled.test_events_v1 import V1 as EVENTS


REPO = Path(__file__).resolve().parents[3]
SHORT_V1 = REPO / "config/research/pooled/short-v1.json"
COHORT3_EVENTS = REPO / "config/research/pooled/cohort3-events-v1.json"
COHORT3_SHORT_V1 = REPO / "config/research/pooled/cohort3-short-v1.json"
SHORT_V1_SHA256 = "441c05b5"  # recorded prefix of the short-v1 file's SHA-256 (docs/alpha-pooled-mining.md)
COHORT3_SHORT_V1_SHA256 = "0bcd6d88"  # recorded prefix of the cohort3-short-v1 file's SHA-256
FILES_SHA256 = "17315ae2525e44481a6c3c13fc75951fe1763af4ab101213c29cce66fe8f83a2"
EXPECTED_SCORES = {
    "light_shorting": "-short_ratio",
    "light_shorting_week": "-ts_mean(short_ratio, 5)",
    "shorts_retreat": "-short_ratio_z",
    "shorts_retreat_week": "-ts_mean(short_ratio_z, 5)",
    "shorts_retreat_volume": "-short_volume_rel",
    "unopposed_strength": "-short_ratio_z / (ts_sum(returns, 5) > 0)",
    "squeeze": "short_ratio_z / (ts_sum(returns, 5) < 0)",
    "quiet_tape": "-offex_share",
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
PARAMS = [
    (SHORT_V1, SHORT_V1_SHA256, EVENTS, "776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344"),
    (
        COHORT3_SHORT_V1,
        COHORT3_SHORT_V1_SHA256,
        COHORT3_EVENTS,
        "8a547b0694d9f171fa1fa7e39f492749f6468393426cf1a69de8a0bde0386c8e",
    ),
]


@pytest.mark.parametrize(("path", "prefix", "parent", "actions_sha"), PARAMS)
def test_short_protocols_are_fixed_sets_sharing_the_parent_cube_with_pinned_sources(path, prefix, parent, actions_sha):
    loaded = load_campaign_protocol(path)
    assert loaded.sha256.startswith(prefix)
    protocol = loaded.protocol
    assert isinstance(protocol, ShortVolumeCampaignProtocol)
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
    s = protocol.short_inputs
    assert s.fields_version == "short_fields_v1"
    assert s.short_volume.model_dump(mode="json") == {
        "source": "finra-regsho-cnms",
        "files_sha256": FILES_SHA256,
        "files_from": "2018-08-01",
        "files_through": "2026-09-01",
        "z_window": 20,
        "min_total_volume": 10000,
        "max_offex_share": 1.5,
    }
    assert s.corporate_actions.content_sha256 == actions_sha
    assert tuple(d.isoformat() for d in s.corporate_actions.window) == ("2016-01-01", "2027-03-31")
    assert s.corporate_actions.trailing_sessions == 252
    assert protocol.power_search is None


def test_short_v1_runs_on_the_cohort_v2_cube():
    protocol = load_campaign_protocol(SHORT_V1).protocol
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")


@pytest.mark.parametrize("path", [SHORT_V1, COHORT3_SHORT_V1])
def test_short_protocols_exclude_every_earlier_family(path):
    excluded = {entry["id"] for entry in json.loads(path.read_text())["excluded_families"]}
    events = json.loads(EVENTS.read_text())
    cohort3 = json.loads(COHORT3_EVENTS.read_text())
    assert {entry["id"] for entry in cohort3["excluded_families"]} <= excluded
    assert {family["id"] for family in events["families"]} <= excluded
    assert not excluded & set(EXPECTED_SCORES)


@pytest.mark.parametrize("path", [SHORT_V1, COHORT3_SHORT_V1])
def test_a_short_formula_never_shares_an_identity_with_the_same_text_under_events(path):
    short = load_campaign_protocol(path).protocol
    events = load_campaign_protocol(EVENTS).protocol
    text = "-short_ratio"
    assert frozen_document(text, short)["identity"] != frozen_document(text, events)["identity"]


def test_every_short_v1_seed_evaluates_on_a_frame_with_short_columns():
    keyed = prepare_daily(ADJUSTED)[NAMES[0]].join(SHORT_FRAMES[NAMES[0]], how="left")
    evaluator = AlphaExpressionEvaluator()
    for expression in EXPECTED_SCORES.values():
        series = evaluator.evaluate(expression, keyed)
        assert isinstance(series, pd.Series) and series.index.equals(keyed.index)
        assert np.isfinite(series.to_numpy(float)).any(), expression


def test_windowed_formulas_are_nan_out_of_window_so_the_cell_is_ineligible():
    """``x / (cond)``: safe_div gives NaN where the 0/1 condition is 0, and evaluate_prepared keeps it."""
    book = ScoreBook.with_fields(ADJUSTED, SHORT_FRAMES, SHORT_FIELDS, BAR_DAYS, SESSIONS, NAMES)
    z = book.panel("short_ratio_z")
    week = book.panel("ts_sum(returns, 5)")
    for key, inside in (
        ("unopposed_strength", np.isfinite(week) & (week > 0)),
        ("squeeze", np.isfinite(week) & (week < 0)),
    ):
        window = book.panel(EXPECTED_SCORES[key])
        outside = np.isfinite(week) & ~inside
        assert (inside & np.isfinite(z)).any() and outside.any()
        assert np.isnan(window[outside]).all() and np.isnan(window[~np.isfinite(week)]).all()
        sign = -1.0 if key == "unopposed_strength" else 1.0
        assert np.array_equal(window[inside], sign * z[inside], equal_nan=True)
        keyed = {name: prepare_daily(ADJUSTED)[name].join(SHORT_FRAMES[name], how="left") for name in NAMES}
        direct = evaluate_prepared(EXPECTED_SCORES[key], keyed, BAR_DAYS, SESSIONS, NAMES)
        assert np.array_equal(direct, window, equal_nan=True)
