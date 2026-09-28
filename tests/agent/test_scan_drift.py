"""PEAD drift cards inside the 10:35 New York suggestion scan (catalog paper probe).

Only the scheduled suggestion scan whose configured time is the entry's decision time
prepares drift candidates. They have their own derived per-session budget (outside the
native budget), skip ``setup_quality`` ranking and the shadow-ranker journal, never
spend the LLM evaluation budget, and every decision is journaled as one
``pead_decision`` event; an unavailable session queues one debounced notice.
"""

import asyncio
import dataclasses
import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import AprioriConfig, ScanBudget
from agentic_trader.constants import SignalStatus, SystemStateKey
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.screeners.earnings_drift import (
    PEAD_DECISION_TIME_ET,
    PEAD_STRATEGY_ID,
    DriftPreparation,
    EarningsDriftService,
)
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_dynamic_universe_scan import (  # noqa: F401  (dailies/dynamic_desk are fixtures)
    QUALITIES,
    STATIC,
    dailies,
    dynamic_desk,
)
from tests.agent.test_scan_budget import (  # noqa: F401  (budget_desk is a fixture)
    budget_desk,
    candidate,
    evaluation,
)


VERSION = "apriori:pead:v2:long:abc"
POLICY = {"kind": "apriori_bracket_v1"}


class FakeDrift:
    decision_time_et = "10:35"
    holding_sessions = 20

    def __init__(self, prep, candidates):
        self.prep, self.candidates, self.prepare_calls = prep, candidates, 0
        self.owned: dict[str, str] | None = None
        self.delay = 0.0

    def applies(self, scheduled):
        return scheduled == "10:35"

    async def prepare(self, *, now, snapshot, owned):
        self.prepare_calls += 1
        self.owned = dict(owned)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.prep, BaseException):
            raise self.prep
        return self.prep

    def live_version(self, snapshot):
        probes = getattr(snapshot, "catalog_probes", ())
        return probes[0] if probes else None

    def candidate(self, event, data, prep):
        return self.candidates.get(event["symbol"], "no intraday price")


def today_ny() -> date:
    return datetime.now(ET_TZ).date()


def drift_event(symbol: str = "WINR") -> dict:
    return {
        "symbol": symbol,
        "session": today_ny().isoformat(),
        "surprise_pct": 10.0,
        "z": 2.0,
        "report_date": "2026-10-26",
        "atr": 1.0,
    }


def drift_candidate(symbol: str = "WINR"):
    return candidate(symbol, 0.0, strategy=PEAD_STRATEGY_ID).model_copy(
        update={
            "timeframe": "1d",
            "catalog_event": drift_event(symbol),
            "alpha_version": VERSION,
            "alpha_policy": POLICY,
            "probe": True,
        }
    )


def ok_prep(*symbols: str) -> DriftPreparation:
    return DriftPreparation(
        "ok",
        today_ny(),
        version_id=VERSION,
        policy=POLICY,
        events=tuple(drift_event(symbol) for symbol in symbols or ("WINR",)),
        counts={"rows": 3, "events": 1, "reasons": {"surprise": 2}},
        report_date="2026-10-26",
        page_sha256="ab" * 32,
        time_exit_at="2026-11-24T20:45:00+00:00",
    )


@pytest.fixture
def drift_desk(budget_desk):  # noqa: F811
    budget_desk.earnings_drift = FakeDrift(ok_prep(), {"WINR": drift_candidate()})
    return budget_desk


async def suggestion_scan(desk, **kwargs):
    arguments = {
        "use_llm": True,
        "dry_run": False,
        "budget": ScanBudget.FULL,
        "shadow_evidence": True,
        "scheduled_time_et": "10:35",
        **kwargs,
    }
    return await desk.run_scan(**arguments)


async def _plant_signal(db, contract, when, strategy="TREND_PULLBACK"):
    """Write one recorded signal at an exact timestamp, bypassing run_scan."""
    async with db.session_factory() as session, session.begin():
        session.add(
            SignalRecord(
                timestamp=when,
                contract=contract,
                strategy=strategy,
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=2.0,
                environment=db.environment,
                execution_mode=db.execution_mode,
            )
        )


async def _drift_events(db) -> list[dict]:
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.PEAD_DECISION]


def _outcomes(event: dict) -> dict[str, str]:
    return {item["symbol"]: item["outcome"] for item in event["payload"]["events"]}


async def _signals(db) -> list[dict]:
    return await db.get_recent_signals(limit=50)


async def _unavailable_notices(db) -> list[dict]:
    queued = [e for e in await db.workflows.events() if e["kind"] == EventKind.NOTIFICATION_QUEUED]
    return [e for e in queued if e["payload"]["dedup_key"] == f"pead-unavailable/{today_ny().isoformat()}"]


async def test_drift_card_is_sent_beside_the_native_budget(drift_desk, temp_db, app_config):
    app_config.scan.max_llm_evaluations_per_scan = 1  # the native winner spends it; drift never does

    summary = await suggestion_scan(drift_desk)

    signals = await _signals(temp_db)
    assert sorted(s["contract"] for s in signals) == ["DDD", "WINR"]
    [winr] = [s for s in signals if s["contract"] == "WINR"]
    provenance = winr["decision_provenance"]
    assert provenance["pead_event"]["symbol"] == "WINR"
    assert provenance["pead_event"]["session"] == today_ny().isoformat()
    assert provenance["rank"] == 1 and provenance["candidates_considered"] == 1
    assert provenance["shadow_ranker"] is None
    assert winr["strategy"] == PEAD_STRATEGY_ID and winr["alpha_version"] == VERSION
    assert summary["drift"] == {"status": "ok", "reason": None, "events": 1, "sent": 1}
    assert summary["approved"] == 5  # native deterministic approvals only
    assert summary["sent"] == 2

    # The drift candidate got the caller's LLM pass (commentary), outside the LLM budget.
    llm_contracts = [
        c.args[0].contract for c in drift_desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")
    ]
    assert llm_contracts == ["DDD", "WINR"]
    # A drift-only name is fetched for its drift candidate only: no strategy scan, no alpha shadow.
    scanned = [c.args[0].contract for c in drift_desk.strategy_engine.scan_contract.call_args_list]
    assert "WINR" not in scanned
    assert "WINR" not in drift_desk.last_scan_summary["fetch_failed"]
    assert drift_desk.last_scan_summary["scanned"] == 5

    [event] = await _drift_events(temp_db)
    assert event["stream"] == f"scan/{today_ny().isoformat()}"
    payload = event["payload"]
    assert payload["scan_id"] == summary["scan_id"]
    assert payload["status"] == "ok" and payload["version_id"] == VERSION
    assert payload["session"] == today_ny().isoformat()
    assert payload["time_exit_at"] == "2026-11-24T20:45:00+00:00"
    assert payload["counts"] == {"rows": 3, "events": 1, "reasons": {"surprise": 2}}
    assert _outcomes(event) == {"WINR": "sent"}

    # The card's durable notification carries the drift facts and is deliverable as-is.
    notices = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    [card] = [
        n.payload["arguments"]
        for n in notices
        if n.payload["kind"] == NotificationKind.SIGNAL and n.payload["arguments"]["strategy"] == PEAD_STRATEGY_ID
    ]
    assert card["drift"] == {
        "surprise_pct": 10.0,
        "z": 2.0,
        "report_date": "2026-10-26",
        "holding_sessions": 20,
        "time_exit_at": "2026-11-24T20:45:00+00:00",
    }
    assert card["probe_risk_cap"] == app_config.alpha_pipeline.probe_risk_dollars
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **card)
    assert await _unavailable_notices(temp_db) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scheduled_time_et": "14:35"},
        {"scheduled_time_et": None},
        {"symbols": ["AAA"]},
        {"dry_run": True},
        {"shadow_evidence": False},
        {"timeframe": "4h"},
    ],
    ids=["other-time", "unscheduled", "restricted", "dry-run", "no-shadow-evidence", "timeframe"],
)
async def test_drift_runs_only_at_the_decision_time(drift_desk, temp_db, kwargs):
    await suggestion_scan(drift_desk, **kwargs)
    assert drift_desk.earnings_drift.prepare_calls == 0
    assert [s for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID] == []
    assert await _drift_events(temp_db) == []
    assert "drift" not in drift_desk.last_scan_summary


async def test_drift_budget_is_derived_after_a_restart(drift_desk, temp_db):
    await _plant_signal(
        temp_db, "PRIOR", drift_desk.session_start_et() + timedelta(minutes=1), strategy=PEAD_STRATEGY_ID
    )

    await suggestion_scan(drift_desk)

    contracts = [s["contract"] for s in await _signals(temp_db)]
    assert "WINR" not in contracts
    assert "DDD" in contracts  # native cards still send
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "drift budget spent"}
    assert drift_desk.last_scan_summary["drift"]["sent"] == 0


async def test_native_budget_excludes_drift_signals(drift_desk, temp_db, app_config):
    app_config.apriori.max_drift_cards_per_session = 3
    for minutes in (1, 2):
        await _plant_signal(
            temp_db, f"PRIOR{minutes}", drift_desk.session_start_et() + timedelta(minutes=minutes), PEAD_STRATEGY_ID
        )

    await suggestion_scan(drift_desk)
    await suggestion_scan(drift_desk)

    native = [s["contract"] for s in await _signals(temp_db) if s["strategy"] != PEAD_STRATEGY_ID]
    assert native == ["DDD", "DDD"]  # max_cards_per_session=2 untouched by the drift rows
    drift = [s["contract"] for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID]
    assert sorted(drift) == ["PRIOR1", "PRIOR2", "WINR"]  # the third drift card, then spent
    first, second = await _drift_events(temp_db)
    assert _outcomes(first) == {"WINR": "sent"}
    assert _outcomes(second) == {"WINR": "drift budget spent"}


async def test_owned_symbols_reach_prepare(drift_desk, temp_db):
    signal_id = await temp_db.record_signal(
        contract="WINR",
        direction="LONG",
        strategy="TREND_PULLBACK",
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=56.0,
        risk_dollars=100.0,
        quantity=50.0,
        asset_class="EQUITY",
    )
    await temp_db.update_signal_execution(
        signal_id=signal_id,
        broker_order_id="ord-1",
        fill_price=50.0,
        status=SignalStatus.EXECUTED,
        quantity=50.0,
        notional_value=2500.0,
        risk_dollars=100.0,
    )
    drift_desk.alpha_repository.snapshot.return_value = RegistrySnapshot(
        0,
        (SimpleNamespace(alpha_id="alpha_x", eligible_symbols=("QQQ",), timeframe="4h"),),
        (),
        probe=(SimpleNamespace(alpha_id="probe_y", eligible_symbols=("IWM", "WINR"), timeframe="4h"),),
    )
    drift_desk.monitor_positions = AsyncMock(return_value=0)  # no broker behind the open position

    await suggestion_scan(drift_desk)

    assert drift_desk.earnings_drift.owned == {
        "WINR": "open position",
        "QQQ": "owned by alpha_x",
        "IWM": "owned by probe_y",
    }


async def test_unavailable_session_sends_one_notice_and_native_cards_continue(drift_desk, temp_db):
    drift_desk.earnings_drift.prep = DriftPreparation(
        "unavailable", today_ny(), reason="calendar_empty", report_date="2026-10-26"
    )

    await suggestion_scan(drift_desk)
    await suggestion_scan(drift_desk)

    [notice] = await _unavailable_notices(temp_db)
    [item] = [
        n
        for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == NotificationKind.MESSAGE
    ]
    text = item.payload["arguments"]["text"]
    assert "calendar_empty" in text and "2026-10-26" in text
    assert notice["stream"] == f"notification/{item.id}"
    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD", "DDD"]
    events = await _drift_events(temp_db)
    assert [e["payload"]["status"] for e in events] == ["unavailable", "unavailable"]
    assert events[0]["payload"]["events"] == []
    assert drift_desk.last_scan_summary["drift"] == {
        "status": "unavailable",
        "reason": "calendar_empty",
        "events": 0,
        "sent": 0,
    }


async def test_a_failing_preparation_is_an_unavailable_session(drift_desk, temp_db):
    drift_desk.earnings_drift.prep = RuntimeError("calendar exploded")

    summary = await suggestion_scan(drift_desk)

    assert summary["drift"]["status"] == "unavailable"
    assert summary["drift"]["reason"] == "error: RuntimeError"
    assert len(await _unavailable_notices(temp_db)) == 1
    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD"]


async def test_drift_candidate_rejected_by_a_deterministic_gate_is_journaled(drift_desk, temp_db):
    async def evaluate(cand, **kwargs):
        return evaluation(cand, approved=cand.contract != "WINR")

    drift_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)

    await suggestion_scan(drift_desk)

    assert [s for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID] == []
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "rejected: risk"}


async def test_a_drift_event_without_intraday_data_is_journaled(drift_desk, temp_db):
    drift_desk.earnings_drift.prep = ok_prep("WINR", "NODATA")

    await suggestion_scan(drift_desk)

    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "sent", "NODATA": "no intraday price"}


async def test_a_failing_drift_candidate_never_stops_the_scan(drift_desk, temp_db):
    def explode(event, data, prep):
        raise ValueError("bad bars")

    drift_desk.earnings_drift.candidate = explode

    summary = await suggestion_scan(drift_desk)

    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD"]
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "error: ValueError"}
    assert summary["drift"]["sent"] == 0


async def test_a_candidate_reason_is_journaled_as_the_event_outcome(drift_desk, temp_db):
    drift_desk.earnings_drift.candidates = {"WINR": "stale intraday price"}

    await suggestion_scan(drift_desk)

    assert [s for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID] == []
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "stale intraday price"}


# --- F2: open drift positions are capped ----------------------------------------------------


async def _open_position(db, contract: str, strategy: str = PEAD_STRATEGY_ID) -> None:
    signal_id = await db.record_signal(
        contract=contract,
        direction="LONG",
        strategy=strategy,
        entry_price=50.0,
        stop_loss=48.0,
        take_profit=56.0,
        risk_dollars=100.0,
        quantity=1.0,
        asset_class="EQUITY",
    )
    await db.update_signal_execution(
        signal_id=signal_id,
        broker_order_id=f"ord-{contract}",
        fill_price=50.0,
        status=SignalStatus.EXECUTED,
        quantity=1.0,
        notional_value=50.0,
        risk_dollars=100.0,
    )


def _fetched(desk) -> list[str]:
    return [call.args[0] for call in desk.data_fetcher.fetch_data.call_args_list]


def test_the_open_drift_position_cap_defaults_to_four():
    assert AprioriConfig().max_open_drift_positions == 4


async def test_at_the_open_drift_position_cap_no_drift_card_is_sent(drift_desk, temp_db, app_config):
    app_config.apriori.max_drift_cards_per_session = 10  # isolate the position cap from the card budget
    app_config.apriori.max_open_drift_positions = 2
    for symbol in ("OLD1", "OLD2"):
        await _open_position(temp_db, symbol)
    drift_desk.monitor_positions = AsyncMock(return_value=0)  # no broker behind the open positions
    drift_desk.earnings_drift.prep = ok_prep("WINR", "ZETA")

    summary = await suggestion_scan(drift_desk)

    recorded = [s["contract"] for s in await _signals(temp_db)]
    assert "WINR" not in recorded and "ZETA" not in recorded
    assert "DDD" in recorded  # native cards unaffected
    [event] = await _drift_events(temp_db)
    payload = event["payload"]
    assert payload["status"] == "ok"
    assert payload["open_drift_positions"] == 2
    assert _outcomes(event) == {"WINR": "drift position cap reached", "ZETA": "drift position cap reached"}
    assert summary["drift"]["sent"] == 0
    assert "WINR" not in _fetched(drift_desk)  # a capped session reads no drift-only bars


async def test_below_the_open_drift_position_cap_the_drift_card_is_sent(drift_desk, temp_db, app_config):
    app_config.apriori.max_drift_cards_per_session = 10
    app_config.apriori.max_open_drift_positions = 2
    await _open_position(temp_db, "OLD1")
    await _open_position(temp_db, "NATV", strategy="TREND_PULLBACK")  # a native position never counts
    drift_desk.monitor_positions = AsyncMock(return_value=0)

    await suggestion_scan(drift_desk)

    assert "WINR" in {s["contract"] for s in await _signals(temp_db)}
    [event] = await _drift_events(temp_db)
    assert event["payload"]["open_drift_positions"] == 1
    assert _outcomes(event) == {"WINR": "sent"}


# --- F12/F13: preparation wall time and owner-skipped events --------------------------------


async def test_the_decision_records_the_preparation_wall_time(drift_desk, temp_db):
    drift_desk.earnings_drift.delay = 0.2

    await suggestion_scan(drift_desk)

    [event] = await _drift_events(temp_db)
    elapsed = event["payload"]["elapsed_seconds"]
    assert 0.2 <= elapsed < 5.0
    assert elapsed == round(elapsed, 1)


async def test_owner_skipped_events_are_journaled_with_their_full_documents(drift_desk, temp_db):
    owned = drift_event("OWND")
    drift_desk.earnings_drift.prep = dataclasses.replace(
        ok_prep("WINR"), skipped={"OWND": "open position"}, skipped_events=(owned,)
    )

    await suggestion_scan(drift_desk)

    [event] = await _drift_events(temp_db)
    payload = event["payload"]
    assert _outcomes(event) == {"WINR": "sent", "OWND": "skipped: open position"}
    assert [e for e in payload["events"] if e["symbol"] == "OWND"] == [{**owned, "outcome": "skipped: open position"}]
    assert payload["skipped"] == {"OWND": "open position"}
    assert "OWND" not in _fetched(drift_desk)  # a skipped event is never fetched or carded


# --- F11: a gated decision scan still journals its PEAD decision ------------------------------


async def _gate(desk, db, gate: str) -> str:
    if gate == "halt":
        await db.set_state(SystemStateKey.TRADING_HALTED, "true")
        await db.set_state(SystemStateKey.TRADING_HALT_REASON, "operator kill")
        return "trading halted: operator kill"
    desk.calendar.is_in_lockout_window.return_value = (
        True,
        SimpleNamespace(title="CPI release", timestamp=datetime(2026, 10, 28, 12, 30, tzinfo=UTC)),
    )
    return "macro lockout: CPI release"


@pytest.mark.parametrize("gate", ["halt", "lockout"])
async def test_a_gated_decision_scan_journals_a_skipped_pead_decision(drift_desk, temp_db, gate):
    drift_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()
    reason = await _gate(drift_desk, temp_db, gate)

    assert await suggestion_scan(drift_desk) is None

    [event] = await _drift_events(temp_db)
    payload = event["payload"]
    assert (payload["status"], payload["reason"]) == ("skipped", reason)
    assert payload["version_id"] == VERSION and payload["events"] == []
    assert payload["session"] == today_ny().isoformat()
    assert event["stream"] == f"scan/{today_ny().isoformat()}"
    assert drift_desk.earnings_drift.prepare_calls == 0
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []  # no notice


@pytest.mark.parametrize("case", ["no-catalog-probe", "other-time", "dry-run", "restricted"])
async def test_a_gated_scan_journals_no_pead_decision_otherwise(drift_desk, temp_db, case):
    drift_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()
    await _gate(drift_desk, temp_db, "halt")
    kwargs = {}
    if case == "no-catalog-probe":
        drift_desk.alpha_repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    elif case == "other-time":
        kwargs["scheduled_time_et"] = "14:35"
    elif case == "dry-run":
        kwargs["dry_run"] = True
    else:
        kwargs["symbols"] = ["AAA"]

    await suggestion_scan(drift_desk, **kwargs)

    assert await _drift_events(temp_db) == []


async def test_drift_is_absent_from_the_shadow_ranking_journal(drift_desk, temp_db):
    await suggestion_scan(drift_desk)

    [ranked] = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    contracts = [c["contract"] for c in ranked["payload"]["candidates"]]
    assert "WINR" not in contracts
    assert sorted(contracts) == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def _copilot(app_config, temp_db, mock_notifier, **kwargs) -> TradingCopilot:
    return TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=True),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
        **kwargs,
    )


def test_the_drift_source_is_built_from_config(app_config, temp_db, mock_notifier):
    app_config.contracts = {
        "SPY": SimpleNamespace(name="SPY", ticker="SPY", asset_class="EQUITY"),
        "/MES": SimpleNamespace(name="MES", ticker="/MES", asset_class="FUTURES"),
        "AAPL": SimpleNamespace(name="AAPL", ticker="AAPL", asset_class="EQUITY"),
    }
    copilot = _copilot(app_config, temp_db, mock_notifier)
    assert isinstance(copilot.earnings_drift, EarningsDriftService)
    assert copilot.earnings_drift.static_symbols == ("AAPL", "SPY")
    assert copilot.earnings_drift.decision_time_et == "10:35"
    assert copilot.earnings_drift.bars.feed == "sip"


def test_the_drift_source_is_injectable_or_disabled(app_config, temp_db, mock_notifier):
    fake = FakeDrift(ok_prep(), {})
    assert _copilot(app_config, temp_db, mock_notifier, earnings_drift=fake).earnings_drift is fake
    app_config.apriori.enabled = False
    assert _copilot(app_config, temp_db, mock_notifier).earnings_drift is None


def _unscheduled_warnings(caplog) -> list:
    return [r for r in caplog.records if r.__dict__.get("event") == "pead_decision_time_unscheduled"]


def test_an_unscheduled_decision_time_is_warned_at_construction(app_config, temp_db, mock_notifier, caplog):
    app_config.scheduler.suggestion_scan_times_et = ["09:45", "14:35"]
    with caplog.at_level("WARNING", logger="copilot"):
        copilot = _copilot(app_config, temp_db, mock_notifier)
    assert isinstance(copilot.earnings_drift, EarningsDriftService)
    [record] = _unscheduled_warnings(caplog)
    message = record.getMessage()
    assert "10:35" in message and "09:45, 14:35" in message


def test_a_scheduled_decision_time_is_not_warned(app_config, temp_db, mock_notifier, caplog):
    assert "10:35" in app_config.scheduler.suggestion_scan_times_et
    with caplog.at_level("WARNING", logger="copilot"):
        _copilot(app_config, temp_db, mock_notifier)
    assert _unscheduled_warnings(caplog) == []


def test_a_missing_entry_leaves_no_drift_source(app_config, temp_db, mock_notifier, tmp_path):
    app_config.apriori.pead_entry_path = str(tmp_path / "missing.json")
    copilot = _copilot(app_config, temp_db, mock_notifier)
    assert copilot.earnings_drift is None
    assert copilot._earnings_drift_error.startswith("FileNotFoundError")


# --- R1: a drift card is never a dynamic name ---------------------------------------------


def _native_setups_on(desk, symbols) -> None:
    desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: (
        [candidate(data.contract, QUALITIES[data.contract])] if data.contract in symbols else []
    )


async def test_a_drift_card_on_a_dynamic_member_follows_a_native_dynamic_card(dynamic_desk, temp_db):  # noqa: F811
    """NEWA (native, dynamic) takes the dynamic group's card; the drift card on dynamic
    member NEWB is still sent, untagged, because it is not a dynamic name."""
    dynamic_desk.earnings_drift = FakeDrift(ok_prep("NEWB"), {"NEWB": drift_candidate("NEWB")})

    await suggestion_scan(dynamic_desk)

    cards = {(s["contract"], s["strategy"]): s["decision_provenance"] for s in await _signals(temp_db)}
    assert set(cards) == {("NEWA", "TREND_PULLBACK"), ("NEWB", PEAD_STRATEGY_ID)}
    assert cards[("NEWA", "TREND_PULLBACK")]["dynamic"] is True
    drift = cards[("NEWB", PEAD_STRATEGY_ID)]
    assert "dynamic" not in drift and "dynamic_source" not in drift
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"NEWB": "sent"}
    assert "dynamic" not in event["payload"]["events"][0]


async def test_a_sent_drift_card_does_not_consume_the_dynamic_group(dynamic_desk, temp_db):  # noqa: F811
    # 10:35: only the static names have native setups; the drift card lands on dynamic member NEWA.
    dynamic_desk.earnings_drift = FakeDrift(ok_prep("NEWA"), {"NEWA": drift_candidate("NEWA")})
    _native_setups_on(dynamic_desk, STATIC)
    await suggestion_scan(dynamic_desk)
    first = sorted((s["contract"], s["strategy"]) for s in await _signals(temp_db))
    assert first == [("DDD", "TREND_PULLBACK"), ("NEWA", PEAD_STRATEGY_ID)]

    # 14:35: NEWA's own native (dynamic) setup may still take the dynamic group's card.
    _native_setups_on(dynamic_desk, QUALITIES)
    await suggestion_scan(dynamic_desk, scheduled_time_et="14:35")
    native_newa = [s for s in await _signals(temp_db) if s["contract"] == "NEWA" and s["strategy"] != PEAD_STRATEGY_ID]
    assert len(native_newa) == 1 and native_newa[0]["decision_provenance"]["dynamic"] is True


async def test_a_dynamic_tagged_drift_row_never_counts_toward_the_dynamic_group(dynamic_desk, temp_db):  # noqa: F811
    await temp_db.record_signal(
        "OLDP", PEAD_STRATEGY_ID, "LONG", 100, 98, 104, 2, decision_provenance={"dynamic": True, "rank": 1}
    )

    await suggestion_scan(dynamic_desk, scheduled_time_et="14:35")

    assert "NEWA" in {s["contract"] for s in await _signals(temp_db)}


async def test_a_sector_group_cap_still_blocks_a_drift_card(drift_desk, temp_db, app_config):
    app_config.portfolio.correlation_groups = {**app_config.portfolio.correlation_groups, "sector_w": ["WINR", "ZZZ"]}
    await temp_db.record_signal("ZZZ", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"rank": 1})

    await suggestion_scan(drift_desk)

    assert [s for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID] == []
    assert "DDD" in {s["contract"] for s in await _signals(temp_db)}  # native cards unaffected
    [event] = await _drift_events(temp_db)
    assert _outcomes(event) == {"WINR": "correlation group already has a card this session"}


# --- R2: a slow preparation never holds the native cards ----------------------------------


async def test_a_slow_preparation_times_out_as_an_unavailable_session(drift_desk, temp_db, monkeypatch):
    monkeypatch.setattr(copilot_module, "DRIFT_PREPARE_TIMEOUT_SECONDS", 0.05)
    drift_desk.earnings_drift.delay = 5.0

    summary = await suggestion_scan(drift_desk)

    assert summary["drift"] == {"status": "unavailable", "reason": "timeout", "events": 0, "sent": 0}
    assert len(await _unavailable_notices(temp_db)) == 1
    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD"]


def test_the_drift_prepare_bound_mirrors_the_dynamic_universe_bound():
    assert copilot_module.DRIFT_PREPARE_TIMEOUT_SECONDS == 60


# --- R3: an enrolled probe is never silently dead -----------------------------------------


def _catalog_snapshot() -> RegistrySnapshot:
    return RegistrySnapshot(0, (), (), catalog_probes=({"version_id": VERSION, "alpha_id": PEAD_STRATEGY_ID},))


@pytest.fixture
def broken_drift_desk(budget_desk):  # noqa: F811
    budget_desk.earnings_drift = None
    budget_desk._earnings_drift_error = "FileNotFoundError: [Errno 2] No such file or directory: 'pead-v2.json'"
    budget_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()
    return budget_desk


async def test_an_unavailable_drift_service_is_journaled_while_a_catalog_probe_is_live(broken_drift_desk, temp_db):
    summary = await suggestion_scan(broken_drift_desk)

    [event] = await _drift_events(temp_db)
    payload = event["payload"]
    assert payload["status"] == "unavailable" and payload["events"] == []
    assert payload["reason"].startswith("service unavailable: FileNotFoundError")
    assert payload["session"] == today_ny().isoformat()
    assert len(await _unavailable_notices(temp_db)) == 1
    assert summary["drift"]["status"] == "unavailable"
    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD"]  # native cards unaffected


async def test_a_halted_scan_journals_a_skipped_decision_even_without_a_drift_service(broken_drift_desk, temp_db):
    reason = await _gate(broken_drift_desk, temp_db, "halt")

    await suggestion_scan(broken_drift_desk)

    [event] = await _drift_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["reason"]) == ("skipped", reason)
    assert event["payload"]["version_id"] == VERSION
    assert await _unavailable_notices(temp_db) == []


@pytest.mark.parametrize("case", ["no-catalog-probe", "other-time", "disabled", "no-error"])
async def test_an_unavailable_drift_service_is_silent_otherwise(broken_drift_desk, temp_db, app_config, case):
    kwargs = {}
    if case == "no-catalog-probe":
        broken_drift_desk.alpha_repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    elif case == "other-time":
        kwargs["scheduled_time_et"] = "14:35"
    elif case == "disabled":
        app_config.apriori.enabled = False
    else:
        broken_drift_desk._earnings_drift_error = None

    await suggestion_scan(broken_drift_desk, **kwargs)

    assert await _drift_events(temp_db) == []
    assert await _unavailable_notices(temp_db) == []
    assert "drift" not in broken_drift_desk.last_scan_summary


def test_the_fallback_decision_time_is_the_frozen_entry_s(app_config):
    entry = load_pead_entry(Path(app_config.apriori.pead_entry_path)).entry
    assert entry.trade.decision_time_et == PEAD_DECISION_TIME_ET
