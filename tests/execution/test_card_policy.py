"""The pure card policy: a switch on the posterior expected excess, never a fail-closed rule."""

import pytest

from agentic_trader.config import CardPolicyConfig
from agentic_trader.execution.card_evidence import CardEvidence
from agentic_trader.execution.card_policy import decide, journal_block
from agentic_trader.research.setups.net_excess import NET_EXCESS_UNIT


def evidence(status="measured", n_mature=30, posterior_mean=-0.4, n_fetch_failed=0, n_unresolved=0):
    measured = status == "measured"
    return CardEvidence(
        status=status,
        strategy="TREND_PULLBACK",
        direction="LONG",
        n_mature=n_mature,
        min_mature=10,
        n_fetch_failed=n_fetch_failed,
        n_unresolved=n_unresolved,
        unit=NET_EXCESS_UNIT,
        timeframe="4h",
        target_rate=0.2 if measured else None,
        stop_rate=0.8 if measured else None,
        timeout_rate=0.0 if measured else None,
        mean_r_cost=0.5,  # a decoy: the policy reads the posterior, never the raw mean
        posterior_mean=posterior_mean if measured else None,
    )


@pytest.mark.parametrize(
    ("mode", "threshold", "status", "n_mature", "posterior_mean", "n_fetch_failed", "withhold", "would"),
    [
        ("off", None, "measured", 30, -0.4, 0, False, False),
        ("off", 0.0, "measured", 30, -0.4, 0, False, False),  # a threshold alone does nothing while off
        ("preview", 0.0, "measured", 30, -0.4, 0, False, True),
        ("enforce", 0.0, "measured", 30, -0.4, 0, True, True),
        ("enforce", 0.0, "measured", 30, 0.0, 0, False, False),  # equal to the threshold is not below it
        ("enforce", 0.0, "measured", 19, -0.4, 0, False, False),  # below min_mature_cards (20)
        ("enforce", -0.2, "measured", 30, -0.1, 0, False, False),  # above a negative threshold
        ("enforce", 0.0, "measured", 30, -0.4, 1, False, False),  # a partial key (fetch failures) never withholds
        ("preview", 0.0, "measured", 30, -0.4, 3, False, False),
        ("enforce", 0.0, "insufficient", 5, None, 0, False, False),
        ("enforce", 0.0, "stale", None, None, 0, False, False),
        ("enforce", 0.0, "unavailable", None, None, 0, False, False),
    ],
)
def test_truth_table(mode, threshold, status, n_mature, posterior_mean, n_fetch_failed, withhold, would):
    decision = decide(
        CardPolicyConfig(mode=mode, min_measured_ev=threshold),
        evidence(status, n_mature, posterior_mean, n_fetch_failed),
    )
    assert (decision.withhold, decision.would_withhold) == (withhold, would)
    if not would:
        assert decision.reason is None


def test_a_partial_key_records_its_fetch_failures():
    decision = decide(CardPolicyConfig(mode="enforce", min_measured_ev=0.0), evidence(n_fetch_failed=2))
    assert (decision.withhold, decision.would_withhold, decision.reason) == (False, False, None)
    assert journal_block(decision, "enforce") == {
        "measured_ev": -0.4,
        "n_mature": 30,
        "would_withhold": False,
        "n_fetch_failed": 2,
        "unit": "net_excess_r_v1",
    }


def test_decide_withholds_on_posterior():
    decision = decide(
        CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence(n_mature=23, posterior_mean=-0.06)
    )
    assert (decision.would_withhold, decision.withhold) == (True, False)
    assert decision.reason == "card policy: expected excess -0.06R over 23 < +0.00R"
    assert decision.measured_ev == -0.06


@pytest.mark.parametrize(
    "case",
    [
        evidence(n_fetch_failed=1),
        evidence(n_unresolved=1),
        evidence("insufficient", 5),
        evidence("stale", None),
        evidence("unavailable", None),
    ],
    ids=["fetch-failed", "unresolved", "insufficient", "stale", "unavailable"],
)
def test_decide_never_on_partial_insufficient_stale(case):
    assert decide(CardPolicyConfig(mode="enforce", min_measured_ev=0.0), case).would_withhold is False


def test_the_reason_names_the_expected_excess_the_sample_and_the_threshold():
    decision = decide(CardPolicyConfig(mode="enforce", min_measured_ev=0.0), evidence(posterior_mean=-0.39))
    assert decision.reason == "card policy: expected excess -0.39R over 30 < +0.00R"
    assert (decision.measured_ev, decision.n_mature) == (-0.39, 30)


@pytest.mark.parametrize(
    ("posterior_mean", "threshold", "reason"),
    [
        (-0.004, 0.0, "card policy: expected excess -0.004R over 30 < +0.000R"),  # -0.00 vs +0.00 would read as a tie
        (-0.104, -0.1, "card policy: expected excess -0.104R over 30 < -0.100R"),
        (-0.39, -0.1, "card policy: expected excess -0.39R over 30 < -0.10R"),  # distinct at two decimals
    ],
)
def test_the_reason_uses_three_decimals_when_two_would_tie(posterior_mean, threshold, reason):
    decision = decide(
        CardPolicyConfig(mode="enforce", min_measured_ev=threshold), evidence(posterior_mean=posterior_mean)
    )
    assert decision.reason == reason


def test_unmeasured_evidence_records_no_ev_and_no_count():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence("insufficient", 5))
    assert decision.reason is None and decision.measured_ev is None and decision.n_mature is None


def test_journal_block_has_unit():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence())
    assert journal_block(decision, "preview") == {
        "measured_ev": -0.4,
        "n_mature": 30,
        "would_withhold": True,
        "n_fetch_failed": 0,
        "unit": "net_excess_r_v1",
    }
    assert journal_block(decision, "off")["would_withhold"] is None
    # No decision (a policy-locked candidate: the policy does not apply): every field is null.
    assert journal_block(None, "enforce") == {
        "measured_ev": None,
        "n_mature": None,
        "would_withhold": None,
        "n_fetch_failed": None,
        "unit": None,
    }
