"""Parity suite for the catalog card seam: the PEAD drift path, pinned whole.

Written against the pre-seam code (main bf10c76) and kept as the regression suite for the
``CatalogCardSource`` refactor: the same candidates, provenance, card payload, journal event,
budget refusals, lateness skip, gated skips (halt, lockout, regime unavailable) and unavailable
decisions on the existing PEAD fixtures. Every assertion compares a whole structure, so a renamed,
added or dropped key fails here even when the narrower tests in ``test_scan_drift`` still pass.
The tests under "the seam itself" were added with the refactor and pin the seam's own surface.
"""

import dataclasses
import inspect
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.catalog_sources import (
    DRIFT_POSITION_CAP_REACHED,
    CatalogCardSource,
    PeadDriftSource,
    pead_base_rate_block,
)
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.agent.insider_watch import InsiderWatchSource
from agentic_trader.agent.macro import MacroInput
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.constants import AssetClass
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_alert_card
from agentic_trader.research.apriori.pead_base_rate import PeadBaseRate
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID, DriftPreparation, drift_card_facts
from tests.agent.test_scan_drift import (  # noqa: F401  (budget_desk, drift_desk, broken_drift_desk are fixtures)
    POLICY,
    VERSION,
    _catalog_snapshot,
    _drift_events,
    _gate,
    _late,
    _open_position,
    _plant_signal,
    _signals,
    broken_drift_desk,
    budget_desk,
    drift_candidate,
    drift_desk,
    drift_event,
    ok_prep,
    suggestion_scan,
    today_ny,
)
from tests.research.apriori.test_pead_base_rate import payload


def _session() -> str:
    return today_ny().isoformat()


def _event_doc(symbol: str, outcome: str) -> dict[str, Any]:
    return {**drift_event(symbol), "outcome": outcome}


def _ok_payload(scan_id: str, events: list[dict[str, Any]], *, open_positions: int = 0, skipped=None) -> dict:
    return {
        "scan_id": scan_id,
        "session": _session(),
        "status": "ok",
        "reason": None,
        "version_id": VERSION,
        "report_date": "2026-10-26",
        "page_sha256": "ab" * 32,
        "counts": {"rows": 3, "events": 1, "reasons": {"surprise": 2}},
        "skipped": skipped or {},
        "time_exit_at": "2026-11-24T20:45:00+00:00",
        "elapsed_seconds": 0.0,
        "open_drift_positions": open_positions,
        "events": events,
    }


def _bare_payload(scan_id: str, status: str, reason: str, *, version_id=VERSION, open_positions=None) -> dict:
    return {
        "scan_id": scan_id,
        "session": _session(),
        "status": status,
        "reason": reason,
        "version_id": version_id,
        "report_date": None,
        "page_sha256": None,
        "counts": {},
        "skipped": {},
        "time_exit_at": None,
        "elapsed_seconds": None,
        "open_drift_positions": open_positions,
        "events": [],
    }


async def _only_event(db) -> dict:
    [event] = await _drift_events(db)
    assert event["stream"] == f"scan/{_session()}"
    assert event["kind"] == EventKind.PEAD_DECISION
    return event


async def _notification_payloads(db) -> list[dict]:
    return [n.payload for n in await db.workflows.list_work(WorkKind.NOTIFICATION)]


async def _drift_cards(db) -> list[dict]:
    return [
        p["arguments"]
        for p in await _notification_payloads(db)
        if p["kind"] == NotificationKind.SIGNAL and p["arguments"]["strategy"] == PEAD_STRATEGY_ID
    ]


async def _messages(db) -> list[dict]:
    return [p for p in await _notification_payloads(db) if p["kind"] == NotificationKind.MESSAGE]


def _with_elapsed(payload: dict, elapsed: float | None) -> dict:
    return {**payload, "elapsed_seconds": elapsed}


# --- a sent card: candidate, provenance, payload, journal --------------------------------------


async def test_a_sent_drift_card_is_pinned_whole(drift_desk, temp_db, app_config):  # noqa: F811
    summary = await suggestion_scan(drift_desk)

    # Candidates: the drift candidate follows every native one through the deterministic pass, then
    # gets the caller's LLM pass (commentary) after the native winner, with no news block.
    calls = drift_desk.evaluator.evaluate_candidate.call_args_list
    assert [(c.args[0].contract, c.kwargs["use_llm"]) for c in calls] == [
        ("AAA", False),
        ("BBB", False),
        ("CCC", False),
        ("DDD", False),
        ("EEE", False),
        ("WINR", False),
        ("DDD", True),
        ("WINR", True),
    ]
    winr_call = calls[-1]
    assert winr_call.args[0] == drift_candidate()
    assert winr_call.kwargs["news_context"] is None
    assert winr_call.kwargs["current_open_notional"] == 100.0  # the native card joined the book first
    # The drift-only name is fetched once, as a synthetic per-scan contract, and never strategy-scanned.
    fetched = sorted(tuple(c.args) for c in drift_desk.data_fetcher.fetch_data.call_args_list)
    assert fetched == [("AAA", "AAA"), ("BBB", "BBB"), ("CCC", "CCC"), ("DDD", "DDD"), ("EEE", "EEE"), ("WINR", "WINR")]
    assert sorted(c.args[0].contract for c in drift_desk.strategy_engine.scan_contract.call_args_list) == [
        "AAA",
        "BBB",
        "CCC",
        "DDD",
        "EEE",
    ]

    # Provenance: the whole recorded block.
    [winr] = [s for s in await _signals(temp_db) if s["contract"] == "WINR"]
    assert (winr["strategy"], winr["timeframe"], winr["alpha_version"], winr["alpha_policy"]) == (
        PEAD_STRATEGY_ID,
        "1d",
        VERSION,
        POLICY,
    )
    provenance = winr["decision_provenance"]
    regime = provenance.pop("regime")
    assert set(regime) >= {"vix", "risk_multiplier", "macro_stress"}
    assert provenance == {
        "candle_timestamp": "2026-09-22T00:00:00+00:00",
        "alpha_score": None,
        "contributors": [],
        "account_risk_fingerprint": None,
        "paper_probe": True,
        "setup_quality": 0.0,
        "rank": 1,
        "candidates_considered": 1,
        "budget": "full",
        "shadow_ranker": None,
        "llm_verdict": None,
        "book_sizing": None,
        "corporate_actions": None,
        "news": None,
        "pead_event": drift_event("WINR"),
        "pead_base_rate": {"status": "unavailable"},
    }

    # Payload: the durable notification's arguments, exactly.
    [card] = await _drift_cards(temp_db)
    assert card == {
        "eval_res": {},
        "strategy": PEAD_STRATEGY_ID,
        "regime_summary": "test",
        "probe_risk_cap": app_config.alpha_pipeline.probe_risk_dollars,
        "drift": {
            "surprise_pct": 10.0,
            "z": 2.0,
            "report_date": "2026-10-26",
            "holding_sessions": 20,
            "time_exit_at": "2026-11-24T20:45:00+00:00",
            "base_rate": {"status": "unavailable"},
        },
        "book_sizing": None,
        "signal_id": winr["id"],
    }

    # Journal: one decision on the session's scan stream, keyed by the scan.
    event = await _only_event(temp_db)
    payload = event["payload"]
    assert payload == _with_elapsed(
        _ok_payload(summary["scan_id"], [_event_doc("WINR", "sent")]), payload["elapsed_seconds"]
    )
    assert isinstance(payload["elapsed_seconds"], float)
    assert summary["drift"] == {"status": "ok", "reason": None, "events": 1, "sent": 1}
    assert "drift_journal_error" not in summary
    assert await _messages(temp_db) == []
    # The drift card is a separate ranking: never a runner-up of the native one, never in its journal.
    assert [r["contract"] for r in summary["runners_up"]] == ["CCC", "BBB", "AAA", "EEE"]
    [ranked] = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    assert [c["contract"] for c in ranked["payload"]["candidates"]] == ["DDD", "CCC", "BBB", "AAA", "EEE"]


async def test_two_drift_cards_rank_among_themselves(drift_desk, temp_db, app_config):  # noqa: F811
    app_config.apriori.max_drift_cards_per_session = 2
    drift_desk.earnings_drift.prep = ok_prep("WINR", "ZETA")
    drift_desk.earnings_drift.candidates = {"WINR": drift_candidate("WINR"), "ZETA": drift_candidate("ZETA")}

    summary = await suggestion_scan(drift_desk)

    drift = {
        s["contract"]: s["decision_provenance"] for s in await _signals(temp_db) if s["strategy"] == PEAD_STRATEGY_ID
    }
    assert {c: (p["rank"], p["candidates_considered"]) for c, p in drift.items()} == {"WINR": (1, 2), "ZETA": (2, 2)}
    payload = (await _only_event(temp_db))["payload"]
    assert payload["events"] == [_event_doc("WINR", "sent"), _event_doc("ZETA", "sent")]
    assert summary["drift"] == {"status": "ok", "reason": None, "events": 2, "sent": 2}
    assert summary["sent"] == 3


# --- budgets -------------------------------------------------------------------------------------


async def test_the_drift_budget_refuses_at_the_cap(drift_desk, temp_db, app_config):  # noqa: F811
    app_config.apriori.max_drift_cards_per_session = 1
    drift_desk.earnings_drift.prep = ok_prep("WINR", "ZETA")
    drift_desk.earnings_drift.candidates = {"WINR": drift_candidate("WINR"), "ZETA": drift_candidate("ZETA")}

    summary = await suggestion_scan(drift_desk)

    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "WINR"]
    payload = (await _only_event(temp_db))["payload"]
    assert payload["events"] == [_event_doc("WINR", "sent"), _event_doc("ZETA", "drift budget spent")]
    assert summary["runners_up"][-1] == {
        "contract": "ZETA",
        "strategy": PEAD_STRATEGY_ID,
        "direction": "LONG",
        "setup_quality": 0.0,
        "reason": "drift budget spent",
    }
    # The native budget is untouched by the drift card and the drift budget by the native ones.
    assert [r["reason"] for r in summary["runners_up"][:4]] == ["per-scan budget spent"] * 4


async def test_a_spent_drift_budget_never_reaches_the_llm(drift_desk, temp_db, app_config):  # noqa: F811
    await _plant_signal(temp_db, "PRIOR", drift_desk.session_start_et() + timedelta(minutes=1), PEAD_STRATEGY_ID)

    summary = await suggestion_scan(drift_desk)

    llm = [c.args[0].contract for c in drift_desk.evaluator.evaluate_candidate.call_args_list if c.kwargs["use_llm"]]
    assert llm == ["DDD"]
    payload = (await _only_event(temp_db))["payload"]
    assert payload["events"] == [_event_doc("WINR", "drift budget spent")]
    assert summary["drift"] == {"status": "ok", "reason": None, "events": 1, "sent": 0}


async def test_native_cards_spent_today_never_spend_the_drift_budget(drift_desk, temp_db, app_config):  # noqa: F811
    app_config.scan.max_cards_per_session = 1
    await _plant_signal(temp_db, "PRIOR", drift_desk.session_start_et() + timedelta(minutes=1), "TREND_PULLBACK")

    await suggestion_scan(drift_desk)

    assert [s["contract"] for s in await _signals(temp_db) if s["contract"] != "PRIOR"] == ["WINR"]
    payload = (await _only_event(temp_db))["payload"]
    assert payload["events"] == [_event_doc("WINR", "sent")]


async def test_the_open_position_cap_is_pinned_whole(drift_desk, temp_db, app_config):  # noqa: F811
    app_config.apriori.max_drift_cards_per_session = 10
    app_config.apriori.max_open_drift_positions = 1
    await _open_position(temp_db, "OLD1")
    drift_desk.monitor_positions = AsyncMock(return_value=0)

    summary = await suggestion_scan(drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    assert payload == _with_elapsed(
        _ok_payload(summary["scan_id"], [_event_doc("WINR", "drift position cap reached")], open_positions=1),
        payload["elapsed_seconds"],
    )
    assert summary["drift"] == {"status": "ok", "reason": None, "events": 1, "sent": 0}
    assert "WINR" not in [c.args[0] for c in drift_desk.data_fetcher.fetch_data.call_args_list]


async def test_an_owner_skipped_event_is_pinned_whole(drift_desk, temp_db):  # noqa: F811
    owned = drift_event("OWND")
    drift_desk.earnings_drift.prep = dataclasses.replace(
        ok_prep("WINR"), skipped={"OWND": "open position"}, skipped_events=(owned,)
    )

    summary = await suggestion_scan(drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    assert payload == _with_elapsed(
        _ok_payload(
            summary["scan_id"],
            [_event_doc("WINR", "sent"), {**owned, "outcome": "skipped: open position"}],
            skipped={"OWND": "open position"},
        ),
        payload["elapsed_seconds"],
    )


# --- skips ---------------------------------------------------------------------------------------


async def test_a_late_decision_is_pinned_whole(drift_desk, temp_db):  # noqa: F811
    drift_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()

    summary = await suggestion_scan(drift_desk, scheduled_at=_late(22.5))

    reason = "late decision: 22 min after 10:35 NY"
    payload = (await _only_event(temp_db))["payload"]
    assert payload == _bare_payload(summary["scan_id"], "skipped", reason, open_positions=0)
    assert summary["drift"] == {"status": "skipped", "reason": reason, "events": 0, "sent": 0}
    assert drift_desk.earnings_drift.prepare_calls == 0
    assert await _messages(temp_db) == []
    assert [s["contract"] for s in await _signals(temp_db)] == ["DDD"]


@pytest.mark.parametrize("gate", ["halt", "lockout", "unverified"])
async def test_a_gated_decision_is_pinned_whole(drift_desk, temp_db, gate):  # noqa: F811
    drift_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()
    reason = await _gate(drift_desk, temp_db, gate)

    assert await suggestion_scan(drift_desk) is None

    payload = (await _only_event(temp_db))["payload"]
    assert len(payload["scan_id"]) == 32
    assert payload == _bare_payload(payload["scan_id"], "skipped", reason)
    assert drift_desk.earnings_drift.prepare_calls == 0
    assert await _messages(temp_db) == []


async def test_a_regime_unavailable_decision_is_pinned_whole(drift_desk, temp_db):  # noqa: F811
    drift_desk.alpha_repository.snapshot.return_value = _catalog_snapshot()
    drift_desk.regime_detector.get_regime.side_effect = RegimeUnavailable(
        {"vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout")}
    )

    assert await suggestion_scan(drift_desk) is None

    payload = (await _only_event(temp_db))["payload"]
    assert payload == _bare_payload(payload["scan_id"], "skipped", "regime unavailable")
    [message] = await _messages(temp_db)
    assert message["arguments"]["text"].endswith(
        "scan was skipped — no cards, no PEAD decision. Later scans retry; this notice is sent once per New York date."
    )


# --- unavailable ---------------------------------------------------------------------------------


async def test_an_unavailable_session_is_pinned_whole(drift_desk, temp_db):  # noqa: F811
    drift_desk.earnings_drift.prep = DriftPreparation(
        "unavailable", today_ny(), version_id=VERSION, policy=POLICY, reason="calendar_empty", report_date="2026-10-26"
    )

    summary = await suggestion_scan(drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    expected = {
        **_bare_payload(summary["scan_id"], "unavailable", "calendar_empty", open_positions=0),
        "report_date": "2026-10-26",
    }
    assert payload == _with_elapsed(expected, payload["elapsed_seconds"])
    assert await _messages(temp_db) == [
        {
            "kind": NotificationKind.MESSAGE,
            "arguments": {
                "text": "🧪 PEAD probe: no drift card this session — calendar_empty (report date 2026-10-26). "
                "Native cards are unaffected.",
                "formatted": False,
            },
        }
    ]
    queued = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.NOTIFICATION_QUEUED]
    assert [e["payload"]["dedup_key"] for e in queued if e["payload"]["dedup_key"].startswith("pead")] == [
        f"pead-unavailable/{_session()}"
    ]
    assert summary["drift"] == {"status": "unavailable", "reason": "calendar_empty", "events": 0, "sent": 0}


@pytest.mark.parametrize(
    ("failure", "reason"), [(RuntimeError("calendar exploded"), "error: RuntimeError"), ("timeout", "timeout")]
)
async def test_a_failed_preparation_is_pinned_whole(drift_desk, temp_db, monkeypatch, failure, reason):  # noqa: F811
    if failure == "timeout":
        monkeypatch.setattr(copilot_module, "DRIFT_PREPARE_TIMEOUT_SECONDS", 0.05)
        drift_desk.earnings_drift.delay = 5.0
    else:
        drift_desk.earnings_drift.prep = failure

    summary = await suggestion_scan(drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    assert payload == _with_elapsed(
        _bare_payload(summary["scan_id"], "unavailable", reason, version_id=None, open_positions=0),
        payload["elapsed_seconds"],
    )
    assert isinstance(payload["elapsed_seconds"], float)
    [message] = await _messages(temp_db)
    assert message["arguments"]["text"] == (
        f"🧪 PEAD probe: no drift card this session — {reason} (report date unknown). Native cards are unaffected."
    )


async def test_an_unbuilt_drift_service_is_pinned_whole(broken_drift_desk, temp_db):  # noqa: F811
    summary = await suggestion_scan(broken_drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    reason = f"service unavailable: {broken_drift_desk._earnings_drift_error}"
    assert payload == _bare_payload(summary["scan_id"], "unavailable", reason, version_id=None, open_positions=0)
    assert summary["drift"] == {"status": "unavailable", "reason": reason, "events": 0, "sent": 0}
    [message] = await _messages(temp_db)
    assert message["arguments"]["text"].startswith("🧪 PEAD probe: no drift card this session — service unavailable")


async def test_an_unbuilt_drift_service_gated_scan_is_skipped_with_the_probe_version(broken_drift_desk, temp_db):  # noqa: F811
    reason = await _gate(broken_drift_desk, temp_db, "halt")

    await suggestion_scan(broken_drift_desk)

    payload = (await _only_event(temp_db))["payload"]
    assert payload == _bare_payload(payload["scan_id"], "skipped", reason)


async def test_a_journal_failure_is_recorded_in_the_summary_and_spares_the_cards(drift_desk, temp_db, monkeypatch):  # noqa: F811
    original = temp_db.workflows.append

    async def fail_on_pead(session, *, stream, kind, payload, key=None, **kwargs):
        if kind == EventKind.PEAD_DECISION:
            raise RuntimeError("journal down")
        return await original(session, stream=stream, kind=kind, payload=payload, key=key, **kwargs)

    monkeypatch.setattr(temp_db.workflows, "append", fail_on_pead)

    summary = await suggestion_scan(drift_desk)

    assert summary["drift_journal_error"] == "RuntimeError: journal down"
    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "WINR"]


# --- the seam itself (added with the refactor) ---------------------------------------------------


def test_pead_is_the_only_catalog_source_and_a_probe(drift_desk):  # noqa: F811
    [source] = drift_desk.catalog_sources
    assert source is drift_desk.pead_source
    assert isinstance(source, CatalogCardSource)
    assert (source.source_id, source.kind, source.strategy_id) == ("pead", "probe", PEAD_STRATEGY_ID)
    assert (source.provenance_key, source.journal_kind) == ("pead_event", EventKind.PEAD_DECISION)
    assert (source.summary_key, source.notification_key) == ("drift", "drift")
    assert source.position_cap_reason == DRIFT_POSITION_CAP_REACHED == "drift position cap reached"


def test_the_pead_source_reads_its_limits_from_the_apriori_config(app_config):
    source = PeadDriftSource()
    app_config.apriori.max_drift_cards_per_session = 3
    app_config.apriori.max_open_drift_positions = 5
    assert source.max_cards_per_session(app_config) == 3
    assert source.max_open_positions(app_config) == 5
    assert source.max_lateness_seconds(app_config) == app_config.apriori.max_drift_lateness_seconds


def test_the_copilot_s_drift_attributes_are_the_pead_source_s(drift_desk):  # noqa: F811
    service = drift_desk.earnings_drift
    assert drift_desk.pead_source.service is service
    drift_desk.earnings_drift = None
    assert drift_desk.pead_source.service is None
    drift_desk._earnings_drift_error = "FileNotFoundError: x"
    assert drift_desk.pead_source.service_error == "FileNotFoundError: x"


def test_the_pead_card_facts_are_drift_card_facts():
    prep = ok_prep()
    source = PeadDriftSource(SimpleNamespace(holding_sessions=20))
    assert source.card_facts(drift_candidate(), prep) == drift_card_facts(drift_event(), prep, 20)
    assert PeadDriftSource().card_facts(drift_candidate(), prep) is None


def test_catalog_strategy_ids_never_count_toward_the_dynamic_group(drift_desk):  # noqa: F811
    assert drift_desk._catalog_strategy_ids() == frozenset({PEAD_STRATEGY_ID})
    assert not drift_desk._counts_as_dynamic({"dynamic": True, "strategy": PEAD_STRATEGY_ID})
    assert drift_desk._counts_as_dynamic({"dynamic": True, "strategy": "TREND_PULLBACK"})


def test_the_generic_card_facts_keyword_is_accepted_and_not_rendered():
    eval_res = LLMTradeEvaluation(
        approved=True,
        rejection_reason=None,
        contract="WINR",
        direction="LONG",
        entry_price=50.0,
        stop_loss=47.5,
        take_profit=57.5,
        stop_distance_points=2.5,
        target_distance_points=7.5,
        risk_reward_ratio=3.0,
        risk_dollars=100.0,
        reward_dollars=300.0,
        notional_value=2000.0,
        effective_leverage=0.2,
        macro_clearance=True,
        thesis_summary="t",
        quantity=40.0,
        asset_class=AssetClass.EQUITY,
    )
    plain = format_alert_card(eval_res, "insider_watch")
    assert format_alert_card(eval_res, "insider_watch", card_facts={"buyers": 3}) == plain
    inspect.signature(TelegramNotifier.send_signal_alert).bind(
        None, eval_res=eval_res, strategy="insider_watch", signal_id=1, card_facts={"buyers": 3}
    )


def _base_rate():
    return PeadBaseRate.model_validate(payload())


def test_pead_base_rate_block_measured_and_unavailable():
    assert pead_base_rate_block(None, "f" * 64) == {"status": "unavailable"}
    block = pead_base_rate_block(_base_rate(), "f" * 64)
    assert block == {
        "status": "measured",
        "sha256": "f" * 64,
        "expected_mean": -0.04,
        "expected_se": 0.0253,
        "window_start": "2016-08-01",
        "window_end": "2026-07-31",
        "recent_from": "2025-01-01",
        "overall_mean": 0.05,
        "n_leg": 100,
        "recent_mean": -0.1,
        "n_recent": 10,
        "recent_p2_mean": 0.02,
    }


def test_pead_source_card_provenance_carries_block():
    block = pead_base_rate_block(_base_rate(), "f" * 64)
    source = PeadDriftSource(SimpleNamespace(holding_sessions=20), base_rate=block)
    prep = ok_prep()
    assert source.card_provenance(drift_candidate(), prep) == {"pead_base_rate": block}
    assert source.card_facts(drift_candidate(), prep) == drift_card_facts(drift_event(), prep, 20, block)
    assert source.card_facts(drift_candidate(), prep)["base_rate"] == block
    assert PeadDriftSource().card_provenance(drift_candidate(), prep) == {}


def test_insider_watch_source_has_no_extra_card_provenance():
    assert InsiderWatchSource.card_provenance(None, None, None) == {}
