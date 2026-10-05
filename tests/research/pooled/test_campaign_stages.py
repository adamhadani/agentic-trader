# tests/research/pooled/test_campaign_stages.py
import hashlib
import json
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from agentic_trader.research.pooled.campaign import (
    CampaignWindows,
    DiscoveryEvaluator,
    InMemoryLedger,
    ScoredFormula,
    Selection,
    _confirmation,
    _discovery,
    _months_before,
    finish_discovery,
    later_stages,
    load_campaign_protocol,
    run_stages,
)
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.setups.study import _finite_json


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


# --- gate, ordering and multi-candidate discrimination (deterministic hand-built cubes) ---

DAYS = tuple(sessions_between(PROTOCOL.windows.discovery[0], PROTOCOL.windows.confirmation[1]))
P1 = PROTOCOL.model_copy(update={"k": 1})  # one pick per session: distinct favourites share no cells
COHORT = "c" * 64


def window_slice(window: tuple[date, date]) -> slice:
    return slice(bisect_left(DAYS, window[0]), bisect_right(DAYS, window[1]))


DISC, SEL, CONF = (
    window_slice(w) for w in (PROTOCOL.windows.discovery, PROTOCOL.windows.selection, PROTOCOL.windows.confirmation)
)
BLOCKS = np.linspace(0, DISC.stop, PROTOCOL.discovery_gate.blocks + 1).astype(int)


def returns(seed: int = 1, n_names: int = 30, favourites: tuple[int, ...] = (0,), fav_sd: float = 0.05) -> np.ndarray:
    """Noisy controls; the favourites are almost noise-free so effects are exact, not chance."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 1.0, (len(DAYS), n_names))
    r[:, list(favourites)] = rng.normal(0.0, fav_sd, (len(DAYS), len(favourites)))
    return r


def cube_of(r: np.ndarray, holding: int = 1) -> LabelCube:
    rng = np.random.default_rng(7)
    shape = r.shape
    arrays = {
        "eligible": np.ones(shape, bool),
        "labelled": np.ones(shape, bool),
        "r_gross": r,
        "r_cost": r,
        "holding": np.full(shape, holding, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": rng.integers(0, 2**62, shape).astype(np.uint64),
        "dollar_volume": np.ones(shape),
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256=COHORT,
        sessions=DAYS,
        symbols=tuple(f"S{j}" for j in range(shape[1])),
        arrays=arrays,
        coverage={},
    )


def favourite(name: str, column: int, nodes: int = 3, rows=None) -> ScoredFormula:
    """Always picks ``column`` (k=1) on the rows ``rows(view)`` allows; every row when None."""

    def panel(view):
        scores = np.full(view.eligible.shape, np.nan)  # NaN: never a fallback pick while held
        scores[:, column] = 1.0
        allowed = np.ones_like(view.eligible)
        if rows is not None:
            allowed = allowed & rows(view)[:, None]
        return scores, allowed

    return ScoredFormula(formula_id=name, nodes=nodes, panel=panel)


def windows_of(cube: LabelCube) -> CampaignWindows:
    return CampaignWindows(cube, PROTOCOL.windows, COHORT)


def discover(cube: LabelCube, *formulas: ScoredFormula):
    rows, carried = _discovery(list(formulas), windows_of(cube).discovery(), P1)
    return {row["formula_id"]: row for row in rows}, carried


def confirm(cube: LabelCube, formulas: list[ScoredFormula], frozen: list[str]):
    rows, confirmed = _confirmation(
        formulas, frozen, windows_of(cube).confirmation(InMemoryLedger(), campaign_id="x", candidates=()), P1
    )
    return {row["formula_id"]: row for row in rows}, confirmed


class SpyLedger(InMemoryLedger):
    def __init__(self, windows: CampaignWindows) -> None:
        super().__init__()
        self._windows = windows
        self.opened_at_call: list[tuple[str, ...]] = []

    def consume_confirmation(self, **kwargs) -> None:
        self.opened_at_call.append(self._windows.opened)
        super().consume_confirmation(**kwargs)


def edge_in(windows: dict[slice, float], seed: int = 1) -> LabelCube:
    r = returns(seed)
    for span, delta in windows.items():
        r[span, 0] += delta
    return cube_of(r)


def test_confirmation_is_consumed_before_its_window_is_opened_and_a_refusal_never_opens_it():
    cube = edge_in({DISC: 0.5, SEL: 0.5, CONF: 0.5})
    first = windows_of(cube)
    spy = SpyLedger(first)
    assert run_stages([favourite("f", 0)], first, spy, P1, campaign_id="a")["status"] == "confirmed"
    assert spy.opened_at_call == [("discovery", "selection")]
    assert first.opened[-1] == "confirmation"
    second = windows_of(cube)
    with pytest.raises(ValueError, match="already consumed"):
        run_stages([favourite("f", 0)], second, spy, P1, campaign_id="b")
    assert "confirmation" not in second.opened


def test_an_edge_that_exists_only_in_discovery_stops_at_selection():
    windows = windows_of(edge_in({DISC: 0.5}))
    ledger = InMemoryLedger()
    outcome = run_stages([favourite("f", 0)], windows, ledger, P1, campaign_id="s")
    assert outcome["carried"] == ["f"]
    assert outcome["status"] == "no_confirmation_candidates"
    assert outcome["frozen"] == [] and ledger.consumed == []
    assert windows.opened == ("discovery", "selection")


def test_a_selection_edge_below_half_the_discovery_edge_is_not_frozen():
    # discovery edge ~0.48; selection edge ~0.10 is positive but under the 0.5 fraction.
    windows = windows_of(edge_in({DISC: 0.5, SEL: 0.1}))
    outcome = run_stages([favourite("f", 0)], windows, InMemoryLedger(), P1, campaign_id="s")
    (row,) = outcome["selection"]
    assert 0 < row["edge"]["mean"] < 0.5 * outcome["discovery"][0]["edge"]["mean"]
    assert outcome["status"] == "no_confirmation_candidates"


def test_too_few_selection_sessions_are_not_frozen():
    sparse = favourite("f", 0, rows=lambda view: (np.arange(len(view.sessions)) % 4 == 0) | (view.offset != SEL.start))
    outcome = run_stages([sparse], windows_of(edge_in({DISC: 0.5, SEL: 0.5})), InMemoryLedger(), P1, campaign_id="s")
    (row,) = outcome["selection"]
    assert row["edge"]["mean"] > 0.25 and row["edge"]["n_sessions"] < PROTOCOL.selection_gate.min_sessions
    assert outcome["status"] == "no_confirmation_candidates"


def test_a_frozen_candidate_without_a_confirmation_edge_is_consumed_and_not_confirmed():
    windows = windows_of(edge_in({DISC: 0.5, SEL: 0.5, CONF: -0.3}))
    ledger = InMemoryLedger()
    outcome = run_stages([favourite("f", 0)], windows, ledger, P1, campaign_id="n")
    assert outcome["status"] == "none_confirmed"
    assert outcome["frozen"] == ["f"] and outcome["confirmed"] == []
    assert len(ledger.consumed) == 1 and windows.opened[-1] == "confirmation"


def test_duplicate_formula_ids_are_refused_before_any_window_opens():
    windows = windows_of(edge_in({DISC: 0.5}))
    ledger = InMemoryLedger()
    with pytest.raises(ValueError, match="duplicate formula_id"):
        run_stages([favourite("f", 0), favourite("f", 1)], windows, ledger, P1, campaign_id="d")
    assert windows.opened == () and ledger.consumed == []


def test_discovery_control_passes_and_each_condition_fails_alone():
    rows, carried = discover(edge_in({DISC: 0.3}), favourite("f", 0))
    assert carried == ["f"] and rows["f"]["passes"]

    # t below the gate: every block positive, leg mean positive, plenty of sessions.
    row = discover(edge_in({DISC: 0.006}), favourite("f", 0))[0]["f"]
    positive = sum(m > 0 for m in row["block_means"])
    assert 0 < row["edge"]["t"] < PROTOCOL.discovery_gate.min_t
    assert positive == 4 and row["leg"]["mean"] > 0 and row["edge"]["n_sessions"] >= 400
    assert not row["passes"]

    # Two positive blocks only, but the pooled t is still above the gate.
    steps = {slice(BLOCKS[0], BLOCKS[2]): 0.6, slice(BLOCKS[2], BLOCKS[4]): -0.2}
    row = discover(edge_in(steps), favourite("f", 0))[0]["f"]
    assert sum(m > 0 for m in row["block_means"]) == 2 < PROTOCOL.discovery_gate.min_positive_blocks
    assert row["edge"]["t"] >= PROTOCOL.discovery_gate.min_t and row["leg"]["mean"] > 0
    assert not row["passes"]

    # Positive paired edge, but the picked leg itself loses money against very negative controls.
    r = returns()
    r[:, 1:] -= 1.0
    r[:, 0] -= 0.2
    row = discover(cube_of(r), favourite("f", 0))[0]["f"]
    assert row["edge"]["t"] >= PROTOCOL.discovery_gate.min_t and row["leg"]["mean"] < 0
    assert sum(m > 0 for m in row["block_means"]) == 4 and not row["passes"]

    # Too few sessions with a pick: everything else is strong.
    sparse = favourite("f", 0, rows=lambda view: np.arange(len(view.sessions)) % 4 == 0)
    row = discover(edge_in({DISC: 0.3}), sparse)[0]["f"]
    assert row["edge"]["n_sessions"] < PROTOCOL.discovery_gate.min_sessions
    assert row["edge"]["t"] >= PROTOCOL.discovery_gate.min_t and sum(m > 0 for m in row["block_means"]) == 4
    assert not row["passes"]


def test_purging_drops_picks_whose_hold_runs_past_the_window_or_block_end():
    hold = 6
    picks = list(range(0, DISC.stop, hold))  # a held name is picked again only after its exit
    r = returns()
    r[DISC, 0] += 0.5
    doomed = next(i for i in picks if BLOCKS[1] - hold < i < BLOCKS[1])  # exits inside block 1
    r[doomed, 0] = -60.0
    row = discover(cube_of(r, holding=hold), favourite("f", 0))[0]["f"]
    usable = [i for i in picks if i + hold - 1 <= DISC.stop - 1]
    assert len(usable) == len(picks) - 1  # the final pick is purged at the window end
    assert row["edge"]["n_sessions"] == len(usable)
    assert row["block_means"][0] > 0  # the pick crossing the block end is purged, not averaged in


def test_selection_purges_a_pick_whose_hold_crosses_into_the_confirmation_window():
    hold = 3
    sessions = SEL.stop - SEL.start
    picks = list(range(0, sessions, hold))  # a held name is picked again only after its exit
    crossing = [i for i in picks if i + hold - 1 > sessions - 1]
    assert len(crossing) == 1  # its label resolves inside the confirmation window
    r = returns()
    r[DISC, 0] += 0.5
    r[SEL, 0] += 0.5
    r[SEL.start + crossing[0], 0] = -200.0  # averaged in, it would turn the selection edge negative
    outcome = run_stages(
        [favourite("f", 0)], windows_of(cube_of(r, holding=hold)), InMemoryLedger(), P1, campaign_id="p"
    )
    (row,) = outcome["selection"]
    assert row["edge"]["n_sessions"] == len(picks) - 1 >= PROTOCOL.selection_gate.min_sessions
    assert row["edge"]["mean"] > 0.25 and row["kept"]
    assert outcome["frozen"] == ["f"]


def test_confirmation_keeps_picks_whose_hold_runs_past_the_window_end():
    hold = 6
    r = returns()
    r[CONF, 0] += 0.5
    rows, _ = confirm(cube_of(r, holding=hold), [favourite("f", 0)], ["f"])
    assert rows["f"]["edge"]["n_sessions"] == len(range(0, CONF.stop - CONF.start, hold))


def confirmation_cube(seed: int = 1, before: float = 0.5, recent: float = 0.5, sparse_recent=None) -> LabelCube:
    r = returns(seed)
    recent_start = bisect_right(DAYS, date(2025, 7, 31))
    r[CONF.start : recent_start, 0] += before
    r[recent_start : CONF.stop, 0] += recent
    return cube_of(r)


def test_confirmation_control_passes_every_condition():
    rows, confirmed = confirm(confirmation_cube(), [favourite("f", 0)], ["f"])
    assert confirmed == ["f"] and rows["f"]["passes"]
    assert rows["f"]["holm_p"] == rows["f"]["edge"]["p_one_sided"]  # one hypothesis: no adjustment


def test_holm_adjustment_can_reject_a_candidate_whose_raw_p_passes():
    cube = confirmation_cube(before=0.025, recent=0.025)
    both = [favourite("a", 0), favourite("b", 0)]
    alone, _ = confirm(cube, both, ["a"])
    together, confirmed = confirm(cube, both, ["a", "b"])
    alpha = PROTOCOL.confirmation_gate.alpha
    raw = together["a"]["edge"]["p_one_sided"]
    assert raw <= alpha < together["a"]["holm_p"] and together["a"]["holm_p"] == pytest.approx(2 * raw)
    assert alone["a"]["passes"] and confirmed == []
    for name in ("a", "b"):  # every other condition holds, so Holm alone decides
        row = together[name]
        assert row["leg"]["ci90"][0] > 0 and row["trimmed_mean"] > 0 and row["recent_edge"] > 0


def test_a_leg_whose_confidence_interval_includes_zero_is_not_confirmed():
    r = returns(seed=2, fav_sd=1.0)
    r[:, 1:] -= 1.0
    rows, confirmed = confirm(cube_of(r), [favourite("f", 0)], ["f"])
    row = rows["f"]
    assert row["edge"]["p_one_sided"] < 0.001 and row["leg"]["ci90"][0] <= 0
    assert row["trimmed_mean"] > 0 and row["recent_edge"] > 0
    assert confirmed == []


def test_an_edge_carried_by_a_few_outliers_fails_the_trimmed_mean():
    r = returns()
    r[CONF, 0] -= 0.1
    trim = int(PROTOCOL.confirmation_gate.trim_fraction * (CONF.stop - CONF.start))
    spots = list(range(CONF.start + 50, CONF.stop, (CONF.stop - CONF.start - 60) // (trim - 2)))[:trim]
    spots[-2:] = [CONF.stop - 30, CONF.stop - 10]  # two of them inside the last twelve months
    r[spots, 0] += 200.0
    row = confirm(cube_of(r), [favourite("f", 0)], ["f"])[0]["f"]
    assert row["edge"]["p_one_sided"] < PROTOCOL.confirmation_gate.alpha
    assert row["leg"]["ci90"][0] > 0 and row["recent_edge"] > 0
    assert row["trimmed_mean"] < 0 and not row["passes"]


def test_a_negative_last_twelve_months_fails_the_recent_edge():
    row = confirm(confirmation_cube(before=0.5, recent=-0.2), [favourite("f", 0)], ["f"])[0]["f"]
    assert row["edge"]["p_one_sided"] < PROTOCOL.confirmation_gate.alpha
    assert row["leg"]["ci90"][0] > 0 and row["trimmed_mean"] > 0
    assert row["recent_edge"] < 0 and not row["passes"]


def test_too_few_confirmation_sessions_are_not_confirmed():
    sparse = favourite("f", 0, rows=lambda view: np.arange(len(view.sessions)) % 3 == 0)
    row = confirm(confirmation_cube(), [sparse], ["f"])[0]["f"]
    assert row["edge"]["n_sessions"] < PROTOCOL.confirmation_gate.min_sessions
    assert row["edge"]["p_one_sided"] < 0.001 and row["leg"]["ci90"][0] > 0
    assert row["trimmed_mean"] > 0 and row["recent_edge"] > 0 and not row["passes"]


def test_the_carry_cap_keeps_the_highest_fitness_disjoint_formulas_in_order():
    favourites = tuple(range(7))
    r = returns(favourites=favourites)
    for column in favourites:
        r[DISC, column] += 0.3 + 0.1 * column
    cube = cube_of(r)
    formulas = [favourite(f"f{c}", c) for c in favourites]
    rows, carried = discover(cube, *formulas)
    assert all(row["passes"] for row in rows.values())
    assert len(rows) > PROTOCOL.discovery_gate.carry == len(carried)
    assert carried == ["f6", "f5", "f4", "f3", "f2"]
    assert carried == sorted(carried, key=lambda i: -rows[i]["fitness"])


def test_dedupe_keeps_the_higher_fitness_duplicate():
    cube = edge_in({DISC: 0.5})
    # Identical picks and t: the smaller formula wins on the complexity penalty, not on id order.
    rows, carried = discover(cube, favourite("a", 0, nodes=10), favourite("b", 0, nodes=3))
    assert carried == ["b"] and rows["b"]["fitness"] > rows["a"]["fitness"]
    # Mostly-overlapping picks (Jaccard 0.6 >= 0.5): the stronger formula survives whatever the input order.
    r = returns(favourites=(0, 1))
    r[DISC, 0] += 0.5
    r[DISC, 1] += 0.2
    mixed = favourite("y", 0)
    mixed_panel = mixed.panel

    def panel(view):
        scores, allowed = mixed_panel(view)
        late = np.arange(len(view.sessions)) % 4 == 3
        scores[late, 0], scores[late, 1] = np.nan, 1.0
        return scores, allowed

    weaker = ScoredFormula("y", 3, panel)
    for order in ((weaker, favourite("x", 0)), (favourite("x", 0), weaker)):
        rows, carried = discover(cube_of(r), *order)
        assert carried == ["x"] and rows["x"]["fitness"] > rows["y"]["fitness"]


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 7, 31), 12, date(2025, 7, 31)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 2, 29)),
        (date(2026, 1, 15), 12, date(2025, 1, 15)),
        (date(2026, 1, 31), 1, date(2025, 12, 31)),
    ],
)
def test_months_before_clamps_only_to_the_target_month_length(day, months, expected):
    assert _months_before(day, months) == expected


def pin_cube() -> LabelCube:
    days = tuple(sessions_between(PROTOCOL.windows.discovery[0], PROTOCOL.windows.confirmation[1]))
    rng = np.random.default_rng(42)
    s, n = len(days), 24
    eligible = rng.random((s, n)) > 0.1
    labelled = eligible & (rng.random((s, n)) > 0.05)
    r = rng.normal(0.02, 1.1, (s, n))
    r[:, 3] += 2.0
    r[:, 7] += 1.0
    r = np.where(labelled, r, np.nan)
    arrays = {
        "eligible": eligible,
        "labelled": labelled,
        "r_gross": r + 0.01,
        "r_cost": r,
        "holding": rng.integers(1, 3, (s, n)).astype(np.int16),
        "hit": np.ones((s, n), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        "dollar_volume": np.ones((s, n)),
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256="c" * 64,
        sessions=days,
        symbols=tuple(f"S{j}" for j in range(n)),
        arrays=arrays,
        coverage={},
    )


def pin_formulas(n_names: int = 24) -> list[ScoredFormula]:
    formulas = []
    for column in (3, 7, 11):

        def panel(view, column=column):
            scores = np.zeros(view.eligible.shape)
            scores[:, column] = 1.0
            return scores, np.ones_like(view.eligible)

        formulas.append(ScoredFormula(formula_id=f"fav-{column}", nodes=3 + column % 4, panel=panel))
    for seed in range(5):

        def panel(view, seed=seed):
            field = np.random.default_rng(seed).normal(size=(view.offset + len(view.sessions), n_names))
            return field[view.offset :], np.ones_like(view.eligible)

        formulas.append(ScoredFormula(formula_id=f"noise-{seed}", nodes=4, panel=panel))
    return formulas


def _six_figures(value):
    """Floats to 6 significant figures: platforms differ in the last bits of summed floats."""
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, dict):
        return {key: _six_figures(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_six_figures(item) for item in value]
    return value


def test_run_stages_output_is_unchanged_by_the_discovery_split():
    # Power check A's machinery must not change. The exact digest of this output was recorded
    # on the unsplit stages (378ab95) and matched after the split on the same machine; this
    # pin hashes 6-significant-figure values so it is identical on every platform.
    cube = pin_cube()
    outcome = run_stages(
        pin_formulas(), CampaignWindows(cube, PROTOCOL.windows, "c" * 64), InMemoryLedger(), PROTOCOL, campaign_id="pin"
    )
    encoded = json.dumps(_six_figures(_finite_json(outcome)), sort_keys=True, default=str)
    assert outcome["status"] == "confirmed" and outcome["confirmed"] == ["fav-3"]
    assert hashlib.sha256(encoded.encode()).hexdigest() == (
        "d2560f0a51efe09865501d843698c6e028e7a4505b5d546ac4da4777d7c11c29"
    )


def test_scoring_one_formula_at_a_time_matches_the_batch_discovery():
    view = CampaignWindows(pin_cube(), PROTOCOL.windows, "c" * 64).discovery()
    formulas = pin_formulas()
    evaluator = DiscoveryEvaluator(view, PROTOCOL)
    rows, carried = finish_discovery([evaluator.score(f) for f in reversed(formulas)], PROTOCOL)
    batch_rows, batch_carried = _discovery(formulas, view, PROTOCOL)
    assert carried == batch_carried

    def encoded(items):
        return sorted(json.dumps(_finite_json(row), sort_keys=True) for row in items)

    assert encoded(rows) == encoded(batch_rows)


def test_dedupe_runs_before_the_carry_cap():
    favourites = tuple(range(7))
    r = returns(favourites=favourites)
    for column in favourites:
        r[DISC, column] += 0.3 + 0.1 * column
    # twin6 repeats f6's picks with one more node (lower fitness): it is a duplicate. Capping
    # before deduplicating would carry only four distinct formulas.
    rows, carried = discover(cube_of(r), *[favourite(f"f{c}", c) for c in favourites], favourite("twin6", 6, nodes=4))
    assert rows["twin6"]["passes"]
    assert carried == ["f6", "f5", "f4", "f3", "f2"]


def test_the_recent_window_starts_strictly_after_the_twelve_month_boundary():
    boundary = bisect_left(DAYS, date(2025, 7, 31))
    assert DAYS[boundary] == date(2025, 7, 31)
    r = returns()
    r[CONF, 0] += 0.5
    r[boundary, 0] -= 1000.0  # the boundary session itself is outside the recent window
    assert confirm(cube_of(r), [favourite("f", 0)], ["f"])[0]["f"]["recent_edge"] > 0
    r[boundary, 0] += 1000.0
    r[boundary + 1, 0] -= 1000.0  # the next session is inside it
    assert confirm(cube_of(r), [favourite("f", 0)], ["f"])[0]["f"]["recent_edge"] < 0


def test_campaign_windows_refuse_a_cube_built_for_another_cohort():
    with pytest.raises(ValueError, match="built for cohort"):
        CampaignWindows(synthetic_cube(), PROTOCOL.windows, cohort_sha256="d" * 64)


def test_the_in_memory_ledger_refuses_an_overlap_whatever_the_cohort():
    ledger = InMemoryLedger()
    ledger.consume_confirmation(
        cohort_sha256="a" * 64, interval=(date(2024, 1, 2), date(2026, 7, 31)), campaign_id="one", candidates=("x",)
    )
    with pytest.raises(ValueError, match="already consumed by campaign one"):
        ledger.consume_confirmation(
            cohort_sha256="b" * 64, interval=(date(2026, 7, 1), date(2027, 7, 1)), campaign_id="two", candidates=()
        )
    ledger.consume_confirmation(
        cohort_sha256="b" * 64, interval=(date(2026, 8, 3), date(2027, 7, 30)), campaign_id="three", candidates=()
    )


def test_later_stages_call_on_frozen_before_the_confirmation_is_consumed():
    cube = edge_in({DISC: 0.5, SEL: 0.5, CONF: 0.5})
    windows = windows_of(cube)
    events = []

    class Spy(InMemoryLedger):
        def consume_confirmation(self, **kwargs):
            events.append(("consume", kwargs["candidates"]))
            super().consume_confirmation(**kwargs)

    formulas = [favourite("f", 0)]
    discovery, carried = _discovery(formulas, windows.discovery(), P1)
    outcome = later_stages(
        formulas,
        discovery,
        carried,
        windows,
        Spy(),
        P1,
        campaign_id="hook",
        on_frozen=lambda frozen: events.append(("frozen", tuple(frozen))),
    )
    assert outcome["status"] == "confirmed"
    assert events == [("frozen", ("f",)), ("consume", ("f",))]


def test_top_fraction_confirmation_rows_report_a_descriptive_top3_edge_that_never_gates():
    v3 = P1.model_copy(update={"k": None, "selection": Selection(rule="top_fraction", fraction=0.10)})
    cube = confirmation_cube()
    windows = windows_of(cube)
    rows, confirmed = _confirmation(
        [favourite("f", 0)], ["f"], windows.confirmation(InMemoryLedger(), campaign_id="x", candidates=()), v3
    )
    row = rows[0]
    assert set(row["top3_edge"]) >= {"mean", "n_sessions", "p_one_sided"}
    assert row["passes"] and confirmed == ["f"]


def test_top_k_confirmation_rows_have_no_descriptive_top3_edge():
    rows, _ = confirm(confirmation_cube(), [favourite("f", 0)], ["f"])
    assert "top3_edge" not in rows["f"]
