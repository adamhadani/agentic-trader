import hashlib

import pytest

from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION, AlphaDefinition
from agentic_trader.research.alpha.strategy import session_entry_policy
from agentic_trader.research.alpha.study import MarketScenario, market_bars, study_catalog
from agentic_trader.research.alpha.validation import ValidationPolicy


# Pinned against commit 261bad3 (the last commit before AlphaMiner.mine() gained the
# `execution` keyword), for method="random" only: generated candidates depend only on the
# seeded RNG, so this digest is platform-stable and detects a regression in shared trial
# code that a post-change-vs-post-change comparison cannot. Genetic search proposals depend
# on floating-point fitness feedback and are intentionally NOT pinned (see
# test_explicit_none_matches_omitted_execution below for that path's consistency check).
# Regenerate only if the study catalog or generated-candidate search legitimately changes:
#   git worktree add --detach <tmp-dir> 261bad3 && cd <tmp-dir> && uv sync && \
#     uv run python <script computing the same trial_count/digest as below> && \
#     cd - && git worktree remove --force <tmp-dir>
PRE_CHANGE_RANDOM_TRIAL_COUNT = 11
PRE_CHANGE_RANDOM_TRIAL_DIGEST = "500e25ffef89e0dabaafc577ed1d7d2c9243b4b587d1c3c8166dfa74bd4a4212"


@pytest.fixture(scope="module")
def frame():
    scenario = MarketScenario(name="dense", observations=900, interval=8, effect=0.004, volatility_persistence=0.0)
    return market_bars(scenario, seed=11)


def mine(frame, method, **kwargs):
    miner = AlphaMiner(seed=5, catalog=study_catalog(), policy=ValidationPolicy())
    miner.mine(
        frame,
        iterations=3,
        symbol="SYNTH",
        method=method,
        min_sharpe=float("-inf"),
        min_dsr=0,
        min_ic=float("-inf"),
        max_seconds=60,
        **kwargs,
    )
    return miner.last_run


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_every_trial_is_evaluated_under_the_requested_policy(frame, method):
    run = mine(frame, method, execution=session_entry_policy())
    definitions = [t["definition"] for t in run["trials"] if t.get("definition")]
    assert definitions
    assert {d["semantics_version"] for d in definitions} == {DAILY_SESSION_SEMANTICS_VERSION}
    assert {d["execution"]["lifetime"]["resting_seconds"] for d in definitions} == {57_600}
    assert {d["execution"]["lifetime"]["holding_seconds"] for d in definitions} == {None}
    assert any(d["alpha_id"] == "synthetic_pulse" for d in definitions)
    evaluated = [t for t in run["trials"] if t.get("candidate")]
    assert evaluated
    assert {t["candidate"]["evidence"]["execution_model"] for t in evaluated} == {
        "session_limit_conservative_brackets_v1"
    }

    default_run = mine(frame, method)
    default_evaluated = [t for t in default_run["trials"] if t.get("candidate")]
    assert default_evaluated
    assert {t["candidate"]["evidence"]["execution_model"] for t in default_evaluated} == {
        "gtc_limit_conservative_brackets_v2"
    }


def _ids(run):
    return [
        t["definition"]["version_id"] if "version_id" in t["definition"] else t["definition"] for t in run["trials"]
    ]


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_explicit_none_matches_omitted_execution(frame, method):
    baseline, again = mine(frame, method), mine(frame, method, execution=None)
    assert _ids(baseline) == _ids(again)
    assert all("lifetime" not in t["definition"]["execution"] for t in baseline["trials"] if t.get("definition"))


def test_default_random_mining_matches_the_pre_change_implementation(frame):
    run = mine(frame, "random")
    version_ids = [
        t["definition"]["version_id"]
        if "version_id" in t["definition"]
        else AlphaDefinition.from_dict(t["definition"]).version_id
        for t in run["trials"]
    ]
    assert len(version_ids) == PRE_CHANGE_RANDOM_TRIAL_COUNT
    digest = hashlib.sha256("\n".join(version_ids).encode()).hexdigest()
    assert digest == PRE_CHANGE_RANDOM_TRIAL_DIGEST


def test_the_policy_changes_which_trades_happen(frame):
    gtc, session = mine(frame, "random"), mine(frame, "random", execution=session_entry_policy())
    known = lambda run: next(t for t in run["trials"] if t["definition"]["alpha_id"] == "synthetic_pulse")  # noqa: E731
    assert known(gtc)["candidate"]["definition"]["execution"] != known(session)["candidate"]["definition"]["execution"]


def test_intraday_mining_rejects_the_daily_policy(frame):
    intraday = frame.copy()
    intraday.attrs["timeframe"] = "4h"
    miner = AlphaMiner(seed=5, catalog=study_catalog(), policy=ValidationPolicy())
    with pytest.raises(ValueError, match="native daily"):
        miner.mine(intraday, iterations=1, timeframe="4h", execution=session_entry_policy())


def test_session_bounded_mining_rejects_a_naive_index_before_charging_any_trial(frame):
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    naive.attrs.update(frame.attrs)
    miner = AlphaMiner(seed=5, catalog=study_catalog(), policy=ValidationPolicy())
    with pytest.raises(ValueError, match="timezone-aware bar index"):
        miner.mine(naive, iterations=3, symbol="SYNTH", execution=session_entry_policy(), max_seconds=60)
    assert miner.last_run == {}
