"""The pure card policy: a switch on measured evidence, never a fail-closed rule."""

import pytest

from agentic_trader.config import CardPolicyConfig
from agentic_trader.execution.card_evidence import CardEvidence
from agentic_trader.execution.card_policy import decide, journal_block


def evidence(status="measured", n_mature=30, mean_r_cost=-0.4):
    measured = status == "measured"
    return CardEvidence(
        status=status,
        strategy="TREND_PULLBACK",
        direction="LONG",
        n_mature=n_mature,
        min_mature=10,
        target_rate=0.2 if measured else None,
        stop_rate=0.8 if measured else None,
        timeout_rate=0.0 if measured else None,
        mean_r_cost=mean_r_cost if measured else None,
    )


@pytest.mark.parametrize(
    ("mode", "threshold", "status", "n_mature", "mean_r_cost", "withhold", "would"),
    [
        ("off", None, "measured", 30, -0.4, False, False),
        ("preview", 0.0, "measured", 30, -0.4, False, True),
        ("enforce", 0.0, "measured", 30, -0.4, True, True),
        ("enforce", 0.0, "measured", 30, 0.0, False, False),  # equal to the threshold is not below it
        ("enforce", 0.0, "measured", 19, -0.4, False, False),  # below min_mature_cards (20)
        ("enforce", -0.2, "measured", 30, -0.1, False, False),  # above a negative threshold
        ("enforce", 0.0, "insufficient", 5, None, False, False),
        ("enforce", 0.0, "stale", None, None, False, False),
        ("enforce", 0.0, "unavailable", None, None, False, False),
    ],
)
def test_truth_table(mode, threshold, status, n_mature, mean_r_cost, withhold, would):
    decision = decide(CardPolicyConfig(mode=mode, min_measured_ev=threshold), evidence(status, n_mature, mean_r_cost))
    assert (decision.withhold, decision.would_withhold) == (withhold, would)


def test_the_reason_names_the_measured_ev_the_sample_and_the_threshold():
    decision = decide(CardPolicyConfig(mode="enforce", min_measured_ev=0.0), evidence(mean_r_cost=-0.39))
    assert decision.reason == "card policy: measured EV -0.39R over 30 < +0.00R"
    assert (decision.measured_ev, decision.n_mature) == (-0.39, 30)


def test_unmeasured_evidence_records_no_ev_and_no_count():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence("insufficient", 5))
    assert decision.reason is None and decision.measured_ev is None and decision.n_mature is None


def test_journal_block():
    decision = decide(CardPolicyConfig(mode="preview", min_measured_ev=0.0), evidence())
    assert journal_block(decision, "preview") == {"measured_ev": -0.4, "n_mature": 30, "would_withhold": True}
    assert journal_block(decision, "off")["would_withhold"] is None
    assert journal_block(None, "enforce") is None
