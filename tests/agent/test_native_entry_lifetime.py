"""Native cards carry a separate one-session entry-lifetime marker; the order stays an explicit GTC."""

from dataclasses import asdict

import pytest

from agentic_trader.broker.base import TimeInForce
from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.execution.lifetime_policy import (
    NATIVE_ENTRY_LIFETIME_KEY,
    daily_entry_lifetime,
    lifetime_from_dict,
    native_entry_lifetime_marker,
)
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.agent.test_card_freshness_tap import record_card, tap_desk  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.agent.test_scan_drift import drift_desk, suggestion_scan  # noqa: F401  (fixture + helper)
from tests.notifier.test_telegram_interactive import eval_res  # noqa: F401  (fixture)


CARD_LINE = "Unfilled entry cancels before the next open."


def _config(mode: str, lifetime: str) -> AppConfig:
    config = AppConfig(execution_mode=mode)
    config.execution.native_entry_lifetime = lifetime
    return config


def test_marker_is_the_daily_policy_and_round_trips():
    marker = native_entry_lifetime_marker(_config(ExecutionMode.ALPACA, "session"))

    assert marker == asdict(daily_entry_lifetime())
    assert NATIVE_ENTRY_LIFETIME_KEY == "entry_lifetime"
    assert lifetime_from_dict(marker) == daily_entry_lifetime()


@pytest.mark.parametrize(
    ("mode", "lifetime"),
    [(ExecutionMode.ALPACA, "off"), (ExecutionMode.PAPER, "session"), (ExecutionMode.PAPER, "off")],
)
def test_no_marker_when_off_or_paper_mode(mode, lifetime):
    assert native_entry_lifetime_marker(_config(mode, lifetime)) is None


async def _provenance(db) -> dict[str, dict]:
    return {row["contract"]: row["decision_provenance"] for row in await db.get_recent_signals(limit=50)}


async def test_native_card_records_entry_lifetime_marker(drift_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.execution.native_entry_lifetime = "session"

    await suggestion_scan(drift_desk)

    provenance = await _provenance(temp_db)
    assert provenance["DDD"][NATIVE_ENTRY_LIFETIME_KEY] == asdict(daily_entry_lifetime())


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
