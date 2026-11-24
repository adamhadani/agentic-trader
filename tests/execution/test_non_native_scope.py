"""One non-native predicate: equal to the release exit's scope, config-bounded, and free of ``agent`` imports."""

import ast
import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_trader.agent.catalog_sources import PeadDriftSource
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.insider_watch import InsiderWatchSource
from agentic_trader.config import PortfolioConfig
from agentic_trader.execution import scope as scope_module
from agentic_trader.execution.release_exit import NATIVE_SCOPE
from agentic_trader.execution.scope import is_non_native
from agentic_trader.research.alpha.probe import PAPER_PROBE_TAG
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID


POLICY = {"kind": "apriori_bracket_v1"}

ROWS = {
    "native": {"strategy": "TREND_PULLBACK", "alpha_version": None, "alpha_policy": None, "decision_provenance": None},
    "native with an empty policy": {
        "strategy": "TREND_PULLBACK",
        "alpha_version": None,
        "alpha_policy": {},
        "decision_provenance": {PAPER_PROBE_TAG: False},
    },
    "native with other provenance": {
        "strategy": "BREAKOUT",
        "alpha_version": None,
        "alpha_policy": None,
        "decision_provenance": {"regime": {}},
    },
    "pead": {
        "strategy": PEAD_STRATEGY_ID,
        "alpha_version": "apriori:pead:v2:long:abc",
        "alpha_policy": POLICY,
        "decision_provenance": {"pead_event": {}},
    },
    "pead by strategy alone": {
        "strategy": PEAD_STRATEGY_ID,
        "alpha_version": None,
        "alpha_policy": None,
        "decision_provenance": None,
    },
    "watch": {
        "strategy": INSIDER_WATCH_STRATEGY_ID,
        "alpha_version": None,
        "alpha_policy": POLICY,
        "decision_provenance": {"insider_watch": {}},
    },
    "watch by strategy alone": {
        "strategy": INSIDER_WATCH_STRATEGY_ID,
        "alpha_version": None,
        "alpha_policy": None,
        "decision_provenance": None,
    },
    "probe": {
        "strategy": "TREND_PULLBACK",
        "alpha_version": None,
        "alpha_policy": None,
        "decision_provenance": {PAPER_PROBE_TAG: True},
    },
    "alpha by version": {
        "strategy": "alpha_x",
        "alpha_version": "alpha_x:abc",
        "alpha_policy": None,
        "decision_provenance": None,
    },
    "alpha by policy": {
        "strategy": "alpha_x",
        "alpha_version": None,
        "alpha_policy": POLICY,
        "decision_provenance": None,
    },
}


def _predicate(row):
    return is_non_native(
        strategy=row["strategy"],
        alpha_version=row["alpha_version"],
        alpha_policy=row["alpha_policy"],
        provenance=row["decision_provenance"],
    )


@pytest.mark.parametrize("name", list(ROWS))
def test_the_predicate_equals_the_release_exit_scope_being_exempt(name):
    stand_in = SimpleNamespace(catalog_sources=[PeadDriftSource(), InsiderWatchSource.__new__(InsiderWatchSource)])
    scope = TradingCopilot._release_exit_scope(stand_in, ROWS[name])
    assert _predicate(ROWS[name]) is (scope != NATIVE_SCOPE)


def test_the_parity_rows_cover_every_scope():
    stand_in = SimpleNamespace(catalog_sources=[PeadDriftSource(), InsiderWatchSource.__new__(InsiderWatchSource)])
    scopes = {TradingCopilot._release_exit_scope(stand_in, row) for row in ROWS.values()}
    assert scopes == {NATIVE_SCOPE, "pead", "insider_watch", "probe", "alpha"}


def test_the_predicate_imports_no_agent_module():
    tree = ast.parse(inspect.getsource(scope_module))
    modules = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        for alias in (node.names if isinstance(node, ast.Import) else [None])
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    assert not [m for m in modules if m and m.startswith("agentic_trader.agent")]


def test_a_non_mapping_provenance_is_native():
    assert not is_non_native(strategy="X", alpha_version=None, alpha_policy=None, provenance=["paper_probe"])  # type: ignore[arg-type]


def test_the_cap_defaults_to_off():
    assert PortfolioConfig().max_non_native_positions is None


@pytest.mark.parametrize("value", [1, 4])
def test_the_cap_accepts_one_through_the_concurrent_position_limit(value):
    assert PortfolioConfig(max_concurrent_positions=4, max_non_native_positions=value).max_non_native_positions == value


@pytest.mark.parametrize("value", [0, -1, 5])
def test_the_cap_outside_one_through_the_concurrent_position_limit_fails_load(value):
    with pytest.raises(ValidationError):
        PortfolioConfig(max_concurrent_positions=4, max_non_native_positions=value)
