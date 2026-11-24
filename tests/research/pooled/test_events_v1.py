import json
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.pooled.campaign import EventsCampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.events import EVENT_FIELDS
from agentic_trader.research.pooled.formula import evaluate_prepared, prepare_daily, require_dimensionless
from agentic_trader.research.pooled.scoring import ScoreBook
from tests.research.pooled.mini_world import ADJUSTED, BAR_DAYS, EVENT_FRAMES, NAMES, SESSIONS
from tests.research.pooled.test_intraday_v1 import V1 as INTRADAY


REPO = Path(__file__).resolve().parents[3]
V1 = REPO / "config/research/pooled/events-v1.json"
EVENTS_SHA256 = "4e156fd8"  # recorded prefix of the events-v1 file's SHA-256 (docs/alpha-pooled-mining.md)
EXPECTED_SCORES = {
    "pead_surprise_decay": "earnings_surprise / (1 + earnings_age)",
    "pead_reaction_decay": "earnings_reaction / (1 + earnings_age)",
    "pead_reaction_window": "earnings_reaction / (earnings_age <= 20)",
    "pead_agreement": "sign(earnings_surprise) * earnings_reaction / (earnings_age <= 20)",
    "surprise_x_reaction": "earnings_surprise * earnings_reaction / (1 + earnings_age)",
    "quiet_momentum": "ts_sum(returns, 20) / (earnings_age > 20)",
    "quiet_reversal": "-ts_sum(returns, 5) / (earnings_age > 20)",
    "dividend_yield": "dividend_yield",
}


def test_events_v1_is_a_fixed_set_on_the_intraday_cube_with_pinned_sources():
    loaded = load_campaign_protocol(V1)
    assert loaded.sha256.startswith(EVENTS_SHA256)
    protocol = loaded.protocol
    assert isinstance(protocol, EventsCampaignProtocol)
    assert protocol.version == 1 and protocol.search_mode == "fixed_set" and protocol.score_inputs == "daily"
    intraday = load_campaign_protocol(INTRADAY).protocol
    assert protocol.cube_spec().identity == intraday.cube_spec().identity
    assert protocol.cube_spec().identity.startswith("f1c835a3d3364d2b")
    for name in (
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
    ):
        assert getattr(protocol, name) == getattr(intraday, name), name
    assert {f.id: f.seeds[0] for f in protocol.families} == EXPECTED_SCORES
    assert [f.id for f in protocol.families] == list(EXPECTED_SCORES)
    assert protocol.formula_budget == 8 and all(not f.mutation_operators for f in protocol.families)
    for seed in protocol.seed_expressions:
        require_dimensionless(seed)
    e = protocol.event_inputs
    assert e.fields_version == "events_fields_v1"
    assert e.earnings.pages_sha256 == "9273e40a7aaed0d925003502a621ecfedf40d0aa889aeaaca992d84f187aa3da"
    assert (e.earnings.calendar_from.isoformat(), e.earnings.calendar_through.isoformat()) == (
        "2016-02-23",
        "2026-07-31",
    )
    assert (e.earnings.min_estimates, e.earnings.min_abs_forecast, e.earnings.vol_window, e.earnings.benchmark) == (
        1,
        0.05,
        20,
        "SPY",
    )
    assert e.corporate_actions.content_sha256 == "776d4df7ef9312456f77a85179434f026be37233efc3a27ad77991ffc41a8344"
    assert tuple(d.isoformat() for d in e.corporate_actions.window) == ("2016-01-01", "2027-03-31")
    assert e.corporate_actions.trailing_sessions == 252
    assert protocol.power_search is None


def test_events_v1_excludes_every_intraday_and_v4_family():
    raw = json.loads(V1.read_text())
    intraday = json.loads(INTRADAY.read_text())
    excluded = {entry["id"] for entry in raw["excluded_families"]}
    assert {entry["id"] for entry in intraday["excluded_families"]} <= excluded
    assert {family["id"] for family in intraday["families"]} <= excluded
    assert not excluded & set(EXPECTED_SCORES)


def test_every_events_v1_seed_evaluates_on_a_frame_with_event_columns():
    keyed = prepare_daily(ADJUSTED)[NAMES[0]].join(EVENT_FRAMES[NAMES[0]], how="left")
    evaluator = AlphaExpressionEvaluator()
    for expression in EXPECTED_SCORES.values():
        series = evaluator.evaluate(expression, keyed)
        assert isinstance(series, pd.Series) and series.index.equals(keyed.index)
        assert np.isfinite(series.to_numpy(float)).any(), expression


def test_windowed_formulas_are_nan_out_of_window_so_the_cell_is_ineligible():
    """``x / (cond)``: safe_div gives NaN where the 0/1 condition is 0, and evaluate_prepared keeps it."""
    book = ScoreBook.with_fields(ADJUSTED, EVENT_FRAMES, EVENT_FIELDS, BAR_DAYS, SESSIONS, NAMES)
    window = book.panel(EXPECTED_SCORES["pead_reaction_window"])
    reaction = book.panel("earnings_reaction")
    age = book.panel("earnings_age")
    inside = np.isfinite(age) & (age <= 20)
    outside = np.isfinite(age) & (age > 20)
    assert inside.any() and outside.any()
    assert np.isnan(window[outside]).all() and np.isnan(window[~np.isfinite(age)]).all()
    assert np.array_equal(window[inside], reaction[inside], equal_nan=True)
    quiet = book.panel(EXPECTED_SCORES["quiet_momentum"])
    momentum = book.panel("ts_sum(returns, 20)")
    assert np.isnan(quiet[inside]).all()
    assert np.array_equal(quiet[outside], momentum[outside], equal_nan=True)
    # The same through evaluate_prepared directly: the panel cell is NaN, i.e. ineligible.
    keyed = {name: prepare_daily(ADJUSTED)[name].join(EVENT_FRAMES[name], how="left") for name in NAMES}
    direct = evaluate_prepared(EXPECTED_SCORES["pead_reaction_window"], keyed, BAR_DAYS, SESSIONS, NAMES)
    assert np.array_equal(direct, window, equal_nan=True)
