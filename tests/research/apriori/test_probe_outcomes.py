from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from agentic_trader.research.apriori.probe_outcomes import decision_frame, probe_signals, summarize_probe
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import SignalRecord


def _event(symbol: str, outcome: str, *, report_date="2026-09-10", session="2026-09-14") -> dict:
    return {
        "symbol": symbol,
        "report_date": report_date,
        "session": session,
        "decision_at": f"{session}T14:35:00+00:00",
        "z": 3.2,
        "surprise_pct": 12.5,
        "surprise_pct_reported": 12.5,
        "atr": 1.5,
        "median_dollar_volume": 5_000_000.0,
        "reference": 4_000_000.0,
        "leg": "LONG",
        "surprise_long": True,
        "surprise_short": False,
        "reaction_long": True,
        "reaction_short": False,
        "outcome": outcome,
    }


def _payload(scan_id: str, *, status="ok", events=None, session="2026-09-14") -> dict:
    return {
        "scan_id": scan_id,
        "session": session,
        "status": status,
        "reason": None,
        "version_id": "apriori:pead:v2:long:deadbeefdeadbeef",
        "report_date": "2026-09-10",
        "page_sha256": "abc123",
        "counts": {},
        "skipped": {},
        "time_exit_at": f"{session}T19:45:00+00:00",
        "events": events or [],
    }


def test_decision_frame_round_trips_events_with_typed_columns():
    payloads = [
        _payload("scan-1", events=[_event("AAA", "sent"), _event("BBB", "rejected: risk")]),
        _payload("scan-2", status="unavailable", events=[]),
    ]

    frame = decision_frame(payloads)

    assert len(frame) == 2
    assert sorted(frame["outcome"]) == ["rejected: risk", "sent"]
    assert isinstance(frame["session"].iloc[0], date)
    assert isinstance(frame["report_date"].iloc[0], date)
    decision_at = frame["decision_at"].iloc[0]
    assert isinstance(decision_at, datetime)
    assert decision_at.tzinfo is not None


def test_decision_frame_dedupes_a_repeated_scan_id():
    payloads = [
        _payload("scan-1", events=[_event("AAA", "sent")]),
        _payload("scan-1", events=[_event("AAA", "sent")]),
    ]

    frame = decision_frame(payloads)

    assert len(frame) == 1


def test_decision_frame_on_no_payloads_has_the_right_columns_and_is_empty():
    frame = decision_frame([])

    assert frame.empty
    assert "outcome" in frame.columns
    assert "session" in frame.columns


def _signal(*, status, executed_at=None, realized_pnl=None, risk_dollars=100.0) -> dict:
    return {
        "status": status,
        "executed_at": executed_at,
        "realized_pnl": realized_pnl,
        "risk_dollars": risk_dollars,
    }


def test_summarize_probe_counts_realized_r_and_counterfactual():
    decisions = pd.DataFrame(
        {"outcome": ["sent", "sent", "drift budget spent", "drift budget spent"]},
    )
    labels = pd.DataFrame({"r_cost": [1.0, -1.0, 3.0]})  # the immature 4th event never becomes a labels row
    signals = [
        _signal(status="CLOSED_LOSS", executed_at="2026-09-14T14:36:00+00:00", realized_pnl=-100.0, risk_dollars=100.0),
        _signal(status="EXPIRED"),
    ]
    forward = {"trades": 1, "cumulative_r": -1.0}
    study_mean_r = 0.149

    summary = summarize_probe(decisions, labels, signals, forward, study_mean_r)

    assert summary["events"] == 4
    assert summary["carded"] == 2
    assert summary["executed"] == 1
    assert summary["closed"] == 1
    assert summary["realized_mean_r"] == -1.0
    assert summary["counterfactual"] == {"mature": 3, "mean_r_cost": 1.0}
    assert summary["forward"] == forward
    assert summary["study_mean_r"] == study_mean_r
    assert "10 closed trades" in summary["note"]


def test_summarize_probe_on_no_evidence_reports_none_not_errors():
    summary = summarize_probe(decision_frame([]), pd.DataFrame(), [], None, None)

    assert summary == {
        "events": 0,
        "carded": 0,
        "executed": 0,
        "closed": 0,
        "realized_mean_r": None,
        "counterfactual": None,
        "forward": None,
        "study_mean_r": None,
        "note": summary["note"],
    }


async def test_probe_signals_scopes_to_pead_long_strategy_and_since(tmp_path):
    db = SignalDatabase(db_path=str(tmp_path / "probe_outcomes.db"))
    try:
        await db.init_db()
        async with db.session_factory() as session, session.begin():
            session.add(
                SignalRecord(
                    timestamp=datetime(2026, 9, 14, 14, 35, tzinfo=UTC),
                    contract="AAA",
                    strategy="pead_long",
                    direction="LONG",
                    entry_price=100.0,
                    stop_loss=98.0,
                    take_profit=106.0,
                    risk_dollars=2.0,
                    status="CLOSED_LOSS",
                    executed_at=datetime(2026, 9, 14, 14, 36, tzinfo=UTC),
                    realized_pnl=-100.0,
                    environment=db.environment,
                    execution_mode=db.execution_mode,
                )
            )
            session.add(
                SignalRecord(
                    timestamp=datetime(2026, 9, 1, 14, 35, tzinfo=UTC),  # before `since`
                    contract="ZZZ",
                    strategy="pead_long",
                    direction="LONG",
                    entry_price=100.0,
                    stop_loss=98.0,
                    take_profit=106.0,
                    risk_dollars=2.0,
                    status="EXPIRED",
                    environment=db.environment,
                    execution_mode=db.execution_mode,
                )
            )
            session.add(
                SignalRecord(
                    timestamp=datetime(2026, 9, 14, 14, 35, tzinfo=UTC),
                    contract="AAA",
                    strategy="BREAKOUT",  # not pead_long
                    direction="LONG",
                    entry_price=100.0,
                    stop_loss=98.0,
                    take_profit=106.0,
                    risk_dollars=2.0,
                    status="EXECUTED",
                    environment=db.environment,
                    execution_mode=db.execution_mode,
                )
            )

        rows = await probe_signals(db, datetime(2026, 9, 10, tzinfo=UTC))

        assert [row["contract"] for row in rows] == ["AAA"]
        assert rows[0]["status"] == "CLOSED_LOSS"
        assert rows[0]["realized_pnl"] == -100.0
        assert rows[0]["risk_dollars"] == 2.0
    finally:
        await db.engine.dispose()
