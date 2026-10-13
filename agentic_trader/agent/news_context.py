"""News as measured context: a direction-free LLM scorer and the per-scan block builder (docs/news-context.md).

One bounded fetch per scan for the ranked symbols; the first ``score_first`` symbols with items are
scored; every requested symbol gets one block with the spec's keys. Nothing here raises into the scan:
a fetch failure is ``unavailable`` for every symbol, a scoring failure is ``score_failed`` for its symbol.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import litellm
from pydantic import BaseModel, field_validator

from agentic_trader.config import AppConfig, NewsConfig
from agentic_trader.data.news import AlpacaNewsSource, NewsItem, NewsSelection, NewsSource, select_items
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.transport.alpaca import BoundedNewsClient


logger = logging.getLogger(__name__)

__all__ = [
    "BLOCK_KEYS",
    "NEWS_PROMPT_SHA256",
    "NEWS_SYSTEM_PROMPT",
    "NewsContextService",
    "NewsScore",
    "NewsScorer",
    "build_news_context_service",
    "unavailable_block",
]

NEWS_SYSTEM_PROMPT = (
    "You label recent news items for a trading desk's research journal. Judge the tone of the news "
    "for the company's shares over the next few sessions, independent of any trade direction; "
    "do not give trading advice. Reply with JSON only, exactly these keys: "
    '{"tone": "negative|neutral|positive", "relevance": "company|sector|market", '
    '"catalyst": "earnings|guidance|m_and_a|regulatory|product|analyst|macro|other", '
    '"one_line": "a neutral summary of at most 160 characters"}.'
)
NEWS_PROMPT_SHA256 = hashlib.sha256(NEWS_SYSTEM_PROMPT.encode()).hexdigest()
ONE_LINE_MAX = 160
SUMMARY_MAX = 400  # provider summaries are clipped in the scorer's prompt

BLOCK_KEYS: tuple[str, ...] = (
    "status",
    "as_of",
    "window_hours",
    "n_items",
    "n_market_wide",
    "truncated",
    "headlines",
    "score",
    "model",
    "prompt_sha256",
    "error_type",
    "page_sha256",
    "applied",
)


class NewsScore(BaseModel):
    """The scorer's labels; enum strings are lower-cased and stripped before validation."""

    model_config = {"frozen": True, "extra": "ignore"}

    tone: Literal["negative", "neutral", "positive"]
    relevance: Literal["company", "sector", "market"]
    catalyst: Literal["earnings", "guidance", "m_and_a", "regulatory", "product", "analyst", "macro", "other"]
    one_line: str

    @field_validator("tone", "relevance", "catalyst", mode="before")
    @classmethod
    def lower(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("one_line", mode="before")
    @classmethod
    def clip(cls, value: Any) -> str:
        return " ".join(str(value or "").split())[:ONE_LINE_MAX]


class NewsScorer:
    """One LiteLLM call per symbol; ``complete`` is injectable for tests and defaults to ``litellm.acompletion``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        timeout_seconds: float,
        complete: Callable[..., Awaitable[Any]] | None = None,
    ):
        self.model, self.api_key, self.timeout_seconds = model, api_key, timeout_seconds
        self.complete = complete if complete is not None else litellm.acompletion

    @staticmethod
    def user_message(symbol: str, items: Sequence[NewsItem]) -> str:
        lines = [
            f"- {item.created_at.isoformat()} [{item.source}] {item.headline}"
            + (f" — {item.summary[:SUMMARY_MAX]}" if item.summary else "")
            for item in items
        ]
        return f"Symbol: {symbol}\nNews, newest first:\n" + "\n".join(lines)

    async def score(self, symbol: str, items: Sequence[NewsItem]) -> NewsScore:
        # The scorer enforces its own deadline even if the client ignores ``timeout``.
        response = await asyncio.wait_for(
            self.complete(
                api_key=self.api_key,
                model=self.model,
                messages=[
                    {"role": "system", "content": NEWS_SYSTEM_PROMPT},
                    {"role": "user", "content": self.user_message(symbol, items)},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                timeout=self.timeout_seconds,
            ),
            timeout=self.timeout_seconds,
        )
        raw_text = response.choices[0].message.content.strip()
        # Clean possible markdown wrapping, exactly as the evaluator does.
        cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise TypeError("News score reply is not a JSON object")
        return NewsScore.model_validate(payload)


def _block(
    config: NewsConfig,
    *,
    status: str,
    as_of: str | None,
    selection: NewsSelection | None,
    truncated: bool,
    score: NewsScore | None,
    model: str | None,
    error_type: str | None,
    page_sha256: str | None,
) -> dict[str, Any]:
    headlines = (
        [
            {"id": item.id, "created_at": item.created_at.isoformat(), "source": item.source, "headline": item.headline}
            for item in selection.items[: config.max_headlines_recorded]
        ]
        if selection is not None
        else []
    )
    return {
        "status": status,
        "as_of": as_of,
        "window_hours": config.window_hours,
        "n_items": selection.n_items if selection is not None else 0,
        "n_market_wide": selection.n_market_wide if selection is not None else 0,
        "truncated": truncated,
        "headlines": headlines,
        "score": score.model_dump() if score is not None else None,
        "model": model,
        "prompt_sha256": NEWS_PROMPT_SHA256 if score is not None else None,
        "error_type": error_type,
        "page_sha256": page_sha256,
        "applied": False,
    }


def unavailable_block(config: NewsConfig, error_type: str | None) -> dict[str, Any]:
    """The block every symbol gets when the fetch failed or the step raised."""
    return _block(
        config,
        status="unavailable",
        as_of=None,
        selection=None,
        truncated=False,
        score=None,
        model=None,
        error_type=error_type,
        page_sha256=None,
    )


class NewsContextService:
    """Per-scan news blocks: one fetch, selection per symbol, scoring for the first ``score_first`` ranked symbols."""

    def __init__(
        self,
        source: NewsSource,
        scorer: NewsScorer | None,
        config: NewsConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.source, self.scorer, self.config, self.clock = source, scorer, config, clock

    async def _score(self, symbol: str, items: Sequence[NewsItem]) -> tuple[NewsScore | None, str | None]:
        assert self.scorer is not None
        try:
            # A hard bound over the provider's own timeout: a client that ignores ``timeout`` cannot hang the scan.
            score = await asyncio.wait_for(self.scorer.score(symbol, items), timeout=self.scorer.timeout_seconds + 5)
        except Exception as exc:
            logger.warning(
                "News scoring failed",
                extra={"event": "news_score_failed", "symbol": symbol, "reason": type(exc).__name__},
            )
            return None, type(exc).__name__
        return score, None

    async def for_symbols(self, symbols: Sequence[str], *, score_first: int) -> dict[str, dict[str, Any]]:
        ordered = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        if not ordered:
            return {}
        now = self.clock()
        start = now - timedelta(hours=self.config.window_hours)
        try:
            page = await asyncio.wait_for(
                asyncio.to_thread(self.source.fetch, ordered, start, now), timeout=self.config.fetch_timeout_seconds
            )
        except Exception as exc:
            logger.warning("News fetch failed", extra={"event": "news_fetch_failed", "reason": type(exc).__name__})
            return {symbol: unavailable_block(self.config, type(exc).__name__) for symbol in ordered}
        selections = {
            symbol: select_items(
                page,
                symbol,
                now=now,
                window_hours=self.config.window_hours,
                max_items=self.config.max_items_per_symbol,
                max_symbols_per_item=self.config.max_symbols_per_item,
            )
            for symbol in ordered
        }
        to_score = [s for s in ordered if selections[s].n_items > 0][: max(score_first, 0)] if self.scorer else []
        scored = dict(
            zip(to_score, await asyncio.gather(*(self._score(s, selections[s].items) for s in to_score)), strict=True)
        )
        as_of = page.received_at.isoformat()
        page_sha = page.page_sha256[-1] if page.page_sha256 else None
        blocks: dict[str, dict[str, Any]] = {}
        for symbol in ordered:
            selection = selections[symbol]
            score, error_type, model = None, None, None
            if selection.n_items == 0:
                status = "no_news"
            elif symbol in scored:
                score, error_type = scored[symbol]
                model = self.scorer.model if self.scorer is not None else None
                status = "ok" if score is not None else "score_failed"
            else:
                status = "unscored"
            blocks[symbol] = _block(
                self.config,
                status=status,
                as_of=as_of,
                selection=selection,
                truncated=page.truncated,
                score=score,
                model=model if score is not None else None,
                error_type=error_type,
                page_sha256=page_sha,
            )
        return blocks


def build_news_context_service(config: AppConfig) -> NewsContextService:
    """The daemon's service: bounded raw-data client, the desk's LLM model unless ``news.model`` overrides it."""
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise ValueError("Alpaca data credentials are required")
    policy = config.news
    client = BoundedNewsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    source = AlpacaNewsSource(
        client,
        pacer=RequestPacer(policy.max_requests_per_minute),
        chunk_size=policy.chunk_size,
        max_items=policy.max_items_per_fetch,
    )
    model = policy.model or config.llm_model
    api_key = config.model_copy(update={"llm_model": model}).llm_api_key if policy.model else config.llm_api_key
    scorer = NewsScorer(model=model, api_key=api_key, timeout_seconds=policy.score_timeout_seconds) if api_key else None
    return NewsContextService(source, scorer, policy)
