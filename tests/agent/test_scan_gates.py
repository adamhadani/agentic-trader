"""The front of ``run_scan``: the idle check, the lockout and the guarded regime read, and the digest.

Order: lock -> registry snapshot -> halt -> session gate -> idle check -> lockout -> regime
(guarded) -> dynamic universe -> drift -> selection. An idle or locked-out scan makes no regime
read. A regime read that raises (a missing VIX, or anything else, by type name) loses the scan
visibly: a qualifying scan journals ``status: regime_unavailable`` and, in the same transaction,
at most one notice per New York date; SCAN readiness goes not ready; the digest lists the slot.
"""

import json
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.macro import MacroInput
from agentic_trader.agent.regime import REGIME_BLOCK_KEYS, RegimeUnavailable
from agentic_trader.agent.scan_journal import ScanNotice, append_scan_journal, regime_unavailable_notice
from agentic_trader.config import ScanBudget
from agentic_trader.constants import ExecutionMode
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.execution.durable import EventKind, ScanStatus, ScanTrigger, WorkKind
from agentic_trader.market.session import ET_TZ
from tests.agent.test_dynamic_universe_scan import ranked_events
from tests.agent.test_scan_budget import budget_desk, instrument  # noqa: F401  (budget_desk is a fixture)
from tests.calendar_doubles import lockout_check


VIX_DOWN = RegimeUnavailable({"vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout")})
NOTICE_TAIL = "Later scans retry; this notice is sent once per New York date."


@pytest.fixture
def desk(budget_desk):  # noqa: F811
    """Five ranked equities on a real temp db, with the real outbox on that db."""
    return budget_desk


async def suggestion(desk, scheduled_time_et: str = "14:35"):
    return await desk.run_scan(
        use_llm=False,
        dry_run=False,
        asset_class="equity",
        budget=ScanBudget.FULL,
        shadow_evidence=True,
        trigger=ScanTrigger.SUGGESTION_SCAN,
        scheduled_time_et=scheduled_time_et,
    )


def _futures(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(name=symbol, ticker=symbol, asset_class="FUTURES")


def noon_ny(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(12), tzinfo=ET_TZ)


async def notices(db) -> dict[str, dict]:
    """Queued durable notices' arguments, by their outbox dedup key."""
    items = {item.id: item for item in await db.workflows.list_work(WorkKind.NOTIFICATION)}
    queued = [e for e in await db.workflows.events() if e["kind"] == EventKind.NOTIFICATION_QUEUED]
    assert len(queued) == len(items)
    return {
        e["payload"]["dedup_key"]: items[e["stream"].removeprefix("notification/")].payload["arguments"] for e in queued
    }


def scan_date(event: dict) -> str:
    """The New York date the journal filed this scan event under."""
    return event["stream"].removeprefix("scan/")


# --- a missing VIX in a qualifying scan ----------------------------------------------------


async def test_vix_missing_suggestion_scan_journals_regime_unavailable_and_one_notice(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    assert await suggestion(desk) is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["candidates"], p["trigger"]) == ("regime_unavailable", [], "suggestion_scan")
    assert p["gate"] == {"rule": "regime_unavailable", "missing": ["vix"], "errors": {"vix": "ReadTimeout"}}
    assert set(p["regime"]) == set(REGIME_BLOCK_KEYS)
    assert p["regime"]["inputs"]["vix"]["error"] == "ReadTimeout" and p["regime"]["vix"] is None
    assert p["regime"]["macro_enabled"] is True
    assert p["calendar"]["verified"] is True and p["calendar"]["lockout"] is None
    assert (p["scheduled_time_et"], p["budget"]) == ("14:35", "full")
    [(key, notice)] = (await notices(temp_db)).items()
    assert key == f"regime-unavailable/{scan_date(event)}"
    assert notice["formatted"] is False
    text = notice["text"]
    assert text.startswith("⚠️ Market regime unavailable (vix: ReadTimeout) at ")
    assert text.endswith(f" NY: the suggestion scan was skipped — no cards. {NOTICE_TAIL}")
    desk.readiness.observe.assert_any_await(
        HealthComponent.SCAN, False, "Regime unavailable (vix: ReadTimeout); scan skipped"
    )
    desk.strategy_engine.scan_contract.assert_not_called()  # nothing fetched or scanned
    assert await temp_db.get_recent_signals(limit=10) == []
    assert desk.last_scan_summary == {
        "scope": {"asset_class": "equity", "timeframe": None, "restricted": False},
        "trigger": "suggestion_scan",
        "budget": "full",
        "status": "regime_unavailable",
        "scheduled_time_et": "14:35",
        "gate_reason": "market regime unavailable",
    }
    [gated] = desk._session_scan_stats[scan_date(event)]
    assert gated == desk.last_scan_summary


async def test_a_second_failure_the_same_new_york_date_adds_no_notice(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    await suggestion(desk, scheduled_time_et="10:35")
    await suggestion(desk, scheduled_time_et="14:35")

    first, second = await ranked_events(temp_db)
    assert first["payload"]["scan_id"] != second["payload"]["scan_id"]
    assert scan_date(first) == scan_date(second)
    assert list(await notices(temp_db)) == [f"regime-unavailable/{scan_date(first)}"]
    assert [s["scheduled_time_et"] for s in desk._session_scan_stats[scan_date(first)]] == ["10:35", "14:35"]


async def test_the_notice_dedups_per_new_york_date(temp_db):
    for day in ("2026-10-28", "2026-10-28", "2026-10-29"):
        await append_scan_journal(
            temp_db,
            et_date=day,
            scan_id=uuid4().hex,
            payload=None,
            notice=regime_unavailable_notice(
                {"vix": "ReadTimeout"}, at=noon_ny(day), trigger=ScanTrigger.SWING_SCAN, pead_skipped=False
            ),
        )

    queued = await notices(temp_db)
    assert sorted(queued) == ["regime-unavailable/2026-10-28", "regime-unavailable/2026-10-29"]
    assert queued["regime-unavailable/2026-10-29"]["text"] == (
        "⚠️ Market regime unavailable (vix: ReadTimeout) at 12:00 NY: the swing scan was skipped — no cards. "
        f"{NOTICE_TAIL}"
    )
    assert await ranked_events(temp_db) == []  # payload None writes no event


def test_the_notice_key_and_clock_are_new_york_s():
    # 02:30 UTC on the 29th is 22:30 New York on the 28th.
    notice = regime_unavailable_notice(
        {"vix": "ReadTimeout"},
        at=datetime(2026, 10, 29, 2, 30, tzinfo=UTC),
        trigger=ScanTrigger.SUGGESTION_SCAN,
        pead_skipped=True,
    )
    assert notice == ScanNotice(
        "regime-unavailable/2026-10-28",
        "⚠️ Market regime unavailable (vix: ReadTimeout) at 22:30 NY: the suggestion scan was skipped — no cards, "
        f"no PEAD decision. {NOTICE_TAIL}",
    )


def test_the_notice_lists_errors_in_decision_input_order():
    notice = regime_unavailable_notice(
        {"hy_oas": "no_observation", "vix": "ReadTimeout", "2y": "unexpected_payload"},
        at=noon_ny("2026-10-28"),
        trigger=ScanTrigger.OPERATOR_SCAN,
        pead_skipped=False,
    )
    assert notice.text.startswith(
        "⚠️ Market regime unavailable (vix: ReadTimeout, 2y: unexpected_payload, hy_oas: no_observation) at 12:00 NY: "
        "the operator scan was skipped"
    )


async def test_event_and_notice_share_one_transaction(temp_db, monkeypatch):
    async def refuse(*args, **kwargs):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(temp_db.workflows, "add_notification", refuse)
    notice = regime_unavailable_notice(
        {"vix": "ReadTimeout"}, at=noon_ny("2026-10-28"), trigger=ScanTrigger.SUGGESTION_SCAN, pead_skipped=False
    )

    with pytest.raises(RuntimeError):
        await append_scan_journal(
            temp_db, et_date="2026-10-28", scan_id=uuid4().hex, payload={"status": "regime_unavailable"}, notice=notice
        )

    assert await ranked_events(temp_db) == []
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []


async def test_a_failed_journal_write_is_recorded_by_type_name_and_still_skips_the_scan(desk, temp_db, monkeypatch):
    async def refuse(*args, **kwargs):
        raise RuntimeError("secret outbox detail")

    monkeypatch.setattr(temp_db.workflows, "add_notification", refuse)
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    assert await suggestion(desk) is None

    assert await ranked_events(temp_db) == []  # the event rolled back with its notice
    assert desk.last_scan_summary["scan_journal_error"] == "RuntimeError"
    assert desk.last_scan_summary["gate_reason"] == "market regime unavailable"
    desk.readiness.observe.assert_any_await(
        HealthComponent.SCAN, False, "Regime unavailable (vix: ReadTimeout); scan skipped"
    )


async def test_any_regime_exception_is_classified_by_type_name(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = RuntimeError("provider said something private")

    assert await suggestion(desk) is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert p["status"] == "regime_unavailable"
    assert p["gate"] == {"rule": "regime_unavailable", "missing": [], "errors": {"regime": "RuntimeError"}}
    assert (p["regime"]["inputs"], p["regime"]["missing"], p["regime"]["vix"]) == ({}, [], None)
    [(_, notice)] = (await notices(temp_db)).items()
    assert notice["text"].startswith("⚠️ Market regime unavailable (regime: RuntimeError) at ")
    desk.readiness.observe.assert_any_await(
        HealthComponent.SCAN, False, "Regime unavailable (regime: RuntimeError); scan skipped"
    )
    assert "private" not in json.dumps([p, notice, desk.last_scan_summary])


async def test_a_missing_decision_input_beside_vix_is_named_but_context_inputs_are_not(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = RegimeUnavailable(
        {
            "vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout"),
            "2y": MacroInput("2y", None, "fred:DGS2", None, True, "unexpected_payload"),
            "dxy": MacroInput("dxy", None, "yfinance:DX-Y.NYB", None, False, "ReadTimeout"),
        }
    )

    await suggestion(desk)

    [event] = await ranked_events(temp_db)
    assert event["payload"]["gate"] == {
        "rule": "regime_unavailable",
        "missing": ["vix", "2y"],
        "errors": {"vix": "ReadTimeout", "2y": "unexpected_payload"},
    }
    assert event["payload"]["regime"]["missing"] == ["vix", "2y", "dxy"]  # the block keeps every input


# --- no regime read before the gates --------------------------------------------------------


async def test_no_regime_read_on_an_idle_or_locked_out_scan(desk, temp_db):
    async def equity_closed(instrument_type="all", timestamp=None):
        return (instrument_type != "equity", "test")

    desk.session_provider.is_session_active.side_effect = equity_closed
    assert await desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL) is None
    desk.regime_detector.get_regime.assert_not_awaited()

    desk.session_provider.is_session_active.side_effect = None
    desk.calendar.check.return_value = lockout_check("CPI release", datetime(2026, 10, 28, 12, 30, tzinfo=UTC))
    assert await suggestion(desk) is None
    desk.regime_detector.get_regime.assert_not_awaited()


async def test_a_locked_out_suggestion_scan_journals_macro_lockout_with_no_regime(desk, temp_db):
    desk.calendar.check.return_value = lockout_check(
        "FOMC Press Conference", datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    )

    assert await suggestion(desk) is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["candidates"], p["regime"], p["scheduled_time_et"]) == ("macro_lockout", [], None, "14:35")
    assert p["gate"] == {
        "rule": "macro_lockout",
        "reason": "Macro event lockout: FOMC Press Conference at 18:30 UTC.",
    }
    assert p["calendar"]["lockout"]["title"] == "FOMC Press Conference"
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []  # the lockout sends no notice
    assert desk.last_scan_summary["status"] == ScanStatus.MACRO_LOCKOUT
    assert desk.last_scan_summary["gate_reason"] == "macro lockout: FOMC Press Conference at 18:30 UTC"


async def test_overnight_swing_scan_under_alpaca_execution_is_idle_and_journals_nothing(scan_desk, temp_db, app_config):
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.contracts = {
        "/MES": _futures("/MES"),
        "/MNQ": _futures("/MNQ"),
        "SPY": instrument("SPY"),
        "QQQ": instrument("QQQ"),
    }

    async def overnight(instrument_type="all", timestamp=None):
        return (instrument_type != "equity", "futures open, equity closed")

    scan_desk.session_provider.is_session_active.side_effect = overnight

    assert (
        await scan_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, trigger=ScanTrigger.SWING_SCAN)
        is None
    )

    assert await temp_db.workflows.events() == []
    scan_desk.regime_detector.get_regime.assert_not_awaited()
    scan_desk.data_fetcher.fetch_data.assert_not_called()
    scan_desk.readiness.observe.assert_awaited_once_with(
        HealthComponent.SCAN, True, "Idle: nothing eligible (2 not executable, 2 equity session closed)"
    )
    assert scan_desk.last_scan_summary == {}


@pytest.mark.parametrize("equity_open", [True, False], ids=["equity-open", "equity-closed"])
async def test_a_dynamic_scan_counts_as_eligible_only_while_the_equity_session_is_open(desk, app_config, equity_open):
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.contracts = {"/MES": _futures("/MES")}  # nothing configured is executable
    app_config.universe.dynamic.enabled = True
    desk.dynamic_universe = None  # unavailable: the scan continues on the static universe

    async def session(instrument_type="all", timestamp=None):
        return (instrument_type != "equity" or equity_open, "test")

    desk.session_provider.is_session_active.side_effect = session

    summary = await desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    if equity_open:
        assert summary is not None and summary["skipped_not_executable"] == ["/MES"]
        desk.regime_detector.get_regime.assert_awaited_once()
    else:
        assert summary is None
        desk.regime_detector.get_regime.assert_not_awaited()
        desk.readiness.observe.assert_awaited_once_with(
            HealthComponent.SCAN, True, "Idle: nothing eligible (1 not executable, 0 equity session closed)"
        )


# --- dry, restricted and operator scans ----------------------------------------------------


async def test_a_dry_run_with_vix_missing_journals_and_notifies_nothing(desk, temp_db, capsys):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    assert await desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL) is None

    assert "[DRY RUN] Market regime unavailable (vix: ReadTimeout); scan skipped." in capsys.readouterr().out
    assert await temp_db.workflows.events() == []
    desk.readiness.observe.assert_not_awaited()
    assert desk.last_scan_summary == {}


async def test_a_restricted_scan_with_vix_missing_gets_readiness_and_reply_only(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    await desk._run_background_scan(
        "AAA", notice_key="symbol-scan/AAA", busy_text="busy", failure_text="failed", log_extra={}
    )

    assert await ranked_events(temp_db) == []
    [(key, reply)] = (await notices(temp_db)).items()
    assert key.startswith("symbol-scan/AAA/")
    assert reply["text"] == TradingCopilot._scan_result_text("AAA", None)
    desk.readiness.observe.assert_any_await(
        HealthComponent.SCAN, False, "Regime unavailable (vix: ReadTimeout); scan skipped"
    )
    assert desk.last_scan_summary == {}
    assert desk._session_scan_stats == {}


async def test_an_unrestricted_no_budget_scan_sends_the_notice_without_a_journal_event(desk, temp_db):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN

    assert await desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.NONE) is None

    assert await ranked_events(temp_db) == []
    [key] = await notices(temp_db)
    assert key.startswith("regime-unavailable/")
    assert desk.last_scan_summary["gate_reason"] == "market regime unavailable"


def test_the_scan_result_text_names_a_regime_outage():
    assert TradingCopilot._scan_result_text("SPY", None) == (
        "No fresh card for SPY: the scan did not run (trading halt, closed session, macro lockout or market regime "
        "unavailable)."
    )


async def test_telegram_scan_says_the_scan_did_not_run_with_its_gate_reason(desk):
    desk.regime_detector.get_regime.side_effect = VIX_DOWN
    assert await desk.run_scan_summary_html() == "⏸️ <b>Scan Did Not Run:</b> market regime unavailable."

    desk.session_provider.is_session_active.return_value = (False, "closed")
    assert await desk.run_scan_summary_html() == (
        "⏸️ <b>Scan Did Not Run:</b> no instrument is eligible in the current session."
    )


# --- the digest ----------------------------------------------------------------------------


class RecordingOutbox:
    def __init__(self) -> None:
        self.published: list[tuple[str | None, str]] = []

    async def publish_message(self, text: str, *, key: str | None = None) -> None:
        self.published.append((key, text))


async def test_digest_counts_suggestion_scans_only_and_lists_skipped_slots(desk):
    desk.outbox = RecordingOutbox()
    await desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, trigger=ScanTrigger.SWING_SCAN)
    await desk.run_scan(use_llm=False, dry_run=False)  # an unrestricted operator scan
    await suggestion(desk, scheduled_time_et="10:35")
    desk.calendar.check.return_value = lockout_check(
        "FOMC Press Conference", datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    )
    await suggestion(desk, scheduled_time_et="14:35")

    text = await desk.publish_scan_digest()

    assert "1 scan(s), 5 scanned" in text
    assert text.endswith(" Skipped: 14:35 (macro lockout: FOMC Press Conference at 18:30 UTC).")
    [(key, published)] = desk.outbox.published
    assert published == text and key.startswith("scan-digest/")


async def test_digest_lists_both_skipped_slots_when_no_suggestion_scan_completed(desk):
    desk.outbox = RecordingOutbox()
    desk.regime_detector.get_regime.side_effect = VIX_DOWN
    await suggestion(desk, scheduled_time_et="10:35")
    desk.calendar.check.return_value = lockout_check(
        "FOMC Press Conference", datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    )
    await suggestion(desk, scheduled_time_et="14:35")
    [d] = desk._session_scan_stats

    text = await desk.publish_scan_digest()

    assert text == (
        f"📋 Scan digest {d}: no suggestion scans completed this session. Skipped: 10:35 (market regime "
        "unavailable), 14:35 (macro lockout: FOMC Press Conference at 18:30 UTC)."
    )


async def test_digest_names_an_unscheduled_skip(desk):
    desk.outbox = RecordingOutbox()
    desk.regime_detector.get_regime.side_effect = VIX_DOWN
    await suggestion(desk, scheduled_time_et=None)

    text = await desk.publish_scan_digest()

    assert text.endswith(
        "no suggestion scans completed this session. Skipped: unscheduled (market regime unavailable)."
    )


async def test_a_completed_summary_carries_trigger_budget_status_and_slot(desk):
    summary = await suggestion(desk, scheduled_time_et="10:35")

    assert (summary["trigger"], summary["budget"], summary["status"], summary["scheduled_time_et"]) == (
        "suggestion_scan",
        "full",
        "completed",
        "10:35",
    )


async def test_a_completed_scan_journals_status_calendar_and_slot(desk, temp_db):
    await suggestion(desk, scheduled_time_et="10:35")

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["gate"], p["scheduled_time_et"]) == ("completed", None, "10:35")
    assert p["calendar"]["verified"] is True and p["calendar"]["unknown_policy"] == "open"
    assert len(p["candidates"]) == 5
