"""Paper auto-entry: the marker at record time and the sweep that enters marked cards.

A native card of a scheduled suggestion scan records ``decision_provenance["auto_entry"]`` and
its card payload ``auto_entry: "placing"``. ``TradingCopilot.auto_enter_pending`` then makes
exactly one attempt per marked, still-``PENDING`` card through ``execute_signal_by_id(...,
trigger=EntryTrigger.AUTO)``, derives the outcome from the signal it re-reads after the call,
and journals one ``auto_entry_attempted`` event with its follow-up in one transaction.
"""

import asyncio
import inspect
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select, update

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.earnings import EarningsLookup
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.broker.base import OrderResult
from agentic_trader.constants import AssetClass, ExecutionMode, SignalStatus
from agentic_trader.execution.auto_entry import (
    AUTO_ENTRY_KEY,
    AUTO_ENTRY_MARKER,
    AutoEntryAttempt,
    attempt_reason,
    sweep_reason,
)
from agentic_trader.execution.durable import EntryTrigger, EventKind, NotificationKind, ScanTrigger, WorkKind
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.execution.lifetime_policy import NATIVE_ENTRY_LIFETIME_KEY, NativeEntryLifetimePolicy
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import AUTO_REFUSED_LINE, TelegramNotifier, format_alert_card
from agentic_trader.presentation.auto_entry_digest import format_auto_entry_digest
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import DomainEventRecord, SignalRecord, WorkItemRecord
from agentic_trader.storage.workflow import WorkflowStore
from tests.agent.test_card_freshness_tap import original_evaluation, session_info
from tests.agent.test_insider_watch import watch_desk  # noqa: F401  (fixture)
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.agent.test_scan_drift import drift_desk, suggestion_scan  # noqa: F401  (fixture + helper)
from tests.calendar_doubles import clear_check, union_double


def _activate(config) -> None:
    """Alpaca paper with session-bounded entries and the switch on: ``auto_entry_active`` is true."""
    config.execution_mode = ExecutionMode.ALPACA
    config.alpaca_paper = True
    config.execution.native_entry_lifetime = "session"
    config.execution.auto_entry = "paper"


@pytest.fixture
def temp_db(tmp_path, app_config):
    """This module's database is the brokerage paper scope (``…/alpaca:paper``), as on the desk."""
    _activate(app_config)
    return SignalDatabase(str(tmp_path / "auto_entry.db"), config=app_config)


def _desk(config, db, notifier) -> TradingCopilot:
    """A desk whose tap-time checks pass for a fresh SPY card (entry 100, stop 95, price 100.5)."""
    config.copilot_chat_enabled = False
    copilot = TradingCopilot(
        config,
        db=db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=notifier,
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )
    copilot.entry_service.authorize.return_value = (None, "stubbed admission")
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_latest_price.return_value = 100.5
    copilot.session_provider = AsyncMock()
    copilot.session_provider.get_session_info.return_value = session_info(
        next_close=datetime.now(UTC) + timedelta(hours=2)
    )
    copilot.calendar = union_double(clear_check())
    copilot.regime_detector = AsyncMock()
    copilot.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="calm", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0
    )
    copilot.earnings_calendar = AsyncMock()
    copilot.earnings_calendar.next_earnings.return_value = EarningsLookup(
        event=None, verified=True, horizon_end=datetime.now(UTC).date()
    )
    return copilot


@pytest.fixture
def auto_desk(app_config, temp_db, mock_notifier, monkeypatch):
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    return _desk(app_config, temp_db, mock_notifier)


async def record_auto_card(
    db,
    *,
    age_seconds: float = 60,
    issued_at: datetime | None = None,
    rank: int = 1,
    contract: str = "SPY",
    provenance_extra: dict | None = None,
) -> int:
    """Record a marked native card exactly as ``run_scan`` does: provenance, marker and its queued card."""
    evaluation = original_evaluation().model_copy(update={"contract": contract})
    valid_until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    provenance = {
        "setup_quality": 0.9,
        "rank": rank,
        "valid_until": valid_until,
        NATIVE_ENTRY_LIFETIME_KEY: asdict(NativeEntryLifetimePolicy()),
        AUTO_ENTRY_KEY: dict(AUTO_ENTRY_MARKER),
        **(provenance_extra or {}),
    }
    signal_id = await db.record_signal(
        contract=contract,
        strategy="TREND_PULLBACK",
        timeframe="4h",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=120.0,
        risk_dollars=evaluation.risk_dollars,
        reward_dollars=evaluation.reward_dollars,
        notional_value=evaluation.notional_value,
        raw_response=evaluation.model_dump_json(),
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        decision_provenance=provenance,
        notification={
            "eval_res": evaluation.model_dump(mode="json"),
            "strategy": "TREND_PULLBACK",
            "regime_summary": "calm",
            "probe_risk_cap": None,
            "book_sizing": None,
            "entry_session_bound": True,
            "valid_until": valid_until,
            "auto_entry": "placing",
        },
    )
    when = issued_at or datetime.now(UTC) - timedelta(seconds=age_seconds)
    async with db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == signal_id).values(timestamp=when))
    return signal_id


async def notifications(db) -> dict[str, dict]:
    """Every queued outbox row's ``{"kind", "arguments"}`` by its dedup key."""
    async with db.session_factory() as session:
        rows = (await session.scalars(select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.NOTIFICATION))).all()
        return {row.dedup_key: json.loads(row.payload) for row in rows}


async def attempt_events(db, signal_id: int) -> list[dict]:
    async with db.session_factory() as session:
        rows = (
            await session.scalars(
                select(DomainEventRecord).where(DomainEventRecord.kind == EventKind.AUTO_ENTRY_ATTEMPTED)
            )
        ).all()
    return [
        {"stream": row.stream, "key": row.event_key, "payload": json.loads(row.payload)}
        for row in rows
        if json.loads(row.payload)["signal_id"] == signal_id
    ]


async def the_attempt(db, signal_id: int) -> dict:
    [event] = await attempt_events(db, signal_id)
    assert (event["stream"], event["key"]) == (f"card/{signal_id}", f"auto-entry/{signal_id}")
    assert set(event["payload"]) == {
        "signal_id",
        "scan_id",
        "outcome",
        "reason",
        "work_status",
        "signal_status",
        "attempted_at",
        "expiry_kind",
    }
    return event["payload"]


def enqueue_for_real(desk, db, config, monkeypatch) -> None:
    """Authorization queues the entry through the real store (account risk stubbed out)."""
    monkeypatch.setattr(WorkflowStore, "_entry_risk", AsyncMock(return_value=None))

    async def authorize(request, *, trigger=EntryTrigger.TAP):
        return await db.workflows.enqueue_entry(request, config, trigger=trigger)

    desk.entry_service.authorize.side_effect = authorize


def stub_execute(desk, reply_text: str = "⚠️ <b>Execution Rejected:</b> stubbed", side_effect=None) -> AsyncMock:
    """Replace the entry call with a recorder that leaves the card ``PENDING`` (a refusal)."""

    async def execute(signal_id, quantity=None, *, trigger=EntryTrigger.TAP):
        if side_effect is not None:
            await side_effect(signal_id)
        return ExecutionReply(False, reply_text)

    desk.execute_signal_by_id = AsyncMock(side_effect=execute)
    return desk.execute_signal_by_id


# --- the marker at record time ------------------------------------------------------------------


async def _recorded(db) -> dict[str, tuple[dict, dict]]:
    """``(provenance, card payload)`` by contract for every recorded signal with a queued card."""
    cards = {
        payload["arguments"]["signal_id"]: payload["arguments"]
        for key, payload in (await notifications(db)).items()
        if key.endswith("/created")
    }
    return {
        row["contract"]: (row["decision_provenance"], cards[row["id"]])
        for row in await db.get_recent_signals(limit=50)
        if row["id"] in cards
    }


async def test_suggestion_scan_marks_native_card(drift_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)

    await suggestion_scan(drift_desk)

    provenance, card = (await _recorded(temp_db))["DDD"]
    assert provenance[AUTO_ENTRY_KEY] == {"mode": "paper"}
    assert card["auto_entry"] == "placing"
    # The durable payload is deliverable by the real Telegram notifier signature.
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **card)


def _swing(desk):
    return suggestion_scan(desk, trigger=ScanTrigger.SWING_SCAN)


def _operator(desk):
    return suggestion_scan(desk, trigger=ScanTrigger.OPERATOR_SCAN)


def _as_alpha(desk):
    scan = desk.strategy_engine.scan_contract.side_effect
    desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        c.model_copy(update={"alpha_version": "alpha-fixture-v1"}) for c in scan(data, **kw)
    ]


def _as_futures(desk):
    scan = desk.strategy_engine.scan_contract.side_effect
    desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        c.model_copy(update={"asset_class": AssetClass.FUTURES}) for c in scan(data, **kw)
    ]
    evaluate = desk.evaluator.evaluate_candidate.side_effect

    async def as_futures(cand, **kwargs):
        evaluated = await evaluate(cand, **kwargs)
        evaluated.asset_class = cand.asset_class
        return evaluated

    desk.evaluator.evaluate_candidate.side_effect = as_futures


@pytest.mark.parametrize(
    ("case", "contract"),
    [
        ("operator_scan", "DDD"),
        ("swing_scan", "DDD"),
        ("drift", "WINR"),
        ("watch", "WTCH"),
        ("alpha", "DDD"),
        ("futures", "DDD"),
        ("inactive", "DDD"),
    ],
)
async def test_no_marker_for_operator_scan_swing_drift_watch_alpha_futures_or_inactive(
    case,
    contract,
    request,
    temp_db,
    app_config,
    monkeypatch,
):
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    desk = request.getfixturevalue("watch_desk" if case == "watch" else "drift_desk")
    scan = suggestion_scan
    if case == "operator_scan":
        scan = _operator
    elif case == "swing_scan":
        scan = _swing
    elif case == "alpha":
        _as_alpha(desk)
    elif case == "futures":
        _as_futures(desk)
    elif case == "inactive":
        app_config.execution.auto_entry = "off"

    await scan(desk)

    recorded = await _recorded(temp_db)
    assert contract in recorded
    provenance, card = recorded[contract]
    assert AUTO_ENTRY_KEY not in provenance
    assert "auto_entry" not in card


async def test_repriced_card_drops_the_auto_marker(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)
    auto_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # +0.4R on a fresh card -> REPRICE

    await auto_desk.execute_signal_by_id(sid)

    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    [replacement] = [
        payload["arguments"]
        for key, payload in (await notifications(temp_db)).items()
        if key != f"signal/{sid}/created" and payload["kind"] == NotificationKind.SIGNAL
    ]
    assert replacement["reprices"] == sid
    assert "auto_entry" not in replacement  # rendered as an ordinary tap card
    new = await temp_db.get_signal_by_id(replacement["signal_id"])
    assert AUTO_ENTRY_KEY not in new["decision_provenance"]
    assert new["decision_provenance"][NATIVE_ENTRY_LIFETIME_KEY]  # the lifetime still rides along


# --- the sweep: one row of the outcome table per test --------------------------------------------


async def test_entered_queues_the_button_swap_and_no_recard(auto_desk, temp_db, app_config, monkeypatch):
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    sid = await record_auto_card(temp_db)

    [attempt] = await auto_desk.auto_enter_pending()

    assert isinstance(attempt, AutoEntryAttempt)
    payload = await the_attempt(temp_db, sid)
    assert payload["outcome"] == attempt.outcome == "entered"
    assert payload["signal_status"] == SignalStatus.SUBMITTING
    assert payload["work_status"] == "queued"
    assert payload["scan_id"] is None
    assert payload["reason"].startswith(f"Entry #{sid} queued as entry-")
    assert "<code>" not in payload["reason"]
    datetime.fromisoformat(payload["attempted_at"])
    queued = await notifications(temp_db)
    assert queued[f"signal/{sid}/auto-entered"] == {
        "kind": NotificationKind.CARD_AUTO_ENTERED,
        "arguments": {"signal_id": sid, "contract": "SPY"},
    }
    assert f"signal/{sid}/auto-refused" not in queued
    # The work item names the sweep, not a tap.
    [item] = await temp_db.workflows.list_work(WorkKind.ENTRY)
    assert item.payload["entry_trigger"] == EntryTrigger.AUTO


async def test_queue_time_refusal_left_pending_is_refused_and_recarded(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)  # the desk's authorization refuses: "stubbed admission"

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert payload["outcome"] == "refused"
    assert payload["signal_status"] == SignalStatus.PENDING and payload["work_status"] is None
    assert payload["reason"] == "stubbed admission"  # the reply's "⚠️ Execution Rejected:" label dropped
    queued = await notifications(temp_db)
    recard = queued[f"signal/{sid}/auto-refused"]
    assert recard["kind"] == NotificationKind.SIGNAL
    original = queued[f"signal/{sid}/created"]["arguments"]
    assert recard["arguments"] == {
        **original,
        "auto_entry": "refused",
        "auto_refusal": "stubbed admission",
    }
    assert f"signal/{sid}/auto-entered" not in queued
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **recard["arguments"])


async def test_synchronous_rejection_is_failed_refused_and_not_recarded(auto_desk, temp_db, app_config, monkeypatch):
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    queue = auto_desk.entry_service.authorize.side_effect
    store = temp_db.workflows

    async def reject(request, *, trigger=EntryTrigger.TAP):
        item, reason = await queue(request, trigger=trigger)
        claimed = await store.claim_entry(lease_seconds=60)
        await store.resolve_entry(claimed, OrderResult(success=False, error_message="preflight refused"))
        return await store.get_work(item.id), reason

    auto_desk.entry_service.authorize.side_effect = reject
    sid = await record_auto_card(temp_db)

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["signal_status"], payload["work_status"]) == (
        "refused",
        SignalStatus.FAILED,
        "rejected",
    )
    queued = await notifications(temp_db)
    assert f"signal/{sid}/auto-refused" not in queued and f"signal/{sid}/auto-entered" not in queued
    # Its existing immediate rejection notice stays the operator's word on it.
    assert any(key.startswith("entry/") and key.endswith("/rejected") for key in queued)


async def test_a_synchronous_rejection_journals_a_fixed_reason_never_the_reply_text(
    auto_desk, temp_db, app_config, monkeypatch
):
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    queue = auto_desk.entry_service.authorize.side_effect
    store = temp_db.workflows

    async def reject(request, *, trigger=EntryTrigger.TAP):
        item, reason = await queue(request, trigger=trigger)
        claimed = await store.claim_entry(lease_seconds=60)
        await store.resolve_entry(claimed, OrderResult(success=False, error_message="BROKER-SECRET-77 insufficient"))
        return await store.get_work(item.id), reason

    auto_desk.entry_service.authorize.side_effect = reject
    sid = await record_auto_card(temp_db)

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["signal_status"], payload["reason"]) == (
        "refused",
        SignalStatus.FAILED,
        "rejected at submission",
    )
    [event] = await attempt_events(temp_db, sid)
    assert "BROKER-SECRET-77" not in json.dumps(event)
    rows = await store.auto_entry_digest_rows(datetime.now(ET_TZ).date())
    digest = format_auto_entry_digest(rows, et_date=datetime.now(ET_TZ).date())
    assert "refused: rejected at submission" in digest and "BROKER-SECRET-77" not in digest


_EXPIRED_CASES = {
    # case: (latest price, provenance override, expected attempt reason, expiry kind)
    "missed": (94.0, {}, "price 94.00 is at or through the stop at 95.00", "expired"),
    "expired": (100.5, {"valid_until": "PAST"}, "Card expired: its session has ended", "expired"),
    "repriced": (102.0, {}, "re-priced", "repriced"),
}


@pytest.mark.parametrize("case", list(_EXPIRED_CASES))
@pytest.mark.parametrize(("contract", "reevaluable"), [("SPY", True), ("ZZZZ", False)])
async def test_missed_expired_or_repriced_strikes_the_card_and_says_not_placed(
    auto_desk, temp_db, case, contract, reevaluable
):
    """An EXPIRED auto card must not keep claiming an entry: the expiry strike plus one "not placed" notice."""
    price, override, reason, kind = _EXPIRED_CASES[case]
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    sid = await record_auto_card(
        temp_db, contract=contract, provenance_extra={k: past if v == "PAST" else v for k, v in override.items()}
    )
    auto_desk.data_fetcher.fetch_latest_price.return_value = price  # +0.4R -> REPRICE; through the stop -> MISSED

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["reason"], payload["expiry_kind"], payload["signal_status"]) == (
        "refused",
        reason,
        kind,
        SignalStatus.EXPIRED,
    )
    queued = await notifications(temp_db)
    replacements = [
        key for key, value in queued.items() if key.endswith("/created") and value["arguments"].get("reprices") == sid
    ]
    assert len(replacements) == (1 if case == "repriced" else 0)
    assert set(queued) == {
        f"signal/{sid}/created",
        f"signal/{sid}/expired",
        f"signal/{sid}/auto-not-placed",
        *replacements,
    }
    assert queued[f"signal/{sid}/expired"] == {
        "kind": NotificationKind.CARD_EXPIRED,
        "arguments": {"signal_id": sid, "contract": contract, "reevaluable": reevaluable},
    }
    if case == "repriced":
        new_id = queued[replacements[0]]["arguments"]["signal_id"]
        text = f"🤖 Auto-entry #{sid} {contract} not placed: re-priced; review card #{new_id}."
    else:
        text = f"🤖 Auto-entry #{sid} {contract} not placed: {reason}."
    assert queued[f"signal/{sid}/auto-not-placed"] == {"kind": NotificationKind.MESSAGE, "arguments": {"text": text}}
    auto_desk.entry_service.authorize.assert_not_awaited()
    # The sweep never re-attempts the replacement: it carries no marker.
    assert await auto_desk.auto_enter_pending() == []
    # The digest still names the expiry.
    rows = await temp_db.workflows.auto_entry_digest_rows(datetime.now(ET_TZ).date())
    detail = "re-priced" if case == "repriced" else f"expired ({reason})"
    assert [(row.signal_id, row.outcome, row.detail) for row in rows] == [(sid, "refused", detail)]


async def test_an_exception_after_the_card_expired_still_strikes_it(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)

    async def expire_then_raise(signal_id):
        assert await temp_db.expire_signal(signal_id)
        raise RuntimeError("secret broker detail")

    stub_execute(auto_desk, side_effect=expire_then_raise)

    [attempt] = await auto_desk.auto_enter_pending()

    assert (attempt.outcome, attempt.reason, attempt.expiry_kind) == ("refused", "RuntimeError", "expired")
    queued = await notifications(temp_db)
    assert set(queued) == {f"signal/{sid}/created", f"signal/{sid}/expired", f"signal/{sid}/auto-not-placed"}
    assert queued[f"signal/{sid}/auto-not-placed"]["arguments"] == {
        "text": f"🤖 Auto-entry #{sid} SPY not placed: card expired."
    }
    assert "secret broker detail" not in json.dumps([await attempt_events(temp_db, sid), queued])


async def test_an_account_risk_refusal_journals_a_fixed_reason(auto_desk, temp_db, app_config, monkeypatch):
    """``Account risk unavailable: {exc}`` embeds an exception's text; the journal and re-card never carry it."""
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    monkeypatch.setattr(WorkflowStore, "_entry_risk", AsyncMock(side_effect=ValueError("SECRET-CHECKPOINT detail")))
    sid = await record_auto_card(temp_db)

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["reason"], payload["signal_status"]) == (
        "refused",
        "account risk unavailable",
        SignalStatus.PENDING,
    )
    queued = await notifications(temp_db)
    assert queued[f"signal/{sid}/auto-refused"]["arguments"]["auto_refusal"] == "account risk unavailable"
    assert "SECRET-CHECKPOINT" not in json.dumps([await attempt_events(temp_db, sid), queued])


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (
            "⚠️ <b>Execution Rejected:</b> Account risk unavailable: stale checkpoint. No order submitted.",
            "account risk unavailable",
        ),
        (
            "Admission evidence unavailable (ValueError: secret). No order submitted.",
            "admission evidence unavailable",
        ),
        (
            "❌ <b>Execution Failed (ALPACA):</b>\n• <b>Contract:</b> SPY (LONG)\n• <b>Error:</b> secret broker text",
            "rejected at submission",
        ),
        ("⚠️ <b>Execution Rejected:</b> cap reached.", "cap reached"),
    ],
)
def test_sweep_reason_maps_exception_bearing_refusals_to_fixed_phrases(reply, reason):
    assert sweep_reason(reply) == reason


async def test_dismissed_after_selection_before_the_call_is_skipped(auto_desk, temp_db, monkeypatch):
    sid = await record_auto_card(temp_db)
    select_pending = temp_db.workflows.pending_auto_entries

    async def select_then_dismiss(et_date):
        selected = await select_pending(et_date)
        assert await temp_db.dismiss_signal(sid)  # the operator's veto lands between selection and the call
        return selected

    monkeypatch.setattr(temp_db.workflows, "pending_auto_entries", select_then_dismiss)
    execute = stub_execute(auto_desk)

    [attempt] = await auto_desk.auto_enter_pending()

    assert attempt.outcome == "skipped_not_pending"
    execute.assert_not_awaited()
    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["signal_status"], payload["work_status"]) == (
        "skipped_not_pending",
        SignalStatus.DISMISSED,
        None,
    )
    assert await temp_db.workflows.list_work(WorkKind.ENTRY) == []
    assert set(await notifications(temp_db)) == {f"signal/{sid}/created"}


async def test_dismissed_during_the_call_is_skipped_and_never_entered(auto_desk, temp_db, app_config, monkeypatch):
    """Review focus 2: exactly one of dismissed / entered wins; the queue's PENDING check refuses the entry."""
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    queue = auto_desk.entry_service.authorize.side_effect
    sid = await record_auto_card(temp_db)

    async def dismiss_then_queue(request, *, trigger=EntryTrigger.TAP):
        assert await temp_db.dismiss_signal(sid)
        return await queue(request, trigger=trigger)

    auto_desk.entry_service.authorize.side_effect = dismiss_then_queue

    await auto_desk.auto_enter_pending()

    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["signal_status"]) == ("skipped_not_pending", SignalStatus.DISMISSED)
    assert await temp_db.workflows.list_work(WorkKind.ENTRY) == []
    assert set(await notifications(temp_db)) == {f"signal/{sid}/created"}


async def test_sweep_calls_execute_once_per_card_with_auto_trigger_in_rank_order(auto_desk, temp_db):
    scan_at = datetime.now(UTC) - timedelta(seconds=120)
    second = await record_auto_card(temp_db, issued_at=scan_at, rank=2)
    first = await record_auto_card(temp_db, issued_at=scan_at, rank=1)
    later = await record_auto_card(temp_db, issued_at=scan_at + timedelta(seconds=60), rank=1)
    tap_card = await record_auto_card(temp_db, provenance_extra={AUTO_ENTRY_KEY: None})
    execute = stub_execute(auto_desk)

    attempts = await auto_desk.auto_enter_pending()

    assert [call.args for call in execute.await_args_list] == [(first, None), (second, None), (later, None)]
    assert all(call.kwargs == {"trigger": EntryTrigger.AUTO} for call in execute.await_args_list)
    assert [a.signal_id for a in attempts] == [first, second, later]
    assert tap_card not in [a.signal_id for a in attempts]


async def test_sweep_selects_only_todays_new_york_cards(auto_desk, temp_db):
    today = datetime.now(UTC).astimezone(ET_TZ).date()
    yesterday = await record_auto_card(
        temp_db, issued_at=datetime.combine(today - timedelta(days=1), time(14, 35), tzinfo=ET_TZ)
    )
    execute = stub_execute(auto_desk)

    assert await auto_desk.auto_enter_pending() == []
    execute.assert_not_awaited()
    assert await attempt_events(temp_db, yesterday) == []


@pytest.mark.parametrize("marker", [{"mode": "live"}, {"mode": "paper", "extra": 1}, True, "paper"])
async def test_an_unknown_marker_value_is_never_selected(auto_desk, temp_db, marker):
    await record_auto_card(temp_db, provenance_extra={AUTO_ENTRY_KEY: marker})
    execute = stub_execute(auto_desk)

    today = datetime.now(UTC).astimezone(ET_TZ).date()
    assert await temp_db.workflows.pending_auto_entries(today) == []
    assert await auto_desk.auto_enter_pending() == []
    execute.assert_not_awaited()


async def test_sweep_is_idempotent(auto_desk, temp_db, app_config, monkeypatch):
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    spy = await record_auto_card(temp_db)
    qqq = await record_auto_card(temp_db, contract="QQQ")

    first = await auto_desk.auto_enter_pending()
    queued = await notifications(temp_db)
    second = await auto_desk.auto_enter_pending()

    assert sorted(a.signal_id for a in first) == sorted([spy, qqq])
    assert second == []
    assert len(await attempt_events(temp_db, spy)) == len(await attempt_events(temp_db, qqq)) == 1
    assert await notifications(temp_db) == queued
    # A crash after the entry was queued leaves the signal SUBMITTING with no attempt event: never selected.
    crashed = await record_auto_card(temp_db, contract="IWM")
    await temp_db.update_signal_status(crashed, SignalStatus.SUBMITTING)
    today = datetime.now(UTC).astimezone(ET_TZ).date()
    assert [s["id"] for s in await temp_db.workflows.pending_auto_entries(today)] == []


async def test_journal_failure_never_retries_the_card_in_this_process(auto_desk, temp_db, monkeypatch):
    sid = await record_auto_card(temp_db)
    execute = stub_execute(auto_desk)
    monkeypatch.setattr(temp_db.workflows, "record_auto_entry_attempt", AsyncMock(side_effect=RuntimeError("db")))

    await auto_desk.auto_enter_pending()
    await auto_desk.auto_enter_pending()

    assert [call.args[0] for call in execute.await_args_list] == [sid]  # one attempt, no retry of any kind


async def test_concurrent_sweeps_serialize(auto_desk, temp_db):
    cards = [await record_auto_card(temp_db), await record_auto_card(temp_db, contract="QQQ")]
    active, peak = 0, 0

    async def overlap(signal_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    execute = stub_execute(auto_desk, side_effect=overlap)

    results = await asyncio.gather(auto_desk.auto_enter_pending(), auto_desk.auto_enter_pending())

    assert peak == 1
    assert sorted(len(r) for r in results) == [0, 2]
    assert sorted(call.args[0] for call in execute.await_args_list) == sorted(cards)


async def test_card_older_than_fresh_seconds_is_abandoned_and_recarded(auto_desk, temp_db, app_config):
    fresh = app_config.execution.card_freshness.fresh_seconds
    issued = datetime.combine(datetime.now(UTC).astimezone(ET_TZ).date(), time(10, 35), tzinfo=ET_TZ)
    late = await record_auto_card(temp_db, issued_at=issued)
    on_time = await record_auto_card(temp_db, issued_at=issued + timedelta(seconds=120), contract="QQQ")
    execute = stub_execute(auto_desk)

    attempts = await auto_desk.auto_enter_pending(now=issued + timedelta(seconds=fresh + 60))

    assert [(a.signal_id, a.outcome) for a in attempts] == [(late, "abandoned"), (on_time, "refused")]
    assert [call.args[0] for call in execute.await_args_list] == [on_time]
    payload = await the_attempt(temp_db, late)
    assert (payload["outcome"], payload["reason"], payload["signal_status"]) == (
        "abandoned",
        "not_attempted_in_time",
        SignalStatus.PENDING,
    )
    assert datetime.fromisoformat(payload["attempted_at"]) == issued + timedelta(seconds=fresh + 60)
    recard = (await notifications(temp_db))[f"signal/{late}/auto-refused"]["arguments"]
    assert recard["auto_entry"] == "abandoned"
    assert recard["auto_refusal"] == "not_attempted_in_time"


async def test_exception_in_one_card_is_refused_with_type_name_and_next_card_runs(auto_desk, temp_db):
    broken = await record_auto_card(temp_db)
    healthy = await record_auto_card(temp_db, contract="QQQ")

    async def explode(signal_id):
        if signal_id == broken:
            raise RuntimeError("secret broker detail")

    execute = stub_execute(auto_desk, side_effect=explode)

    attempts = await auto_desk.auto_enter_pending()

    assert [call.args[0] for call in execute.await_args_list] == [broken, healthy]
    assert [(a.signal_id, a.outcome) for a in attempts] == [(broken, "refused"), (healthy, "refused")]
    payload = await the_attempt(temp_db, broken)
    assert (payload["reason"], payload["signal_status"]) == ("RuntimeError", SignalStatus.PENDING)
    queued = await notifications(temp_db)
    assert queued[f"signal/{broken}/auto-refused"]["arguments"]["auto_refusal"] == "RuntimeError"
    assert "secret broker detail" not in json.dumps([await attempt_events(temp_db, broken), queued])


def queue_then_raise(desk, db, config, monkeypatch) -> None:
    """Authorization really queues the entry (signal SUBMITTING, work item ``auto``), then raises."""
    enqueue_for_real(desk, db, config, monkeypatch)
    queue = desk.entry_service.authorize.side_effect

    async def raise_after_queueing(request, *, trigger=EntryTrigger.TAP):
        await queue(request, trigger=trigger)
        raise RuntimeError("secret claim detail")

    desk.entry_service.authorize.side_effect = raise_after_queueing


async def test_an_exception_after_the_entry_was_queued_is_entered_from_state(
    auto_desk, temp_db, app_config, monkeypatch
):
    queue_then_raise(auto_desk, temp_db, app_config, monkeypatch)
    sid = await record_auto_card(temp_db)

    [attempt] = await auto_desk.auto_enter_pending()

    assert (attempt.outcome, attempt.reason) == ("entered", "RuntimeError")
    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["reason"], payload["signal_status"], payload["work_status"]) == (
        "entered",
        "RuntimeError",
        SignalStatus.SUBMITTING,
        "queued",
    )
    queued = await notifications(temp_db)
    assert queued[f"signal/{sid}/auto-entered"]["arguments"] == {"signal_id": sid, "contract": "SPY"}
    assert f"signal/{sid}/auto-refused" not in queued
    assert "secret claim detail" not in json.dumps([await attempt_events(temp_db, sid), queued])


async def test_an_exception_whose_state_reread_also_fails_is_refused(auto_desk, temp_db, app_config, monkeypatch):
    queue_then_raise(auto_desk, temp_db, app_config, monkeypatch)
    sid = await record_auto_card(temp_db)
    monkeypatch.setattr(auto_desk, "_auto_entry_state", AsyncMock(side_effect=ValueError("reread")))

    [attempt] = await auto_desk.auto_enter_pending()

    assert (attempt.outcome, attempt.reason) == ("refused", "RuntimeError")
    payload = await the_attempt(temp_db, sid)
    assert (payload["outcome"], payload["reason"]) == ("refused", "RuntimeError")
    queued = await notifications(temp_db)
    # No swap without state evidence; no re-card, since the signal is not PENDING under the journal's lock.
    assert not any(key.startswith(f"signal/{sid}/auto-") for key in queued)


async def test_a_failing_selection_never_raises(auto_desk, temp_db, monkeypatch, caplog):
    monkeypatch.setattr(temp_db.workflows, "pending_auto_entries", AsyncMock(side_effect=RuntimeError("db down")))

    with caplog.at_level(logging.ERROR, logger="copilot"):
        assert await auto_desk.auto_enter_pending() == []
    assert [r for r in caplog.records if r.__dict__.get("event") == "auto_entry_sweep_failed"]


async def test_non_paper_scope_refuses_without_entry(tmp_path, app_config, mock_notifier, monkeypatch):
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.alpaca_paper = False  # a live Alpaca account: scope …/alpaca:live
    live_db = SignalDatabase(str(tmp_path / "live.db"), config=app_config)
    desk = _desk(app_config, live_db, mock_notifier)
    sid = await record_auto_card(live_db)
    execute = stub_execute(desk)

    [attempt] = await desk.auto_enter_pending()

    execute.assert_not_awaited()
    assert (attempt.outcome, attempt.reason) == ("refused", "not_paper_scope")
    payload = await the_attempt(live_db, sid)
    assert (payload["outcome"], payload["reason"]) == ("refused", "not_paper_scope")
    assert await live_db.workflows.list_work(WorkKind.ENTRY) == []
    assert (await live_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING


async def test_reason_is_stripped_and_capped_at_300(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)
    stub_execute(auto_desk, reply_text="⚠️ <b>Execution   Rejected:</b>\n cap &amp; <i>limit</i> " + "x" * 400)

    await auto_desk.auto_enter_pending()

    reason = (await the_attempt(temp_db, sid))["reason"]
    assert reason.startswith("cap & limit xxx")
    assert len(reason) == 300 and "<" not in reason and "&amp;" not in reason
    # Queued raw: the renderer escapes it.
    assert (await notifications(temp_db))[f"signal/{sid}/auto-refused"]["arguments"]["auto_refusal"] == reason


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("⚠️ <b>Execution Rejected:</b> cap reached.", "cap reached"),
        (
            "🛑 <b>Execution Blocked:</b> Emergency trading halt active (drill). Use /resume to unhalt.",
            "Emergency trading halt active (drill). Use /resume to unhalt",
        ),
        ("⚠️ Checks unavailable; try again shortly.", "Checks unavailable; try again shortly"),
        (
            "⚠️ <b>Execution Rejected:</b> Account risk unavailable: stale. No order submitted.",
            "Account risk unavailable: stale. No order submitted",
        ),
        ("Execution Rejected: quantity must be finite and positive.", "quantity must be finite and positive"),
    ],
)
def test_attempt_reason_drops_the_reply_label_and_trailing_period(reply, reason):
    assert attempt_reason(reply) == reason


async def test_a_recard_reads_as_one_sentence(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)
    stub_execute(auto_desk, reply_text="⚠️ <b>Execution Rejected:</b> cap reached.")

    await auto_desk.auto_enter_pending()

    recard = (await notifications(temp_db))[f"signal/{sid}/auto-refused"]["arguments"]
    assert recard["auto_refusal"] == "cap reached"
    text = format_alert_card(
        LLMTradeEvaluation.model_validate(recard["eval_res"]),
        recard["strategy"],
        auto_entry=recard["auto_entry"],
        auto_refusal=recard["auto_refusal"],
    )
    assert "🤖 Auto-entry refused: cap reached. Tap Execute to enter it yourself." in text


async def test_a_missing_original_card_logs_and_still_journals_the_refusal(auto_desk, temp_db, caplog):
    sid = await record_auto_card(temp_db)
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(delete(WorkItemRecord).where(WorkItemRecord.dedup_key == f"signal/{sid}/created"))
    stub_execute(auto_desk)

    with caplog.at_level(logging.WARNING, logger="agentic_trader.storage.workflow"):
        await auto_desk.auto_enter_pending()

    assert (await the_attempt(temp_db, sid))["outcome"] == "refused"
    assert f"signal/{sid}/auto-refused" not in await notifications(temp_db)
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "auto_entry_recard_missing"]
    assert record.levelno == logging.WARNING and record.__dict__["signal_id"] == sid


# --- review focus 5: the re-card owns the stored message id -------------------------------------


def _telegram(db, *message_ids: int) -> tuple[TelegramNotifier, AsyncMock, AsyncMock]:
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=db)
    send = AsyncMock(side_effect=[SimpleNamespace(message_id=m) for m in message_ids])
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send, edit_message_reply_markup=edit))
    return notifier, send, edit


async def test_recard_moves_message_id_and_expiry_strikes_it(auto_desk, temp_db, app_config):
    sid = await record_auto_card(temp_db)
    notifier, send, edit = _telegram(temp_db, 100, 200)
    dispatcher = NotificationDispatcher(temp_db.workflows, notifier, app_config.execution)
    assert await dispatcher.dispatch_one()  # the original "placing" card
    assert (await temp_db.get_signal_by_id(sid))["telegram_message_id"] == 100
    stub_execute(auto_desk, reply_text="⚠️ <b>Execution Rejected:</b> cap reached")

    await auto_desk.auto_enter_pending()
    assert await dispatcher.dispatch_one()  # the re-card

    assert (await temp_db.get_signal_by_id(sid))["telegram_message_id"] == 200
    recard = send.await_args_list[1].kwargs
    assert AUTO_REFUSED_LINE.format(reason="cap reached") in recard["text"]
    callbacks = [b.callback_data for row in recard["reply_markup"].inline_keyboard for b in row]
    assert callbacks == [f"exec_{sid}", f"dism_{sid}"]

    assert await temp_db.expire_stale_signals(datetime.now(UTC) + timedelta(hours=3)) == [sid]
    assert await dispatcher.dispatch_one()  # the CARD_EXPIRED strike
    edit.assert_awaited_once()
    assert edit.await_args.kwargs["message_id"] == 200


async def test_the_original_cards_dismiss_still_dismisses_a_recarded_pending_card(auto_desk, temp_db):
    sid = await record_auto_card(temp_db)
    stub_execute(auto_desk)
    await auto_desk.auto_enter_pending()

    assert await temp_db.dismiss_signal(sid)  # the original card's Dismiss (dism_{id}) on the PENDING card
    assert not await temp_db.dismiss_signal(sid)  # the re-card's Dismiss afterwards is harmless
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.DISMISSED
