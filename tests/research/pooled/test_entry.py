from datetime import date, timedelta

import numpy as np
import pytest

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.entry import REPO_ROOT, evaluate_entry, load_pooled_entry
from agentic_trader.research.pooled.formula import Picks
from agentic_trader.research.pooled.stats import bootstrap_draws, session_table


ENTRIES = ["config/research/pooled/high52-v1.json", "config/research/pooled/reversal-lowmax-v1.json"]


@pytest.mark.parametrize("relative", ENTRIES)
def test_committed_entries_share_the_campaign_cube_and_pins(relative):
    loaded = load_pooled_entry(REPO_ROOT / relative)
    protocol = load_campaign_protocol(REPO_ROOT / "config/research/pooled/campaign-v1.json")
    assert loaded.entry.cube_spec() == protocol.protocol.cube_spec()
    assert loaded.entry.campaign_protocol_sha256 == protocol.sha256
    assert loaded.entry.cohort_sha256 == load_cohort(REPO_ROOT / loaded.entry.cohort).sha256
    assert loaded.entry.formula.k == 3


def view_with(edge_per_session: float, n_sessions: int, start=date(2021, 1, 4)) -> tuple[CubeView, Picks]:
    days: list[date] = []
    d = start
    while len(days) < n_sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rng = np.random.default_rng(3)
    r = rng.normal(0.0, 1.0, (n_sessions, 10))
    r[:, 0] += edge_per_session
    view = CubeView(
        sessions=tuple(days),
        symbols=tuple(f"S{j}" for j in range(10)),
        offset=0,
        eligible=np.ones(r.shape, bool),
        labelled=np.ones(r.shape, bool),
        r_gross=r,
        r_cost=r,
        holding=np.ones(r.shape, np.int16),
        hit=np.ones(r.shape, np.int8),
        tiebreak=np.zeros(r.shape, np.uint64),
        dollar_volume=np.ones(r.shape),
    )
    return view, Picks(np.arange(n_sessions), np.zeros(n_sessions, dtype=int))


def test_pass_rule_accepts_a_clear_edge_and_rejects_a_short_sample():
    entry = load_pooled_entry(REPO_ROOT / ENTRIES[0]).entry
    view, picks = view_with(1.0, 1500, start=date(2020, 1, 1))
    table = session_table(picks, view, purge=False)
    result = evaluate_entry(table, entry, bootstrap_draws(1500, 20, 300, 1))
    assert result["passes"]
    short_view, short_picks = view_with(1.0, 200, start=date(2020, 1, 1))
    short = evaluate_entry(session_table(short_picks, short_view, purge=False), entry, bootstrap_draws(200, 20, 300, 1))
    assert not short["p4"]["holds"] and not short["passes"]


def test_pass_rule_rejects_no_edge():
    entry = load_pooled_entry(REPO_ROOT / ENTRIES[0]).entry
    view, picks = view_with(0.0, 1500, start=date(2020, 1, 1))
    result = evaluate_entry(session_table(picks, view, purge=False), entry, bootstrap_draws(1500, 20, 300, 1))
    assert not result["p2"]["holds"] or not result["p1"]["holds"]
