# tests/research/pooled/test_genetic.py
import pytest

from agentic_trader.research.pooled import genetic
from agentic_trader.research.pooled.campaign import CampaignWindows, DiscoveryEvaluator, Family, InMemoryLedger
from agentic_trader.research.pooled.genetic import family_search, run_search_stages, search_campaign
from agentic_trader.research.pooled.scoring import formula_id
from tests.research.pooled.mini_world import (
    COHORT,
    FAMILIES,
    V1,
    RecordingCharger,
    book,
    label_cube,
    mini_protocol,
    planted_cube,
)


def discovery_view(protocol):
    return label_cube().window(*protocol.windows.discovery)


def test_each_family_reserves_its_share_and_charges_every_evaluated_formula_once():
    protocol = mini_protocol()
    charger = RecordingCharger()
    outcome = search_campaign(protocol, discovery_view(protocol), book(), charger)
    assert charger.reserved == [("reversal", 3), ("range_location", 3), ("abnormal_volume", 3)]
    assert [run.charged for run in outcome.runs] == [3, 3, 3]
    expressions = [expression for _, expression, _ in charger.charged]
    assert len(set(expressions)) == len(expressions) == 9
    assert (
        {fid for *_, fid in charger.charged} == set(outcome.formulas) == {s.row["formula_id"] for s in outcome.scores}
    )
    assert all(outcome.families[fid] == family for family, _, fid in charger.charged)
    assert [r["expression"] for r in outcome.records[:2]] == ["-1.0 * roc(close, 5)", "-1.0 * roc(close, 21)"]


def test_the_search_is_deterministic_for_a_fixed_protocol():
    protocol = mini_protocol()
    first = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger())
    second = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger())
    assert first.records == second.records


def test_forbidden_proposals_are_rejected_uncharged_and_unevaluated_until_the_grammar_runs_out():
    # One int-free seed and one forbidden wrapper: every mutation is ts_std(returns, w).
    trapped = Family(id="trapped", rationale="t", seeds=("returns",), mutation_operators=("ts_std",))
    protocol = mini_protocol(
        families=(trapped, FAMILIES[0]),
        formula_budget=6,
        search=V1.search.model_copy(update={"forbidden_operators": ("TS_STD",)}),
    )
    charger = RecordingCharger()
    outcome = search_campaign(protocol, discovery_view(protocol), book(), charger)
    run = outcome.runs[0]
    assert run.charged == 1 and len(run.scores) == 1  # the seed only
    assert run.rejected == {"forbidden operator": 8}  # one per wrapper window, then nothing new
    assert run.stopped_short.startswith("search exhausted")
    assert all("ts_std" not in expression for _, expression, _ in charger.charged)
    assert outcome.runs[1].charged == 3


def test_a_run_of_rejections_stops_the_family(monkeypatch):
    monkeypatch.setattr(genetic, "MAX_CONSECUTIVE_REJECTIONS", 3)
    trapped = Family(id="trapped", rationale="t", seeds=("returns",), mutation_operators=("ts_std",))
    protocol = mini_protocol(
        families=(trapped,), formula_budget=4, search=V1.search.model_copy(update={"forbidden_operators": ("ts_std",)})
    )
    run = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger()).runs[0]
    assert run.stopped_short == "3 consecutive rejected proposals"
    assert (run.charged, sum(run.rejected.values())) == (1, 3)


def test_an_evaluation_error_is_charged_recorded_and_the_search_goes_on():
    protocol = mini_protocol()
    evaluator = DiscoveryEvaluator(discovery_view(protocol), protocol)
    first = formula_id("-1.0 * roc(close, 5)")

    class Exploding:
        def score(self, formula):
            if formula.formula_id == first:
                raise RuntimeError("boom")
            return evaluator.score(formula)

    charger = RecordingCharger()
    run = family_search(
        protocol.families[0], 0, 3, protocol=protocol, evaluator=Exploding(), book=book(), charger=charger, seen=set()
    )
    assert (run.charged, run.errors, len(run.scores)) == (3, 1, 2)
    assert run.records[0] == {
        "family": "reversal",
        "expression": "-1.0 * roc(close, 5)",
        "formula_id": first,
        "status": "error",
        "error": "RuntimeError: boom",
    }
    assert charger.charged[0][2] == first


def test_a_planted_seed_is_carried_frozen_before_consumption_and_confirmed():
    protocol = mini_protocol()
    planted = "-1.0 * roc(close, 21)"
    events = []

    class Spy(InMemoryLedger):
        def consume_confirmation(self, **kwargs):
            events.append(("consume", kwargs["candidates"]))
            super().consume_confirmation(**kwargs)

    windows = CampaignWindows(planted_cube(planted, 1.0), protocol.windows, COHORT)
    outcome, search = run_search_stages(
        protocol,
        windows,
        book(),
        Spy(),
        RecordingCharger(),
        campaign_id="mini",
        on_frozen=lambda frozen, found: events.append(("frozen", tuple(frozen))),
    )
    target = formula_id(planted)
    assert outcome["status"] == "confirmed" and target in outcome["confirmed"]
    assert events[0][0] == "frozen" and target in events[0][1]
    assert events[1] == ("consume", events[0][1])
    assert search.expressions[target] == planted
    assert windows.opened == ("discovery", "selection", "confirmation")


@pytest.mark.parametrize("index", [0, 1])
def test_each_family_search_is_seeded_by_the_protocol_seed_and_its_index(monkeypatch, index):
    seeds = []
    real = genetic.TypedGeneticSearch

    def recording(seed, *args, **kwargs):
        seeds.append(seed)
        return real(seed, *args, **kwargs)

    monkeypatch.setattr(genetic, "TypedGeneticSearch", recording)
    protocol = mini_protocol()
    evaluator = DiscoveryEvaluator(discovery_view(protocol), protocol)
    family_search(
        protocol.families[index],
        index,
        1,
        protocol=protocol,
        evaluator=evaluator,
        book=book(),
        charger=RecordingCharger(),
        seen=set(),
    )
    assert seeds == [protocol.search.seed * 100 + index]


def test_each_family_progress_line_reports_its_evaluation_errors(monkeypatch):
    protocol = mini_protocol()
    real = DiscoveryEvaluator.score
    broken = formula_id("volume / ts_mean(volume, 50)")

    def score(self, formula):
        if formula.formula_id == broken:
            raise FloatingPointError("overflow")
        return real(self, formula)

    monkeypatch.setattr(DiscoveryEvaluator, "score", score)
    lines: list[str] = []
    search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger(), progress=lines.append)
    assert [line.split(":")[0] for line in lines] == [f"family {f.id}" for f in protocol.families]
    assert "0 errors" in lines[0] and "1 errors" in lines[2]
