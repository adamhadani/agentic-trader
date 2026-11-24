"""Actual SDK/HTTP activity pagination -> journal -> replay -> performance."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from alpaca.common.exceptions import APIError
from click.testing import CliRunner

from agentic_trader.accounting.service import CHANGED_DURING_IMPORT, FEED_BEHIND, AccountLedgerService
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.cli.main import cli
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.storage.ledger import LedgerStore
from scripts.verify_runtime import verified_account_report


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


def _activity_gets(venue) -> int:
    return sum(1 for method, path, *_ in venue.calls if method == "GET" and path == "/v2/account/activities")


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
    one_import = _activity_gets(venue)
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
        seen = _activity_gets(venue)
        report = await service.refresh()
        assert not report.ready and report.gross_realized is None
        assert "Unavailable" in TelegramHtmlFormatter.format_account_ledger_html(report)
        if problem in {"cash", "race"}:
            # The cash moved since the last committed checkpoint and only a cash mismatch is left: all three
            # reads run, and the last, stable but still unexplained, says the feed is behind.
            assert _activity_gets(venue) - seen == 3 * one_import
            assert FEED_BEHIND in report.issues and CHANGED_DURING_IMPORT not in report.issues
            # Against the committed mismatch the next refresh reads once and no longer blames the feed.
            seen = _activity_gets(venue)
            again = await service.refresh()
            assert _activity_gets(venue) - seen == one_import
            assert not again.ready and FEED_BEHIND not in again.issues
        else:
            # An issue a re-read cannot clear is committed after one read.
            assert _activity_gets(venue) - seen == one_import
    else:
        with pytest.raises((ValueError, APIError)):
            await service.refresh()
        report, reason = await service.current()
        assert report is None and "failed" in reason
    # Previously imported evidence survives request/pagination failures.
    assert len(await service.store.activities()) >= 101
    assert {c[0] for c in venue.calls} == {"GET"}


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
@pytest.mark.parametrize("landed", ["during-the-read", "before-the-refresh"])
async def test_a_fill_torn_import_is_re_read_and_commits_one_reconciled_checkpoint(ledger_desk, landed):
    """The 2026-11-23 shape on the real store: the account holds the change while the feed shows it only from
    the next read. It lands between the first read's snapshots, or before the refresh, so only the move from
    the last committed checkpoint reveals it."""
    service, venue, state = ledger_desk
    await service.refresh()
    flows = (await service.current_risk()).cash_flows
    one_import = _activity_gets(venue)
    checkpoints = len(await service.store.store.events(stream="account/ledger"))
    deposit = {"id": "late-deposit", "activity_type": "CSD", "net_amount": "100"}
    original = venue.override
    reads = 0

    def move_cash():
        state["account"]["cash"] = str(Decimal(state["account"]["cash"]) + 100)

    def lagging_feed(method, path, query, body):
        nonlocal reads
        if path == "/v2/account/activities" and not query.get("page_token"):
            reads += 1
            if reads == 1 and landed == "during-the-read":
                move_cash()
            elif reads == 2:
                state["activities"].append(deposit)
        return original(method, path, query, body)

    if landed == "before-the-refresh":
        move_cash()
    venue.override = lagging_feed
    report = await service.refresh()
    assert report.ready and report.issues == []
    assert reads == 2 and _activity_gets(venue) - one_import == 2 * one_import
    events = await service.store.store.events(stream="account/ledger")
    assert len(events) == checkpoints + 1, "Only the reconciled read is committed"
    assert (await service.current_risk()).cash_flows == flows + 100
    assert "late-deposit" in {a["id"] for a in await service.store.activities()}


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


@pytest.mark.parametrize(
    ("mode", "ledger_desk"),
    [(mode, "sqlite") for mode in ("missing", "stale", "failed", "unreconciled", "ready", "inflight")]
    + [pytest.param("inflight", "postgres", marks=pytest.mark.postgres)],
    indirect=["ledger_desk"],
)
async def test_runtime_verification_is_passive_and_preserves_importer_ownership(ledger_desk, mode):
    service, venue, state = ledger_desk
    if mode != "missing":
        if mode == "unreconciled":
            state["account"]["cash"] = "8999"
        await service.refresh()
    if mode == "stale":
        service.config.max_age_seconds = 0
    if mode in ("failed", "inflight"):
        token = await service.store.begin("account")
        if mode == "failed":
            await service.store.fail(token, "TimeoutError")
    before = await service.store.status()
    requests = len(venue.calls)
    if mode in ("ready", "inflight"):
        report = await verified_account_report(service)
        assert report.ready
    else:
        with pytest.raises(RuntimeError, match="Account activity"):
            await verified_account_report(service)
    assert len(venue.calls) == requests, "Verification must not launch a second broker importer"
    assert await service.store.status() == before
    if mode == "inflight":
        assert await service.store.commit(token, state["activities"], before), (
            "Verifier stole the daemon's import token"
        )


@pytest.mark.parametrize("ledger_desk", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True)
@pytest.mark.parametrize("baseline_kind", ["CSD", "JNLC"])
async def test_sdk_account_risk_tracks_marks_and_transfers_and_survives_replay(ledger_desk, baseline_kind):
    service, venue, state = ledger_desk
    state["activities"][0]["activity_type"] = baseline_kind
    await service.refresh()
    baseline = await service.current_risk()
    assert baseline.equity == Decimal(10050) and baseline.drawdown_pct == 0
    assert baseline.cash_flows == (10000 if baseline_kind == "CSD" else 0)
    assert bool(baseline.baseline_cash_journal_fingerprints) is (baseline_kind == "JNLC")

    venue.position["unrealized_pl"] = "-452.5"
    await service.refresh()
    loss = await service.current_risk()
    assert loss.drawdown_pct == Decimal(".05")

    for kind, amount in (("CSD", "5000"), ("CSW", "-3000")):
        state["activities"].append({"id": kind, "activity_type": kind, "net_amount": amount})
        state["account"]["cash"] = str(Decimal(state["account"]["cash"]) + Decimal(amount))
        await service.refresh()
        risk = await service.current_risk()
        assert risk.drawdown_pct == loss.drawdown_pct
        assert risk.adjusted_equity == loss.adjusted_equity
        assert risk.baseline_observed_at == baseline.observed_at

    state["activities"].append({"id": "fee", "activity_type": "FEE", "net_amount": "-20"})
    state["account"]["cash"] = str(Decimal(state["account"]["cash"]) - 20)
    await service.refresh()
    risk = await service.current_risk()
    assert risk.adjusted_equity == loss.adjusted_equity - 20
    assert risk.drawdown_pct > loss.drawdown_pct, "Fees are performance, not external capital"

    before = await service.store.status()
    await service.store.rebuild()
    restarted = AccountLedgerService(service.broker, LedgerStore(service.store.store), service.config)
    assert await restarted.store.status() == before
    assert (await restarted.current_risk()).fingerprint == risk.fingerprint
    assert {call[0] for call in venue.calls} == {"GET"}


@pytest.mark.parametrize("problem", ["http", "unreconciled", "journal", "revision", "retraction"])
async def test_sdk_invalid_risk_preserves_history_and_never_defaults_to_zero(ledger_desk, problem):
    service, venue, state = ledger_desk
    await service.refresh()
    baseline = await service.current_risk()
    if problem == "http":
        state["failure"] = (403, {"message": "denied"})
        with pytest.raises(APIError):
            await service.refresh()
    else:
        if problem == "unreconciled":
            state["account"]["cash"] = "999999"
        elif problem == "journal":
            state["activities"].append({"id": "journal", "activity_type": "JNLC", "net_amount": "-100"})
            state["account"]["cash"] = "8900"
        elif problem == "revision":
            state["activities"][0]["net_amount"] = "20000"
            state["account"]["cash"] = "19000"
        else:
            state["activities"].pop(0)
            state["account"]["cash"] = "-1000"
        report = await service.refresh()
        assert report.ready is (problem != "unreconciled")
    with pytest.raises(ValueError):
        await service.current_risk()
    checkpoint = await service.store.status()
    assert checkpoint["risk"] == baseline.model_dump(mode="json")
    await service.store.rebuild()
    assert await service.store.status() == checkpoint
    with pytest.raises(ValueError):
        await service.current_risk()
    assert {call[0] for call in venue.calls} == {"GET"}
