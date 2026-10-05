# tests/research/pooled/test_search_power.py
import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled import search_power
from agentic_trader.research.pooled.campaign import LoadedProtocol, _calls
from agentic_trader.research.pooled.search_power import execute_search_power, hidden_expression, run_search_power
from tests.research.pooled.mini_world import COHORT, book, cube_build, label_cube, mini_protocol, mini_v3_protocol


def exact_seed(family, *, protocol, rng, book, view):
    """A hidden expression equal to the family's first seed: the search must recover it."""
    expression = canonical_expression(family.seeds[0])
    return expression, protocol.select(*book.formula(expression).panel(view), view)


def with_delta(protocol, delta):
    return protocol.model_copy(update={"power_search": protocol.power_search.model_copy(update={"delta": delta})})


def test_hidden_expressions_are_reproducible_near_seed_mutations_with_enough_sessions():
    protocol = mini_protocol()
    family = protocol.families[0]
    view = label_cube().window(*protocol.windows.discovery)
    expression, picks = hidden_expression(family, protocol=protocol, rng=random.Random(5), book=book(), view=view)
    assert expression not in {canonical_expression(seed) for seed in family.seeds}
    assert _calls(expression) <= {"roc", *family.mutation_operators}
    assert np.unique(picks.session_idx).size >= protocol.discovery_gate.min_sessions
    again, _ = hidden_expression(family, protocol=protocol, rng=random.Random(5), book=book(), view=view)
    assert again == expression


def test_hidden_expressions_pick_the_v3_decile_without_hold_skipping():
    protocol = mini_v3_protocol()
    view = label_cube().window(*protocol.windows.discovery)
    expression, picks = hidden_expression(
        protocol.families[0], protocol=protocol, rng=random.Random(5), book=book(), view=view
    )
    expected = protocol.select(*book().formula(expression).panel(view), view)
    assert np.array_equal(picks.codes(len(view.symbols)), expected.codes(len(view.symbols)))
    per_session = np.bincount(picks.session_idx)[np.unique(picks.session_idx)]
    assert set(per_session.tolist()) == {2}  # ceil(0.10 * 12), never top-3
    assert any(  # a name held across consecutive sessions is still picked again: no hold-skipping
        set(picks.symbol_idx[picks.session_idx == i]) & set(picks.symbol_idx[picks.session_idx == i + 1])
        for i in np.unique(picks.session_idx)[:-1]
    )


def test_a_planted_seed_is_recovered_and_a_vanishing_plant_is_not(monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    found = run_search_power(with_delta(mini_protocol(), 2.0), label_cube(), book())
    assert found["status"] == "passed" and found["recovered"] == 3
    assert found["errors_total"] == 0 == sum(s["errors"] for s in found["seeds"])
    assert [s["family"] for s in found["seeds"]] == ["reversal", "range_location", "abnormal_volume"]
    assert all(s["best_passing_jaccard"] == 1.0 for s in found["seeds"])
    missed = run_search_power(with_delta(mini_protocol(), 1e-9), label_cube(), book())
    assert missed["status"] == "gate_failed" and missed["recovered"] == 0


def test_search_power_is_reproducible():
    protocol = mini_protocol()
    assert run_search_power(protocol, label_cube(), book()) == run_search_power(protocol, label_cube(), book())


def test_a_protocol_without_a_search_power_seed_is_refused():
    protocol = mini_protocol()
    v1_like = protocol.model_copy(update={"power_search": protocol.power_search.model_copy(update={"seed": None})})
    with pytest.raises(ValueError, match="power_search.seed"):
        run_search_power(v1_like, label_cube(), book())


def _loaded(protocol):
    return LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("campaign.json"))


def _power(cube_sha256, **override):
    return {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": cube_sha256,
        **override,
    }


def test_execute_search_power_writes_its_manifest_first_and_binds_the_cube(tmp_path, monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    cube = label_cube()
    seen = {}

    async def build():
        seen["manifest"] = (tmp_path / "out" / "manifest.json").exists()
        return cube_build(cube)

    result = asyncio.run(
        execute_search_power(
            _loaded(with_delta(mini_protocol(), 2.0)),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result=_power(cube.sha256),
            environment={"runtime": {"revision": "abc1234"}},
        )
    )
    assert seen == {"manifest": True}
    assert result["status"] == "passed" and result["cube_sha256"] == cube.sha256
    assert result["campaign_protocol_sha256"] == "p" * 64 and result["authorizes_promotion"] is False
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["check"] == "search_power"
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["recovered"] == 3


def test_execute_search_power_refuses_a_failed_power_check_or_another_cube(tmp_path):
    cube = label_cube()
    built = []

    async def build():
        built.append(1)
        return cube_build(cube)

    def run(out, power_result):
        return asyncio.run(
            execute_search_power(
                _loaded(mini_protocol()),
                tmp_path / out,
                cohort=SimpleNamespace(sha256=COHORT),
                build=build,
                power_result=power_result,
                environment={},
            )
        )

    failed = run("one", _power(cube.sha256, status="gate_failed"))
    assert failed["status"] == "failed" and "has not passed" in failed["error"] and built == []
    other = run("two", _power("x" * 64))
    assert other["status"] == "failed" and "different cube" in other["error"]


def test_a_planted_decile_seed_is_recovered_under_v3(monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    found = run_search_power(with_delta(mini_v3_protocol(), 2.0), label_cube(), book())
    assert found["status"] == "passed" and found["recovered"] == 3
    assert all(s["best_passing_jaccard"] == 1.0 for s in found["seeds"])
