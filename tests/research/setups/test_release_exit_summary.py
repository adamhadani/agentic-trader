"""``release_exit_summary``: the descriptive ``cards outcomes`` block over the ``release-exit`` journal."""

from __future__ import annotations

from datetime import date

from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups.outcomes import release_exit_summary


def _entry(scope, status, decision):
    return {"signal_id": 1, "symbol": "X", "scope": scope, "status": status, "decision": decision}


def _pass(day, mode, entries):
    return {
        "kind": EventKind.RELEASE_EXIT_PASS,
        "stream": "release-exit",
        "payload": {"mode": mode, "trading_day": day, "next_trading_day": None, "positions": entries},
    }


def _close(day, outcome):
    return {
        "kind": EventKind.RELEASE_EXIT_CLOSE,
        "stream": "release-exit",
        "payload": {"trading_day": day, "signal_id": 1, "outcome": outcome},
    }


EVENTS = [
    _pass("2026-10-30", "preview", [_entry("native", "would_close", "exit")]),  # before the window
    _pass(
        "2026-11-05",
        "preview",
        [
            _entry("native", "would_close", "exit"),
            _entry("native", "none", "none"),
            _entry("pead", "exempt", "exit"),
            _entry("probe", "exempt", "none"),
        ],
    ),
    _pass(
        "2026-11-06",
        "enforce",
        [_entry("native", "close", "exit"), _entry("native", "unknown", "unknown"), _entry("pead", "exempt", "exit")],
    ),
    _close("2026-11-06", "submitted"),
    _close("2026-10-30", "refused"),
]


def test_counts_by_status_scope_and_date():
    out = release_exit_summary(EVENTS, since=date(2026, 11, 1))
    assert out["passes"] == 2 and out["positions"] == 7
    assert out["by_status"] == {"close": 1, "exempt": 3, "none": 1, "unknown": 1, "would_close": 1}
    assert out["by_scope"] == {"native": 4, "pead": 2, "probe": 1}
    assert out["exempt_through_report"] == 2
    assert out["by_date"] == {
        "2026-11-05": {
            "mode": "preview",
            "by_status": {"exempt": 2, "none": 1, "would_close": 1},
            "exempt_through_report": 1,
        },
        "2026-11-06": {
            "mode": "enforce",
            "by_status": {"close": 1, "exempt": 1, "unknown": 1},
            "exempt_through_report": 1,
        },
    }
    assert out["closes"] == {"n": 1, "by_outcome": {"submitted": 1}}


def test_without_a_window_every_pass_counts():
    assert release_exit_summary(EVENTS)["passes"] == 3


def test_summary_of_nothing_is_empty_not_an_error():
    assert release_exit_summary([]) == {
        "passes": 0,
        "positions": 0,
        "by_status": {},
        "by_scope": {},
        "exempt_through_report": 0,
        "by_date": {},
        "closes": {"n": 0, "by_outcome": {}},
    }
