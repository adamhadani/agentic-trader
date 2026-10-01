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
    assert not result["passes"]


# --- P3 / P4 isolation: hand-built cubes on a real weekday calendar around recent_from ---------------------------

RECENT_FROM = date(2023, 1, 1)


def calendar_view(col0: np.ndarray) -> tuple[CubeView, int]:
    """Weekday sessions from 2020-01-01; column 0 carries ``col0``, the other nine are noise around 0.

    Returns the view and ``first``, the index of the first session on or after RECENT_FROM (2023-01-02).
    """
    days: list[date] = []
    d = date(2020, 1, 1)
    while d < date(2023, 1, 10) or len(days) < len(col0):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    first = next(i for i, day in enumerate(days) if day >= RECENT_FROM)
    assert days[first - 1] < RECENT_FROM <= days[first]
    n = len(days)
    r = np.random.default_rng(11).normal(0.0, 0.3, (n, 10))
    r[:, 0] = 0.0
    r[: len(col0), 0] = col0[:n]
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
    return view, first


def first_recent_index() -> int:
    return calendar_view(np.zeros(1))[1]


def evaluate_picked(sessions_picked: list[int], col0: np.ndarray) -> dict:
    entry = load_pooled_entry(REPO_ROOT / ENTRIES[0]).entry
    view, _ = calendar_view(col0)
    picked = np.array(sorted(sessions_picked))
    table = session_table(Picks(picked, np.zeros(len(picked), dtype=int)), view, purge=False)
    return evaluate_entry(table, entry, bootstrap_draws(len(table.sessions), 20, 300, 1))


def strong_col0(length: int) -> np.ndarray:
    # +1.0 with a small deterministic wobble so bootstrap standard errors are non-zero.
    return 1.0 + 0.05 * np.sin(np.arange(length) * 1.7)


def test_p4_fails_on_too_few_recent_sessions_despite_enough_total():
    first = first_recent_index()
    picked = list(range(first - 550, first + 100))  # 650 sessions with a pick, only 100 recent
    result = evaluate_picked(picked, strong_col0(first + 100))
    assert result["p4"]["sessions"] >= 500 and result["p4"]["recent_sessions"] < 150
    assert result["p4"]["holds"] is False
    assert all(result[k]["holds"] is True for k in ("p1", "p2", "p3"))
    assert result["passes"] is False


def test_p4_boundary_pins_the_thresholds_and_the_recent_date():
    first = first_recent_index()
    col0 = strong_col0(first + 150)
    # 350 before + 150 from the first session on/after 2023-01-01: exactly at both thresholds.
    at = evaluate_picked(list(range(first - 350, first + 150)), col0)
    assert (at["p4"]["sessions"], at["p4"]["recent_sessions"]) == (500, 150)
    assert at["p4"]["holds"] is True and at["passes"] is True
    # 500 in total but the first recent session is skipped: 149 recent, the last 2022 session must not count.
    below = evaluate_picked(list(range(first - 351, first)) + list(range(first + 1, first + 150)), col0)
    assert (below["p4"]["sessions"], below["p4"]["recent_sessions"]) == (500, 149)
    assert below["p4"]["holds"] is False and below["passes"] is False
    assert all(below[k]["holds"] is True for k in ("p1", "p2", "p3"))
    # 499 in total with 150 recent: the total threshold is also inclusive.
    short = evaluate_picked(list(range(first - 349, first + 150)), col0)
    assert (short["p4"]["sessions"], short["p4"]["recent_sessions"]) == (499, 150)
    assert short["p4"]["holds"] is False


def test_p3_fails_when_the_recent_edge_is_negative():
    first = first_recent_index()
    col0 = np.concatenate([strong_col0(first), -0.3 + 0.05 * np.sin(np.arange(200) * 1.7)])
    result = evaluate_picked(list(range(first - 600, first + 200)), col0)
    assert result["p3"]["recent_edge"] < 0
    assert result["p3"]["trimmed_mean"] > 0
    assert result["p3"]["holds"] is False
    assert all(result[k]["holds"] is True for k in ("p1", "p2", "p4"))
    assert result["passes"] is False


def test_p3_fails_when_a_few_outliers_carry_the_leg_mean():
    first = first_recent_index()
    col0 = -0.05 + 0.02 * np.sin(np.arange(first + 200) * 1.7)
    outliers = list(range(first + 10, first + 200, 27))[:7]  # 7 of 700 picks: exactly the 1% trim on each side
    assert len(outliers) == 7
    col0[outliers] = 200.0
    result = evaluate_picked(list(range(first - 500, first + 200)), col0)
    assert result["p4"]["holds"] is True
    assert result["p3"]["trimmed_mean"] <= 0 < result["p3"]["recent_edge"]
    assert result["p3"]["holds"] is False
    # P1/P2 are deliberately not asserted here: the outliers can make the untrimmed tests look positive.
    assert result["passes"] is False
