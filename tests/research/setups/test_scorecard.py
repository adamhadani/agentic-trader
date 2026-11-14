"""``build_scorecard``: per-lane activity, realized record, counterfactual record and the account, from fakes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import agentic_trader.research.setups.scorecard as scorecard_module
from agentic_trader.execution.durable import EntryTrigger, EventKind
from agentic_trader.research.setups.scorecard import LaneScorecard, build_scorecard
from agentic_trader.storage.db import UNKNOWN_EXECUTION_MODE, SignalDatabase
from agentic_trader.storage.ledger import LEDGER_STREAM
from agentic_trader.storage.models import DomainEventRecord, SignalRecord


NOW = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)  # Saturday 09:00 New York (EST)
SESSION = "2026-11-10"
SECRET = "secret-detail-must-not-leak"


@pytest.fixture
def temp_db(tmp_path, app_config) -> SignalDatabase:
    """A database bound to the config's execution mode (the conftest one has the unknown mode)."""
    db = SignalDatabase(str(tmp_path / "scorecard.db"), config=app_config)
    assert db.execution_mode != UNKNOWN_EXECUTION_MODE
    return db


class FakeBars:
    """A fake ``BarSource``: no network, one canned frame (or one raised error) for every read."""

    def __init__(self, frame: pd.DataFrame | None = None, error: Exception | None = None):
        self.frame, self.error = frame, error
        self.calls: list[tuple] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        if self.error is not None:
            raise self.error
        return self.frame


def _frame(hours: list[datetime], prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": 1.0},
        index=pd.DatetimeIndex(hours),
    )


def native_bars() -> FakeBars:
    hours = [datetime(2026, 11, 10, 15, tzinfo=UTC), datetime(2026, 11, 10, 16, tzinfo=UTC)]
    frame = pd.DataFrame(
        {"Open": [100.5, 100.6], "High": [100.8, 102.5], "Low": [100.2, 100.3], "Close": [100.6, 102.0]},
        index=pd.DatetimeIndex(hours),
    )
    return FakeBars(frame)


def sip_bars() -> FakeBars:
    hours = [datetime(2026, 11, 10, h, tzinfo=UTC) for h in (14, 15, 16, 17, 18)]  # 09:00..13:00 ET (EST)
    return FakeBars(_frame(hours, [100.0, 100.0, 100.0, 112.0, 112.0]))


def _signal(db, *, contract, strategy, ts, status, **fields) -> SignalRecord:
    return SignalRecord(
        timestamp=ts,
        contract=contract,
        strategy=strategy,
        direction="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=106.0,
        risk_dollars=fields.pop("risk_dollars", 100.0),
        status=status,
        environment=db.environment,
        execution_mode=db.execution_mode,
        **fields,
    )


def _at(day: int, hour: int = 15, *, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


async def seed_signals(db) -> dict[str, int]:
    rows = {
        # native: two closed in the window (+2R, -1R), one open, one dismissed; one closed before the window (+1.5R)
        "N1": _signal(
            db,
            contract="AAA",
            strategy="BREAKOUT",
            ts=_at(10),
            status="CLOSED_WIN",
            executed_at=_at(10),
            exit_timestamp=_at(12),
            realized_pnl=200.0,
        ),
        "N2": _signal(
            db,
            contract="BBB",
            strategy="BREAKOUT",
            ts=_at(11),
            status="CLOSED_LOSS",
            executed_at=_at(11),
            exit_timestamp=_at(13),
            realized_pnl=-100.0,
        ),
        "N3": _signal(db, contract="CCC", strategy="BREAKOUT", ts=_at(12), status="EXECUTED", executed_at=_at(12)),
        "N4": _signal(db, contract="DDD", strategy="BREAKOUT", ts=_at(13), status="DISMISSED"),
        "N5": _signal(
            db,
            contract="EEE",
            strategy="BREAKOUT",
            ts=_at(20, month=10),
            status="CLOSED_WIN",
            executed_at=_at(20, month=10),
            exit_timestamp=_at(25, month=10),
            realized_pnl=150.0,
        ),
        # opened before the window, closed inside it: a close of the week, not a card of the week
        "N6": _signal(
            db,
            contract="GGG",
            strategy="BREAKOUT",
            ts=_at(30, month=10),
            status="CLOSED_WIN",
            executed_at=_at(30, month=10),
            exit_timestamp=_at(9),
            realized_pnl=100.0,
        ),
        # an alpha-versioned card is not a native card
        "ALPHA": _signal(
            db,
            contract="FFF",
            strategy="BREAKOUT",
            ts=_at(11),
            status="EXECUTED",
            alpha_version="v1",
            executed_at=_at(11),
        ),
        "P1": _signal(
            db,
            contract="PPP",
            strategy="pead_long",
            ts=_at(10),
            status="CLOSED_LOSS",
            executed_at=_at(10),
            exit_timestamp=_at(12),
            realized_pnl=-50.0,
        ),
        "P2": _signal(db, contract="QQQ1", strategy="pead_long", ts=_at(12), status="PENDING"),
        "W1": _signal(
            db, contract="WW1", strategy="insider_watch_long", ts=_at(11), status="EXECUTED", executed_at=_at(11)
        ),
        "W2": _signal(
            db, contract="WW2", strategy="insider_watch_long", ts=_at(12), status="EXECUTED", executed_at=_at(12)
        ),
    }
    # A closed win whose execution mode is unknown cannot be attributed to this paper scope: like
    # ``storage/probe_state.py``'s forward record, the scorecard counts it nowhere (``db._scope()`` would admit it).
    rows["UNKNOWN_MODE"] = _signal(
        db,
        contract="UUU",
        strategy="BREAKOUT",
        ts=_at(11),
        status="CLOSED_WIN",
        executed_at=_at(11),
        exit_timestamp=_at(12),
        realized_pnl=500.0,
    )
    rows["UNKNOWN_MODE"].execution_mode = UNKNOWN_EXECUTION_MODE
    async with db.session_factory() as session, session.begin():
        session.add_all(rows.values())
    return {name: row.id for name, row in rows.items()}


def route_triggers(db, monkeypatch, ids: dict[str, int]) -> None:
    triggers = {
        ids["N1"]: EntryTrigger.AUTO,
        ids["N2"]: EntryTrigger.TAP,
        ids["N3"]: EntryTrigger.AUTO,
        ids["P1"]: EntryTrigger.AUTO,
        ids["W1"]: EntryTrigger.TAP,
        ids["W2"]: EntryTrigger.AUTO,
    }

    async def entry_work_states(signal_ids):
        return {i: ("done", triggers[i]) for i in signal_ids if i in triggers}

    monkeypatch.setattr(db.workflows, "entry_work_states", entry_work_states)


async def journal(db, stream: str, kind: EventKind, key: str, payload: dict) -> None:
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(session, stream=stream, kind=kind, payload=payload, key=key)


async def seed_journals(db) -> None:
    decided_native = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    await journal(
        db,
        f"scan/{SESSION}",
        EventKind.SCAN_CANDIDATES_RANKED,
        "scan_candidates_ranked/sc-1",
        {
            "scan_id": "sc-1",
            "decided_at": decided_native.isoformat(),
            "scope": "universe",
            "budget": "full",
            "ranking_key": "setup_quality",
            "candidates": [
                {
                    "contract": "AAA",
                    "strategy": "BREAKOUT",
                    "timeframe": "1h",
                    "direction": "LONG",
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "atr_14": 1.0,
                    "setup_quality": 0.8,
                    "rank": 1,
                    "outcome": "sent",
                    "shadow": {"score": 0.7},
                    "llm": {
                        "approved": True,
                        "rejection_reason": None,
                        "stop_loss": 99.0,
                        "take_profit": 102.0,
                        "applied": True,
                    },
                    "signal_id": None,
                }
            ],
        },
    )
    decision_at = datetime(2026, 11, 10, 15, 35, tzinfo=UTC).isoformat()
    await journal(
        db,
        f"scan/{SESSION}",
        EventKind.PEAD_DECISION,
        "pead-1",
        {
            "scan_id": "p-1",
            "session": SESSION,
            "status": "ok",
            "events": [
                {
                    "symbol": "PPP",
                    "report_date": "2026-11-09",
                    "session": SESSION,
                    "decision_at": decision_at,
                    "atr": 2.0,
                    "leg": "LONG",
                    "outcome": "sent",
                }
            ],
        },
    )
    await journal(
        db,
        f"scan/{SESSION}",
        EventKind.INSIDER_WATCH_DECISION,
        "watch-1",
        {
            "scan_id": "w-1",
            "session": SESSION,
            "status": "completed",
            "events": [
                {
                    "event_id": "WW1-1",
                    "symbol": "WW1",
                    "session": SESSION,
                    "decision_at": decision_at,
                    "atr": 2.0,
                    "buyers": 3,
                    "opp_dollars": 900_000.0,
                    "outcome": "sent",
                }
            ],
        },
    )


async def seed_equity(db) -> None:
    async with db.session_factory() as session, session.begin():
        for key, ts, equity in (("e1", _at(7, 20), "100000"), ("e2", _at(13, 20), "101500.50")):
            session.add(
                DomainEventRecord(
                    scope=db.workflows.scope,
                    event_key=key,
                    stream=LEDGER_STREAM,
                    kind=EventKind.LEDGER_CHECKPOINT,
                    payload=json.dumps({"risk": {"adjusted_equity": equity}}),
                    recorded_at=ts,
                )
            )


@pytest.fixture
def fake_pead_labels(monkeypatch):
    seen: list[dict] = []

    def label_events(events, frames, entry):
        seen.append(dict(frames))
        labels = pd.DataFrame({"direction": ["LONG", "LONG"], "is_leg": [True, True], "r_cost": [0.5, -0.1]})
        return labels, {}

    monkeypatch.setattr(scorecard_module, "label_events", label_events)
    return seen


@pytest.fixture
def immature_pead_labels(monkeypatch):
    def label_events(events, frames, entry):
        labels = pd.DataFrame({"direction": ["LONG"], "is_leg": [True], "r_cost": [0.5]})
        return labels, {"immature": 8, "legs": {"immature": {"LONG": 3, "SHORT": 0}}}

    monkeypatch.setattr(scorecard_module, "label_events", label_events)


def lane(card: LaneScorecard, name: str):
    return {row.lane: row for row in card.lanes}[name]


async def test_lane_numbers_are_exact(temp_db, config, monkeypatch, fake_pead_labels):
    ids = await seed_signals(temp_db)
    route_triggers(temp_db, monkeypatch, ids)
    await seed_journals(temp_db)
    await seed_equity(temp_db)
    bars, sip = native_bars(), sip_bars()

    card = await build_scorecard(temp_db, config, now=NOW, bars=bars, sip=lambda: sip)

    assert card.window_end == NOW
    assert card.window_start == datetime(2026, 11, 8, 5, 0, tzinfo=UTC)  # 7 ET days ending today: midnight ET
    assert [row.lane for row in card.lanes] == ["native", "pead", "insider_watch"]

    native = lane(card, "native")
    assert (native.sent, native.attempted_auto, native.attempted_tap, native.filled) == (4, 2, 1, 3)
    assert native.closed == 3
    assert native.realized_r_sum == pytest.approx(2.0)  # +2R, -1R and the +1R opened last month, closed in the window
    assert native.record_closed == 4
    assert native.record_mean_r == pytest.approx((2.0 - 1.0 + 1.5 + 1.0) / 4)
    assert native.open_positions == 1
    assert native.cf_mature == 1 and native.cf_total == 1 and native.cf_mean_r is not None and native.cf_mean_r > 1.0
    assert native.note is None
    assert native.refused == 0

    pead = lane(card, "pead")
    assert (pead.sent, pead.attempted_auto, pead.attempted_tap, pead.filled, pead.closed) == (2, 1, 0, 1, 1)
    assert pead.realized_r_sum == pytest.approx(-0.5)
    assert (pead.record_closed, pead.record_mean_r, pead.open_positions) == (1, pytest.approx(-0.5), 0)
    assert pead.cf_mature == 2 and pead.cf_total == 2 and pead.cf_mean_r == pytest.approx(0.2)
    assert pead.cf_mean_excess_r is None  # the PEAD record has no SPY control
    assert pead.note is None
    assert {call[0] for call in sip.calls} >= {"PPP", "WW1", "SPY"}

    watch = lane(card, "insider_watch")
    assert (watch.sent, watch.attempted_auto, watch.attempted_tap, watch.filled, watch.closed) == (2, 1, 1, 2, 0)
    assert watch.realized_r_sum == 0.0
    assert (watch.record_closed, watch.record_mean_r, watch.open_positions) == (0, None, 2)
    assert watch.cf_mature == 1 and watch.cf_total == 1 and watch.cf_mean_r is not None and watch.cf_mean_r > 0

    assert card.account is not None
    assert (card.account.start_equity, card.account.end_equity) == (100000.0, 101500.5)


async def test_pead_total_adds_the_long_legs_still_immature(temp_db, config, immature_pead_labels):
    await seed_journals(temp_db)

    card = await build_scorecard(temp_db, config, now=NOW, bars=native_bars(), sip=lambda: sip_bars())

    pead = lane(card, "pead")
    assert (pead.cf_mature, pead.cf_total) == (1, 4)  # 3 LONG-leg immature, not the 8 both-direction misses


async def test_refused_counts_attempts_that_ended_with_no_order(temp_db, config, monkeypatch):
    async with temp_db.session_factory() as session, session.begin():
        failed = _signal(temp_db, contract="RRR", strategy="BREAKOUT", ts=_at(12), status="FAILED")
        failed_tap = _signal(temp_db, contract="SSS", strategy="BREAKOUT", ts=_at(12), status="FAILED")
        filled = _signal(
            temp_db, contract="TTT", strategy="BREAKOUT", ts=_at(12), status="EXECUTED", executed_at=_at(12)
        )
        session.add_all([failed, failed_tap, filled])
    triggers = {failed.id: EntryTrigger.AUTO, failed_tap.id: EntryTrigger.TAP, filled.id: EntryTrigger.AUTO}

    async def entry_work_states(signal_ids):
        return {i: ("done", triggers[i]) for i in signal_ids if i in triggers}

    monkeypatch.setattr(temp_db.workflows, "entry_work_states", entry_work_states)

    card = await build_scorecard(
        temp_db, config, now=NOW, bars=FakeBars(pd.DataFrame()), sip=lambda: FakeBars(pd.DataFrame())
    )

    native = lane(card, "native")
    assert (native.sent, native.attempted_auto, native.attempted_tap) == (3, 2, 1)
    assert (native.refused, native.filled) == (2, 1)


async def test_a_lane_with_no_cards_still_builds(temp_db, config):
    bars, sip = FakeBars(pd.DataFrame()), FakeBars(pd.DataFrame())

    card = await build_scorecard(temp_db, config, now=NOW, bars=bars, sip=lambda: sip)

    for row in card.lanes:
        assert (row.sent, row.attempted_auto, row.attempted_tap, row.filled, row.closed) == (0, 0, 0, 0, 0)
        assert (row.realized_r_sum, row.record_closed, row.record_mean_r, row.open_positions) == (0.0, 0, None, 0)
        assert (
            row.refused == 0
            and row.cf_mature == 0
            and row.cf_total == 0
            and row.cf_mean_r is None
            and row.cf_mean_excess_r is None
        )
        assert row.note is None
    assert card.account is None
    assert bars.calls == [] and sip.calls == []


async def test_a_bar_failure_voids_that_lanes_counterfactual_and_names_the_type_only(
    temp_db, config, monkeypatch, fake_pead_labels
):
    ids = await seed_signals(temp_db)
    route_triggers(temp_db, monkeypatch, ids)
    await seed_journals(temp_db)

    card = await build_scorecard(
        temp_db,
        config,
        now=NOW,
        bars=FakeBars(error=TimeoutError(SECRET)),
        sip=lambda: FakeBars(error=ConnectionError(SECRET)),
    )

    native, pead, watch = (lane(card, name) for name in ("native", "pead", "insider_watch"))
    for row, error_type in ((native, "TimeoutError"), (pead, "ConnectionError"), (watch, "ConnectionError")):
        assert (row.cf_mature, row.cf_total, row.cf_mean_r, row.cf_mean_excess_r) == (None, None, None, None)
        assert row.note is not None and error_type in row.note and SECRET not in row.note
    # Activity and the realized record do not depend on bars.
    assert native.sent == 4 and native.record_closed == 4
    assert pead.sent == 2 and watch.sent == 2


async def test_an_unexpected_failure_in_a_lane_is_contained(temp_db, config, monkeypatch, fake_pead_labels):
    await seed_journals(temp_db)

    async def broken(*args, **kwargs):
        raise KeyError(SECRET)

    monkeypatch.setattr(scorecard_module, "pead_probe_record", broken)

    card = await build_scorecard(temp_db, config, now=NOW, bars=native_bars(), sip=lambda: sip_bars())

    pead = lane(card, "pead")
    assert pead.cf_mature is None and pead.cf_total is None and pead.note is not None
    assert "KeyError" in pead.note and SECRET not in pead.note
    assert lane(card, "insider_watch").cf_mature == 1


async def test_window_dates_are_new_york_days(temp_db, config):
    card = await build_scorecard(
        temp_db,
        config,
        now=NOW + timedelta(hours=20),
        activity_days=3,
        bars=FakeBars(pd.DataFrame()),
        sip=lambda: FakeBars(pd.DataFrame()),
    )
    # 2026-11-15 11:00 ET: three ET days are the 13th, 14th and 15th.
    assert card.window_start == datetime(2026, 11, 13, 5, 0, tzinfo=UTC)
