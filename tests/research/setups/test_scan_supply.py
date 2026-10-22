"""``scan_supply``: scans, status, zero-candidate scans, sent cards and funnels per trigger, from the events."""

from __future__ import annotations

from typing import Any

from agentic_trader.execution.durable import RankedOutcome, ScanStatus, ScanTrigger
from agentic_trader.research.setups.outcomes import scan_supply
from tests.research.setups.test_outcomes import DECIDED_AT, _candidate, _event


def _funnel(**overrides: Any) -> dict[str, Any]:
    """A ``ScanFunnel.journal_block()``-shaped funnel, all zero beyond the overrides."""
    block: dict[str, Any] = {
        **dict.fromkeys(
            (
                "selected",
                "scanned",
                "fetch_failed",
                "insufficient",
                "coverage_excluded",
                "dynamic_excluded",
                "raw_setups",
                "duplicates",
                "account_risk_unavailable",
                "setups_lost_to_errors",
                "admitted",
            ),
            0,
        ),
        "raw_setups_by_strategy": {},
        "rejected_by_rule": {},
        "errors": {},
    }
    return {**block, **overrides}


def _scan(scan_id: str, candidates: list[dict[str, Any]], **payload: Any) -> dict[str, Any]:
    """A ``_event`` with extra top-level payload fields (``status``, ``funnel``, ``trigger``...)."""
    event = _event(scan_id, DECIDED_AT, candidates)
    event["payload"].update(payload)
    return event


def _supply_of(*events: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return scan_supply(list(events))


SUGGESTION = ScanTrigger.SUGGESTION_SCAN.value
SWING = ScanTrigger.SWING_SCAN.value

MIXED = [
    # 1. Written before status, trigger, funnel and calendar existed.
    _scan("old", [_candidate(contract="AAA", outcome=RankedOutcome.SENT)]),
    # 2. A completed scan that ranked nothing.
    _scan(
        "empty",
        [],
        trigger=SUGGESTION,
        status=ScanStatus.COMPLETED.value,
        gate=None,
        funnel=_funnel(
            selected=100,
            scanned=94,
            fetch_failed=4,
            insufficient=2,
            raw_setups=2,
            raw_setups_by_strategy={"BREAKOUT": 2},
            rejected_by_rule={"concurrent_positions": 1, "unclassified": 1},
            errors={"TimeoutError": 1, "ValueError": 1},
        ),
    ),
    # 3. Gated by the macro lockout: no regime read, no funnel.
    _scan(
        "gated",
        [],
        trigger=SUGGESTION,
        status=ScanStatus.MACRO_LOCKOUT.value,
        gate={"rule": "macro_lockout", "reason": "FOMC"},
        funnel=None,
        regime=None,
    ),
    # 4. A completed scan with one card sent and two runners-up, one refused by a selection rule.
    _scan(
        "ranked",
        [
            _candidate(contract="BBB", rank=1, outcome=RankedOutcome.SENT),
            _candidate(contract="SPY", rank=2, outcome=RankedOutcome.BROAD_INDEX_EXCLUDED),
            _candidate(contract="CCC", rank=3, outcome="per-scan budget spent"),
        ],
        trigger=SUGGESTION,
        status=ScanStatus.COMPLETED.value,
        funnel=_funnel(
            selected=100,
            scanned=95,
            fetch_failed=1,
            coverage_excluded=4,
            raw_setups=6,
            raw_setups_by_strategy={"BREAKOUT": 4, "TREND_PULLBACK": 2},
            duplicates=1,
            rejected_by_rule={"concurrent_positions": 2},
            errors={"TimeoutError": 1},
            admitted=3,
        ),
    ),
    # 5. A journal-only swing scan: two candidates, none sent.
    _scan(
        "swing",
        [
            _candidate(contract="DDD", rank=1, outcome=RankedOutcome.JOURNAL_ONLY),
            _candidate(contract="EEE", rank=2, outcome=RankedOutcome.JOURNAL_ONLY),
        ],
        trigger=SWING,
        status=ScanStatus.COMPLETED.value,
        funnel=_funnel(selected=80, scanned=80, raw_setups=2, admitted=2),
    ),
]


def test_scan_supply_counts_zero_candidate_and_gated_scans_per_trigger():
    supply = scan_supply(MIXED)
    assert set(supply) == {"unknown", SUGGESTION, SWING}
    suggestion = supply[SUGGESTION]
    assert suggestion["scans"] == 3
    assert suggestion["by_status"] == {"completed": 2, "macro_lockout": 1}
    assert suggestion["zero_candidate"] == 1
    assert suggestion["sent"] == 1
    assert supply[SWING]["scans"] == 1 and supply[SWING]["zero_candidate"] == 0
    assert supply[SWING]["sent"] == 0
    assert supply["unknown"]["scans"] == 1 and supply["unknown"]["sent"] == 1
    assert supply["unknown"]["funnel_unknown"] == 1


def test_an_event_without_status_counts_as_completed():
    old = _supply_of(_scan("old", [_candidate()]))
    assert old["unknown"]["by_status"] == {"completed": 1}
    # ... including a zero-candidate one: an old event with an empty list is a completed scan.
    empty = _supply_of(_scan("old-empty", []))
    assert empty["unknown"]["zero_candidate"] == 1


def test_funnel_sums_integers_and_each_key_of_the_dict_fields():
    funnel = scan_supply(MIXED)[SUGGESTION]["funnel"]
    assert funnel["selected"] == 200 and funnel["scanned"] == 189
    assert funnel["fetch_failed"] == 5 and funnel["insufficient"] == 2 and funnel["coverage_excluded"] == 4
    assert funnel["raw_setups"] == 8 and funnel["duplicates"] == 1 and funnel["admitted"] == 3
    assert funnel["raw_setups_by_strategy"] == {"BREAKOUT": 6, "TREND_PULLBACK": 2}
    assert funnel["rejected_by_rule"] == {"concurrent_positions": 3, "unclassified": 1}
    assert funnel["errors"] == {"TimeoutError": 2, "ValueError": 1}
    # The identity each completed funnel satisfies survives summing.
    assert funnel["raw_setups"] == (
        funnel["duplicates"]
        + funnel["account_risk_unavailable"]
        + sum(funnel["rejected_by_rule"].values())
        + funnel["admitted"]
        + funnel["setups_lost_to_errors"]
    )


def test_a_gated_scan_has_no_funnel_but_is_not_an_unknown_one():
    supply = scan_supply(MIXED)
    assert supply[SUGGESTION]["funnel_unknown"] == 0
    assert supply[SWING]["funnel_unknown"] == 0
    gated_only = _supply_of(MIXED[2])
    assert gated_only[SUGGESTION] == {
        "scans": 1,
        "by_status": {"macro_lockout": 1},
        "zero_candidate": 0,
        "sent": 0,
        "funnel": {},
        "funnel_unknown": 0,
    }


def test_a_completed_event_with_a_null_or_missing_funnel_is_unknown_and_adds_nothing():
    null_funnel = _scan("null", [], trigger=SUGGESTION, status=ScanStatus.COMPLETED.value, funnel=None)
    missing_funnel = _scan("missing", [], trigger=SUGGESTION, status=ScanStatus.COMPLETED.value)
    supply = _supply_of(null_funnel, missing_funnel)
    assert supply[SUGGESTION]["funnel_unknown"] == 2
    assert supply[SUGGESTION]["funnel"] == {}
    assert supply[SUGGESTION]["zero_candidate"] == 2


def test_a_regime_unavailable_scan_counts_by_status_only():
    event = _scan(
        "regime",
        [],
        trigger=SUGGESTION,
        status=ScanStatus.REGIME_UNAVAILABLE.value,
        gate={"rule": "regime_unavailable", "missing": ["vix"], "errors": {"vix": "TimeoutError"}},
        funnel=None,
    )
    entry = _supply_of(event)[SUGGESTION]
    assert entry["by_status"] == {"regime_unavailable": 1}
    assert entry["zero_candidate"] == 0 and entry["funnel_unknown"] == 0


def test_sent_counts_every_status_and_only_the_sent_outcome():
    events = [
        _scan(
            "a", [_candidate(outcome="sent"), _candidate(contract="B", outcome=RankedOutcome.LLM_VETOED)], trigger=SWING
        ),
        _scan("b", [_candidate(contract="C", outcome="sent")], trigger=SWING, status=ScanStatus.MACRO_LOCKOUT.value),
    ]
    assert _supply_of(*events)[SWING]["sent"] == 2


def test_other_event_kinds_and_scopes_are_not_scans():
    other_kind = _event("k", DECIDED_AT, [_candidate()], kind="health_observed")
    other_scope = _event("s", DECIDED_AT, [_candidate()], scope="symbols")
    assert scan_supply([other_kind, other_scope]) == {}
    assert scan_supply([]) == {}
