"""PEAD drift cards inside the 10:35 New York suggestion scan (catalog paper probe).

Only the scheduled suggestion scan whose configured time is the entry's decision time
prepares drift candidates. They have their own derived per-session budget (outside the
native budget), skip ``setup_quality`` ranking and the shadow-ranker journal, never
spend the LLM evaluation budget, and every decision is journaled as one
``pead_decision`` event; an unavailable session queues one debounced notice.
"""

import inspect
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import ScanBudget
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID, DriftPreparation, EarningsDriftService
from agentic_trader.storage.models import SignalRecord
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

    def applies(self, scheduled):
        return scheduled == "10:35"

    async def prepare(self, *, now, snapshot, owned):
        self.prepare_calls += 1
        self.owned = dict(owned)
        if isinstance(self.prep, BaseException):
            raise self.prep
        return self.prep

    def candidate(self, event, data, prep):
        return self.candidates.get(event["symbol"])


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


def test_a_missing_entry_leaves_no_drift_source(app_config, temp_db, mock_notifier, tmp_path):
    app_config.apriori.pead_entry_path = str(tmp_path / "missing.json")
    copilot = _copilot(app_config, temp_db, mock_notifier)
    assert copilot.earnings_drift is None
    assert copilot._earnings_drift_error.startswith("FileNotFoundError")
