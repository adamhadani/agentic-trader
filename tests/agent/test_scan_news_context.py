"""News blocks in `run_scan`: one fetch per scan, rank-ordered scoring, provenance, journal, failure modes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.news_context import NewsContextService, NewsScorer
from agentic_trader.config import NewsConfig, ScanBudget
from agentic_trader.execution.durable import EventKind
from tests.agent.test_news_context import GOOD, FakeComplete, FakeSource, page_with
from tests.agent.test_scan_budget import budget_desk, candidate, evaluation  # noqa: F401  (fixture + helpers)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)
from tests.data.test_news import NOW, row


def with_news(cand, news_context=None):
    """The stub evaluation with the scan's news block, shaped like `with_verdict` in the LLM-verdict tests."""
    return SimpleNamespace(
        **{**vars(evaluation(cand)), "news_context": news_context, "model_dump": lambda mode=None: {}}
    )


async def _ranked(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


def install(desk, source, *, answers=(), score_first=6):
    desk.config.news = NewsConfig(max_scored_per_scan=score_first)
    scorer = NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=FakeComplete(answers))
    # The fixture rows are stamped relative to tests.data.test_news.NOW; the service's window must
    # use the same clock or the items age out of the 48 h window as the calendar advances.
    desk.news_context = NewsContextService(source, scorer, desk.config.news, clock=lambda: NOW)
    return scorer


async def test_news_blocks_follow_rank_and_land_in_provenance_and_journal(shadow_desk, temp_db):  # noqa: F811
    now = datetime.now(UTC)
    page = page_with(row(1, "DDD news", ["DDD"]), row(2, "CCC news", ["CCC"]), row(3, "AAA news", ["AAA"]))
    page = page.__class__(page.items, now, now, page.symbols, page.window, 1, ("sha-p",), False)
    source = FakeSource(page)
    scorer = install(shadow_desk, source, answers=[GOOD, GOOD], score_first=2)
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: with_news(cand, kw.get("news_context"))
    )

    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert len(source.calls) == 1  # one fetch per scan
    [event] = await _ranked(temp_db)
    by_contract = {c["contract"]: c for c in event["payload"]["candidates"]}
    ranks = sorted(by_contract.values(), key=lambda c: c["rank"])
    scored = [c["contract"] for c in ranks if c["news"]["status"] == "ok"]
    assert scored == [c["contract"] for c in ranks[:2] if c["news"]["n_items"]]  # the first two ranked with items
    assert all(c["news"] is not None and c["news"]["applied"] is False for c in ranks)
    assert len(scorer.complete.calls) == len(scored)
    signals = await temp_db.get_recent_signals(limit=10)
    for signal in signals:
        assert signal["decision_provenance"]["news"] == by_contract[signal["contract"]]["news"]
        assert signal["decision_provenance"]["news"]["as_of"] == now.isoformat()
    # The evaluator received the same block the journal recorded.
    # (The deterministic pre-ranking pass carries no news; only the send phase does.)
    sent = [c for c in shadow_desk.evaluator.evaluate_candidate.call_args_list if "news_context" in c.kwargs]
    assert sent
    for call in sent:
        contract = call.args[0].contract
        assert call.kwargs["news_context"] == by_contract[contract]["news"]


async def test_timeframe_scan_skips_news_entirely(shadow_desk, temp_db):  # noqa: F811
    """The 15-minute intraday job passes a timeframe: it never fetches or scores news."""
    page = page_with(row(1, "DDD news", ["DDD"]), row(2, "CCC news", ["CCC"]))
    source = FakeSource(page)
    scorer = install(shadow_desk, source, answers=[GOOD, GOOD])
    qualities = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5}
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, qualities[data.contract]).model_copy(update={"timeframe": "15m"})
    ]
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: with_news(cand, kw.get("news_context"))
    )

    await shadow_desk.run_scan(use_llm=True, dry_run=False, timeframe="15m", budget=ScanBudget.FULL)

    signals = await temp_db.get_recent_signals(limit=10)
    assert signals  # the scan ranked and sent: the step was reachable and skipped
    assert source.calls == [] and scorer.complete.calls == []
    assert all(signal["decision_provenance"]["news"] is None for signal in signals)
    assert await _ranked(temp_db) == []  # a timeframe scan journals no ranked candidates
    sent = [c for c in shadow_desk.evaluator.evaluate_candidate.call_args_list if "news_context" in c.kwargs]
    assert sent and all(call.kwargs["news_context"] is None for call in sent)


async def test_no_llm_scan_keeps_counts_but_never_scores(shadow_desk, temp_db):  # noqa: F811
    page = page_with(row(1, "DDD news", ["DDD"]), row(2, "CCC news", ["CCC"]))
    source = FakeSource(page)
    scorer = install(shadow_desk, source, answers=[GOOD, GOOD])
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: with_news(cand, kw.get("news_context"))
    )

    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    assert len(source.calls) == 1
    [event] = await _ranked(temp_db)
    by_contract = {c["contract"]: c["news"] for c in event["payload"]["candidates"]}
    assert {block["status"] for block in by_contract.values()} <= {"no_news", "unscored"}
    assert by_contract["DDD"]["status"] == "unscored" and by_contract["DDD"]["n_items"] == 1
    assert by_contract["DDD"]["headlines"][0]["headline"] == "DDD news"
    assert scorer.complete.calls == []


async def test_news_service_failure_is_unavailable_and_changes_nothing(shadow_desk, temp_db):  # noqa: F811
    install(shadow_desk, FakeSource(error=RuntimeError("boom")))
    shadow_desk.news_context.for_symbols = AsyncMock(side_effect=TypeError("not a dict"))
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: with_news(cand, kw.get("news_context"))
    )
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
    [event] = await _ranked(temp_db)
    statuses = {c["news"]["status"] for c in event["payload"]["candidates"]}
    assert statuses == {"unavailable"}
    assert {c["news"]["error_type"] for c in event["payload"]["candidates"]} == {"TypeError"}
    assert len(await temp_db.get_recent_signals(limit=10)) == shadow_desk.config.scan.max_cards_per_scan
    assert "boom" not in str(event["payload"])


async def test_disabled_service_leaves_blocks_absent(shadow_desk, temp_db):  # noqa: F811
    shadow_desk.news_context = None
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kw: evaluation(cand))
    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)
    [event] = await _ranked(temp_db)
    assert all(c["news"] is None for c in event["payload"]["candidates"])
    [signal, *_] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["news"] is None


async def test_futures_candidates_get_no_block_and_symbols_are_equities_only(shadow_desk, temp_db):  # noqa: F811
    source = FakeSource(page_with())
    install(shadow_desk, source)
    ranked = [
        (type("C", (), {"contract": "/MES", "catalog_event": None})(), None, None),
        (type("C", (), {"contract": "AAA", "catalog_event": None})(), None, None),
        (type("C", (), {"contract": "BBB", "catalog_event": {"x": 1}})(), None, None),
    ]
    blocks = await shadow_desk._news_blocks(ranked, use_llm=True)
    assert source.calls[0][0] == ("AAA",)
    assert blocks[0] is None and blocks[2] is None and blocks[1]["status"] == "no_news"


async def test_drift_card_sharing_a_native_symbol_gets_no_block(shadow_desk):  # noqa: F811
    source = FakeSource(page_with())
    install(shadow_desk, source)
    ranked = [
        (SimpleNamespace(contract="AAA", catalog_event=None), None, None),
        (SimpleNamespace(contract="AAA", catalog_event={"x": 1}), None, None),
    ]
    blocks = await shadow_desk._news_blocks(ranked, use_llm=True)
    assert source.calls[0][0] == ("AAA",)
    assert blocks[0] is not None and blocks[0]["status"] == "no_news"
    assert blocks[1] is None


async def test_failure_fallback_matches_a_lower_case_or_padded_contract(shadow_desk):  # noqa: F811
    install(shadow_desk, FakeSource(page_with()))
    shadow_desk.news_context.for_symbols = AsyncMock(side_effect=RuntimeError("boom"))
    ranked = [(SimpleNamespace(contract="aaa ", catalog_event=None), None, None)]
    [block] = await shadow_desk._news_blocks(ranked, use_llm=True)
    assert block is not None
    assert (block["status"], block["error_type"]) == ("unavailable", "RuntimeError")


async def test_news_block_helper_only_accepts_dicts():
    assert TradingCopilot._news_block(SimpleNamespace(news_context={"status": "ok"})) == {"status": "ok"}
    assert TradingCopilot._news_block(SimpleNamespace(news_context=None)) is None
    assert TradingCopilot._news_block(SimpleNamespace()) is None


def test_no_gate_or_policy_module_reads_news():
    root = Path(__file__).resolve().parents[2] / "agentic_trader"
    paths = [
        root / "execution/card_policy.py",
        root / "research/setups/card_stats.py",
        root / "agent/position_sizing.py",
        *sorted((root / "risk").rglob("*.py")),
        *sorted((root / "execution").rglob("*.py")),
        *sorted((root / "broker").rglob("*.py")),
    ]
    assert len(paths) > 10 and all(path.is_file() for path in paths)
    for path in paths:
        assert "news" not in path.read_text().lower(), path
