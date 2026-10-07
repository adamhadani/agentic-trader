import ast
import hashlib

import pytest

from agentic_trader.research.alpha import search as search_module
from agentic_trader.research.alpha.dsl import AlphaDSLSyntaxError
from agentic_trader.research.alpha.search import WINDOWS, TypedGeneticSearch


FAMILY_SEEDS = ("close / ts_max(high, 252)", "close / ts_max(high, 126)")
OPERATORS = ("ts_mean", "ts_rank", "zscore", "delay", "ema")
CONSTANTS = (126, 189, 252)


def _fitness(expression: str) -> float:
    return int(hashlib.sha256(expression.encode()).hexdigest()[:8], 16) % 1000 / 1000


def _drive(search: TypedGeneticSearch, n: int = 80) -> list[str]:
    out = []
    for _ in range(n):
        expression = search.ask()
        out.append(expression)
        search.tell(expression, _fitness(expression))
    return out


def _calls(expression: str) -> set[str]:
    tree = ast.parse(expression, mode="eval")
    return {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _int_constants(expression: str) -> set[int]:
    tree = ast.parse(expression, mode="eval")
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and type(node.value) is int}


def test_the_default_search_proposes_exactly_what_it_did_before_families_existed():
    # Digest recorded on the unmodified search (378ab95): the per-symbol miner must not change.
    proposals = _drive(TypedGeneticSearch(seed=11, archive_size=16))
    digest = hashlib.sha256("\n".join(proposals).encode()).hexdigest()
    assert digest == "5ef01834e977f3b8e2eb5781d74834470adbd96c5320fe562bae6f23fae8cbad"


def test_a_family_search_stays_inside_its_seeds_operators_and_windows():
    search = TypedGeneticSearch(
        seed=3, archive_size=16, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS
    )
    proposals = _drive(search, 120)
    assert proposals[:2] == ["close / ts_max(high, 252)", "close / ts_max(high, 126)"]
    assert all(_calls(expression) <= {"ts_max", *OPERATORS} for expression in proposals)
    assert all(_int_constants(expression) <= set(CONSTANTS) | set(WINDOWS) for expression in proposals)


def test_an_empty_archive_draws_parents_from_the_family_seeds():
    search = TypedGeneticSearch(seed=9, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS)
    proposals = [search.ask() for _ in range(30)]  # nothing is told: the archive stays empty
    assert all(_calls(expression) <= {"ts_max", *OPERATORS} for expression in proposals)


def test_the_mutation_fallback_returns_a_family_seed_never_a_global_one(monkeypatch):
    search = TypedGeneticSearch(seed=5, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS)

    def refuse(expression):
        raise AlphaDSLSyntaxError("refused")

    monkeypatch.setattr(search_module, "canonical_expression", refuse)
    for _ in range(20):
        assert search.mutate("close / ts_max(high, 252)") in FAMILY_SEEDS


def test_a_search_without_operators_proposes_its_seeds_then_stops():
    search = TypedGeneticSearch(seed=1, seeds=("-1.0 * roc(close, 5)",), operators=())
    assert search.ask() == "-1.0 * roc(close, 5)"
    with pytest.raises(ValueError, match="no mutation operators"):
        search.ask()
    assert search.mutation_count == 0


def test_mutating_without_operators_raises_instead_of_choosing_from_nothing():
    search = TypedGeneticSearch(seed=1, seeds=("returns",), operators=())
    with pytest.raises(ValueError, match="no mutation operators"):
        search.mutate("returns")  # no integer window to vary, so only an operator could mutate it
