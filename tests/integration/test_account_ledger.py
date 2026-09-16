"""Actual SDK/HTTP activity pagination -> journal -> replay -> performance."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from alpaca.common.exceptions import APIError
from click.testing import CliRunner

from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.cli.main import cli
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture
async def ledger_desk(alpaca_http, temp_db, app_config, request):
    venue, broker = alpaca_http
    db = (
        SignalDatabase(db_url=request.getfixturevalue("postgres_test_db"))
        if getattr(request, "param", "sqlite") == "postgres"
        else temp_db
    )
    activities = [{"id": "deposit", "activity_type": "CSD", "net_amount": "10000"}]
    # Individual partial executions, all tied to one exact order.
    activities += [
        {
            "id": f"fill-{i:03}",
            "activity_type": "FILL",
            "symbol": "SPY",
            "order_id": venue.entry["id"],
            "side": "buy",
            "qty": "0.1",
            "price": "100",
            "transaction_time": datetime.now(UTC).isoformat(),
        }
        for i in range(100)
    ]
    state = {"account": {"id": "account", "cash": "9000", "currency": "USD"}, "activities": activities, "failure": None}

    def dispatch(method, path, query, body):
        if path == "/v2/account":
            return 200, state["account"]
        if path == "/v2/account/activities":
            if state["failure"]:
                return state["failure"]
            token = query.get("page_token", [None])[0]
            rows = state["activities"]
            start = next(i + 1 for i, a in enumerate(rows) if a["id"] == token) if token else 0
            return 200, rows[start : start + int(query["page_size"][0])]
        return None

    venue.override = dispatch
    service = AccountLedgerService(broker, LedgerStore(db.workflows), app_config.accounting)
    yield service, venue, state
    await db.engine.dispose()


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
async def test_http_pagination_replay_and_report(ledger_desk):
    service, venue, state = ledger_desk
    report = await service.refresh()
    assert report.ready and report.fill_count == 100
    assert report.gross_realized == 0 and report.unrealized == Decimal(50)
    pages = [c for c in venue.calls if c[1] == "/v2/account/activities"]
    assert len(pages) == 2 and pages[1][2]["page_token"] == [state["activities"][99]["id"]]
    before = await service.store.status()
    await service.store.rebuild()
    assert await service.store.status() == before
    assert len(await service.store.activities()) == 101
    html = TelegramHtmlFormatter.format_account_ledger_html(report)
    assert "100 executions" in html and "$+50.00" in html
    assert {c[0] for c in venue.calls} == {"GET"}


@pytest.mark.parametrize(
    "problem", ["saturated", "nonadvancing", "http", "cash", "corporate", "identity", "race", "malformed"]
)
async def test_import_failures_never_publish_unreconciled_account_pnl(ledger_desk, problem):
    service, venue, state = ledger_desk
    await service.refresh()
    if problem == "saturated":
        service.config.max_pages = 1
    elif problem == "nonadvancing":
        state["activities"][-1]["id"] = state["activities"][0]["id"]
    elif problem == "http":
        state["failure"] = (403, {"code": 403, "message": "Forbidden"})
    elif problem == "cash":
        state["account"]["cash"] = "8999"
    elif problem == "corporate":
        state["activities"].append({"id": "corp", "activity_type": "SPLIT"})
    elif problem == "identity":
        state["account"]["id"] = "different-account"
    elif problem == "race":
        original = venue.override

        def racing(method, path, query, body):
            if path == "/v2/account/activities":
                state["account"]["cash"] = "8999"
            return original(method, path, query, body)

        venue.override = racing
    else:
        state["activities"][1]["price"] = "NaN"
    if problem in {"cash", "corporate", "race", "malformed"}:
        report = await service.refresh()
        assert not report.ready and report.gross_realized is None
        assert "Unavailable" in TelegramHtmlFormatter.format_account_ledger_html(report)
    else:
        with pytest.raises((ValueError, APIError)):
            await service.refresh()
        report, reason = await service.current()
        assert report is None and "failed" in reason
    # Previously imported evidence survives request/pagination failures.
    assert len(await service.store.activities()) >= 101
    assert {c[0] for c in venue.calls} == {"GET"}


async def test_initial_snapshot_failure_and_staleness_are_visible(ledger_desk):
    service, venue, _state = ledger_desk
    report = await service.refresh()
    service.config.max_age_seconds = 0
    assert (await service.current())[0] is None
    original = venue.override
    venue.override = lambda method, path, query, body: (
        (403, {"message": "denied"}) if path == "/v2/account" else original(method, path, query, body)
    )
    with pytest.raises(APIError):
        await service.refresh()
    assert "failed" in (await service.current())[1]
    assert report.ready


async def test_cli_and_telegram_performance_use_the_imported_account_ledger(ledger_desk, app_config, monkeypatch):
    service, venue, _state = ledger_desk
    copilot = TradingCopilot(
        app_config,
        db=service.store.store.db,
        broker=service.broker,
        ledger=service,
        notifier=TelegramNotifier(None, None),
    )
    await service.refresh()
    telegram = await copilot.get_performance_summary_html()
    assert "100 executions" in telegram and "Tracked trade statistics" in telegram
    # Account auth itself is covered elsewhere; this fixture uses minimal raw GET
    # account payloads so decimal preservation stays at the actual SDK boundary.
    monkeypatch.setattr(service.broker, "connect", AsyncMock(return_value=True))
    monkeypatch.setattr("agentic_trader.cli.commands.trade.get_copilot_and_config", lambda: (copilot, app_config))
    result = await asyncio.to_thread(CliRunner().invoke, cli, ["perf"])
    assert result.exit_code == 0, result.exception
    assert "100 executions" in result.output and "$+50.00" in result.output
    assert {c[0] for c in venue.calls} == {"GET"}
