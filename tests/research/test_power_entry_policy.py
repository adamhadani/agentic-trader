import json

import pytest

from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION
from agentic_trader.research.alpha.power_study import PowerProtocol, power_jobs, power_seeds, select_power_candidates
from agentic_trader.research.alpha.study import market_bars


SMALL = {
    "observations": 900,
    "development_replicates": 1,
    "null_replicates": 1,
    "edge_replicates": 1,
    "generated_candidates": 2,
}

# Pinned on commit 6e7ecdb3eb77f97d86c749474231b21c2769c1e7 (before this change), via:
#   uv run python -c "from agentic_trader.research.alpha.power_study import PowerProtocol; \
#       print(PowerProtocol(seed=7, observations=900, development_replicates=1, \
#       null_replicates=1, edge_replicates=1, generated_candidates=2).identity)"
PINNED_GTC_PROTOCOL_IDENTITY = "1d8b6edf3fe13d532468a87e14ae074f56317a06db32906f711d9fcb7aec13a0"


def test_gtc_protocol_document_and_identity_are_unchanged():
    protocol = PowerProtocol(seed=7, **SMALL)
    document = protocol.document()
    assert "entry_policy" not in document
    assert PowerProtocol.from_document(json.loads(json.dumps(document))) == protocol
    # Pinned on the commit before this change; an added serialized field would move it.
    assert protocol.identity == PINNED_GTC_PROTOCOL_IDENTITY


def test_session_protocol_has_a_distinct_identity_and_round_trips():
    gtc, session = PowerProtocol(seed=7, **SMALL), PowerProtocol(seed=7, entry_policy="session", **SMALL)
    assert session.document()["entry_policy"] == "session"
    assert session.identity != gtc.identity
    assert PowerProtocol.from_document(json.loads(json.dumps(session.document()))) == session


@pytest.mark.parametrize(("entry_policy", "timed"), [("gtc", False), ("session", True)])
def test_selection_mines_the_known_control_and_winner_under_the_declared_policy(entry_policy, timed):
    protocol = PowerProtocol(seed=7, entry_policy=entry_policy, **SMALL)
    job = next(job for job in power_jobs(protocol) if job.effect > 0)
    seeds = power_seeds(job, protocol)
    bars = market_bars(job.scenario, seed=seeds["data"])
    selection = select_power_candidates(bars, protocol, search_seed=seeds[f"search/{job.method}"], method=job.method)
    for route in ("known", "winner"):
        definition = selection["routes"][route]["definition"]
        assert ("lifetime" in definition["execution"]) is timed
        assert (definition["semantics_version"] == DAILY_SESSION_SEMANTICS_VERSION) is timed
