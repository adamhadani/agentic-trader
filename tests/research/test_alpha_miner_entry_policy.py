import pytest

from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION
from agentic_trader.research.alpha.strategy import session_entry_policy
from agentic_trader.research.alpha.study import MarketScenario, market_bars, study_catalog
from agentic_trader.research.alpha.validation import ValidationPolicy


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


def _ids(run):
    return [
        t["definition"]["version_id"] if "version_id" in t["definition"] else t["definition"] for t in run["trials"]
    ]


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_default_mining_is_unchanged(frame, method):
    baseline, again = mine(frame, method), mine(frame, method, execution=None)
    assert _ids(baseline) == _ids(again)
    assert all("lifetime" not in t["definition"]["execution"] for t in baseline["trials"] if t.get("definition"))


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
