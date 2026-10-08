"""run_scan applies the configured card policy before the LLM, journals it and keeps labelling."""

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from agentic_trader.config import CardPolicyConfig, ScanBudget
from agentic_trader.execution.durable import EventKind, RankedOutcome
from agentic_trader.research.setups.outcomes import label_journaled, summarize
from tests.agent.test_scan_budget import budget_desk, candidate  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import FakeCardStats, key_stats, make_snapshot


QUALITIES = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5}
NEGATIVE = key_stats(
    "TREND_PULLBACK", "LONG", target_rate=0.23, stop_rate=0.77, timeout_rate=0.0, mean_r_cost=-0.39, mean_timeout_r=None
)
REASON = "card policy: measured EV -0.39R over 30 < +0.00R"


@pytest.fixture
def policy_desk(shadow_desk):  # noqa: F811
    """DDD (rank 1) is TREND_PULLBACK, measured at -0.39R over 30; every other name is SQUEEZE_BREAKOUT with 5 mature."""
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(
            data.contract,
            QUALITIES[data.contract],
            strategy="TREND_PULLBACK" if data.contract == "DDD" else "SQUEEZE_BREAKOUT",
        )
    ]
    shadow_desk.card_stats = FakeCardStats(
        make_snapshot(keys=[NEGATIVE, key_stats("SQUEEZE_BREAKOUT", "LONG", n_mature=5, mean_r_cost=0.1)])
    )
    return shadow_desk


async def _ranked(db):
    [event] = [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    return event["payload"], {c["contract"]: c for c in event["payload"]["candidates"]}


def _llm_contracts(desk):
    return [c.args[0].contract for c in desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")]


async def test_off_sends_and_journals_evidence_without_a_verdict(policy_desk, temp_db):
    await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    payload, by_contract = await _ranked(temp_db)
    assert payload["card_policy"] == {
        "mode": "off",
        "min_measured_ev": None,
        "min_mature_cards": 20,
        "snapshot_key": policy_desk.card_stats.snapshot.snapshot_key,
    }
    assert by_contract["DDD"]["card_policy"] == {
        "measured_ev": -0.39,
        "n_mature": 30,
        "would_withhold": None,
        "n_fetch_failed": 0,
    }
    assert by_contract["CCC"]["card_policy"] == {
        "measured_ev": None,
        "n_mature": None,
        "would_withhold": None,
        "n_fetch_failed": 0,
    }


async def test_preview_sends_and_journals_would_withhold(policy_desk, temp_db, app_config, caplog):
    app_config.card_policy = CardPolicyConfig(mode="preview", min_measured_ev=0.0)
    with caplog.at_level(logging.INFO, logger="copilot"):
        await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_policy"] == {
        "measured_ev": -0.39,
        "n_mature": 30,
        "would_withhold": True,
        "n_fetch_failed": 0,
    }
    _, by_contract = await _ranked(temp_db)
    assert by_contract["DDD"]["outcome"] == "sent" and by_contract["DDD"]["card_policy"]["would_withhold"] is True
    assert by_contract["CCC"]["card_policy"]["would_withhold"] is False
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "card_policy_would_withhold"]
    assert record.contract == "DDD" and record.measured_ev == -0.39


async def test_enforce_withholds_before_the_llm_and_falls_through(policy_desk, temp_db, app_config):
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    # One LLM evaluation and one card for the session: rank 2 can only be sent if the withheld
    # rank 1 spent neither.
    app_config.scan.max_llm_evaluations_per_scan = 1
    app_config.scan.max_cards_per_session = 1
    await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["CCC"]
    assert _llm_contracts(policy_desk) == ["CCC"]  # the withheld rank spent no LLM budget
    assert len(await temp_db.signals_since(policy_desk.session_start_et())) == 1  # nor card budget
    reasons = {r["contract"]: r["reason"] for r in policy_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == REASON
    payload, by_contract = await _ranked(temp_db)
    assert payload["card_policy"]["mode"] == "enforce" and payload["card_policy"]["min_measured_ev"] == 0.0
    ddd = by_contract["DDD"]
    assert ddd["outcome"] == RankedOutcome.CARD_POLICY_WITHHELD == "card_policy_withheld"
    assert ddd["signal_id"] is None and ddd["llm"] is None
    assert ddd["card_policy"] == {"measured_ev": -0.39, "n_mature": 30, "would_withhold": True, "n_fetch_failed": 0}
    [signal] = await temp_db.get_recent_signals(limit=10)
    provenance = signal["decision_provenance"]
    assert provenance["card_policy"] == {
        "measured_ev": None,
        "n_mature": None,
        "would_withhold": False,
        "n_fetch_failed": 0,
    }
    assert provenance["card_evidence"]["status"] == "insufficient"


@pytest.mark.parametrize("kind", ["insufficient", "stale", "unavailable", "partial"])
async def test_enforce_never_withholds_without_complete_measured_evidence(policy_desk, temp_db, app_config, kind):
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    snapshot = {
        "insufficient": make_snapshot(keys=[{**NEGATIVE, "n_mature": 19}]),
        "stale": make_snapshot(keys=[NEGATIVE], computed_at=datetime.now(UTC) - timedelta(days=5)),
        "unavailable": None,
        # A partial key (some candidates' bars could not be read) is shown but never withheld.
        "partial": make_snapshot(keys=[{**NEGATIVE, "n_fetch_failed": 2}]),
    }[kind]
    policy_desk.card_stats = FakeCardStats(snapshot)

    await policy_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    _, by_contract = await _ranked(temp_db)
    assert by_contract["DDD"]["outcome"] == "sent" and by_contract["DDD"]["card_policy"]["would_withhold"] is False
    if kind == "partial":
        assert by_contract["DDD"]["card_policy"] == {
            "measured_ev": -0.39,
            "n_mature": 30,
            "would_withhold": False,
            "n_fetch_failed": 2,
        }
        [signal] = await temp_db.get_recent_signals(limit=10)
        assert signal["decision_provenance"]["card_evidence"]["n_fetch_failed"] == 2


NULL_POLICY = {"measured_ev": None, "n_mature": None, "would_withhold": None, "n_fetch_failed": None}


@pytest.mark.parametrize(
    "lock",
    [{"alpha_version": "alpha:x:v3"}, {"alpha_policy": {"id": "alpha_x"}}, {"probe": True}],
    ids=["alpha-version", "alpha-policy", "probe"],
)
async def test_the_policy_skips_policy_locked_cards_but_they_still_show_evidence(
    policy_desk, temp_db, app_config, lock
):
    """A versioned alpha or a paper probe is outside the policy: enforce on a negative key still sends it."""
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    native = policy_desk.strategy_engine.scan_contract.side_effect
    policy_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        item.model_copy(update=lock) if item.contract == "DDD" else item for item in native(data, **kw)
    ]

    await policy_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    assert _llm_contracts(policy_desk) == ["DDD"]
    _, by_contract = await _ranked(temp_db)
    assert by_contract["DDD"]["outcome"] == "sent" and by_contract["DDD"]["card_policy"] == NULL_POLICY
    assert by_contract["CCC"]["card_policy"]["would_withhold"] is False  # native candidates are still decided
    [signal] = await temp_db.get_recent_signals(limit=10)
    provenance = signal["decision_provenance"]
    assert provenance["card_policy"] == NULL_POLICY
    assert provenance["card_evidence"]["status"] == "measured" and provenance["card_evidence"]["mean_r_cost"] == -0.39


class _StaleBars:
    """Bars entirely before the scan's decision time: every candidate is IMMATURE."""

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        index = pd.date_range("2020-01-01", periods=2, freq="h", tz="UTC")
        return pd.DataFrame({"Open": [1.0] * 2, "High": [1.0] * 2, "Low": [1.0] * 2, "Close": [1.0] * 2}, index=index)


async def test_withheld_candidates_stay_journaled_and_keep_being_labelled(shadow_desk, temp_db, app_config):  # noqa: F811
    """Integration: run_scan journal -> label_journaled -> summarize, every candidate below the threshold."""
    app_config.card_policy = CardPolicyConfig(mode="enforce", min_measured_ev=0.0)
    shadow_desk.card_stats = FakeCardStats(make_snapshot(keys=[NEGATIVE]))  # all five are TREND_PULLBACK LONG

    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert await temp_db.get_recent_signals(limit=10) == []
    assert _llm_contracts(shadow_desk) == []
    events = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    frame = label_journaled(events, _StaleBars())
    assert len(frame) == 5 and frame["card_policy_withheld"].all() and (frame["would_withhold"] == True).all()  # noqa: E712
    block = summarize(frame)["card_policy"]
    assert block == {
        "withheld": 5,
        "would_withhold": 5,
        "withheld_mean_r_cost": None,
        "would_withhold_mean_r_cost": None,
    }
