import json
import math
from pathlib import Path

import pytest

from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.research.alpha.strategy import (
    APRIORI_BRACKET_KIND,
    AlphaExecutionPolicy,
    AprioriBracketPolicy,
    TimedAlphaExecutionPolicy,
    bracket_prices,
    execution_policy_from_dict,
    policy_trails,
    session_entry_policy,
    trailing_price,
)
from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy
from agentic_trader.research.apriori.pead_study import levels_for


ENTRY = load_pead_entry(Path("config/research/apriori/pead-v2.json")).entry
POLICY = pead_execution_policy(ENTRY)


def test_pead_policy_carries_the_frozen_trade_rule():
    assert (POLICY.stop_atr, POLICY.atr_window, POLICY.reward_risk, POLICY.tick_size) == (2.0, 14, 3.0, 0.01)
    assert POLICY.lifetime == SessionLifetimePolicy(resting_seconds=57_600, holding_sessions=20, close_time_et="15:45")
    assert POLICY.to_dict()["kind"] == APRIORI_BRACKET_KIND


@pytest.mark.parametrize("entry,atr", [(100.0, 1.2345), (37.21, 0.5), (412.07, 9.87), (10.0, 0.013)])
def test_levels_equal_the_study_rounded_outward_and_ignore_swings(entry, atr):
    stop, target = bracket_prices(entry, 1, atr, swing_low=1.0, swing_high=1e6, policy=POLICY)
    study = levels_for("LONG", entry, atr, ENTRY)
    assert stop <= study.stop and stop > study.stop - 0.01 - 1e-9
    risk = entry - stop
    assert math.isclose(target - entry, math.ceil(round(risk / 0.01) * 3.0 - 1e-10) * 0.01, abs_tol=1e-8)
    assert (target - entry) / risk >= 3.0 - 1e-9


def test_policy_round_trips_and_never_trails():
    document = json.loads(json.dumps(POLICY.to_dict()))
    assert execution_policy_from_dict(document) == POLICY
    assert trailing_price(100.0, 150.0, 95.0, 5.0, 1, POLICY) == 95.0
    assert policy_trails(document) is False
    assert policy_trails(AlphaExecutionPolicy().to_dict()) is True and policy_trails(None) is True


def test_existing_documents_are_byte_identical():
    plain, timed = AlphaExecutionPolicy(), session_entry_policy()
    assert "kind" not in plain.to_dict() and "kind" not in timed.to_dict()
    assert execution_policy_from_dict(plain.to_dict()) == plain
    assert isinstance(execution_policy_from_dict(timed.to_dict()), TimedAlphaExecutionPolicy)


def test_unknown_policy_kind_is_refused():
    with pytest.raises(ValueError, match="Unsupported execution policy kind"):
        execution_policy_from_dict({**POLICY.to_dict(), "kind": "apriori_bracket_v9"})


@pytest.mark.parametrize(
    "field,value", [("stop_atr", 0.0), ("reward_risk", float("nan")), ("atr_window", 1), ("kind", "x")]
)
def test_invalid_apriori_policies_are_refused(field, value):
    fields = {"stop_atr": 2.0, "atr_window": 14, "reward_risk": 3.0, "lifetime": POLICY.lifetime, field: value}
    with pytest.raises(ValueError):
        AprioriBracketPolicy(**fields)
