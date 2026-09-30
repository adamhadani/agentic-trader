# tests/research/pooled/test_campaign_stages.py
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from agentic_trader.research.pooled.campaign import (
    CampaignWindows,
    InMemoryLedger,
    ScoredFormula,
    load_campaign_protocol,
    run_stages,
)
from agentic_trader.research.pooled.cube import LabelCube


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol


def sessions_between(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_cube(n_names=40, seed=0, planted=None, delta=0.0) -> LabelCube:
    days = tuple(sessions_between(PROTOCOL.windows.discovery[0], PROTOCOL.windows.confirmation[1]))
    rng = np.random.default_rng(seed)
    s = len(days)
    r = rng.normal(0.0, 1.2, (s, n_names))
    if planted is not None:
        r[:, planted] += delta
    arrays = {
        "eligible": np.ones((s, n_names), bool),
        "labelled": np.ones((s, n_names), bool),
        "r_gross": r,
        "r_cost": r,
        "holding": np.ones((s, n_names), np.int16),
        "hit": np.ones((s, n_names), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n_names)).astype(np.uint64),
        "dollar_volume": np.ones((s, n_names)),
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256="c" * 64,
        sessions=days,
        symbols=tuple(f"S{j}" for j in range(n_names)),
        arrays=arrays,
        coverage={},
    )


def constant_formula(name: str, favourite: int) -> ScoredFormula:
    def panel(view):
        scores = np.zeros(view.eligible.shape)
        scores[:, favourite] = 1.0
        return scores, np.ones_like(view.eligible)

    return ScoredFormula(formula_id=name, nodes=3, panel=panel)


def test_a_strong_planted_edge_is_confirmed_and_the_window_consumed_once():
    cube = synthetic_cube(planted=0, delta=1.0)
    windows = CampaignWindows(cube, PROTOCOL.windows, cohort_sha256="c" * 64)
    ledger = InMemoryLedger()
    outcome = run_stages(
        [constant_formula("planted", 0), constant_formula("null", 5)], windows, ledger, PROTOCOL, campaign_id="t1"
    )
    assert outcome["status"] == "confirmed"
    assert outcome["confirmed"] == ["planted"]
    assert windows.opened == ("discovery", "selection", "confirmation")
    assert len(ledger.consumed) == 1 and ledger.consumed[0]["candidates"] == ("planted",)


def test_no_discovery_survivor_leaves_later_windows_unread():
    windows = CampaignWindows(synthetic_cube(), PROTOCOL.windows, cohort_sha256="c" * 64)
    ledger = InMemoryLedger()
    outcome = run_stages([constant_formula("null", 5)], windows, ledger, PROTOCOL, campaign_id="t2")
    assert outcome["status"] == "no_finalists"
    assert windows.opened == ("discovery",)
    assert ledger.consumed == []


def test_confirmation_is_consumed_before_it_is_read_and_never_twice():
    cube = synthetic_cube(planted=0, delta=1.0)
    ledger = InMemoryLedger()
    run_stages(
        [constant_formula("planted", 0)],
        CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
        ledger,
        PROTOCOL,
        campaign_id="a",
    )
    with pytest.raises(ValueError, match="already consumed"):
        run_stages(
            [constant_formula("planted", 0)],
            CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
            ledger,
            PROTOCOL,
            campaign_id="b",
        )


def test_duplicates_are_deduplicated_by_pick_overlap():
    cube = synthetic_cube(planted=0, delta=1.0)
    outcome = run_stages(
        [constant_formula("a", 0), constant_formula("b", 0)],
        CampaignWindows(cube, PROTOCOL.windows, "c" * 64),
        InMemoryLedger(),
        PROTOCOL,
        campaign_id="d",
    )
    assert len(outcome["carried"]) == 1
