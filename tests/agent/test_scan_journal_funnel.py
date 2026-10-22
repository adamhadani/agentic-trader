"""A complete scan journal: every eligible scan journals once, with its status, gate and collect funnel.

An eligible scan is unrestricted (no symbols, no timeframe), not dry, budgeted (not NONE) and past
the idle check. It journals one ``scan_candidates_ranked`` event even with zero candidates, and a
lockout-gated or regime-unavailable scan journals too, with ``funnel: null``. Halted, closed-session,
idle, dry, restricted and unbudgeted scans journal nothing. The funnel counts native setups only, by
rule id and exception type name, never reason text:

    raw_setups = duplicates + account_risk_unavailable + sum(rejected_by_rule) + admitted + setups_lost_to_errors
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.calendar import Tier1Kind
from agentic_trader.agent.macro import MacroInput
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.agent.scan_journal import AdmissionRefusal, RefusalKind, ScanFunnel
from agentic_trader.config import ScanBudget
from agentic_trader.constants import SystemStateKey
from agentic_trader.execution.durable import EventKind, ScanTrigger
from agentic_trader.risk import RiskRule
from tests.agent.test_dynamic_universe_scan import (  # noqa: F401  (dailies/dynamic_desk are fixtures)
    dailies,
    daily_frame,
    dynamic_desk,
    ranked_events,
)
from tests.agent.test_scan_budget import (  # noqa: F401  (budget_desk is a fixture)
    budget_desk,
    candidate,
    evaluation,
    instrument,
)
from tests.agent.test_scan_drift import drift_desk  # noqa: F401  (a fixture)
from tests.calendar_doubles import lockout_check, union_double


EMPTY_FUNNEL = {
    "selected": 0,
    "scanned": 0,
    "fetch_failed": 0,
    "insufficient": 0,
    "coverage_excluded": 0,
    "dynamic_excluded": 0,
    "raw_setups": 0,
    "raw_setups_by_strategy": {},
    "duplicates": 0,
    "account_risk_unavailable": 0,
    "rejected_by_rule": {},
    "errors": {},
    "setups_lost_to_errors": 0,
    "admitted": 0,
}


async def suggestion(desk, **kwargs):
    arguments = {
        "use_llm": False,
        "dry_run": False,
        "budget": ScanBudget.FULL,
        "trigger": ScanTrigger.SUGGESTION_SCAN,
        **kwargs,
    }
    return await desk.run_scan(**arguments)


def identity(funnel: dict) -> int:
    return (
        funnel["duplicates"]
        + funnel["account_risk_unavailable"]
        + sum(funnel["rejected_by_rule"].values())
        + funnel["admitted"]
        + funnel["setups_lost_to_errors"]
    )


def observed_risk() -> SimpleNamespace:
    return SimpleNamespace(drawdown_pct=Decimal(0), equity=Decimal(10000), fingerprint="observed-risk")


# --- the refusal and the funnel ------------------------------------------------------------


def test_a_refusal_reads_as_its_reason_text():
    refusal = AdmissionRefusal(RefusalKind.REJECTED, "per_trade_notional", "rejected: notional above cap")
    assert str(refusal) == "rejected: notional above cap"
    assert str(AdmissionRefusal(RefusalKind.DUPLICATE, None, "duplicate")) == "duplicate"


def test_the_funnel_counts_refusals_by_kind_and_rejections_by_rule():
    funnel = ScanFunnel()
    funnel.record_refusal(AdmissionRefusal(RefusalKind.DUPLICATE, None, "duplicate"))
    funnel.record_refusal(AdmissionRefusal(RefusalKind.ACCOUNT_RISK_UNAVAILABLE, None, "account risk unavailable: x"))
    funnel.record_refusal(AdmissionRefusal(RefusalKind.REJECTED, str(RiskRule.REWARD_RISK), "rejected: rr"))
    funnel.record_refusal(AdmissionRefusal(RefusalKind.REJECTED, str(RiskRule.CORRELATION_GROUP), "rejected: g"))
    funnel.record_refusal(AdmissionRefusal(RefusalKind.REJECTED, str(RiskRule.REWARD_RISK), "rejected: rr"))
    funnel.record_refusal(AdmissionRefusal(RefusalKind.REJECTED, None, "rejected: llm"))

    block = funnel.journal_block()

    assert (block["duplicates"], block["account_risk_unavailable"]) == (1, 1)
    assert block["rejected_by_rule"] == {"correlation_group": 1, "reward_risk": 2, "unclassified": 1}
    assert list(block["rejected_by_rule"]) == ["correlation_group", "reward_risk", "unclassified"]  # sorted keys


def test_the_journal_block_is_plain_json():
    funnel = ScanFunnel(selected=2, scanned=1)
    funnel.raw_setups_by_strategy["ZETA"] += 1
    funnel.raw_setups_by_strategy["ALPHA"] += 2
    funnel.errors["RuntimeError"] += 1

    block = funnel.journal_block()

    assert block == {
        **EMPTY_FUNNEL,
        "selected": 2,
        "scanned": 1,
        "raw_setups_by_strategy": {"ALPHA": 2, "ZETA": 1},
        "errors": {"RuntimeError": 1},
    }
    assert list(block["raw_setups_by_strategy"]) == ["ALPHA", "ZETA"]
    assert all(type(value) in (int, dict) for value in block.values())
    assert json.loads(json.dumps(block)) == block


# --- eligible scans journal once -----------------------------------------------------------


async def test_a_zero_candidate_eligible_scan_journals_once_with_a_full_funnel(budget_desk, temp_db):  # noqa: F811
    budget_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: []

    await suggestion(budget_desk)

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["candidates"], p["gate"]) == ("completed", [], None)
    # The fixture configures five equities (AAA..EEE), all fetched and scanned.
    assert p["funnel"] == {**EMPTY_FUNNEL, "selected": 5, "scanned": 5}
    assert p["scan_id"] == budget_desk.last_scan_summary["scan_id"]


async def test_a_completed_scan_with_candidates_journals_its_funnel(budget_desk, temp_db):  # noqa: F811
    await suggestion(budget_desk)

    [event] = await ranked_events(temp_db)
    funnel = event["payload"]["funnel"]
    assert funnel == {
        **EMPTY_FUNNEL,
        "selected": 5,
        "scanned": 5,
        "raw_setups": 5,
        "raw_setups_by_strategy": {"TREND_PULLBACK": 5},
        "admitted": 5,
    }
    assert funnel["admitted"] == budget_desk.last_scan_summary["approved"] == len(event["payload"]["candidates"])


async def test_funnel_identity_holds_across_every_refusal_kind(budget_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    """Review Focus 5: one setup per refusal kind, an admitted setup and two errored contracts.

    AAA duplicate; BBB account risk unavailable (the ledger's first read raises: AAA, a duplicate,
    never reads it); CCC rejected by ``per_trade_notional``; DDD rejected with no rule; EEE admitted;
    FFF has two setups and its first dedup read raises, losing that one setup (the second is never
    reached); GGG's strategy scan raises before any setup.
    """
    app_config.contracts = {s: instrument(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")}
    app_config.portfolio.correlation_groups = {}

    def scan(data, **kwargs):
        if data.contract == "GGG":
            raise RuntimeError("strategy boom")
        if data.contract == "FFF":
            return [candidate("FFF", 0.4), candidate("FFF", 0.3, strategy="BREAKOUT")]
        return [candidate(data.contract, 0.5)]

    async def is_duplicate_recent(contract, strategy, **kwargs):
        if contract == "FFF":
            raise RuntimeError("dedup boom")
        return contract == "AAA"

    async def evaluate(cand, **kwargs):
        if cand.contract == "CCC":
            return SimpleNamespace(
                **{**vars(evaluation(cand, approved=False)), "rejection_rule": str(RiskRule.PER_TRADE_NOTIONAL)}
            )
        return evaluation(cand, approved=cand.contract != "DDD")  # DDD: refused with no shared rule

    reads = 0

    async def current_risk():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise ValueError("stale observation")
        return observed_risk()

    budget_desk.strategy_engine.scan_contract.side_effect = scan
    budget_desk.db.is_duplicate_recent = AsyncMock(side_effect=is_duplicate_recent)
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    monkeypatch.setattr(copilot_module, "requires_account_risk", lambda config: True)
    budget_desk.ledger = AsyncMock()
    budget_desk.ledger.current_risk.side_effect = current_risk

    await suggestion(budget_desk)

    [event] = await ranked_events(temp_db)
    payload = event["payload"]
    f = payload["funnel"]
    assert (f["selected"], f["scanned"]) == (7, 7)
    assert (f["duplicates"], f["account_risk_unavailable"]) == (1, 1)
    assert f["rejected_by_rule"] == {"per_trade_notional": 1, "unclassified": 1}
    assert f["errors"] == {"RuntimeError": 2} and f["setups_lost_to_errors"] == 1 and f["admitted"] == 1
    assert f["raw_setups_by_strategy"] == {"TREND_PULLBACK": 6}
    assert f["raw_setups"] == identity(f) == 6
    assert [c["contract"] for c in payload["candidates"]] == ["EEE"]
    assert budget_desk.last_scan_summary["duplicates"] == ["AAA"]
    text = json.dumps(f)
    assert all(secret not in text for secret in ("stale observation", "dedup boom", "strategy boom"))


async def test_the_funnel_counts_each_contract_gate_once(dynamic_desk, temp_db, dailies, monkeypatch):  # noqa: F811
    """A static fetch failure, an empty frame, a coverage exclusion and a thin dynamic name."""
    dailies["NEWB"] = daily_frame(volume=1_000.0)  # below the liquidity reference: a dynamic exclusion
    dailies["BBB"] = pd.DataFrame({"Close": []})  # fetched but empty: insufficient
    original = dynamic_desk.data_fetcher.fetch_data.side_effect

    def fetch(contract, ticker, include_fifteen_min=True):
        if contract == "AAA":
            raise TimeoutError("slow provider")
        return original(contract, ticker, include_fifteen_min=include_fifteen_min)

    dynamic_desk.data_fetcher.fetch_data.side_effect = fetch
    monkeypatch.setattr(copilot_module, "coverage_exclusions", lambda datasets, **kwargs: ({"CCC"}, None))

    await suggestion(dynamic_desk, asset_class="equity", shadow_evidence=True)

    [event] = await ranked_events(temp_db)
    f = event["payload"]["funnel"]
    summary = dynamic_desk.last_scan_summary
    assert summary["dynamic"]["excluded"] == {"NEWB": "dollar_volume"}
    selected = len(dynamic_desk.config.contracts) + 2  # every configured equity plus NEWA and NEWB
    assert (f["selected"], f["dynamic_excluded"], f["fetch_failed"], f["insufficient"], f["coverage_excluded"]) == (
        selected,
        1,
        1,
        1,
        1,
    )
    assert f["scanned"] == summary["scanned"] == selected - 4
    # DDD, EEE and NEWA carry setups; the reference names carry none.
    assert (f["raw_setups"], f["admitted"]) == (3, 3)
    assert f["raw_setups"] == identity(f)
    assert {c["contract"] for c in event["payload"]["candidates"]} == {"DDD", "EEE", "NEWA"}


# --- gated scans: status, gate and a null funnel -------------------------------------------


async def test_a_lockout_gated_suggestion_scan_journals_macro_lockout_with_null_regime(budget_desk, temp_db):  # noqa: F811
    budget_desk.calendar = union_double(
        lockout_check(
            "FOMC Press Conference",
            datetime(2026, 10, 28, 18, 30, tzinfo=UTC),
            kind=Tier1Kind.FOMC_PRESS_CONFERENCE,
        )
    )

    assert await suggestion(budget_desk, scheduled_time_et="14:35") is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert p["gate"] == {"rule": "macro_lockout", "reason": "Macro event lockout: FOMC Press Conference at 18:30 UTC."}
    assert (p["status"], p["regime"], p["funnel"], p["candidates"]) == ("macro_lockout", None, None, [])
    assert p["calendar"]["lockout"]["title"] == "FOMC Press Conference"
    assert (p["trigger"], p["budget"], p["scheduled_time_et"]) == ("suggestion_scan", "full", "14:35")
    budget_desk.regime_detector.get_regime.assert_not_awaited()


async def test_a_regime_unavailable_scan_journals_a_null_funnel(budget_desk, temp_db):  # noqa: F811
    budget_desk.regime_detector.get_regime.side_effect = RegimeUnavailable(
        {"vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout")}
    )

    assert await suggestion(budget_desk) is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["funnel"], p["candidates"]) == ("regime_unavailable", None, [])


# --- scans that never journal --------------------------------------------------------------


async def _equity_closed(instrument_type="all", timestamp=None):
    return (instrument_type != "equity", "equity closed")


@pytest.mark.parametrize("case", ["halted", "closed-session", "idle", "dry-run", "no-budget", "symbols", "timeframe"])
async def test_halted_closed_session_idle_dry_and_restricted_scans_journal_nothing(
    budget_desk,  # noqa: F811
    temp_db,
    case,
):
    # Zero candidates everywhere: dropping the candidate-count condition must not journal these.
    budget_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: []
    kwargs: dict = {}
    if case == "halted":
        await temp_db.set_state(SystemStateKey.TRADING_HALTED, "true")
        await temp_db.set_state(SystemStateKey.TRADING_HALT_REASON, "operator kill")
    elif case == "closed-session":
        budget_desk.session_provider.is_session_active.return_value = (False, "Closed")
    elif case == "idle":
        budget_desk.session_provider.is_session_active.side_effect = _equity_closed
    elif case == "dry-run":
        kwargs["dry_run"] = True
    elif case == "no-budget":
        kwargs["budget"] = ScanBudget.NONE
    elif case == "symbols":
        kwargs["symbols"] = ["AAA"]
    else:
        kwargs["timeframe"] = "4h"

    await suggestion(budget_desk, **kwargs)

    assert await ranked_events(temp_db) == []


# --- drift keeps its own refusal text -------------------------------------------------------


@pytest.mark.parametrize("refusal", ["duplicate", "account risk", "rejected"])
async def test_drift_refusals_keep_their_text(drift_desk, temp_db, monkeypatch, refusal):  # noqa: F811
    if refusal == "duplicate":
        drift_desk.db.is_duplicate_recent = AsyncMock(side_effect=lambda contract, strategy, **kw: contract == "WINR")
        expected = "duplicate"
    elif refusal == "account risk":
        monkeypatch.setattr(copilot_module, "requires_account_risk", lambda config: True)
        drift_desk.ledger = AsyncMock()
        drift_desk.ledger.current_risk.side_effect = ValueError("stale observation")
        expected = "account risk unavailable: stale observation"
    else:

        async def evaluate(cand, **kwargs):
            return evaluation(cand, approved=cand.contract != "WINR")

        drift_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
        expected = "rejected: risk"

    await drift_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True, scheduled_time_et="10:35"
    )

    [decision] = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.PEAD_DECISION]
    assert {item["symbol"]: item["outcome"] for item in decision["payload"]["events"]} == {"WINR": expected}
    # The funnel counts the five native setups only; the drift refusal is the PEAD decision's.
    [event] = await ranked_events(temp_db)
    f = event["payload"]["funnel"]
    assert f["raw_setups"] == identity(f) == 5
    if refusal == "account risk":
        assert (f["account_risk_unavailable"], f["admitted"]) == (5, 0)
        assert event["payload"]["candidates"] == []
    else:
        assert (f["duplicates"], f["rejected_by_rule"], f["admitted"]) == (0, {}, 5)
