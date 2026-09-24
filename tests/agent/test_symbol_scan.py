"""`/scan SYMBOL`: an operator single-symbol scan from Telegram.

A configured contract gets a background single-contract ``run_scan`` under the NONE
budget with no duplicate exemption. An unconfigured US equity is validated with the
dynamic-universe machinery (the same asset/instrument filters and the liquidity gate
against the latest journaled suggestion-scan reference) and scanned as one dynamic name
with source ``operator``, still sharing the one-dynamic-card-per-session cap.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent import copilot as copilot_module
from agentic_trader.config import ScanBudget
from agentic_trader.execution.durable import EventKind
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.screeners.dynamic_universe import AssetInfo, ScreenerEntry, StaticReference
from agentic_trader.storage.workflow import WorkflowStore
from tests.agent.test_card_freshness_tap import (  # noqa: F401  (tap_desk is a fixture)
    message_texts,
    record_card,
    session_info,
    tap_desk,
)
from tests.agent.test_dynamic_universe_scan import (  # noqa: F401  (fixtures)
    FakeSource,
    asset,
    budget_desk,
    dailies,
    daily_frame,
    dynamic_desk,
    strategy_scanned,
)


SCANNING = "🔍 Scanning {}… a card or a result message will follow."
# `journal_reference` default: the threshold equals the reference value.
_DEFAULT = object()


async def finish_background_scans(copilot):
    await asyncio.gather(*list(copilot.background_scan_tasks))


async def journal_reference(
    desk,
    *,
    value: float | None = 1.0e8,
    threshold: float | None | object = _DEFAULT,
    feed: str | None = None,
    built_at: datetime | None = None,
    percentile: float = 0.25,
    names: int = 25,
) -> None:
    """Append one ``dynamic_universe_built`` event, as a scheduled suggestion scan journals it."""
    built_at = built_at or datetime.now(UTC)
    payload = {
        "scan_id": "s",
        "built_at": built_at.isoformat(),
        "available": True,
        "error": None,
        "raw_counts": {},
        "reasons": {},
        "members": [],
        "scanned": [],
        "excluded": {},
        "feed": feed or desk.config.market_data.alpaca_feed,
        "threshold": value if threshold is _DEFAULT else threshold,
        "reference": {"percentile": percentile, "names": names, "value": value, "floor": 0},
    }
    workflows = desk.db.workflows
    async with desk.db.session_factory() as session, session.begin():
        await workflows.lock(session)
        await workflows.append(
            session,
            stream=f"scan/{desk.session_start_et(built_at).date().isoformat()}",
            kind=EventKind.DYNAMIC_UNIVERSE_BUILT,
            payload=payload,
        )


@pytest.fixture
def operator_desk(dynamic_desk):  # noqa: F811
    dynamic_desk.session_provider.get_session_info.return_value = session_info(
        next_close=datetime.now(UTC) + timedelta(hours=2)
    )
    return dynamic_desk


# --------------------------------------------------------------------------
# Configured contracts
# --------------------------------------------------------------------------


async def test_configured_contract_runs_a_background_single_contract_scan_without_a_dedup_exemption(tap_desk):  # noqa: F811
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await tap_desk.request_symbol_scan("SPY")

    assert reply == ExecutionReply(True, SCANNING.format("SPY"))
    await finish_background_scans(tap_desk)
    tap_desk.run_scan.assert_awaited_once_with(
        symbols=["SPY"],
        budget=ScanBudget.NONE,
        scan_lock_timeout=copilot_module.REEVALUATE_SCAN_WAIT_SECONDS,
    )


@pytest.mark.parametrize("requested", ["MES", "/MES", "/mes", " mes "])
async def test_a_futures_contract_matches_with_or_without_its_slash(tap_desk, requested):  # noqa: F811
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await tap_desk.request_symbol_scan(requested)

    assert reply.ok is True and "/MES" in reply.text
    await finish_background_scans(tap_desk)
    assert tap_desk.run_scan.await_args.kwargs["symbols"] == ["/MES"]
    tap_desk.session_provider.get_session_info.assert_awaited_with("/MES")


async def test_symbol_scan_does_not_run_inline(tap_desk):  # noqa: F811
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_scan(**kwargs):
        started.set()
        await release.wait()
        return {"sent": 1, "runners_up": []}

    tap_desk.run_scan = AsyncMock(side_effect=slow_scan)

    reply = await asyncio.wait_for(tap_desk.request_symbol_scan("SPY"), timeout=1)

    assert reply.ok is True and not release.is_set()
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await finish_background_scans(tap_desk)


async def test_symbol_scan_logs_an_operator_event(tap_desk, caplog):  # noqa: F811
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    with caplog.at_level("INFO", logger="copilot"):
        await tap_desk.request_symbol_scan("spy")
        await finish_background_scans(tap_desk)

    [record] = [r for r in caplog.records if getattr(r, "event", None) == "operator_symbol_scan"]
    assert record.symbol == "SPY" and record.configured is True


async def test_a_live_card_refuses_the_symbol_scan(tap_desk, temp_db):  # noqa: F811
    live = await record_card(temp_db)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.request_symbol_scan("SPY")

    assert reply == ExecutionReply(False, f"A live card for SPY already exists (#{live}).")
    assert tap_desk.background_scan_tasks == set()
    tap_desk.run_scan.assert_not_awaited()


async def test_a_closed_market_refuses_with_the_next_open(tap_desk):  # noqa: F811
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.request_symbol_scan("SPY")

    assert reply.ok is False
    assert reply.text == "Market closed; Next regular open: 2026-09-24 13:30 UTC (09:30 NY)."
    assert tap_desk.background_scan_tasks == set()


async def test_an_unavailable_session_refuses(tap_desk):  # noqa: F811
    tap_desk.session_provider.get_session_info.side_effect = RuntimeError("clock down")
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.request_symbol_scan("SPY")

    assert reply == ExecutionReply(False, "⚠️ Market session unavailable; try again shortly.")
    tap_desk.run_scan.assert_not_awaited()


async def test_a_second_request_for_the_same_symbol_is_refused_while_one_runs(tap_desk):  # noqa: F811
    release = asyncio.Event()

    async def slow_scan(**kwargs):
        await release.wait()
        return {"sent": 0, "runners_up": []}

    tap_desk.run_scan = AsyncMock(side_effect=slow_scan)
    first = await tap_desk.request_symbol_scan("SPY")
    second = await tap_desk.request_symbol_scan("SPY")
    release.set()
    await finish_background_scans(tap_desk)

    assert first.ok is True
    assert second == ExecutionReply(False, "A scan of SPY is already running.")
    tap_desk.run_scan.assert_awaited_once()


async def test_no_setup_is_reported_through_the_outbox(tap_desk, temp_db):  # noqa: F811
    tap_desk.run_scan = AsyncMock(
        return_value={"sent": 0, "runners_up": [{"contract": "SPY", "reason": "rejected: <weak>"}]}
    )

    await tap_desk.request_symbol_scan("SPY")
    await finish_background_scans(tap_desk)

    [text] = await message_texts(temp_db)
    assert "No valid setup for SPY right now" in text and "rejected: &lt;weak&gt;" in text
    tap_desk.notifier.send_message.assert_not_called()


async def test_a_busy_scanner_is_reported_through_the_outbox(tap_desk, temp_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(copilot_module, "REEVALUATE_SCAN_WAIT_SECONDS", 0.05)
    await tap_desk._scan_lock.acquire()
    try:
        await tap_desk.request_symbol_scan("SPY")
        await finish_background_scans(tap_desk)
    finally:
        tap_desk._scan_lock.release()

    [text] = await message_texts(temp_db)
    assert "SPY" in text and "scanner busy, try again shortly" in text


async def test_a_failed_scan_is_reported_through_the_outbox(tap_desk, temp_db):  # noqa: F811
    tap_desk.run_scan = AsyncMock(side_effect=RuntimeError("provider down"))

    await tap_desk.request_symbol_scan("SPY")
    await finish_background_scans(tap_desk)

    [text] = await message_texts(temp_db)
    assert "SPY" in text and "failed; see logs" in text


async def test_shutdown_cancels_symbol_scans_and_reevaluations_together(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    started, cancelled = [], []

    async def hanging_scan(**kwargs):
        started.append(kwargs["symbols"])
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(kwargs["symbols"])
            raise

    tap_desk.run_scan = AsyncMock(side_effect=hanging_scan)
    await tap_desk.reevaluate_signal(sid)
    await tap_desk.request_symbol_scan("/MES")
    while len(started) < 2:
        await asyncio.sleep(0)

    await asyncio.wait_for(tap_desk.cancel_background_scans(), timeout=1)

    assert sorted(cancelled) == [["/MES"], ["SPY"]]
    assert tap_desk.background_scan_tasks == set()
    assert await message_texts(temp_db) == []  # a cancelled scan reports nothing


# --------------------------------------------------------------------------
# Unconfigured equities: the dynamic-universe path
# --------------------------------------------------------------------------


def named(symbol: str, name: str, **fields) -> AssetInfo:
    return AssetInfo(
        **{
            "symbol": symbol,
            "name": name,
            "asset_class": "us_equity",
            "exchange": "NYSE",
            "status": "active",
            "tradable": True,
            **fields,
        }
    )


async def refusal_notice(desk, symbol: str) -> str:
    """Request a symbol scan that the background validation refuses; returns its outbox text."""
    desk.run_scan = AsyncMock()
    reply = await desk.request_symbol_scan(symbol)
    assert reply == ExecutionReply(True, SCANNING.format(symbol.strip().upper()))
    await finish_background_scans(desk)
    desk.run_scan.assert_not_awaited()
    [text] = await message_texts(desk.db)
    return text


@pytest.mark.parametrize(
    ("symbol", "assets", "phrase"),
    [
        pytest.param("SOXS", {}, "not an active Alpaca US equity", id="asset_missing"),
        pytest.param(
            "SOXS",
            {"SOXS": named("SOXS", "Direxion Daily Semiconductor Bear 3X Shares")},
            "leveraged/inverse fund",
            id="leveraged",
        ),
        pytest.param(
            "SOXS",
            {"SOXS": named("SOXS", "Acme Acquisition Corp Warrants")},
            "warrant/right/unit or volatility/option-income product",
            id="instrument",
        ),
        pytest.param(
            "SOXS",
            {"SOXS": named("SOXS", "Acme Inc", exchange="OTC")},
            "not listed on a major US exchange",
            id="exchange",
        ),
        pytest.param(
            "SOXS", {"SOXS": named("SOXS", "Acme Inc", status="inactive")}, "not an active asset", id="inactive"
        ),
        pytest.param(
            "SOXS", {"SOXS": named("SOXS", "Acme Inc", tradable=False)}, "not tradable at Alpaca", id="untradable"
        ),
        pytest.param(
            "SOXS", {"SOXS": named("SOXS", "Acme Inc", asset_class="crypto")}, "not a US equity", id="asset_class"
        ),
        pytest.param("BTCX", {"BTCX": named("BTCX", "Acme Inc")}, "the symbol is routed as crypto", id="crypto_prefix"),
        pytest.param("BRK.B", {}, "not a plain US equity symbol (1-5 letters)", id="shape"),
    ],
)
async def test_an_unconfigured_symbol_failing_the_asset_filters_is_refused_through_the_outbox(
    operator_desk, symbol, assets, phrase
):
    operator_desk.dynamic_universe = FakeSource([], assets)
    await journal_reference(operator_desk)

    text = await refusal_notice(operator_desk, symbol)

    assert text == f"Cannot scan {symbol}: {phrase}."


async def test_the_asset_list_is_read_in_the_background_not_in_the_handler(operator_desk):
    """A cold Alpaca asset list must never hold the serialized Telegram handler."""
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_assets():
        started.set()
        await release.wait()
        return {"NEWA": asset("NEWA")}

    operator_desk.dynamic_universe.assets = slow_assets
    await journal_reference(operator_desk)
    operator_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await asyncio.wait_for(operator_desk.request_symbol_scan("NEWA"), timeout=1)

    assert reply == ExecutionReply(True, SCANNING.format("NEWA")) and not release.is_set()
    await asyncio.wait_for(started.wait(), timeout=1)
    # The task is registered before any await, so a repeat is refused at once.
    assert await operator_desk.request_symbol_scan("NEWA") == ExecutionReply(
        False, "A scan of NEWA is already running."
    )
    release.set()
    await finish_background_scans(operator_desk)
    operator_desk.run_scan.assert_awaited_once()


async def test_a_disabled_dynamic_universe_refuses_unconfigured_symbols_directly(operator_desk, app_config):
    """The WS3 kill switch is honoured even with a source injected."""
    app_config.universe.dynamic.enabled = False
    await journal_reference(operator_desk)
    operator_desk.run_scan = AsyncMock()

    reply = await operator_desk.request_symbol_scan("NEWA")

    assert reply == ExecutionReply(
        False, "Cannot scan NEWA: unconfigured symbols need the dynamic universe, which is disabled."
    )
    assert operator_desk.background_scan_tasks == set()
    assert operator_desk.dynamic_universe.assets_calls == 0
    operator_desk.run_scan.assert_not_awaited()


@pytest.mark.parametrize("failure", ["missing", "error", "timeout"])
async def test_an_unavailable_asset_lookup_is_refused(operator_desk, monkeypatch, failure):
    if failure == "missing":
        operator_desk.dynamic_universe = None
    elif failure == "error":
        operator_desk.dynamic_universe = FakeSource([], {}, fail="assets")
    else:
        monkeypatch.setattr(copilot_module, "DYNAMIC_UNIVERSE_TIMEOUT_SECONDS", 0.01)

        async def slow_assets():
            await asyncio.sleep(1)
            return {}

        operator_desk.dynamic_universe.assets = slow_assets

    text = await refusal_notice(operator_desk, "NEWA")

    assert text == "Cannot scan NEWA: asset lookup unavailable; try again shortly."


async def test_a_failing_reference_read_is_reported_as_a_failure(operator_desk, caplog):
    operator_desk.db.workflows.recent_events = AsyncMock(side_effect=RuntimeError("db down"))

    with caplog.at_level("ERROR", logger="copilot"):
        text = await refusal_notice(operator_desk, "NEWA")

    assert text == "Scan of NEWA failed; see logs."
    assert any(getattr(r, "event", None) == "operator_symbol_scan_failed" for r in caplog.records)


NO_REFERENCE = (
    "Cannot scan NEWA: no recent liquidity reference (the scheduled suggestion scan records one); "
    "try after the next suggestion scan."
)


@pytest.mark.parametrize(
    "reference",
    [
        pytest.param(None, id="none"),
        pytest.param({"feed": "other"}, id="wrong_feed"),
        pytest.param({"built_at": datetime.now(UTC) - timedelta(days=7, hours=1)}, id="too_old"),
        pytest.param({"built_at": datetime.now(UTC) + timedelta(hours=1)}, id="future"),
        pytest.param({"value": None, "threshold": None}, id="null_threshold"),
    ],
)
async def test_an_unconfigured_symbol_without_a_recent_reference_is_refused(operator_desk, reference):
    if reference is not None:
        await journal_reference(operator_desk, **reference)

    text = await refusal_notice(operator_desk, "NEWA")

    assert text == NO_REFERENCE


async def test_a_newer_wrong_feed_reference_never_shadows_an_older_valid_one(operator_desk):
    await journal_reference(operator_desk, value=6.0e7, built_at=datetime.now(UTC) - timedelta(days=2))
    await journal_reference(operator_desk, value=9.0e9, feed="other")
    operator_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    reference = operator_desk.run_scan.await_args.kwargs["operator_dynamic"].reference
    assert reference == StaticReference(percentile=0.25, names=25, value=6.0e7)


async def test_the_latest_usable_reference_is_reconstructed_and_passed_to_run_scan(operator_desk, caplog):
    await journal_reference(operator_desk, value=5.0e7, built_at=datetime.now(UTC) - timedelta(days=6))
    await journal_reference(operator_desk, value=8.0e7, percentile=0.3, names=31)
    await journal_reference(operator_desk, value=None, threshold=None)  # newest, but unusable
    operator_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    with caplog.at_level("INFO", logger="copilot"):
        reply = await operator_desk.request_symbol_scan("newa")
        await finish_background_scans(operator_desk)

    assert reply == ExecutionReply(True, SCANNING.format("NEWA"))
    kwargs = operator_desk.run_scan.await_args.kwargs
    assert kwargs["symbols"] == ["NEWA"] and kwargs["budget"] == ScanBudget.NONE
    operator = kwargs["operator_dynamic"]
    assert operator.selection.members == (
        ScreenerEntry(symbol="NEWA", source="operator", rank=0, price=None, percent_change=None),
    )
    assert operator.asset == asset("NEWA")
    assert operator.reference == StaticReference(percentile=0.3, names=31, value=8.0e7)
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "operator_symbol_scan"]
    assert record.symbol == "NEWA" and record.configured is False


async def test_an_operator_dynamic_card_is_flagged_dynamic_with_source_operator(operator_desk, temp_db, app_config):
    contracts_before = dict(app_config.contracts)
    await journal_reference(operator_desk)
    before = len([e for e in await temp_db.workflows.events() if e["kind"] == EventKind.DYNAMIC_UNIVERSE_BUILT])

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    [card] = await temp_db.get_recent_signals(limit=10)
    assert card["contract"] == "NEWA"
    provenance = card["decision_provenance"]
    assert provenance["dynamic"] is True and provenance["dynamic_source"] == "operator"
    assert provenance["budget"] == "none"
    assert strategy_scanned(operator_desk) == ["NEWA"]
    [call] = operator_desk.strategy_engine.scan_contract.call_args_list
    assert call.kwargs["native_only"] is True
    assert app_config.contracts == contracts_before
    # An operator scan never journals a scheduled-scan dynamic universe.
    after = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.DYNAMIC_UNIVERSE_BUILT]
    assert len(after) == before
    assert await message_texts(temp_db) == []  # the card is the result


async def test_an_operator_dynamic_name_shares_the_dynamic_card_cap(operator_desk, temp_db):
    await journal_reference(operator_desk)
    await temp_db.record_signal(
        "OLDX", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"dynamic": True, "rank": 1}
    )

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["OLDX"]
    [text] = await message_texts(temp_db)
    assert "NEWA" in text and "correlation group already has a card this session" in text


async def test_a_configured_contract_under_none_ignores_the_group_cap(operator_desk, temp_db):
    """BBB shares sector_x with a card recorded today; the NONE budget still skips the group cap."""
    await temp_db.record_signal("AAA", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"rank": 1})
    await temp_db.record_signal(
        "OLDX", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"dynamic": True, "rank": 1}
    )

    await operator_desk.request_symbol_scan("BBB")
    await finish_background_scans(operator_desk)

    assert "BBB" in {s["contract"] for s in await temp_db.get_recent_signals(limit=10)}


@pytest.mark.parametrize(
    ("daily", "phrase"),
    [
        pytest.param({"volume": 1_000.0}, "below the liquidity threshold", id="dollar_volume"),
        pytest.param({"sessions": 10}, "fewer than 20 completed daily bars", id="insufficient_bars"),
    ],
)
async def test_a_gated_operator_name_reports_why(operator_desk, temp_db, dailies, daily, phrase):  # noqa: F811
    dailies["NEWA"] = daily_frame(**daily)
    await journal_reference(operator_desk)

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    assert await temp_db.get_recent_signals(limit=10) == []
    assert "NEWA" not in strategy_scanned(operator_desk)
    [text] = await message_texts(temp_db)
    assert text == f"NEWA was not scanned: {phrase}."


async def test_a_failed_operator_fetch_reports_market_data_unavailable(operator_desk, temp_db):
    fetch = operator_desk.data_fetcher.fetch_data.side_effect

    def failing(contract, ticker, include_fifteen_min=True):
        if contract == "NEWA":
            raise RuntimeError("feed down")
        return fetch(contract, ticker, include_fifteen_min)

    operator_desk.data_fetcher.fetch_data.side_effect = failing
    await journal_reference(operator_desk)

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    [text] = await message_texts(temp_db)
    assert text == "NEWA was not scanned: market data unavailable."


async def test_the_liquidity_decision_uses_the_journaled_reference(operator_desk, temp_db):
    """NEWA trades ~$200M/day: a $500M journaled reference excludes it (no static bars are read)."""
    await journal_reference(operator_desk, value=5.0e8)

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    fetched = [c.args[0] for c in operator_desk.data_fetcher.fetch_data.call_args_list]
    assert fetched == ["NEWA"]
    [text] = await message_texts(temp_db)
    assert "below the liquidity threshold" in text


async def test_a_halted_desk_runs_no_operator_scan(operator_desk, temp_db):
    await journal_reference(operator_desk)

    async def halted_check():
        operator_desk.is_halted = True
        return True

    operator_desk.check_halt_state = halted_check

    await operator_desk.request_symbol_scan("NEWA")
    await finish_background_scans(operator_desk)

    assert await temp_db.get_recent_signals(limit=10) == []
    [text] = await message_texts(temp_db)
    assert "the scan did not run" in text


# --------------------------------------------------------------------------
# Journal read
# --------------------------------------------------------------------------


async def test_recent_events_reads_one_kind_from_the_named_streams_newest_first(temp_db):
    workflows = temp_db.workflows
    async with temp_db.session_factory() as session, session.begin():
        await workflows.lock(session)
        for stream, kind, n in [
            ("scan/2026-09-20", EventKind.DYNAMIC_UNIVERSE_BUILT, 1),
            ("scan/2026-09-21", EventKind.SCAN_CANDIDATES_RANKED, 2),
            ("scan/2026-09-21", EventKind.DYNAMIC_UNIVERSE_BUILT, 3),
            ("scan/2026-09-01", EventKind.DYNAMIC_UNIVERSE_BUILT, 4),
        ]:
            await workflows.append(session, stream=stream, kind=kind, payload={"n": n})

    events = await workflows.recent_events(
        EventKind.DYNAMIC_UNIVERSE_BUILT, streams=["scan/2026-09-20", "scan/2026-09-21"]
    )

    assert [e["payload"]["n"] for e in events] == [3, 1]


async def test_recent_events_is_scoped_and_empty_without_streams(temp_db):
    other = WorkflowStore(temp_db)
    other.scope = "other-environment/paper"
    for store in (temp_db.workflows, other):
        async with temp_db.session_factory() as session, session.begin():
            await store.lock(session)
            await store.append(
                session,
                stream="scan/2026-09-21",
                kind=EventKind.DYNAMIC_UNIVERSE_BUILT,
                payload={"scope": store.scope},
            )

    events = await temp_db.workflows.recent_events(EventKind.DYNAMIC_UNIVERSE_BUILT, streams=["scan/2026-09-21"])

    assert [e["payload"]["scope"] for e in events] == [temp_db.workflows.scope]
    assert await temp_db.workflows.recent_events(EventKind.DYNAMIC_UNIVERSE_BUILT, streams=[]) == []
