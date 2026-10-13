"""News scorer (direction-free JSON labels) and the per-scan service that builds the context blocks."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agentic_trader.agent.news_context import (
    BLOCK_KEYS,
    NEWS_PROMPT_SHA256,
    NewsContextService,
    NewsScore,
    NewsScorer,
    unavailable_block,
)
from agentic_trader.config import NewsConfig
from agentic_trader.data.news import NewsPage, normalize_news
from tests.data.test_news import NOW, row


def reply(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


GOOD = {"tone": "negative", "relevance": "company", "catalyst": "earnings", "one_line": "Missed on revenue."}


class FakeComplete:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return reply(answer)


def items(*headlines):
    return normalize_news([row(i, h, ["AAPL"], hours_ago=i) for i, h in enumerate(headlines, 1)])


async def test_scorer_builds_a_direction_free_prompt_and_parses_json():
    complete = FakeComplete([GOOD])
    scorer = NewsScorer(model="openai/gpt-4o", api_key="k", timeout_seconds=7, complete=complete)
    score = await scorer.score("AAPL", items("Apple misses", "Apple guides lower"))
    assert score == NewsScore(**GOOD)
    [call] = complete.calls
    assert (call["model"], call["api_key"], call["temperature"], call["timeout"]) == ("openai/gpt-4o", "k", 0, 7)
    assert call["response_format"] == {"type": "json_object"}
    system, user = call["messages"]
    assert system["role"] == "system" and "independent of any trade direction" in system["content"]
    assert (
        "Symbol: AAPL" in user["content"]
        and "Apple misses" in user["content"]
        and "Apple guides lower" in user["content"]
    )
    assert "LONG" not in user["content"] and "SHORT" not in user["content"]
    assert len(NEWS_PROMPT_SHA256) == 64


async def test_scorer_normalises_case_and_ignores_extra_keys():
    complete = FakeComplete(
        [{**GOOD, "tone": " Negative ", "catalyst": "EARNINGS", "confidence": 0.9, "one_line": "x" * 500}]
    )
    score = await NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=complete).score("AAPL", items("a"))
    assert (score.tone, score.catalyst) == ("negative", "earnings") and len(score.one_line) == 160


async def test_scorer_invalid_tone_is_score_failed_through_the_service():
    complete = FakeComplete([{**GOOD, "tone": "bullish"}])
    scorer = NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=complete)
    with pytest.raises(ValueError):
        await scorer.score("AAPL", items("a"))


async def test_scorer_strips_markdown_fences_like_the_evaluator():
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"

    async def complete(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=fenced))])

    score = await NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=complete).score("AAPL", items("a"))
    assert score == NewsScore(**GOOD)


def test_user_message_clips_each_summary_to_400_characters():
    long = normalize_news([row(1, "Apple misses", ["AAPL"], summary="s" * 2000)])
    message = NewsScorer.user_message("AAPL", long)
    assert "Apple misses — " + "s" * 400 in message and "s" * 401 not in message


def page_with(*rows):
    return NewsPage(
        normalize_news(rows), NOW, NOW, ("AAPL", "MSFT"), (NOW - timedelta(hours=48), NOW), 1, ("sha-p",), False
    )


class FakeSource:
    def __init__(self, page=None, error=None, delay=0.0):
        self.page, self.error, self.delay, self.calls = page, error, delay, []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        if self.error is not None:
            raise self.error
        return self.page


def service(source, answers=(), **config):
    cfg = NewsConfig(**config)
    scorer = NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=FakeComplete(answers))
    return NewsContextService(source, scorer, cfg, clock=lambda: NOW), scorer


async def test_service_scores_in_rank_order_and_builds_every_status():
    page = page_with(
        row(1, "Apple misses", ["AAPL"], hours_ago=1),
        row(2, "Apple list", ["AAPL", "B", "C", "D", "E", "F"], hours_ago=2),
        row(3, "Microsoft wins", ["MSFT"], hours_ago=3),
        row(4, "Nvidia", ["NVDA"], hours_ago=1),
    )
    source = FakeSource(page)
    svc, scorer = service(source, answers=[GOOD], max_headlines_recorded=1)
    blocks = await svc.for_symbols(["aapl", "TSLA", "MSFT", "NVDA"], score_first=1)
    assert set(blocks) == {"AAPL", "TSLA", "MSFT", "NVDA"}
    assert source.calls[0][0] == ("AAPL", "TSLA", "MSFT", "NVDA")
    assert source.calls[0][1] == NOW - timedelta(hours=48) and source.calls[0][2] == NOW
    aapl = blocks["AAPL"]
    assert tuple(aapl) == BLOCK_KEYS
    assert aapl["status"] == "ok" and aapl["score"] == GOOD and aapl["model"] == "m"
    assert aapl["prompt_sha256"] == NEWS_PROMPT_SHA256 and aapl["applied"] is False
    assert (aapl["n_items"], aapl["n_market_wide"], aapl["window_hours"], aapl["truncated"]) == (1, 1, 48, False)
    assert aapl["headlines"] == [
        {
            "id": 1,
            "created_at": (NOW - timedelta(hours=1)).isoformat(),
            "source": "benzinga",
            "headline": "Apple misses",
        }
    ]
    assert aapl["as_of"] == NOW.isoformat() and aapl["page_sha256"] == "sha-p" and aapl["error_type"] is None
    assert (
        blocks["TSLA"]["status"] == "no_news" and blocks["TSLA"]["score"] is None and blocks["TSLA"]["headlines"] == []
    )
    assert blocks["MSFT"]["status"] == "unscored" and blocks["MSFT"]["n_items"] == 1 and blocks["MSFT"]["model"] is None
    assert blocks["NVDA"]["status"] == "unscored"
    assert len(scorer.complete.calls) == 1  # only the first ranked symbol with items


async def test_service_symbol_without_items_is_no_news():
    svc, _ = service(FakeSource(page_with()))
    blocks = await svc.for_symbols(["ZZZ"], score_first=3)
    assert blocks["ZZZ"]["status"] == "no_news" and blocks["ZZZ"]["n_items"] == 0


async def test_service_score_failure_keeps_headlines():
    page = page_with(row(1, "a", ["AAPL"]), row(2, "b", ["MSFT"]))
    svc, _ = service(FakeSource(page), answers=[RuntimeError("llm down"), {**GOOD, "tone": "bullish"}])
    blocks = await svc.for_symbols(["AAPL", "MSFT"], score_first=2)
    assert blocks["AAPL"]["status"] == "score_failed" and blocks["AAPL"]["error_type"] == "RuntimeError"
    assert blocks["AAPL"]["headlines"][0]["headline"] == "a" and blocks["AAPL"]["score"] is None
    assert blocks["MSFT"]["status"] == "score_failed" and blocks["MSFT"]["error_type"] == "ValidationError"
    assert "llm down" not in json.dumps(blocks)


async def test_service_fetch_failure_or_timeout_is_unavailable_for_all():
    svc, scorer = service(FakeSource(error=ConnectionError("x")))
    blocks = await svc.for_symbols(["AAPL", "MSFT"], score_first=2)
    assert {b["status"] for b in blocks.values()} == {"unavailable"}
    assert blocks["AAPL"]["error_type"] == "ConnectionError" and blocks["AAPL"]["as_of"] is None
    assert blocks["AAPL"] == unavailable_block(NewsConfig(), "ConnectionError")
    assert scorer.complete.calls == []

    class Slow:
        def fetch(self, symbols, start, end):
            time.sleep(0.3)
            return page_with()

    svc, _ = service(Slow(), fetch_timeout_seconds=0.05)
    blocks = await svc.for_symbols(["AAPL"], score_first=1)
    assert blocks["AAPL"]["status"] == "unavailable" and blocks["AAPL"]["error_type"] == "TimeoutError"


async def test_service_scoring_timeout_is_score_failed():
    async def hang(**kwargs):
        await asyncio.sleep(0.3)
        return reply(GOOD)

    scorer = NewsScorer(model="m", api_key=None, timeout_seconds=0.05, complete=hang)
    svc = NewsContextService(FakeSource(page_with(row(1, "a", ["AAPL"]))), scorer, NewsConfig(), clock=lambda: NOW)
    blocks = await svc.for_symbols(["AAPL"], score_first=1)
    assert blocks["AAPL"]["status"] == "score_failed" and blocks["AAPL"]["error_type"] == "TimeoutError"


async def test_service_without_a_scorer_marks_items_unscored_and_blocks_are_json_safe():
    svc = NewsContextService(FakeSource(page_with(row(1, "a", ["AAPL"]))), None, NewsConfig(), clock=lambda: NOW)
    blocks = await svc.for_symbols(["AAPL"], score_first=5)
    assert blocks["AAPL"]["status"] == "unscored"
    json.dumps(blocks, allow_nan=False)


async def test_service_empty_symbols_returns_empty_without_fetching():
    source = FakeSource(page_with())
    svc, _ = service(source)
    assert await svc.for_symbols([], score_first=1) == {} and source.calls == []
