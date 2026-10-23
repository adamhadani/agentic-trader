"""Native cards carry a separate one-session entry-lifetime marker; the order stays an explicit GTC."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.base import TimeInForce
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, ExecutionMode
from agentic_trader.execution.durable import WorkKind
from agentic_trader.execution.lifetime_policy import (
    NATIVE_ENTRY_LIFETIME_KEY,
    NativeEntryLifetimePolicy,
    daily_entry_lifetime,
    entry_lifetime_of,
    native_entry_lifetime_marker,
)
from agentic_trader.execution.lifetimes import RestingCancelResult
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.agent.test_card_freshness_tap import record_card, tap_desk  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.agent.test_scan_drift import drift_desk, suggestion_scan  # noqa: F401  (fixture + helper)
from tests.notifier.test_telegram_interactive import eval_res  # noqa: F401  (fixture)


CARD_LINE = "Unfilled entry is cancelled at the session close."


def _config(mode: str, lifetime: str) -> AppConfig:
    config = AppConfig(execution_mode=mode)
    config.execution.native_entry_lifetime = lifetime
    return config


def test_marker_is_the_native_session_close_policy_and_round_trips():
    marker = native_entry_lifetime_marker(_config(ExecutionMode.ALPACA, "session"), "EQUITY")

    assert marker == asdict(NativeEntryLifetimePolicy())
    assert NATIVE_ENTRY_LIFETIME_KEY == "entry_lifetime"
    assert (
        entry_lifetime_of({"decision_provenance": {NATIVE_ENTRY_LIFETIME_KEY: marker}}) == NativeEntryLifetimePolicy()
    )


@pytest.mark.parametrize(
    ("mode", "lifetime"),
    [(ExecutionMode.ALPACA, "off"), (ExecutionMode.PAPER, "session"), (ExecutionMode.PAPER, "off")],
)
def test_no_marker_when_off_or_paper_mode(mode, lifetime):
    assert native_entry_lifetime_marker(_config(mode, lifetime), "EQUITY") is None


async def _provenance(db) -> dict[str, dict]:
    return {row["contract"]: row["decision_provenance"] for row in await db.get_recent_signals(limit=50)}


async def test_native_card_records_entry_lifetime_marker(drift_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.execution.native_entry_lifetime = "session"

    await suggestion_scan(drift_desk)

    provenance = await _provenance(temp_db)
    assert provenance["DDD"][NATIVE_ENTRY_LIFETIME_KEY] == asdict(NativeEntryLifetimePolicy())
    # The card the scan queues renders the session-close line for the marked card only.
    contracts = {row["id"]: row["contract"] for row in await temp_db.get_recent_signals(limit=50)}
    cards = {
        contracts[item.payload["arguments"]["signal_id"]]: item.payload["arguments"]
        for item in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if "eval_res" in item.payload.get("arguments", {})
    }
    assert cards["DDD"]["entry_session_bound"] is True
    assert "entry_session_bound" not in cards["WINR"]


async def test_a_futures_card_under_alpaca_gets_no_marker_or_card_line(
    drift_desk,  # noqa: F811
    temp_db,
    app_config,
    monkeypatch,
):
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.execution.native_entry_lifetime = "session"
    scan = drift_desk.strategy_engine.scan_contract.side_effect
    drift_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        c.model_copy(update={"asset_class": AssetClass.FUTURES}) for c in scan(data, **kw)
    ]
    evaluate = drift_desk.evaluator.evaluate_candidate.side_effect

    async def as_futures(cand, **kwargs):
        evaluated = await evaluate(cand, **kwargs)
        evaluated.asset_class = cand.asset_class
        return evaluated

    drift_desk.evaluator.evaluate_candidate.side_effect = as_futures

    await suggestion_scan(drift_desk)

    provenance = await _provenance(temp_db)
    assert "DDD" in provenance
    assert NATIVE_ENTRY_LIFETIME_KEY not in provenance["DDD"]
    cards = [
        item.payload["arguments"]
        for item in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if "eval_res" in item.payload.get("arguments", {})
    ]
    assert cards and not any(card.get("entry_session_bound") for card in cards)


async def test_the_notifier_cancel_handler_is_the_copilot_cancel_under_the_reconciliation_lock(app_config, temp_db):
    copilot = TradingCopilot(app_config, db=temp_db)
    assert copilot.notifier.cancel_handler == copilot.cancel_resting_entry
    held: list[tuple[bool, bool]] = []

    async def cancel(signal_id, *, confirm):
        held.append((confirm, copilot._reconciliation_lock.locked()))
        return RestingCancelResult("preview", None, signal_id, "SPY", None)

    copilot.lifetime_service = SimpleNamespace(cancel_resting_entry=cancel)
    await copilot.notifier.cancel_handler(42, True)
    await copilot.notifier.cancel_handler(42, False)
    assert held == [(True, True), (False, False)]


async def test_alpha_and_catalog_cards_get_no_provenance_marker(drift_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.execution.native_entry_lifetime = "session"

    await suggestion_scan(drift_desk)

    provenance = await _provenance(temp_db)
    assert "WINR" in provenance  # the catalog drift card carries alpha_policy.lifetime instead
    assert NATIVE_ENTRY_LIFETIME_KEY not in provenance["WINR"]


async def test_off_scan_records_no_marker(drift_desk, temp_db, app_config):  # noqa: F811
    app_config.execution.native_entry_lifetime = "off"

    await suggestion_scan(drift_desk)

    assert all(NATIVE_ENTRY_LIFETIME_KEY not in p for p in (await _provenance(temp_db)).values())


async def test_native_entry_order_is_explicit_gtc(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db)

    await tap_desk.execute_signal_by_id(sid)

    request = tap_desk.entry_service.authorize.await_args.args[0]
    assert request.time_in_force == TimeInForce.GTC
    assert "time_in_force" in request.model_fields_set


def test_card_text_says_unfilled_entry_cancels(eval_res):  # noqa: F811
    for render in (format_alert_card, format_terminal_card):
        assert CARD_LINE in render(eval_res, "TREND_PULLBACK", entry_session_bound=True)
        assert CARD_LINE not in render(eval_res, "TREND_PULLBACK")
        assert CARD_LINE not in render(eval_res, "TREND_PULLBACK", entry_session_bound=False)


async def test_reprice_keeps_the_marker_and_the_card_flag(tap_desk, temp_db):  # noqa: F811
    marker = asdict(daily_entry_lifetime())
    sid = await record_card(temp_db, age_seconds=3600, provenance_extra={NATIVE_ENTRY_LIFETIME_KEY: marker})
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # +0.4R -> REPRICE

    await tap_desk.execute_signal_by_id(sid)

    [notification] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    arguments = notification.payload["arguments"]
    assert arguments["entry_session_bound"] is True
    replacement = await temp_db.get_signal_by_id(arguments["signal_id"])
    assert replacement["decision_provenance"][NATIVE_ENTRY_LIFETIME_KEY] == marker


async def test_reprice_of_an_unmarked_card_stays_unmarked(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    await tap_desk.execute_signal_by_id(sid)

    [notification] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    arguments = notification.payload["arguments"]
    assert "entry_session_bound" not in arguments
    replacement = await temp_db.get_signal_by_id(arguments["signal_id"])
    assert NATIVE_ENTRY_LIFETIME_KEY not in replacement["decision_provenance"]
