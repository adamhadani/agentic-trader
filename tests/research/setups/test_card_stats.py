"""Pure per-(strategy, direction) card statistics from a labelled frame."""

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

import agentic_trader.research.setups.outcomes as outcomes_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups.card_stats import (
    AGGREGATE,
    CARD_STATS_STREAM,
    LABELLER_PROTOCOL,
    CardStatsSnapshot,
    card_stats_key,
    compute_card_stats,
)
from agentic_trader.research.setups.outcomes import label_journaled
from agentic_trader.research.setups.sources import scan_ranked_events
from tests.research.setups.test_outcomes import (
    AAPL_BARS,
    DECIDED_AT,
    NOW,
    SPY_BARS,
    FakeBarSource,
    _candidate,
    _event,
)


COMPUTED_AT = datetime(2026, 10, 8, 12, 31, tzinfo=UTC)


def _labelled(rows):
    """Rows of (strategy, direction, hit, r_cost, holding_sessions, decided_at) in label_journaled's columns."""
    records = [
        {
            **dict.fromkeys(outcomes_module._COLUMNS),
            "strategy": strategy,
            "direction": direction,
            "hit": hit,
            "r_cost": r_cost,
            "holding_sessions": holding,
            "decided_at": pd.Timestamp(decided_at),
        }
        for strategy, direction, hit, r_cost, holding, decided_at in rows
    ]
    return pd.DataFrame(records, columns=list(outcomes_module._COLUMNS))


def _compute(frame, **overrides):
    kwargs = {
        "window_start": date(2026, 7, 11),
        "window_end": date(2026, 10, 8),
        "feed": "iex",
        "cost_bps": 5.0,
        "max_hold_sessions": 20,
        "code_revision": "test-rev",
        "now": COMPUTED_AT,
        "events_considered": 12,
    }
    return compute_card_stats(frame, **{**kwargs, **overrides})


FIRST, MID, LAST = "2026-07-01T14:35:00+00:00", "2026-08-01T14:35:00+00:00", "2026-09-30T18:35:00+00:00"
TP = [
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, FIRST),
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, MID),
    ("TREND_PULLBACK", "LONG", "target", 1.9, 2, MID),
    *[("TREND_PULLBACK", "LONG", "stop", -1.1, 1, MID)] * 5,
    ("TREND_PULLBACK", "LONG", "timeout", 0.3, 20, MID),
    ("TREND_PULLBACK", "LONG", "timeout", -0.1, 20, MID),
    ("TREND_PULLBACK", "LONG", "immature", None, 0, MID),
    ("TREND_PULLBACK", "LONG", "fetch_failed", None, 0, LAST),
]
SQ = [("SQUEEZE_BREAKOUT", "SHORT", "stop", -1.05, 1, MID)]


def _by_key(payload):
    return {(row["strategy"], row["direction"]): row for row in payload["keys"]}


def test_rates_are_per_key_over_mature_rows_with_fetch_failures_excluded():
    keys = _by_key(_compute(_labelled([*TP, *SQ])))
    tp = keys[("TREND_PULLBACK", "LONG")]
    assert (tp["n_mature"], tp["n_immature"], tp["n_fetch_failed"]) == (10, 1, 1)
    assert tp["target_rate"] == pytest.approx(0.3)
    assert tp["stop_rate"] == pytest.approx(0.5)
    assert tp["timeout_rate"] == pytest.approx(0.2)
    assert tp["mean_r_cost"] == pytest.approx(0.04)  # (3*1.9 - 5*1.1 + 0.3 - 0.1) / 10
    assert tp["mean_timeout_r"] == pytest.approx(0.1)  # the timeouts' partial R
    assert tp["median_holding_sessions"] == pytest.approx(1.5)
    assert tp["first_decided_at"] == FIRST and tp["last_decided_at"] == LAST
    sq = keys[("SQUEEZE_BREAKOUT", "SHORT")]
    assert sq["n_mature"] == 1 and sq["stop_rate"] == 1.0 and sq["mean_timeout_r"] is None


def test_the_aggregate_key_covers_every_row():
    agg = _by_key(_compute(_labelled([*TP, *SQ])))[(AGGREGATE, AGGREGATE)]
    assert (agg["n_mature"], agg["n_immature"], agg["n_fetch_failed"]) == (11, 1, 1)
    assert agg["target_rate"] == pytest.approx(3 / 11)
    assert agg["stop_rate"] == pytest.approx(6 / 11)
    assert agg["mean_r_cost"] == pytest.approx(-0.65 / 11)


def test_a_row_without_a_strategy_counts_only_in_the_aggregate():
    payload = _compute(_labelled([*SQ, (None, "LONG", "stop", -1.0, 1, MID)]))
    keys = _by_key(payload)
    assert set(keys) == {("SQUEEZE_BREAKOUT", "SHORT"), (AGGREGATE, AGGREGATE)}
    assert keys[(AGGREGATE, AGGREGATE)]["n_mature"] == 2


def test_output_is_nan_free_and_a_key_without_finite_r_reports_none():
    payload = _compute(_labelled([("X", "LONG", "stop", float("nan"), 1, MID), *SQ]))
    json.dumps(payload, allow_nan=False)  # the journal encoder's own rule
    assert _by_key(payload)[("X", "LONG")]["mean_r_cost"] is None


def test_a_key_with_only_immature_rows_has_no_rates():
    row = _by_key(_compute(_labelled([("X", "LONG", "immature", None, 0, MID)])))[("X", "LONG")]
    assert row["n_mature"] == 0 and row["n_immature"] == 1
    assert row["target_rate"] is None and row["mean_r_cost"] is None and row["median_holding_sessions"] is None


def test_an_empty_frame_gives_no_keys_and_full_provenance():
    payload = _compute(pd.DataFrame(columns=list(outcomes_module._COLUMNS)))
    assert payload["keys"] == []
    assert payload["rows_labelled"] == 0 and payload["events_considered"] == 12
    assert payload["labeller_protocol"] == LABELLER_PROTOCOL == "setup-outcomes-v1"
    assert payload["computed_at"] == COMPUTED_AT.isoformat()
    assert (payload["window_start"], payload["window_end"]) == ("2026-07-11", "2026-10-08")
    assert (payload["feed"], payload["cost_bps_per_side"], payload["max_hold_sessions"]) == ("iex", 5.0, 20)
    snapshot = CardStatsSnapshot.model_validate(payload)
    assert snapshot.snapshot_key == card_stats_key(date(2026, 10, 8)) == "card_stats/2026-10-08"


def test_the_snapshot_reads_back_and_finds_a_key():
    snapshot = CardStatsSnapshot.model_validate(_compute(_labelled(TP)))
    assert snapshot.stats("TREND_PULLBACK", "LONG").n_mature == 10
    assert snapshot.stats("TREND_PULLBACK", "SHORT") is None
    assert snapshot.computed_at.utcoffset() is not None


def test_vocabulary():
    assert CARD_STATS_STREAM == "card_stats"
    assert EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"


async def test_journaled_candidates_round_trip_into_a_snapshot(temp_db):
    """Integration: the real SQLite journal -> scan_ranked_events -> label_journaled -> compute -> model."""
    event = _event(
        "scan-1",
        DECIDED_AT,
        [
            _candidate(contract="AAPL", rank=1, outcome="sent"),
            _candidate(contract="MSFT", rank=2, outcome="per-scan budget spent"),
        ],
    )
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await temp_db.workflows.append(
            session,
            stream=event["stream"],
            kind=EventKind.SCAN_CANDIDATES_RANKED,
            payload=event["payload"],
            key="scan_candidates_ranked/scan-1",
        )
    events = await scan_ranked_events(temp_db, 3, now=NOW)
    frame = label_journaled(events, FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS}, raise_for={"MSFT"}), now=NOW)
    payload = compute_card_stats(
        frame,
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 2),
        feed="iex",
        cost_bps=5.0,
        max_hold_sessions=20,
        code_revision="test-rev",
        now=NOW,
        events_considered=len(events),
    )
    snapshot = CardStatsSnapshot.model_validate(json.loads(json.dumps(payload, allow_nan=False)))
    stats = snapshot.stats("BREAKOUT", "LONG")
    assert stats.n_mature == 1 and stats.n_fetch_failed == 1 and stats.target_rate == 1.0
    assert snapshot.events_considered == 1 and snapshot.rows_labelled == 2
