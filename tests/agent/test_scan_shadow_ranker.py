"""Shadow-only setup ranker at RANK, and the one-per-scan ``scan_candidates_ranked`` journal.

The shadow block is evidence only: ranking, the card budget, sizing and every sent
card must be exactly what they would have been without it, and any failure in the
shadow computation or in journaling must never block a card or a budget charge.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

import agentic_trader.agent.copilot as copilot_module
import agentic_trader.research.setups.ranker as ranker_module
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import ScanBudget, UniverseConfig, UniverseEntry
from agentic_trader.constants import AssetClass
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups.features import CROSS_SECTIONAL, FEATURES_VERSION
from agentic_trader.research.setups.outcomes import label_journaled
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_scan_budget import (  # noqa: F401  (budget_desk is a fixture)
    budget_desk,
    candidate,
    evaluation,
    frame,
    instrument,
)


SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")


def daily_frame(seed: int, sessions: int = 300) -> pd.DataFrame:
    """A realistic daily frame: one row per business day, ending with the previous session."""
    end = pd.Timestamp(datetime.now(UTC).date()) - pd.offsets.BDay(1)
    index = pd.bdate_range(end=end, periods=sessions)
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, sessions)))
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Volume": 1_000_000.0}, index=index)


@pytest.fixture
def shadow_desk(budget_desk, app_config):  # noqa: F811
    app_config.universe = UniverseConfig(
        groups={"test": [UniverseEntry(symbol=s, sector="technology") for s in SYMBOLS]}, max_symbols=10
    )
    dailies = {symbol: daily_frame(i) for i, symbol in enumerate(SYMBOLS)}
    budget_desk.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: (
        SimpleNamespace(contract=contract, daily=dailies[contract], four_hour=frame(), hourly=frame())
    )
    return budget_desk


@pytest.fixture
def artifact(tmp_path):
    """A linear ranker that ranks *inversely* to setup_quality, so an order leak would show."""
    directory = tmp_path / "ranker"
    directory.mkdir()
    doc = {
        "scorer": "ridge",
        "features_version": FEATURES_VERSION,
        "features": ["mom_60", "setup_quality"],
        "coefficients": [0.0, -1.0],
        "intercept": 0.25,
        "imputer_medians": [0.5, 0.5],
        "scaler_mean": [0.0, 0.0],
        "scaler_scale": [1.0, 1.0],
    }
    path = directory / "ranker.json"
    path.write_text(json.dumps(doc))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "selection.json").write_text(json.dumps({"winner": "ridge", "ranker_sha256": sha}))
    return path, sha


async def _ranked_events(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


def _llm_order(desk) -> list[str]:
    return [c.args[0].contract for c in desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")]


async def test_rank_order_unchanged_with_shadow_block(shadow_desk, temp_db, app_config, artifact):
    app_config.portfolio.correlation_groups = {}
    app_config.scan.max_cards_per_scan = 3
    app_config.scan.max_cards_per_session = 3

    outcomes = []
    for path in (None, artifact[0]):
        app_config.scan.shadow_ranker_artifact = path
        shadow_desk.evaluator.evaluate_candidate.reset_mock()
        await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
        signals = sorted(await temp_db.get_recent_signals(limit=10), key=lambda s: s["decision_provenance"]["rank"])
        outcomes.append(
            (
                [(s["contract"], s["decision_provenance"]["rank"], s["quantity"]) for s in signals],
                shadow_desk.last_scan_summary["runners_up"],
                _llm_order(shadow_desk),
                shadow_desk.last_scan_summary["sent"],
            )
        )
        async with temp_db.session_factory() as session, session.begin():
            await session.execute(delete(SignalRecord))

    assert outcomes[0] == outcomes[1]
    assert [contract for contract, _, _ in outcomes[0][0]] == ["DDD", "CCC", "BBB"]


async def test_sent_card_provenance_has_shadow_block(shadow_desk, temp_db, app_config, artifact):
    path, sha = artifact
    app_config.scan.shadow_ranker_artifact = path
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    block = signal["decision_provenance"]["shadow_ranker"]
    assert set(block) == {"features_version", "features", "score", "ranker_sha"}
    assert block["features_version"] == FEATURES_VERSION
    assert block["ranker_sha"] == sha
    features = block["features"]
    assert features["setup_quality"] == 0.9
    assert features["stop_atr"] == pytest.approx(2.0)  # |100 - 98| / atr_14 (1.0)
    assert features["reward_risk"] == pytest.approx(2.0)  # |104 - 100| / |100 - 98|
    assert features["strategy=TREND_PULLBACK"] == 1.0 and features["timeframe=4h"] == 1.0
    assert all(0.0 < features[name] <= 1.0 for name in ("mom_60", "rev_5", "vol_20", "sector_rel_mom_60"))
    assert set(CROSS_SECTIONAL) <= set(features)
    # No SPY in this universe: market features are NaN, recorded as JSON null rather than
    # breaking record_signal's allow_nan=False encoding.
    assert features["spy_above_200"] is None and features["spy_vol20_pct"] is None
    assert block["score"] == pytest.approx(0.25 - 0.9)


async def test_runners_up_and_sent_are_journaled_once(shadow_desk, temp_db, app_config, artifact):
    app_config.scan.shadow_ranker_artifact = artifact[0]
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked_events(temp_db)
    et_date = shadow_desk.session_start_et().date().isoformat()
    assert event["stream"] == f"scan/{et_date}"
    payload = event["payload"]
    assert payload["budget"] == "full" and payload["ranking_key"] == "setup_quality"
    assert payload["scope"] == "universe"
    assert payload["trigger"] == "suggestion_scan"
    assert payload["scan_id"] == shadow_desk.last_scan_summary["scan_id"]
    assert datetime.fromisoformat(payload["decided_at"]).utcoffset() == timedelta(0)
    candidates = payload["candidates"]
    assert [(c["contract"], c["rank"]) for c in candidates] == [
        ("DDD", 1),
        ("CCC", 2),
        ("BBB", 3),
        ("AAA", 4),
        ("EEE", 5),
    ]
    assert candidates[0]["outcome"] == "sent"
    assert {c["outcome"] for c in candidates[1:]} == {"per-scan budget spent"}
    first = candidates[0]
    assert (first["entry"], first["stop"], first["target"], first["atr_14"]) == (100.0, 98.0, 104.0, 1.0)
    assert (first["strategy"], first["timeframe"], first["direction"], first["setup_quality"]) == (
        "TREND_PULLBACK",
        "4h",
        "LONG",
        0.9,
    )
    assert all(c["shadow"]["features_version"] == FEATURES_VERSION for c in candidates)
    assert all(c["shadow"]["score"] is not None for c in candidates)

    # One event per scan: a second scan appends a second, distinct event.
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
    events = await _ranked_events(temp_db)
    assert len(events) == 2
    assert events[0]["payload"]["scan_id"] != events[1]["payload"]["scan_id"]


async def test_rejected_send_evaluation_is_journaled_with_its_reason(shadow_desk, temp_db):
    async def evaluate(cand, use_llm=False, **kwargs):
        approved = not (use_llm and cand.contract == "DDD")
        return SimpleNamespace(
            **{
                **vars(evaluation(cand)),
                "approved": approved,
                "rejection_reason": None if approved else "llm says no",
            }
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
    [event] = await _ranked_events(temp_db)
    outcomes = {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}
    assert outcomes["DDD"] == "rejected: llm says no" and outcomes["CCC"] == "sent"


async def test_journal_failure_does_not_block_card(shadow_desk, temp_db, caplog):
    original = temp_db.workflows.append

    async def failing_append(session, *, kind, **kwargs):
        if kind == EventKind.SCAN_CANDIDATES_RANKED:
            raise RuntimeError("journal boom")
        return await original(session, kind=kind, **kwargs)

    temp_db.workflows.append = failing_append
    with caplog.at_level(logging.ERROR, logger="copilot"):
        await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
    assert shadow_desk.last_scan_summary["sent"] == 1
    assert "journal boom" in shadow_desk.last_scan_summary["scan_journal_error"]
    assert any(getattr(r, "event", None) == "scan_journal_failed" for r in caplog.records)
    assert await _ranked_events(temp_db) == []
    shadow_desk.readiness.observe.assert_awaited()


@pytest.mark.parametrize("mismatch", ["features_version", "selection_sha"])
async def test_artifact_mismatch_records_features_only(shadow_desk, temp_db, app_config, artifact, mismatch, caplog):
    path, _ = artifact
    if mismatch == "features_version":
        doc = json.loads(path.read_text())
        doc["features_version"] = "setup_features_v0"
        path.write_text(json.dumps(doc))  # selection.json now also mismatches; either rejects it
    else:
        (path.parent / "selection.json").write_text(json.dumps({"winner": "ridge", "ranker_sha256": "0" * 64}))
    app_config.scan.shadow_ranker_artifact = path

    with caplog.at_level(logging.WARNING):
        await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    block = signal["decision_provenance"]["shadow_ranker"]
    assert block["score"] is None and block["ranker_sha"] is None
    assert block["features"]["setup_quality"] == 0.9
    assert "shadow_ranker_error" not in shadow_desk.last_scan_summary
    assert "ranker" in caplog.text.lower()


async def test_no_artifact_records_features_only(shadow_desk, temp_db):
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
    [signal] = await temp_db.get_recent_signals(limit=10)
    block = signal["decision_provenance"]["shadow_ranker"]
    assert block["score"] is None and block["ranker_sha"] is None and block["features"]["setup_quality"] == 0.9


async def test_dry_run_does_not_journal(shadow_desk, temp_db):
    async def evaluate(cand, **kwargs):
        return LLMTradeEvaluation(
            approved=True,
            contract=cand.contract,
            direction="LONG",
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=104.0,
            stop_distance_points=2.0,
            target_distance_points=4.0,
            risk_reward_ratio=2.0,
            risk_dollars=2.0,
            reward_dollars=4.0,
            notional_value=100.0,
            effective_leverage=1.0,
            macro_clearance=True,
            thesis_summary="dry",
            quantity=1.0,
            asset_class=AssetClass.EQUITY,
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    # shadow_evidence=True proves a dry run refuses to journal even when the caller
    # requests shadow evidence; a dry scan must never touch the durable journal.
    await shadow_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL, shadow_evidence=True)
    assert await _ranked_events(temp_db) == []


async def test_no_budget_scan_does_not_journal(shadow_desk, temp_db):
    # shadow_evidence=True proves an unbudgeted scan (--no-budget) refuses to journal
    # even when the caller requests shadow evidence.
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.NONE, shadow_evidence=True)
    assert len(await temp_db.get_recent_signals(limit=10)) == 5
    assert await _ranked_events(temp_db) == []


async def test_feature_failure_is_contained(shadow_desk, temp_db, monkeypatch, caplog):
    def boom(*_args, **_kwargs):
        raise RuntimeError("features boom")

    monkeypatch.setattr(copilot_module, "live_cross_section", boom)
    with caplog.at_level(logging.ERROR, logger="copilot"):
        await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["shadow_ranker"] is None
    assert "features boom" in shadow_desk.last_scan_summary["shadow_ranker_error"]
    assert any(getattr(r, "event", None) == "shadow_ranker_failed" for r in caplog.records)
    [event] = await _ranked_events(temp_db)
    assert [c["shadow"] for c in event["payload"]["candidates"]] == [None] * 5


async def test_shadow_ranker_empty_universe_logs_a_warning_without_traceback(shadow_desk, temp_db, monkeypatch, caplog):
    """``live_cross_section`` raising for an anticipated, recoverable gap (no completed
    daily session yet in this scan's data) is not a bug: log it once at WARNING with no
    traceback, distinct from ``shadow_ranker_failed`` (an unexpected exception)."""

    def no_data(*_args, **_kwargs):
        raise ValueError("No completed daily session before 2026-09-23 in this scan's data")

    monkeypatch.setattr(copilot_module, "live_cross_section", no_data)
    with caplog.at_level(logging.WARNING, logger="copilot"):
        await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["shadow_ranker"] is None
    assert "No completed daily session" in shadow_desk.last_scan_summary["shadow_ranker_error"]
    warnings = [r for r in caplog.records if getattr(r, "event", None) == "shadow_ranker_skipped"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert warnings[0].exc_info is None
    assert not any(getattr(r, "event", None) == "shadow_ranker_failed" for r in caplog.records)


@pytest.mark.parametrize(
    "scope",
    [
        {"symbols": ["CCC", "DDD"]},  # an operator /scan of named symbols
        {"timeframe": "4h"},  # a timeframe-filtered scan, like the 15-minute intraday job
        {"symbols": ["DDD"], "timeframe": "4h"},
    ],
)
async def test_restricted_scans_neither_compute_nor_journal_the_shadow(
    shadow_desk, temp_db, app_config, artifact, monkeypatch, scope
):
    app_config.scan.shadow_ranker_artifact = artifact[0]

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the shadow cross-section is only for full-universe scans")

    monkeypatch.setattr(copilot_module, "live_cross_section", unexpected)
    # shadow_evidence=True proves the restriction guard defends even a caller that
    # (incorrectly) also asks for shadow evidence on a symbol- or timeframe-scoped scan.
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True, **scope)

    [signal] = await temp_db.get_recent_signals(limit=10)  # the card itself is unaffected
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["shadow_ranker"] is None
    assert await _ranked_events(temp_db) == []
    assert "shadow_ranker_error" not in shadow_desk.last_scan_summary
    assert "shadow_ranker_seconds" not in shadow_desk.last_scan_summary


async def test_live_cross_section_is_universe_groups_only(shadow_desk, temp_db, app_config, monkeypatch):
    """An explicit contracts: equity outside universe.groups is scanned (and may win a card)
    but is not part of the cross-section its own and every other name's ranks come from."""
    app_config.contracts = {**app_config.contracts, "ZZZ": instrument("ZZZ")}
    dailies = {symbol: daily_frame(i) for i, symbol in enumerate((*SYMBOLS, "ZZZ"))}
    shadow_desk.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: (
        SimpleNamespace(contract=contract, daily=dailies[contract], four_hour=frame(), hourly=frame())
    )
    qualities = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5, "ZZZ": 0.95}
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, qualities[data.contract])
    ]
    populations = []
    original = ranker_module.cross_section

    def spy(daily, sectors, as_of):
        populations.append(set(daily))
        return original(daily, sectors, as_of)

    monkeypatch.setattr(ranker_module, "cross_section", spy)
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert populations == [set(SYMBOLS)]
    [event] = await _ranked_events(temp_db)
    shadow = {c["contract"]: c["shadow"] for c in event["payload"]["candidates"]}
    assert set(shadow) == {*SYMBOLS, "ZZZ"}
    assert all(shadow["ZZZ"]["features"][name] is None for name in CROSS_SECTIONAL)
    assert all(shadow["DDD"]["features"][name] is not None for name in ("mom_60", "vol_20"))


async def test_swing_scan_shaped_call_neither_computes_nor_journals_shadow(
    shadow_desk, temp_db, app_config, artifact, monkeypatch
):
    """The daemon's 4-hourly swing scan calls ``run_scan(use_llm, dry_run, budget=FULL)``
    with no symbols/timeframe and no ``shadow_evidence`` -- the same shape a full-universe
    scan has, but not the suggestion-scan job, so it must neither compute nor journal."""
    app_config.scan.shadow_ranker_artifact = artifact[0]

    def unexpected(*_args, **_kwargs):
        raise AssertionError("shadow evidence is scoped to the suggestion-scan job alone")

    monkeypatch.setattr(copilot_module, "live_cross_section", unexpected)
    await shadow_desk.run_scan(True, False, budget=ScanBudget.FULL)  # positional use_llm, dry_run like the cron job

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["shadow_ranker"] is None
    assert await _ranked_events(temp_db) == []
    assert "shadow_ranker_error" not in shadow_desk.last_scan_summary


async def test_unrestricted_manual_scan_neither_computes_nor_journals_shadow(
    shadow_desk, temp_db, app_config, artifact, monkeypatch
):
    """An operator ``copilot scan``/Telegram ``/scan`` with no symbols and no timeframe
    is shaped exactly like the suggestion scan, but never sets ``shadow_evidence``."""
    app_config.scan.shadow_ranker_artifact = artifact[0]

    def unexpected(*_args, **_kwargs):
        raise AssertionError("shadow evidence is scoped to the suggestion-scan job alone")

    monkeypatch.setattr(copilot_module, "live_cross_section", unexpected)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["shadow_ranker"] is None
    assert await _ranked_events(temp_db) == []
    assert "shadow_ranker_error" not in shadow_desk.last_scan_summary


async def test_ranking_and_cards_unaffected_by_the_shadow_evidence_flag(shadow_desk, temp_db, app_config, artifact):
    """The flag governs shadow computation/journaling only; it must never change what
    is ranked, budgeted or sent."""
    app_config.scan.shadow_ranker_artifact = artifact[0]

    outcomes = []
    for shadow_evidence in (False, True):
        await shadow_desk.run_scan(
            use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=shadow_evidence
        )
        signals = sorted(await temp_db.get_recent_signals(limit=10), key=lambda s: s["decision_provenance"]["rank"])
        outcomes.append([(s["contract"], s["decision_provenance"]["rank"], s["quantity"]) for s in signals])
        async with temp_db.session_factory() as session, session.begin():
            await session.execute(delete(SignalRecord))

    assert outcomes[0] == outcomes[1]


async def test_scan_candidates_ranked_round_trips_through_label_journaled(shadow_desk, temp_db, app_config, artifact):
    """Write-side journal payload and read-side ``label_journaled`` agree end to end."""
    app_config.scan.shadow_ranker_artifact = artifact[0]
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked_events(temp_db)
    assert event["payload"]["trigger"] == "suggestion_scan"

    class _StaleBars:
        """Bars entirely before the scan's decision time: every candidate is IMMATURE."""

        def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
            index = pd.date_range("2020-01-01", periods=2, freq="h", tz="UTC")
            return pd.DataFrame(
                {"Open": [1.0, 1.0], "High": [1.0, 1.0], "Low": [1.0, 1.0], "Close": [1.0, 1.0]}, index=index
            )

    frame = label_journaled([event], _StaleBars())

    assert set(frame["contract"]) == set(SYMBOLS)
    assert (frame["hit"] == "immature").all()
    sent = frame.loc[frame["contract"] == "DDD"].iloc[0]
    assert bool(sent["sent"]) is True
    assert sent["scan_id"] == event["payload"]["scan_id"]
