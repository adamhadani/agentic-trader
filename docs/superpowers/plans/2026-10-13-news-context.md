# News as measured context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a labelled, LLM-scored news block to every ranked candidate of a universe scan, carry it on the card, in provenance and in the scan journal, and attribute it in `cards outcomes` the way the LLM gate is attributed — never a gate.

**Architecture:** A typed Alpaca news source (Benzinga through the desk's data keys, bounded transport) is fetched once per scan for the ranked symbols; a direction-free scorer labels the first `max_scored_per_scan` symbols; the block rides on `LLMTradeEvaluation.news_context` (the `corporate_actions` precedent) into the card, `decision_provenance["news"]` and the `scan_candidates_ranked` candidate's `news`. `cards outcomes` flattens it into `news_*` columns and a `news_context` summary with a deterministic direction alignment. A research-side cached acquisition (`alpha news`) mirrors the corporate-actions one.

**Tech Stack:** Python 3.14, `uv`, pydantic v2 (frozen models), alpaca-py `NewsClient`/`NewsRequest` (0.44.0), LiteLLM (`acompletion`), pandas, click, pytest (network blocked).

**Spec:** `docs/superpowers/specs/2026-10-13-news-context-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-news`; never touch `/Users/adamhadani/Development/agentic-trader`.
- ruff strict: no `dict()`/`list()` calls where a literal works (C408), imports at module top (PLC0415; `import litellm` at top as `evaluator.py` does), no unused unpacked variables (RUF059), no builtin shadowing (A002: never name a parameter `id`, use `item_id`). mypy must pass. Run `env -u VIRTUAL_ENV uv run pre-commit run --all-files` before each commit; the ruff-format hook also reformats fenced code in Markdown — re-add and recommit when it changes a doc.
- No new schema or migration (head stays `008_alpha_pipeline`); no new `EventKind`; nothing in `agentic_trader/risk`, `execution/card_policy.py`, `research/setups/card_stats.py` or admission reads news.
- The LLM gate's prompt (`agent/prompts.py`, `USER_EVALUATION_TEMPLATE`, `build_system_prompt`) does not change.
- Every news block is JSON-safe (no NaN, datetimes as ISO strings) and carries type names only in `error_type`, never exception text.
- Tests use fakes only (the suite blocks network I/O); pydantic models are frozen; no compatibility aliases.
- Spec block schema (§3.2), card texts (§3.4), config fields and defaults (§3.6) are binding and copied verbatim below.

## Review Focus

1. Provider rows with a `created_at` string lacking a timezone or in another zone, or `symbols` in lower case / with a dot (`BRK.B`) → normalised to UTC and upper case, never a crash (Task 2 test `test_normalize_news_handles_naive_timestamps_and_symbol_case`).
2. The LLM answers with capitalised or padded enum values (`"Negative"`, `" earnings "`) or extra keys → lower-cased/stripped before validation, extra keys ignored; a value outside the enum is `score_failed` (Task 3 tests `test_scorer_normalises_case_and_ignores_extra_keys`, `test_scorer_invalid_tone_is_score_failed`).
3. A requested symbol absent from every provider item → `no_news`, not a `KeyError` (Task 3 `test_service_symbol_without_items_is_no_news`).
4. Headline or one-liner containing `<`, `&`, quotes or newlines → HTML-escaped and whitespace-collapsed on the Telegram card (Task 5 `test_news_line_escapes_html_and_collapses_whitespace`).
5. The scan's news step raising anything (source constructor, `for_symbols`, a non-dict return) → every block `unavailable`, the scan's cards, budget and order unchanged (Task 4 `test_news_service_failure_is_unavailable_and_changes_nothing`).

---

### Task 1: `NewsConfig`, `news_live`, and the loader reads `news:` and `corporate_actions:`

**Files:**
- Modify: `agentic_trader/config.py` (after `CorporateActionsConfig` ~line 346; `AppConfig` fields ~line 819; `corporate_actions_live` ~line 907; `load_config` kwargs ~line 1086)
- Modify: `config/config.yaml` (append two blocks at the end)
- Test: `tests/config/test_news_config.py`

**Interfaces:**
- Produces: `class NewsConfig(BaseModel)` with the fields below; `AppConfig.news: NewsConfig`; `def news_live(config: AppConfig) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""News context config: defaults, validation, and the loader passing `news:` and `corporate_actions:`."""

import textwrap

import pytest

from agentic_trader.config import AppConfig, NewsConfig, load_config, news_live


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_defaults_match_the_spec():
    cfg = NewsConfig()
    assert cfg.enabled is True
    assert (cfg.window_hours, cfg.max_items_per_symbol, cfg.max_symbols_per_item) == (48, 12, 5)
    assert (cfg.max_scored_per_scan, cfg.max_headlines_recorded, cfg.max_headline_chars) == (6, 5, 110)
    assert (cfg.chunk_size, cfg.max_items_per_fetch, cfg.max_requests_per_minute) == (50, 1000, 100)
    assert (cfg.fetch_timeout_seconds, cfg.score_timeout_seconds, cfg.model) == (15.0, 20.0, None)
    assert AppConfig().news == cfg


@pytest.mark.parametrize(
    "bad",
    [
        {"window_hours": 0},
        {"window_hours": 169},
        {"max_scored_per_scan": 0},
        {"fetch_timeout_seconds": 0},
        {"fetch_timeout_seconds": float("inf")},
        {"max_items_per_fetch": 10},
        {"unknown": 1},
    ],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValueError):
        NewsConfig(**bad)


def test_loader_passes_news_and_corporate_actions_blocks(tmp_path):
    path = write(
        tmp_path,
        """
        news: {enabled: false, window_hours: 24, model: "openai/gpt-4o-mini"}
        corporate_actions: {entry_block: enforce, pre_days: 3}
        database: {path: test.db}
        """,
    )
    config = load_config(path, environ=PRODUCTION)
    assert config.news.enabled is False and config.news.window_hours == 24
    assert config.news.model == "openai/gpt-4o-mini"
    assert config.corporate_actions.entry_block == "enforce" and config.corporate_actions.pre_days == 3


def test_news_live_needs_the_flag_and_alpaca_data_keys():
    base = AppConfig()
    assert news_live(base) is False  # no keys
    keyed = base.model_copy(update={"alpaca_api_key": "k", "alpaca_api_secret": "s"})
    assert news_live(keyed) is True
    assert news_live(keyed.model_copy(update={"news": NewsConfig(enabled=False)})) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/config/test_news_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'NewsConfig'`.

- [ ] **Step 3: Implement the config model, the live helper and the loader lines**

After `CorporateActionsConfig` in `agentic_trader/config.py`:

```python
class NewsConfig(BaseModel):
    """News as measured context (docs/news-context.md).

    One bounded news fetch per universe scan for the ranked candidates' symbols; the first
    ``max_scored_per_scan`` ranked symbols with items are scored by the LLM (tone, catalyst),
    the rest keep counts and headlines. The block is context on the card and in the journal,
    attributed by ``cards outcomes``; nothing reads it as a gate or signal.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = True
    window_hours: int = Field(default=48, ge=1, le=168)
    max_items_per_symbol: int = Field(default=12, ge=1, le=100)
    max_symbols_per_item: int = Field(default=5, ge=1, le=50)
    max_scored_per_scan: int = Field(default=6, ge=1, le=50)
    max_headlines_recorded: int = Field(default=5, ge=1, le=20)
    max_headline_chars: int = Field(default=110, ge=20, le=300)
    chunk_size: int = Field(default=50, ge=1, le=200)
    max_items_per_fetch: int = Field(default=1000, ge=50, le=5000)
    max_requests_per_minute: int = Field(default=100, ge=1, le=200)
    fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120, allow_inf_nan=False)
    score_timeout_seconds: float = Field(default=20.0, gt=0, le=120, allow_inf_nan=False)
    model: str | None = None
```

In `AppConfig`, directly after `corporate_actions: CorporateActionsConfig = …`:

```python
    news: NewsConfig = Field(default_factory=NewsConfig)
```

After `corporate_actions_live`:

```python
def news_live(config: AppConfig) -> bool:
    """News context is computed only with the feature on and Alpaca data credentials present."""
    return config.news.enabled and bool(config.alpaca_api_key and config.alpaca_api_secret)
```

In `load_config`'s `AppConfig(...)` call, directly after the `book_sizing=BookSizingConfig(...)` line:

```python
corporate_actions = (CorporateActionsConfig(**(cfg_dict.get("corporate_actions") or {})),)
news = (NewsConfig(**(cfg_dict.get("news") or {})),)
```

Append to `config/config.yaml` (defaults spelled out; behaviour-neutral):

```yaml

# Corporate actions (docs/corporate-actions.md): daily capture, entry rule mode. `enforce` is an operator decision.
corporate_actions:
  enabled: true
  entry_block: "preview"

# News as measured context (docs/news-context.md): one bounded fetch per universe scan, LLM tone for the
# first `max_scored_per_scan` ranked symbols; a card line and journal block, never a gate.
news:
  enabled: true
  window_hours: 48
  max_scored_per_scan: 6
```

- [ ] **Step 4: Run the tests and the existing config tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/config -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/config.py config/config.yaml tests/config/test_news_config.py
git commit -m "News context: NewsConfig, news_live, loader passes news and corporate_actions blocks"
```

---

### Task 2: News model, Alpaca source, selection — `agentic_trader/data/news.py`

**Files:**
- Create: `agentic_trader/data/news.py`
- Modify: `agentic_trader/transport/alpaca.py` (add `BoundedNewsClient` after `BoundedCorporateActionsClient`; import `NewsClient` from `alpaca.data.historical.news`)
- Test: `tests/data/test_news.py`

**Interfaces:**
- Produces:
  - `class NewsItem(BaseModel)` frozen: `id: int`, `headline: str`, `summary: str = ""`, `source: str`, `url: str | None = None`, `created_at: datetime` (UTC-aware), `symbols: tuple[str, ...]`.
  - `def normalize_news(rows: Iterable[Mapping[str, Any]]) -> tuple[NewsItem, ...]` — de-duplicated by `id` (first wins), newest first.
  - `@dataclass(frozen=True) class NewsPage`: `items: tuple[NewsItem, ...]`, `requested_at: datetime`, `received_at: datetime`, `symbols: tuple[str, ...]`, `window: tuple[datetime, datetime]`, `pages: int`, `page_sha256: tuple[str, ...]`, `truncated: bool`.
  - `class NewsSource(Protocol)`: `def fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage`.
  - `SDK_PAGE_CAP = 2500` (alpaca-py follows at most 50 pages of 50).
  - `class AlpacaNewsSource(client, *, pacer: RequestPacer | None = None, chunk_size: int = 50, max_items: int | None = 1000, clock=...)` with `fetch(...)`.
  - `@dataclass(frozen=True) class NewsSelection`: `items: tuple[NewsItem, ...]`, `n_items: int`, `n_market_wide: int`.
  - `def select_items(page: NewsPage, symbol: str, *, now: datetime, window_hours: int, max_items: int, max_symbols_per_item: int) -> NewsSelection`.
  - `class BoundedNewsClient(BoundedTransport, NewsClient)` in `transport/alpaca.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Typed news: normalisation of Alpaca rows, the chunked source with page evidence, and selection."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentic_trader.data.news import (
    SDK_PAGE_CAP,
    AlpacaNewsSource,
    NewsItem,
    NewsPage,
    normalize_news,
    select_items,
)


NOW = datetime(2026, 10, 13, 14, 35, tzinfo=UTC)


def row(item_id, headline, symbols, *, hours_ago=1.0, summary="", source="benzinga", created_at=None):
    return {
        "id": item_id,
        "headline": headline,
        "author": "x",
        "created_at": created_at or (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "updated_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "summary": summary,
        "content": "<p>never fetched</p>",
        "url": "https://example.test/a",
        "images": [],
        "symbols": symbols,
        "source": source,
    }


def test_normalize_news_handles_naive_timestamps_and_symbol_case():
    items = normalize_news(
        [
            row(1, "A", ["aapl", "brk.b"], created_at="2026-10-13T13:00:00"),
            row(2, "B", ["MSFT"], hours_ago=0.5),
            row(1, "A again", ["AAPL"]),
        ]
    )
    assert [i.id for i in items] == [2, 1]  # newest first, duplicate id dropped
    assert items[1].symbols == ("AAPL", "BRK.B")
    assert items[1].created_at.tzinfo is UTC and items[1].created_at.hour == 13
    assert items[1].summary == "" and items[1].url == "https://example.test/a"


class FakeClient:
    """A raw-data news client: records requests, replays canned pages, drives the observer."""

    def __init__(self, pages_by_chunk, *, fail_on=None, token_for=()):
        self.pages_by_chunk, self.fail_on, self.token_for, self.requests = pages_by_chunk, fail_on, token_for, []
        self.observer = None

    @contextmanager
    def observe_responses(self, observer):
        self.observer = observer
        try:
            yield
        finally:
            self.observer = None

    def get_news(self, request):
        self.requests.append(request)
        key = tuple(request.symbols.split(","))
        if self.fail_on is not None and self.fail_on in key:
            raise RuntimeError("provider down")
        rows = self.pages_by_chunk[key]
        response = {"news": rows, "next_page_token": "more" if key in self.token_for else None}
        self.observer(SimpleNamespace(response=response))
        return response


def test_source_chunks_symbols_dedupes_items_and_records_page_evidence():
    shared = row(7, "AAPL and MSFT", ["AAPL", "MSFT"])
    client = FakeClient({("AAPL", "MSFT"): [shared, row(1, "A", ["AAPL"])], ("NVDA",): [shared]})
    source = AlpacaNewsSource(client, chunk_size=2, max_items=100, clock=lambda: NOW)
    page = source.fetch(["nvda", "aapl", "MSFT"], NOW - timedelta(hours=48), NOW)
    assert [r.symbols for r in client.requests] == ["AAPL,MSFT", "NVDA"]
    first = client.requests[0]
    assert (first.limit, first.sort, first.include_content) == (100, "desc", False)
    assert sorted(i.id for i in page.items) == [1, 7]
    assert page.symbols == ("AAPL", "MSFT", "NVDA") and page.pages == 2 and len(page.page_sha256) == 2
    assert page.truncated is False and page.requested_at == page.received_at == NOW


def test_source_marks_truncation_from_a_page_token_or_the_cap():
    client = FakeClient({("AAPL",): [row(1, "A", ["AAPL"])]}, token_for={("AAPL",)})
    assert AlpacaNewsSource(client, max_items=100, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is True
    many = [row(i, "A", ["AAPL"]) for i in range(100)]
    client = FakeClient({("AAPL",): many})
    assert AlpacaNewsSource(client, max_items=100, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is True
    client = FakeClient({("AAPL",): many})
    assert AlpacaNewsSource(client, max_items=None, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW).truncated is False
    assert SDK_PAGE_CAP == 2500


def test_source_sends_limit_none_when_unbounded():
    client = FakeClient({("AAPL",): []})
    AlpacaNewsSource(client, max_items=None, clock=lambda: NOW).fetch(["AAPL"], NOW, NOW)
    assert client.requests[0].limit is None


def test_source_fails_whole_fetch_on_a_chunk_failure_and_rejects_non_mapping():
    client = FakeClient({("AAPL",): [], ("MSFT",): []}, fail_on="MSFT")
    with pytest.raises(RuntimeError):
        AlpacaNewsSource(client, chunk_size=1, clock=lambda: NOW).fetch(["AAPL", "MSFT"], NOW, NOW)

    class Typed:
        @contextmanager
        def observe_responses(self, observer):
            yield

        def get_news(self, request):
            return SimpleNamespace(data={"news": []})

    with pytest.raises(TypeError):
        AlpacaNewsSource(Typed(), clock=lambda: NOW).fetch(["AAPL"], NOW, NOW)


def test_source_requires_symbols_and_a_positive_chunk():
    with pytest.raises(ValueError):
        AlpacaNewsSource(FakeClient({}), chunk_size=0)
    with pytest.raises(ValueError):
        AlpacaNewsSource(FakeClient({}), clock=lambda: NOW).fetch([" "], NOW, NOW)


def page_of(*rows):
    items = normalize_news(rows)
    return NewsPage(items, NOW, NOW, ("AAPL",), (NOW - timedelta(hours=48), NOW), 1, ("p",), False)


def test_select_items_window_market_wide_and_cap():
    page = page_of(
        row(1, "fresh", ["AAPL"], hours_ago=1),
        row(2, "edge in", ["AAPL"], hours_ago=48),
        row(3, "too old", ["AAPL"], hours_ago=48.01),
        row(4, "list", ["AAPL", "B", "C", "D", "E", "F"], hours_ago=2),
        row(5, "future", ["AAPL"], hours_ago=-0.1),
        row(6, "other", ["MSFT"], hours_ago=1),
        row(7, "second", ["AAPL"], hours_ago=3),
    )
    picked = select_items(page, "aapl", now=NOW, window_hours=48, max_items=2, max_symbols_per_item=5)
    assert [i.id for i in picked.items] == [1, 7]  # newest two of the three in-window company items
    assert picked.n_items == 3 and picked.n_market_wide == 1


def test_select_items_for_an_absent_symbol_is_empty():
    picked = select_items(
        page_of(row(1, "a", ["AAPL"])), "TSLA", now=NOW, window_hours=48, max_items=5, max_symbols_per_item=5
    )
    assert picked == select_items(page_of(), "TSLA", now=NOW, window_hours=48, max_items=5, max_symbols_per_item=5)
    assert picked.items == () and picked.n_items == 0 and picked.n_market_wide == 0


def test_news_item_is_frozen():
    item = normalize_news([row(1, "a", ["AAPL"])])[0]
    with pytest.raises(Exception):
        item.headline = "b"  # type: ignore[misc]
    assert isinstance(item, NewsItem)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/data/test_news.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.data.news'`.

- [ ] **Step 3: Implement the module and the bounded client**

`agentic_trader/transport/alpaca.py`: add `from alpaca.data.historical.news import NewsClient` beside the other SDK imports and, after `BoundedCorporateActionsClient`:

```python
class BoundedNewsClient(BoundedTransport, NewsClient):
    """Benzinga news (``/v1beta1/news``) under the same socket bounds."""
```

`agentic_trader/data/news.py`:

```python
"""Typed news items from Alpaca's news API, a chunked bounded source, and per-symbol selection.

Research and the scan share one normalisation. Article bodies are never requested
(``include_content=False``). Research only on the acquisition side; the scan reads one page per scan.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from alpaca.data.requests import NewsRequest
from pydantic import BaseModel, Field, field_validator

from agentic_trader.data.pacing import RequestPacer


__all__ = [
    "SDK_PAGE_CAP",
    "AlpacaNewsSource",
    "NewsItem",
    "NewsPage",
    "NewsSelection",
    "NewsSource",
    "normalize_news",
    "select_items",
]

# alpaca-py's ``_get_marketdata`` follows at most 50 pages of 50 items when ``limit`` is None.
SDK_PAGE_CAP = 2500


class NewsItem(BaseModel):
    """One provider item: headline-level only, symbols as tagged (upper case)."""

    model_config = {"frozen": True, "extra": "ignore"}

    id: int
    headline: str
    summary: str = ""
    source: str
    url: str | None = None
    created_at: datetime
    symbols: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("created_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @field_validator("symbols", mode="before")
    @classmethod
    def upper(cls, value: Any) -> tuple[str, ...]:
        return tuple(str(s).strip().upper() for s in (value or ()) if str(s).strip())

    @field_validator("summary", mode="before")
    @classmethod
    def text(cls, value: Any) -> str:
        return "" if value is None else str(value)


def normalize_news(rows: Iterable[Mapping[str, Any]]) -> tuple[NewsItem, ...]:
    """Typed items, de-duplicated by id (first wins), newest first."""
    seen: dict[int, NewsItem] = {}
    for raw in rows:
        item = NewsItem.model_validate(dict(raw))
        seen.setdefault(item.id, item)
    return tuple(sorted(seen.values(), key=lambda item: (item.created_at, item.id), reverse=True))


@dataclass(frozen=True)
class NewsPage:
    items: tuple[NewsItem, ...]
    requested_at: datetime
    received_at: datetime
    symbols: tuple[str, ...]
    window: tuple[datetime, datetime]
    pages: int
    page_sha256: tuple[str, ...]
    truncated: bool


class NewsSource(Protocol):
    def fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage: ...


def _page_digest(response: Any) -> str:
    return hashlib.sha256(json.dumps(response, sort_keys=True, default=str).encode()).hexdigest()


class AlpacaNewsSource:
    """Blocking source over a raw-data ``BoundedNewsClient``; callers offload it to a thread.

    Symbols are requested in chunks as the comma-joined string the SDK expects. ``max_items`` is the
    SDK ``limit``, a cap on the whole paginated result of one request; ``None`` follows every page
    up to the SDK's own 50-page cap. A chunk is ``truncated`` when the provider still had a page
    token or the chunk filled its cap. A failed chunk fails the whole fetch.
    """

    def __init__(
        self,
        client: Any,
        *,
        pacer: RequestPacer | None = None,
        chunk_size: int = 50,
        max_items: int | None = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be positive or None")
        self.client, self.pacer, self.chunk_size, self.max_items, self.clock = (
            client,
            pacer,
            chunk_size,
            max_items,
            clock,
        )

    def fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage:
        ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not ordered:
            raise ValueError("At least one symbol is required")
        requested_at = self.clock()
        digests: list[str] = []
        rows: list[Mapping[str, Any]] = []
        truncated = False
        cap = self.max_items if self.max_items is not None else SDK_PAGE_CAP

        def observe(page: Any) -> None:
            digests.append(_page_digest(page.response))

        for index in range(0, len(ordered), self.chunk_size):
            chunk = ordered[index : index + self.chunk_size]
            if self.pacer is not None:
                self.pacer.acquire()
            request = NewsRequest(
                symbols=",".join(chunk),
                start=start,
                end=end,
                sort="desc",
                include_content=False,
                limit=self.max_items,
            )
            with self.client.observe_responses(observe):
                data = self.client.get_news(request)
            if not isinstance(data, Mapping):
                raise TypeError("News response is not a mapping; construct the client with raw_data=True")
            chunk_rows = list(data.get("news") or [])
            rows.extend(chunk_rows)
            if data.get("next_page_token") or len(chunk_rows) >= cap:
                truncated = True
        return NewsPage(
            items=normalize_news(rows),
            requested_at=requested_at,
            received_at=self.clock(),
            symbols=tuple(ordered),
            window=(start, end),
            pages=len(digests),
            page_sha256=tuple(digests),
            truncated=truncated,
        )


@dataclass(frozen=True)
class NewsSelection:
    items: tuple[NewsItem, ...]
    n_items: int
    n_market_wide: int


def select_items(
    page: NewsPage,
    symbol: str,
    *,
    now: datetime,
    window_hours: int,
    max_items: int,
    max_symbols_per_item: int,
) -> NewsSelection:
    """The symbol's company-specific items inside the window, newest first, capped.

    Items tagged with more than ``max_symbols_per_item`` symbols are market-wide lists: excluded and
    counted. Items dated after ``now`` are outside the window.
    """
    wanted = symbol.strip().upper()
    start = now - timedelta(hours=window_hours)
    company: list[NewsItem] = []
    market_wide = 0
    for item in page.items:
        if wanted not in item.symbols or not start <= item.created_at <= now:
            continue
        if len(item.symbols) > max_symbols_per_item:
            market_wide += 1
            continue
        company.append(item)
    company.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return NewsSelection(items=tuple(company[:max_items]), n_items=len(company), n_market_wide=market_wide)
```

Check the SDK's `NewsRequest` field types before committing: `sort` is `Sort` enum-compatible (`"desc"` is accepted as a string by pydantic's enum coercion — if the test's `first.sort == "desc"` fails because it returns `Sort.DESC`, compare with `str(first.sort) == "desc"` or `first.sort == Sort.DESC` and record that in the report; the behaviour is the same).

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/data/test_news.py tests/notifier/test_transport.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/data/news.py agentic_trader/transport/alpaca.py tests/data/test_news.py
git commit -m "News context: typed news items, chunked bounded Alpaca source, per-symbol selection"
```

---

### Task 3: Scorer and per-scan service — `agentic_trader/agent/news_context.py`

**Files:**
- Create: `agentic_trader/agent/news_context.py`
- Test: `tests/agent/test_news_context.py`

**Interfaces:**
- Consumes (Task 1): `NewsConfig`, `AppConfig.llm_model`, `AppConfig.llm_api_key`, `news_live`. (Task 2): `NewsSource`, `NewsPage`, `NewsItem`, `select_items`, `AlpacaNewsSource`, `BoundedNewsClient`.
- Produces:
  - `NEWS_SYSTEM_PROMPT: str`, `NEWS_PROMPT_SHA256: str`.
  - `class NewsScore(BaseModel)` frozen: `tone: Literal["negative", "neutral", "positive"]`, `relevance: Literal["company", "sector", "market"]`, `catalyst: Literal["earnings", "guidance", "m_and_a", "regulatory", "product", "analyst", "macro", "other"]`, `one_line: str` (≤ 160 chars).
  - `class NewsScorer(*, model: str, api_key: str | None, timeout_seconds: float, complete: Callable[..., Awaitable[Any]] | None = None)` with `async score(symbol: str, items: Sequence[NewsItem]) -> NewsScore`.
  - `def unavailable_block(config: NewsConfig, error_type: str | None) -> dict[str, Any]`.
  - `class NewsContextService(source: NewsSource, scorer: NewsScorer | None, config: NewsConfig, *, clock=...)` with `async for_symbols(symbols: Sequence[str], *, score_first: int) -> dict[str, dict[str, Any]]`.
  - `def build_news_context_service(config: AppConfig) -> NewsContextService` (raises `ValueError` without Alpaca data keys).
  - `BLOCK_KEYS: tuple[str, ...]` — the spec's block keys in order: `status, as_of, window_hours, n_items, n_market_wide, truncated, headlines, score, model, prompt_sha256, error_type, page_sha256, applied`.

- [ ] **Step 1: Write the failing tests**

```python
"""News scorer (direction-free JSON labels) and the per-scan service that builds the context blocks."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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
            import time

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_news_context.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.agent.news_context'`.

- [ ] **Step 3: Implement the module**

```python
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
            + (f" — {item.summary}" if item.summary else "")
            for item in items
        ]
        return f"Symbol: {symbol}\nNews, newest first:\n" + "\n".join(lines)

    async def score(self, symbol: str, items: Sequence[NewsItem]) -> NewsScore:
        response = await self.complete(
            api_key=self.api_key,
            model=self.model,
            messages=[
                {"role": "system", "content": NEWS_SYSTEM_PROMPT},
                {"role": "user", "content": self.user_message(symbol, items)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=self.timeout_seconds,
        )
        payload = json.loads(response.choices[0].message.content)
        if not isinstance(payload, dict):
            raise ValueError("News score reply is not a JSON object")
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
```

Note for the `score_failed` test: the `_block` call passes `model=None` when there is no score; the test asserts `model is None` only for `unscored`, and for `ok` expects `"m"`. Keep the spec: `model`/`prompt_sha256` are `None` when not scored (a failed score is "not scored").

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_news_context.py tests/data/test_news.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/agent/news_context.py tests/agent/test_news_context.py
git commit -m "News context: direction-free LLM scorer and the per-scan block service"
```

---

### Task 4: Evaluation field, scan integration, provenance and journal

**Files:**
- Modify: `agentic_trader/agent/evaluator.py` (`LLMTradeEvaluation` fields ~line 117; `evaluate_candidate` signature ~358; the three `LLMTradeEvaluation(...)` constructions at ~686, ~865 (`model_copy`), ~880; the `data.pop` block ~845)
- Modify: `agentic_trader/agent/copilot.py` (`__init__` kwargs ~249-305; `_llm_block` ~642; `run_scan` after `policy_by_rank` ~1230; `evaluate_candidate` call ~1343; provenance ~1453; journal call ~1576; `_journal_scan_ranking` ~1825-1866)
- Test: `tests/agent/test_evaluator_news_context.py`, `tests/agent/test_scan_news_context.py`

**Interfaces:**
- Consumes (Task 3): `NewsContextService.for_symbols(symbols, *, score_first)`, `unavailable_block`, `build_news_context_service`; (Task 1): `news_live`, `config.news.max_scored_per_scan`.
- Produces: `LLMTradeEvaluation.news_context: dict[str, Any] | None = None`; `RiskEvaluator.evaluate_candidate(..., news_context: Mapping[str, Any] | None = None)`; `TradingCopilot(..., news_context: NewsContextService | None = None)` and `self.news_context`; `TradingCopilot._news_block(evaluation) -> dict | None` (static); `TradingCopilot._news_blocks(ranked) -> list[dict | None]`; `_journal_scan_ranking(..., news_by_rank: list[dict[str, Any] | None])` writing candidate key `"news"`; provenance key `"news"`.

- [ ] **Step 1: Write the failing evaluator tests**

```python
"""`news_context` rides on the evaluation on every path and never reaches the LLM gate's prompt."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent import evaluator as evaluator_module
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from tests.agent.test_evaluator import create_equity_candidate, evaluator_factory  # noqa: F401  (fixture)


BLOCK = {
    "status": "ok",
    "as_of": "2026-10-13T14:35:00+00:00",
    "window_hours": 48,
    "n_items": 2,
    "n_market_wide": 0,
    "truncated": False,
    "headlines": [
        {"id": 1, "created_at": "2026-10-13T13:00:00+00:00", "source": "benzinga", "headline": "SECRET-HEADLINE"}
    ],
    "score": {"tone": "negative", "relevance": "company", "catalyst": "earnings", "one_line": "Missed."},
    "model": "m",
    "prompt_sha256": "a" * 64,
    "error_type": None,
    "page_sha256": "p",
    "applied": False,
}


async def test_deterministic_path_attaches_the_block(config, evaluator_factory):  # noqa: F811
    eval_res = await evaluator_factory().evaluate_candidate(
        create_equity_candidate(), use_llm=False, news_context=BLOCK
    )
    assert eval_res.news_context == BLOCK and eval_res.approved is True


async def test_default_is_none_and_the_field_survives_a_json_round_trip(config, evaluator_factory):  # noqa: F811
    eval_res = await evaluator_factory().evaluate_candidate(create_equity_candidate(), use_llm=False)
    assert eval_res.news_context is None
    with_block = eval_res.model_copy(update={"news_context": BLOCK})
    assert LLMTradeEvaluation.model_validate_json(with_block.model_dump_json()).news_context == BLOCK


def llm_reply(**extra):
    payload = {
        "approved": True,
        "rejection_reason": None,
        "stop_loss": 146.0,
        "take_profit": 158.0,
        "thesis_summary": "ok",
        "macro_clearance": True,
        **extra,
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


async def test_llm_path_attaches_block_pops_reply_key_and_keeps_news_out_of_the_prompt(
    config,
    evaluator_factory,
    monkeypatch,  # noqa: F811
):
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return llm_reply(news_context={"status": "ok", "score": "forged"})

    monkeypatch.setattr(evaluator_module.litellm, "acompletion", fake_completion)
    evaluator = evaluator_factory(config=config.model_copy(update={"openai_api_key": "k"}))
    eval_res = await evaluator.evaluate_candidate(create_equity_candidate(), use_llm=True, news_context=BLOCK)
    assert eval_res.news_context == BLOCK  # ours, never the reply's
    assert eval_res.llm_verdict is not None
    prompt_text = json.dumps(calls[0]["messages"])
    assert "SECRET-HEADLINE" not in prompt_text and "news" not in prompt_text.lower()


async def test_fallback_path_attaches_the_block(config, evaluator_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(evaluator_module.litellm, "acompletion", AsyncMock(side_effect=RuntimeError("down")))
    evaluator = evaluator_factory(config=config.model_copy(update={"openai_api_key": "k"}))
    eval_res = await evaluator.evaluate_candidate(create_equity_candidate(), use_llm=True, news_context=BLOCK)
    assert eval_res.news_context == BLOCK and eval_res.llm_verdict is None
```

Check `tests/agent/test_evaluator.py` for how its LLM-path tests stub `litellm.acompletion` (search `acompletion`) and mirror that exact mechanism if it differs from `monkeypatch.setattr(evaluator_module.litellm, "acompletion", …)`; the assertions above stay.

- [ ] **Step 2: Write the failing scan tests**

```python
"""News blocks in `run_scan`: one fetch per scan, rank-ordered scoring, provenance, journal, failure modes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.news_context import NewsContextService, NewsScorer
from agentic_trader.config import NewsConfig, ScanBudget
from agentic_trader.execution.durable import EventKind
from tests.agent.test_news_context import GOOD, FakeComplete, FakeSource, page_with
from tests.agent.test_scan_budget import budget_desk, evaluation  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)
from tests.data.test_news import row


async def _ranked(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


def install(desk, source, *, answers=(), score_first=6):
    desk.config.news = NewsConfig(max_scored_per_scan=score_first)
    scorer = NewsScorer(model="m", api_key=None, timeout_seconds=1, complete=FakeComplete(answers))
    desk.news_context = NewsContextService(source, scorer, desk.config.news, clock=lambda: datetime.now(UTC))
    return scorer


async def test_news_blocks_follow_rank_and_land_in_provenance_and_journal(shadow_desk, temp_db):  # noqa: F811
    now = datetime.now(UTC)
    page = page_with(row(1, "DDD news", ["DDD"]), row(2, "CCC news", ["CCC"]), row(3, "AAA news", ["AAA"]))
    page = page.__class__(page.items, now, now, page.symbols, page.window, 1, ("sha-p",), False)
    source = FakeSource(page)
    scorer = install(shadow_desk, source, answers=[GOOD, GOOD], score_first=2)
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: evaluation(cand, **{"news_context": kw.get("news_context")})
    )

    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

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
    for call in shadow_desk.evaluator.evaluate_candidate.call_args_list:
        contract = call.args[0].contract
        assert call.kwargs["news_context"] == by_contract[contract]["news"]


async def test_news_service_failure_is_unavailable_and_changes_nothing(shadow_desk, temp_db):  # noqa: F811
    install(shadow_desk, FakeSource(error=RuntimeError("boom")))
    shadow_desk.news_context.for_symbols = AsyncMock(side_effect=TypeError("not a dict"))
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(
        side_effect=lambda cand, **kw: evaluation(cand, **{"news_context": kw.get("news_context")})
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
    blocks = await shadow_desk._news_blocks(ranked)
    assert source.calls[0][0] == ("AAA",)
    assert blocks[0] is None and blocks[2] is None and blocks[1]["status"] == "no_news"


async def test_news_block_helper_only_accepts_dicts():
    from agentic_trader.agent.copilot import TradingCopilot
    from types import SimpleNamespace

    assert TradingCopilot._news_block(SimpleNamespace(news_context={"status": "ok"})) == {"status": "ok"}
    assert TradingCopilot._news_block(SimpleNamespace(news_context=None)) is None
    assert TradingCopilot._news_block(SimpleNamespace()) is None
```

Check `tests/agent/test_scan_budget.py::evaluation` — it builds an `LLMTradeEvaluation` from a candidate; confirm it accepts `**fields` overrides (the llm-verdict tests pass `llm_verdict=` through `with_verdict`, which spreads `vars(evaluation(cand))`). If `evaluation(cand, **fields)` does not take overrides, write a local helper in this test file exactly like `with_verdict` in `tests/agent/test_scan_llm_verdict.py` (a `SimpleNamespace` with `model_dump` and `model_dump_json`) — `record_signal` needs `eval_res.model_dump_json()`, so give the namespace `model_dump_json=lambda: "{}"` as well, and `news_context` set.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_evaluator_news_context.py tests/agent/test_scan_news_context.py -q`
Expected: FAIL (`unexpected keyword argument 'news_context'`, `AttributeError: _news_blocks`).

- [ ] **Step 4: Implement the evaluator changes**

`LLMTradeEvaluation`, after `corporate_actions`:

```python
    # The scan's news block (docs/news-context.md): labelled context on the card and in the journal, never read as a gate.
    news_context: dict[str, Any] | None = None
```

`evaluate_candidate` signature: add `news_context: Mapping[str, Any] | None = None,` after `corporate_actions`. Near where `corporate_block` is computed, add `news_block = dict(news_context) if news_context is not None else None`. Add `news_context=news_block,` to the deterministic return (after `corporate_actions=corporate_block`) and to the fallback return. In the LLM path, after `data.pop("corporate_actions", None)` add:

```python
            data.pop("news_context", None)  # attached below from the scan's block; never taken from the reply
```

and change the `model_copy(update={...})` to `update={"earnings_note": earnings_note_value, "corporate_actions": corporate_block, "news_context": news_block}`.

- [ ] **Step 5: Implement the copilot changes**

Imports: `from agentic_trader.agent.news_context import NewsContextService, build_news_context_service, unavailable_block` and `news_live` from `agentic_trader.config` (extend the existing import line).

`__init__`: add kwarg `news_context: NewsContextService | None = None,` after `corporate_actions`; after the `self.corporate_actions = (...)` block:

```python
        # News as measured context (docs/news-context.md): a per-scan fetch and scorer, None when off or without keys.
        self.news_context: NewsContextService | None = (
            news_context if news_context is not None else (build_news_context_service(config) if news_live(config) else None)
        )
```

Static helper beside `_llm_block`:

```python
    @staticmethod
    def _news_block(evaluation: Any) -> dict[str, Any] | None:
        """The evaluation's news block as recorded (already JSON-safe), or None when the scan computed none."""
        block = getattr(evaluation, "news_context", None)
        return dict(block) if isinstance(block, dict) else None
```

Method beside `_corporate_actions_lookup`:

```python
    async def _news_blocks(self, ranked: list[tuple[Any, Any, Any]]) -> list[dict[str, Any] | None]:
        """One news block per ranked equity candidate (drift and futures candidates get None); never raises.

        One fetch per scan; scoring follows rank order up to ``news.max_scored_per_scan``. Any failure
        of the step yields ``unavailable`` blocks carrying the exception type name only.
        """
        blocks: list[dict[str, Any] | None] = [None] * len(ranked)
        service = self.news_context
        if service is None or not ranked:
            return blocks
        wanted = [
            candidate.contract
            for candidate, _det, _risk in ranked
            if "/" not in candidate.contract and getattr(candidate, "catalog_event", None) is None
        ]
        if not wanted:
            return blocks
        try:
            found = await service.for_symbols(wanted, score_first=self.config.news.max_scored_per_scan)
            if not isinstance(found, dict):
                raise TypeError("news service returned a non-mapping")
        except Exception as exc:
            logger.exception("News context failed; cards are unaffected", extra={"event": "news_context_failed"})
            found = {symbol: unavailable_block(self.config.news, type(exc).__name__) for symbol in wanted}
        for index, (candidate, _det, _risk) in enumerate(ranked):
            if candidate.contract in wanted:
                blocks[index] = found.get(candidate.contract.strip().upper())
        return blocks
```

In `run_scan`, right after the `policy_by_rank` list comprehension:

```python
            # NEWS CONTEXT: one fetch per scan, before the send loop, for every ranked equity candidate.
            news_by_rank = await self._news_blocks(ranked)
```

In the send loop's `evaluate_candidate` call add `news_context=news_by_rank[rank - 1],` after `corporate_actions=corporate_lookup`. In the provenance dict add `"news": self._news_block(eval_res),` after `"corporate_actions": eval_res.corporate_actions`. In the `_journal_scan_ranking` call add `news_by_rank=news_by_rank[:native_count],`; in its signature add `news_by_rank: list[dict[str, Any] | None],` after `llm_by_rank`, and in the candidate dict add `"news": news_by_rank[rank - 1],` after `"llm"`.

Also update the other caller(s) of `_journal_scan_ranking` if any (`grep -n "_journal_scan_ranking(" agentic_trader tests`): pass `news_by_rank=[None] * len(ranked)` where no blocks exist.

- [ ] **Step 6: Run the tests and the neighbouring scan suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/agent/test_evaluator_news_context.py tests/agent/test_scan_news_context.py tests/agent/test_scan_llm_verdict.py tests/agent/test_scan_book_sizing.py tests/agent/test_scan_card_policy.py tests/agent/test_scan_shadow_ranker.py tests/agent/test_evaluator.py tests/agent/test_scan_budget.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agentic_trader/agent/evaluator.py agentic_trader/agent/copilot.py tests/agent/test_evaluator_news_context.py tests/agent/test_scan_news_context.py
git commit -m "News context: evaluation field, per-scan blocks in run_scan, provenance and journal"
```

---

### Task 5: The card line — `notifier/telegram_bot.py`

**Files:**
- Modify: `agentic_trader/notifier/telegram_bot.py` (add `_news_line` after `_corporate_action_line` ~line 172; append it to both `evidence_block` expressions ~306-310 and ~451-455)
- Test: `tests/notifier/test_news_line.py`

**Interfaces:**
- Consumes: `LLMTradeEvaluation.news_context` (Task 4), the block schema (Task 3).
- Produces: `def _news_line(block: dict[str, Any] | None, *, markup: bool, now: datetime | None = None) -> str` (newline-terminated or "").

- [ ] **Step 1: Write the failing tests**

```python
"""The news card line: one labelled line per status, escaped, clipped, nothing without a block."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.notifier.telegram_bot import _news_line, format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res as base_eval_res  # noqa: F401  (fixture)


NOW = datetime(2026, 10, 13, 14, 35, tzinfo=UTC)


def block(status="ok", **overrides):
    base = {
        "status": status,
        "as_of": NOW.isoformat(),
        "window_hours": 48,
        "n_items": 4,
        "n_market_wide": 1,
        "truncated": False,
        "headlines": [
            {
                "id": 1,
                "created_at": (NOW - timedelta(hours=2)).isoformat(),
                "source": "benzinga",
                "headline": "Apple misses Q3 <est> & guides lower",
            }
        ],
        "score": {
            "tone": "negative",
            "relevance": "company",
            "catalyst": "earnings",
            "one_line": "Revenue miss;\n guidance cut.",
        },
        "model": "m",
        "prompt_sha256": "a" * 64,
        "error_type": None,
        "page_sha256": "p",
        "applied": False,
    }
    return {**base, **overrides}


def test_ok_line_has_counts_tone_catalyst_one_liner_and_latest_headline():
    line = _news_line(block(), markup=False, now=NOW)
    assert line == (
        '📰 News (context, not a gate): 4 items/48h · tone negative · earnings · "Revenue miss; guidance cut." '
        '· latest "Apple misses Q3 <est> & guides lower" (benzinga, 2h ago)\n'
    )


def test_news_line_escapes_html_and_collapses_whitespace():
    line = _news_line(block(), markup=True, now=NOW)
    assert "&lt;est&gt; &amp; guides" in line and "<est>" not in line
    assert "Revenue miss; guidance cut." in line and "\n guidance" not in line
    assert line.startswith("📰 News (context, not a gate):") and line.endswith("\n")


def test_headline_is_clipped_and_age_in_days():
    long = block(
        headlines=[
            {
                "id": 1,
                "created_at": (NOW - timedelta(hours=30)).isoformat(),
                "source": "benzinga",
                "headline": "x" * 300,
            }
        ]
    )
    line = _news_line(long, markup=False, now=NOW)
    assert '"' + "x" * 109 + '…"' in line and "(benzinga, 1d ago)" in line


@pytest.mark.parametrize(
    ("status", "overrides", "expected"),
    [
        (
            "unscored",
            {"score": None, "model": None},
            '4 items/48h · not scored · latest "Apple misses Q3 <est> & guides lower" (benzinga, 2h ago)',
        ),
        ("no_news", {"n_items": 0, "headlines": [], "score": None}, "none in 48h"),
        ("score_failed", {"score": None, "error_type": "TimeoutError"}, "4 items/48h · scoring failed (TimeoutError)"),
        (
            "unavailable",
            {"n_items": 0, "headlines": [], "score": None, "error_type": "ConnectionError", "as_of": None},
            "unavailable (ConnectionError)",
        ),
        (
            "unavailable",
            {"n_items": 0, "headlines": [], "score": None, "error_type": None, "as_of": None},
            "unavailable",
        ),
    ],
)
def test_other_statuses(status, overrides, expected):
    assert (
        _news_line(block(status, **overrides), markup=False, now=NOW) == f"📰 News (context, not a gate): {expected}\n"
    )


def test_truncated_flag_and_missing_or_unknown_block():
    assert _news_line(block(truncated=True), markup=False, now=NOW).endswith(" · feed truncated\n")
    assert _news_line(None, markup=False) == "" and _news_line({}, markup=False) == ""
    assert _news_line(block("weird"), markup=False, now=NOW) == ""


def test_cards_render_the_line_from_the_evaluation(base_eval_res):  # noqa: F811
    with_block = base_eval_res.model_copy(update={"news_context": block()})
    assert "News (context, not a gate)" in format_alert_card(with_block, "TREND_PULLBACK")
    assert "News (context, not a gate)" in format_terminal_card(with_block, "TREND_PULLBACK")
    assert "News (context" not in format_alert_card(base_eval_res, "TREND_PULLBACK")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/notifier/test_news_line.py -q`
Expected: FAIL with `ImportError: cannot import name '_news_line'`.

- [ ] **Step 3: Implement the line**

After `_corporate_action_line` (imports: `from datetime import UTC, datetime` is likely present — check; `MAX_HEADLINE_CHARS = 110` module constant mirrors `NewsConfig.max_headline_chars`'s default, since the renderer has no config):

```python
NEWS_LABEL = "📰 News (context, not a gate): "
NEWS_HEADLINE_CHARS = 110  # the renderer has no config; mirrors NewsConfig.max_headline_chars' default


def _age(created_at: str, now: datetime) -> str:
    try:
        seconds = (now - datetime.fromisoformat(created_at)).total_seconds()
    except TypeError, ValueError:
        return "recent"
    hours = max(int(seconds // 3600), 0)
    return f"{hours // 24}d ago" if hours >= 24 else f"{hours}h ago"


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _news_line(block: dict[str, Any] | None, *, markup: bool, now: datetime | None = None) -> str:
    """The news context line, newline-terminated; empty without a block or for an unknown status.

    One line per card, always labelled as context. Text from the provider or the LLM is
    whitespace-collapsed, clipped and HTML-escaped for the Telegram renderer.
    """
    if not block or not isinstance(block, dict):
        return ""
    status = block.get("status")
    window = block.get("window_hours")
    n_items = block.get("n_items")
    error_type = block.get("error_type")
    headlines = block.get("headlines") or []
    latest = headlines[0] if headlines and isinstance(headlines[0], dict) else None
    latest_text = ""
    if latest is not None:
        age = _age(str(latest.get("created_at")), now or datetime.now(UTC))
        latest_text = f' · latest "{_clip(str(latest.get("headline", "")), NEWS_HEADLINE_CHARS)}" ({latest.get("source", "?")}, {age})'
    if status == "ok" and isinstance(block.get("score"), dict):
        score = block["score"]
        text = (
            f"{n_items} items/{window}h · tone {score.get('tone')} · {score.get('catalyst')} "
            f'· "{_clip(str(score.get("one_line", "")), 160)}"{latest_text}'
        )
    elif status == "unscored":
        text = f"{n_items} items/{window}h · not scored{latest_text}"
    elif status == "no_news":
        text = f"none in {window}h"
    elif status == "score_failed":
        text = f"{n_items} items/{window}h · scoring failed ({error_type or 'unknown'})"
    elif status == "unavailable":
        text = f"unavailable ({error_type})" if error_type else "unavailable"
    else:
        return ""
    if block.get("truncated"):
        text += " · feed truncated"
    return NEWS_LABEL + (html.escape(text, quote=False) if markup else text) + "\n"
```

Append `+ _news_line(eval_res.news_context, markup=True)` to the alert `evidence_block` and `+ _news_line(eval_res.news_context, markup=False)` to the terminal one. `html.escape(..., quote=False)` keeps the quotation marks readable; `<`, `>`, `&` are escaped (the test asserts those).

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/notifier -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/notifier/telegram_bot.py tests/notifier/test_news_line.py
git commit -m "News context: one labelled card line per status on alert and terminal cards"
```

---

### Task 6: `cards outcomes` attribution — `research/setups/outcomes.py`

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (`_COLUMNS` ~line 55; `_row` ~232-285; `summarize` ~522-660)
- Test: `tests/research/setups/test_outcomes.py` (append)

**Interfaces:**
- Consumes: the journal candidate's `news` block (Task 4).
- Produces: columns `news_status`, `news_n_items`, `news_tone`, `news_catalyst`, `news_relevance`, `news_alignment`; `def news_alignment(direction: str | None, tone: str | None) -> str` (`aligned`/`opposed`/`neutral`/`none`); `summarize()["news_context"]` block: `{"blocks": int, "by_status": {status: n}, "scored": int, "by_alignment": {alignment: {"n", "mean_r_cost", "hit_rate"}}, "by_tone": {tone: {"n", "mean_r_cost", "hit_rate"}}, "by_catalyst": {catalyst: n}}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/research/setups/test_outcomes.py`; reuse its `_candidate`, `_event`, `DECIDED_AT`, `NOW`, `label_journaled`/`summarize` imports and whatever bar fixture the existing `test_summary_selection_by_scorer` uses to build a mature frame)

```python
def _news(status="ok", tone="positive", catalyst="earnings", n_items=3):
    return {
        "status": status,
        "as_of": DECIDED_AT.isoformat(),
        "window_hours": 48,
        "n_items": n_items,
        "n_market_wide": 0,
        "truncated": False,
        "headlines": [],
        "score": {"tone": tone, "relevance": "company", "catalyst": catalyst, "one_line": "x"}
        if status == "ok"
        else None,
        "model": "m" if status == "ok" else None,
        "prompt_sha256": "a" * 64 if status == "ok" else None,
        "error_type": None,
        "page_sha256": "p",
        "applied": False,
    }


@pytest.mark.parametrize(
    ("direction", "tone", "expected"),
    [
        ("LONG", "positive", "aligned"),
        ("LONG", "negative", "opposed"),
        ("SHORT", "negative", "aligned"),
        ("SHORT", "positive", "opposed"),
        ("LONG", "neutral", "neutral"),
        ("LONG", None, "none"),
        (None, "positive", "none"),
    ],
)
def test_news_alignment(direction, tone, expected):
    assert news_alignment(direction, tone) == expected


def test_rows_flatten_the_news_block_and_absent_blocks():
    candidates = [
        _candidate(contract="AAPL", rank=1) | {"news": _news()},
        _candidate(contract="MSFT", rank=2, direction="SHORT", outcome="runner_up")
        | {"news": _news(tone="negative", catalyst="guidance")},
        _candidate(contract="NVDA", rank=3, outcome="runner_up") | {"news": _news(status="unscored")},
        _candidate(contract="TSLA", rank=4, outcome="runner_up"),
    ]
    frame = _frame_for(candidates)  # see note below
    by = frame.set_index("contract")
    assert by.loc["AAPL", "news_status"] == "ok" and by.loc["AAPL", "news_tone"] == "positive"
    assert by.loc["AAPL", "news_alignment"] == "aligned" and by.loc["AAPL", "news_catalyst"] == "earnings"
    assert by.loc["MSFT", "news_alignment"] == "aligned" and by.loc["MSFT", "news_n_items"] == 3
    assert by.loc["NVDA", "news_status"] == "unscored" and by.loc["NVDA", "news_alignment"] == "none"
    assert by.loc["TSLA", "news_status"] == "absent" and by.loc["TSLA", "news_alignment"] == "none"
    assert pd.isna(by.loc["TSLA", "news_n_items"])
    summary = summarize(frame)["news_context"]
    assert summary["blocks"] == 3 and summary["by_status"] == {"ok": 2, "unscored": 1}
    assert summary["scored"] == 2 and summary["by_catalyst"] == {"earnings": 1, "guidance": 1}
    assert set(summary["by_alignment"]) == {"aligned", "opposed", "neutral", "none"}
    aligned = summary["by_alignment"]["aligned"]
    assert aligned["n"] == 2 and isinstance(aligned["mean_r_cost"], float) and 0 <= aligned["hit_rate"] <= 1
    assert summary["by_alignment"]["none"]["n"] == 2
    assert summary["by_tone"]["positive"]["n"] == 1 and summary["by_tone"]["negative"]["n"] == 1


def test_summary_empty_frame_carries_the_news_block():
    summary = summarize(pd.DataFrame(columns=_COLUMNS))
    assert summary["news_context"] == {
        "blocks": 0,
        "by_status": {},
        "scored": 0,
        "by_alignment": {
            k: {"n": 0, "mean_r_cost": None, "hit_rate": None} for k in ("aligned", "opposed", "neutral", "none")
        },
        "by_tone": {},
        "by_catalyst": {},
    }
```

Note on `_frame_for`: build the mature frame exactly the way `test_summary_selection_by_scorer` does (it calls `label_journaled([... _event(...)], bars=..., now=NOW, ...)` with a bar fixture that resolves hits). Read that test and write `_frame_for(candidates)` as a small local helper that calls the same function with the same bar fixture so every row is mature. If the existing helper needs a per-symbol bars mapping, give the four contracts the same bars. Import `news_alignment` and `_COLUMNS` from `agentic_trader.research.setups.outcomes` at the top of the test module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py -q -k news`
Expected: FAIL with `ImportError: cannot import name 'news_alignment'`.

- [ ] **Step 3: Implement**

`_COLUMNS`: insert after `"book_would_scale"`: `"news_status", "news_n_items", "news_tone", "news_catalyst", "news_relevance", "news_alignment",`.

Module-level:

```python
NEWS_ALIGNMENTS = ("aligned", "opposed", "neutral", "none")


def news_alignment(direction: str | None, tone: str | None) -> str:
    """Deterministic direction × tone bucket; the scorer never sees the direction."""
    if tone not in {"positive", "negative", "neutral"} or direction not in {"LONG", "SHORT"}:
        return "none"
    if tone == "neutral":
        return "neutral"
    bullish = tone == "positive"
    return "aligned" if bullish == (direction == "LONG") else "opposed"
```

In `_row`, after `llm = ...`:

```python
    raw_news = entry.get("news")
    news: dict[str, Any] = raw_news if isinstance(raw_news, dict) else {}
    raw_score = news.get("score")
    score: dict[str, Any] = raw_score if isinstance(raw_score, dict) else {}
```

and in the returned dict after `"book_would_scale"`:

```python
        "news_status": news.get("status") if news else "absent",
        "news_n_items": news.get("n_items") if news else None,
        "news_tone": score.get("tone"),
        "news_catalyst": score.get("catalyst"),
        "news_relevance": score.get("relevance"),
        "news_alignment": news_alignment(entry.get("direction"), score.get("tone")),
```

Helper and summary:

```python
def _news_bucket(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"n": 0, "mean_r_cost": None, "hit_rate": None}
    hits = rows["hit"] == BracketHit.TARGET.value
    return {"n": int(len(rows)), "mean_r_cost": _mean_r_cost(rows), "hit_rate": float(hits.mean())}


def _news_summary(frame: pd.DataFrame, mature: pd.DataFrame) -> dict[str, Any]:
    present = frame.loc[frame["news_status"].notna() & (frame["news_status"] != "absent")]
    by_status = {str(k): int(v) for k, v in present["news_status"].value_counts().items()} if len(present) else {}
    catalysts = present["news_catalyst"].dropna() if len(present) else pd.Series(dtype=object)
    by_catalyst = {str(k): int(v) for k, v in catalysts.value_counts().items()} if len(catalysts) else {}
    tones = mature["news_tone"].dropna() if "news_tone" in mature else pd.Series(dtype=object)
    return {
        "blocks": int(len(present)),
        "by_status": by_status,
        "scored": int((present["news_status"] == "ok").sum()) if len(present) else 0,
        "by_alignment": {
            key: _news_bucket(mature.loc[mature["news_alignment"] == key]) if len(mature) else _news_bucket(mature)
            for key in NEWS_ALIGNMENTS
        },
        "by_tone": {
            str(tone): _news_bucket(mature.loc[mature["news_tone"] == tone]) for tone in sorted(tones.unique())
        },
        "by_catalyst": by_catalyst,
    }
```

Check the `BracketHit` enum's target member name (`grep -n "class BracketHit" -A8 agentic_trader/research/setups/labels.py` or wherever it lives) and how `_base_rate` computes a hit rate; reuse that definition of a hit so `hit_rate` means the same thing as the base rates. In `summarize`: add `"news_context": _news_summary(frame, mature),` to the populated dict after `"book_sizing"` and `"news_context": _news_summary(frame, frame),` to the empty-frame dict (the empty frame has every column, so the helper returns the zero block).

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py tests/cli -q -k "outcomes or news"`
Expected: PASS (the `cards outcomes` CLI test, if any, still passes).

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py
git commit -m "News context: news_* columns, direction alignment and a descriptive news_context summary in cards outcomes"
```

---

### Task 7: Research acquisition and `alpha news`

**Files:**
- Create: `agentic_trader/research/news.py`, `agentic_trader/cli/commands/alpha_news.py`
- Modify: `agentic_trader/cli/main.py` (import + `alpha_group.add_command(news_cmd)` beside `corporate_actions_cmd`)
- Test: `tests/research/test_news_cached.py`, `tests/cli/test_alpha_news.py`

**Interfaces:**
- Consumes (Task 2): `NewsItem`, `NewsPage`, `NewsSource`, `AlpacaNewsSource`, `BoundedNewsClient`, `SDK_PAGE_CAP`; (Task 1): `NewsConfig.chunk_size`, `max_requests_per_minute`; `research/corporate_actions.py`: `cohort_symbols`, `_write_private` idiom (copy it; do not import private names).
- Produces: `def build_news_source(config: AppConfig) -> AlpacaNewsSource` (unbounded `max_items=None`, paced); `def month_windows(start: date, end: date) -> list[tuple[date, date]]`; `async def fetch_news_cached(source, symbols, start: date, end: date, cache_dir: Path, *, pace, chunk_symbols: int = 20) -> tuple[tuple[NewsItem, ...], dict[str, Any]]`; click command `news_cmd` (`alpha news`).

- [ ] **Step 1: Write the failing tests**

`tests/research/test_news_cached.py`:

```python
"""Cached news acquisition: month windows, splitting on truncation, de-duplication, digest-checked reuse."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime

import pytest

from agentic_trader.data.news import NewsPage, normalize_news
from agentic_trader.research.news import fetch_news_cached, month_windows
from tests.data.test_news import row


async def _noop():
    return None


class FakeSource:
    """Replays items per (symbols, start date, end date); a window listed in `truncate` reports truncation."""

    def __init__(self, items_by_symbol, *, truncate=()):
        self.items_by_symbol, self.truncate, self.calls = items_by_symbol, set(truncate), []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start.date(), end.date()))
        rows = [
            r
            for s in symbols
            for r in self.items_by_symbol.get(s, [])
            if start <= datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) <= end
        ]
        truncated = (start.date(), end.date()) in self.truncate
        return NewsPage(normalize_news(rows), start, end, tuple(symbols), (start, end), 1, ("p",), truncated)


def at(day, item_id, symbol):
    return row(item_id, f"h{item_id}", [symbol], created_at=f"2024-03-{day:02d}T12:00:00Z")


def test_month_windows_are_inclusive_and_clipped():
    assert month_windows(date(2024, 1, 15), date(2024, 3, 10)) == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]
    assert month_windows(date(2024, 3, 5), date(2024, 3, 5)) == [(date(2024, 3, 5), date(2024, 3, 5))]


async def test_fetch_requests_chunk_by_month_dedupes_and_caches(tmp_path):
    shared = row(9, "both", ["AAPL", "MSFT"], created_at="2024-03-02T12:00:00Z")
    source = FakeSource(
        {"AAPL": [at(1, 1, "AAPL"), shared], "MSFT": [shared, at(3, 2, "MSFT")], "NVDA": [at(4, 3, "NVDA")]}
    )
    items, manifest = await fetch_news_cached(
        source, ["nvda", "AAPL", "MSFT"], date(2024, 3, 1), date(2024, 3, 31), tmp_path, pace=_noop, chunk_symbols=2
    )
    assert sorted(i.id for i in items) == [1, 2, 3, 9]
    assert source.calls == [
        (("AAPL", "MSFT"), date(2024, 3, 1), date(2024, 3, 31)),
        (("NVDA",), date(2024, 3, 1), date(2024, 3, 31)),
    ]
    assert manifest["count"] == 4 and manifest["symbols"] == ["AAPL", "MSFT", "NVDA"] and manifest["cached"] is False
    assert manifest["requests"] == 2 and manifest["splits"] == 0
    assert manifest["counts_by_symbol"] == {"AAPL": 2, "MSFT": 2, "NVDA": 1} and manifest["counts_by_month"] == {
        "2024-03": 4
    }
    path = tmp_path / "news" / f"{manifest['request_sha256']}.json.gz"
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    again, manifest2 = await fetch_news_cached(
        source, ["AAPL", "MSFT", "NVDA"], date(2024, 3, 1), date(2024, 3, 31), tmp_path, pace=_noop, chunk_symbols=2
    )
    assert again == items and manifest2["cached"] is True and len(source.calls) == 2


async def test_truncated_window_is_split_down_to_a_day(tmp_path):
    source = FakeSource(
        {"AAPL": [at(d, d, "AAPL") for d in range(1, 11)]},
        truncate={(date(2024, 3, 1), date(2024, 3, 10)), (date(2024, 3, 1), date(2024, 3, 5))},
    )
    items, manifest = await fetch_news_cached(
        source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 10), tmp_path, pace=_noop
    )
    assert len(items) == 10 and manifest["splits"] == 2 and manifest["requests"] == 5
    assert source.calls[0][1:] == (date(2024, 3, 1), date(2024, 3, 10))
    assert (date(2024, 3, 1), date(2024, 3, 3)) in [c[1:] for c in source.calls]


async def test_one_day_window_that_still_truncates_fails(tmp_path):
    source = FakeSource({"AAPL": [at(1, 1, "AAPL")]}, truncate={(date(2024, 3, 1), date(2024, 3, 1))})
    with pytest.raises(ValueError, match="page cap"):
        await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)


async def test_corrupt_cache_is_refused(tmp_path):
    source = FakeSource({"AAPL": [at(1, 1, "AAPL")]})
    _, manifest = await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)
    path = tmp_path / "news" / f"{manifest['request_sha256']}.json.gz"
    document = json.loads(gzip.decompress(path.read_bytes()))
    document["items"][0]["headline"] = "tampered"
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    with pytest.raises(ValueError, match="SHA-256"):
        await fetch_news_cached(source, ["AAPL"], date(2024, 3, 1), date(2024, 3, 1), tmp_path, pace=_noop)


async def test_rejects_empty_symbols_and_inverted_windows(tmp_path):
    with pytest.raises(ValueError):
        await fetch_news_cached(FakeSource({}), [" "], date(2024, 3, 1), date(2024, 3, 2), tmp_path, pace=_noop)
    with pytest.raises(ValueError):
        await fetch_news_cached(FakeSource({}), ["AAPL"], date(2024, 3, 2), date(2024, 3, 1), tmp_path, pace=_noop)
```

`tests/cli/test_alpha_news.py`:

```python
"""`alpha news`: symbols from --symbol and --cohort, private gzip output, no database."""

import gzip
import json
from datetime import UTC, datetime

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_news as cmd
from agentic_trader.data.news import NewsPage, normalize_news
from tests.data.test_news import row


class FakeSource:
    def __init__(self):
        self.requests = []

    def fetch(self, symbols, start, end):
        self.requests.append((tuple(symbols), start, end))
        rows = [
            row(1, "a", ["AAPL"], created_at="2024-03-02T12:00:00Z"),
            row(2, "b", ["XOM"], created_at="2024-03-03T12:00:00Z"),
        ]
        return NewsPage(normalize_news(rows), start, end, tuple(symbols), (start, end), 1, ("p",), False)


def test_command_writes_items_and_manifest(tmp_path, monkeypatch):
    source = FakeSource()
    monkeypatch.setattr(cmd, "build_news_source", lambda config: source)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"sectors": {"energy": ["XOM"], "technology": ["NVDA", "AAPL"]}}))
    output = tmp_path / "out"
    result = CliRunner().invoke(
        cmd.news_cmd,
        [
            "--symbol",
            "pxd",
            "--cohort",
            str(cohort),
            "--start",
            "2024-03-01",
            "--end",
            "2024-03-31",
            "--cache",
            str(tmp_path / "cache"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert source.requests[0][0] == ("AAPL", "NVDA", "PXD", "XOM")
    items = json.loads(gzip.decompress((output / "news.json.gz").read_bytes()))
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(items) == 2 and manifest["count"] == 2 and manifest["symbols"] == ["AAPL", "NVDA", "PXD", "XOM"]
    assert manifest["request_sha256"] and manifest["content_sha256"] and "environment" in manifest
    assert "2 news items" in result.output
    assert oct(output.stat().st_mode & 0o777) == "0o700"
    assert oct((tmp_path / "cache" / "news").stat().st_mode & 0o777) == "0o700"


def test_command_refuses_an_existing_output_directory_and_needs_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(cmd, "build_news_source", lambda config: FakeSource())
    output = tmp_path / "out"
    output.mkdir()
    args = [
        "--symbol",
        "AAPL",
        "--start",
        "2024-03-01",
        "--end",
        "2024-03-31",
        "--cache",
        str(tmp_path / "cache"),
        "--output",
        str(output),
    ]
    result = CliRunner().invoke(cmd.news_cmd, args)
    assert result.exit_code != 0 and "exists" in result.output
    result = CliRunner().invoke(
        cmd.news_cmd,
        [
            "--start",
            "2024-03-01",
            "--end",
            "2024-03-31",
            "--cache",
            str(tmp_path / "c"),
            "--output",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code != 0 and "--symbol" in result.output


def test_command_is_registered():
    from agentic_trader.cli.main import alpha_group

    assert "news" in alpha_group.commands
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_news_cached.py tests/cli/test_alpha_news.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the research module**

```python
"""Cached news acquisition for research (docs/news-context.md#research-acquisition).

One gzip JSON file per request under ``cache_dir/news/<sha256 of the request>.json.gz`` holding the
normalised items (headline, summary, source, url, created_at, symbols — never bodies), the receipt and
the content digest; a reuse verifies the digest and refuses a corrupt file. Requests go per symbol
chunk per calendar month with full pagination; a truncated window is split in half down to one day.
Research only: no database, registry, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from agentic_trader.config import AppConfig
from agentic_trader.data.news import AlpacaNewsSource, NewsItem, NewsSource
from agentic_trader.data.pacing import RequestPacer
from agentic_trader.storage.workflow import encode
from agentic_trader.transport.alpaca import BoundedNewsClient


__all__ = ["build_news_source", "fetch_news_cached", "month_windows"]

FORMAT = 1


def build_news_source(config: AppConfig) -> AlpacaNewsSource:
    """The research source: bounded raw client, full pagination (``max_items=None``), the configured pacer."""
    if not (config.alpaca_api_key and config.alpaca_api_secret):
        raise ValueError("Alpaca data credentials are required")
    client = BoundedNewsClient(
        config.alpaca_api_key,
        config.alpaca_api_secret,
        request_timeout=config.market_data.timeout_seconds,
        raw_data=True,
    )
    return AlpacaNewsSource(
        client,
        pacer=RequestPacer(config.news.max_requests_per_minute),
        chunk_size=config.news.chunk_size,
        max_items=None,
    )


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive calendar-month windows covering ``[start, end]``, the first and last clipped."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        windows.append((cursor, min(next_month - timedelta(days=1), end)))
        cursor = next_month
    return windows


def _sha256(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    temporary.replace(path)


def _load_cached(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise ValueError(f"News cache {path.name} is unreadable: {type(exc).__name__}") from None
    missing = [key for key in ("items", "content_sha256", "requested_at", "received_at") if key not in document]
    if missing:
        raise ValueError(f"News cache {path.name} is missing {', '.join(missing)}")
    if _sha256(document["items"]) != document["content_sha256"]:
        raise ValueError(f"News cache {path.name} does not match its recorded SHA-256; refusing a corrupt cache")
    return document


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min, UTC), datetime.combine(end, time.max, UTC)


async def _fetch_window(
    source: NewsSource,
    chunk: list[str],
    start: date,
    end: date,
    pace: Callable[[], Awaitable[None]],
    stats: Counter[str],
) -> list[NewsItem]:
    await pace()
    stats["requests"] += 1
    page = await asyncio.to_thread(source.fetch, chunk, *_bounds(start, end))
    if not page.truncated:
        return list(page.items)
    if start == end:
        raise ValueError(
            f"News window {start} for {len(chunk)} symbols exceeds the provider page cap; use fewer symbols"
        )
    stats["splits"] += 1
    middle = start + (end - start) // 2
    first = await _fetch_window(source, chunk, start, middle, pace, stats)
    second = await _fetch_window(source, chunk, middle + timedelta(days=1), end, pace, stats)
    return first + second


async def fetch_news_cached(
    source: NewsSource,
    symbols: Sequence[str],
    start: date,
    end: date,
    cache_dir: Path,
    *,
    pace: Callable[[], Awaitable[None]],
    chunk_symbols: int = 20,
) -> tuple[tuple[NewsItem, ...], dict[str, Any]]:
    ordered = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not ordered:
        raise ValueError("At least one symbol is required")
    if end < start:
        raise ValueError("The window end precedes its start")
    if chunk_symbols < 1:
        raise ValueError("chunk_symbols must be positive")
    request = {
        "source": "alpaca-news",
        "format": FORMAT,
        "symbols": ordered,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    request_sha = _sha256(request)
    path = Path(cache_dir) / "news" / f"{request_sha}.json.gz"
    stats: Counter[str] = Counter()
    if path.exists():
        document = await asyncio.to_thread(_load_cached, path)
        cached = True
    else:
        requested_at = datetime.now(UTC)
        collected: dict[int, NewsItem] = {}
        for index in range(0, len(ordered), chunk_symbols):
            chunk = ordered[index : index + chunk_symbols]
            for window_start, window_end in month_windows(start, end):
                for item in await _fetch_window(source, chunk, window_start, window_end, pace, stats):
                    collected.setdefault(item.id, item)
        items_sorted = sorted(collected.values(), key=lambda item: (item.created_at, item.id))
        rows = [item.model_dump(mode="json") for item in items_sorted]
        document = {
            "request": request,
            "request_sha256": request_sha,
            "items": rows,
            "content_sha256": _sha256(rows),
            "requested_at": requested_at.isoformat(),
            "received_at": datetime.now(UTC).isoformat(),
            "requests": stats["requests"],
            "splits": stats["splits"],
        }
        payload = gzip.compress(json.dumps(document, sort_keys=True).encode())
        await asyncio.to_thread(_write_private, path, payload)
        cached = False
    items = tuple(NewsItem.model_validate(item_row) for item_row in document["items"])
    by_symbol: Counter[str] = Counter()
    by_month: Counter[str] = Counter()
    for item in items:
        by_month[item.created_at.strftime("%Y-%m")] += 1
        for symbol in item.symbols:
            if symbol in ordered:
                by_symbol[symbol] += 1
    manifest = {
        "request_sha256": request_sha,
        "content_sha256": document["content_sha256"],
        "symbols": ordered,
        "window": [start.isoformat(), end.isoformat()],
        "count": len(items),
        "counts_by_symbol": dict(sorted(by_symbol.items())),
        "counts_by_month": dict(sorted(by_month.items())),
        "requests": int(document.get("requests", 0)),
        "splits": int(document.get("splits", 0)),
        "cache_file": str(path),
        "cached": cached,
        "requested_at": document["requested_at"],
        "received_at": document["received_at"],
    }
    return items, manifest
```

`agentic_trader/cli/commands/alpha_news.py`:

```python
"""Research-only news acquisition (headlines, no bodies) for a symbol list or a frozen cohort file."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import click

from agentic_trader.cli.commands.alpha import research_environment
from agentic_trader.cli.utils import coro
from agentic_trader.config import load_config
from agentic_trader.research.corporate_actions import cohort_symbols
from agentic_trader.research.news import build_news_source, fetch_news_cached


async def _noop_pace() -> None:
    return None


@click.command("news")
@click.option("--symbol", "symbols", multiple=True, help="A symbol; repeatable.")
@click.option(
    "--cohort", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A frozen cohort JSON file."
)
@click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
@click.option("--cache", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--chunk-symbols", default=20, show_default=True, type=click.IntRange(1, 200))
@coro
async def news_cmd(symbols, cohort: Path | None, start, end, cache: Path, output: Path, chunk_symbols: int):
    """Fetch and cache news headlines (created_at window) for research; no database, no trading."""
    if output.exists():
        raise click.ClickException(f"Output directory exists: {output}")
    try:
        wanted = list(symbols) + (cohort_symbols(cohort) if cohort else [])
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not wanted:
        raise click.ClickException("Pass --symbol and/or --cohort")
    start_date, end_date = start.date(), end.date()
    if end_date < start_date:
        raise click.ClickException("--end must not precede --start")
    try:
        source = build_news_source(load_config())
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        items, manifest = await fetch_news_cached(
            source, wanted, start_date, end_date, cache, pace=_noop_pace, chunk_symbols=chunk_symbols
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    manifest["environment"] = await asyncio.to_thread(research_environment)
    output.mkdir(mode=0o700, parents=True)
    rows = [item.model_dump(mode="json") for item in items]
    (output / "news.json.gz").write_bytes(gzip.compress(json.dumps(rows, sort_keys=True).encode()))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    click.echo(f"{len(items)} news items for {len(manifest['symbols'])} symbols; {output / 'manifest.json'}")
```

`cli/main.py`: `from agentic_trader.cli.commands.alpha_news import news_cmd` and `alpha_group.add_command(news_cmd)` after `corporate_actions_cmd`.

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_news_cached.py tests/cli/test_alpha_news.py tests/cli/test_alpha_corporate_actions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/news.py agentic_trader/cli/commands/alpha_news.py agentic_trader/cli/main.py tests/research/test_news_cached.py tests/cli/test_alpha_news.py
git commit -m "News context: cached research acquisition (month windows, split on truncation) and alpha news"
```

---

### Task 8: Documentation

**Files:**
- Create: `docs/news-context.md`
- Modify: `docs/card-evidence.md` (one paragraph pointing at news context under the evidence/attribution section), `docs/production.md` (one operations paragraph), `docs/alpha-roadmap.md` (item 4 `*Status (October 13)*`), `CLAUDE.md` (one paragraph after the corporate-actions paragraph; also the `corporate_actions` loader fix sentence), `docs/corporate-actions.md` (one sentence: the YAML block is now read by `load_config`), `docs/superpowers/specs/2026-10-13-news-context-design.md` §3.3 (reprice sentence: the block rides in the card's stored `raw_response`, so `_replacement_card` carries it without a code change; a test in Task 4 covers the JSON round trip)

**Interfaces:** none (prose).

- [ ] **Step 1: Write `docs/news-context.md`** with these sections, each true to the code as built: *What it is* (one fetch per universe scan, Benzinga via Alpaca data keys, headlines only; scoring for the first `max_scored_per_scan` ranked symbols; block on the card, in `decision_provenance["news"]` and the journal candidate's `news`; `applied: false` always); *Block schema* (the spec §3.2 table); *Card line* (the §3.4 table); *Attribution* (`cards outcomes` `news_*` columns, `news_alignment` rule, `news_context` summary; descriptive, never a gate; `card_policy` and `card_stats` do not read it); *Configuration* (§3.6 table; `news.enabled: false` removes the step; `model` override); *Failure behaviour* (§4 table); *Operations* (`copilot db events --stream scan/<date> --limit 1`, `copilot cards outcomes --days 30`, cost: ≤ 6 LLM calls and one news request per scan; measured 2026-10-13: 1,003 items/7 days for 159 names in 3.3 s); *Research acquisition* (`alpha news --cohort config/research/spread/cohort-v1.json --start 2016-01-01 --end 2026-10-01 --cache DIR --output DIR`; month windows, split on truncation, gzip cache with SHA-256; no study exists yet, a frozen entry needs its own spec); *Design decisions* (§7 summarised; the gate prompt is untouched to keep attribution clean; no worker).

- [ ] **Step 2: Edit the other docs**

`docs/alpha-roadmap.md` item 4, append:

```markdown
   *Status (October 13):* wired as measured context ([news context](news-context.md), [spec](superpowers/specs/2026-10-13-news-context-design.md)): one bounded Benzinga fetch per universe scan through the desk's Alpaca data keys, a direction-free LLM tone score for the first six ranked symbols, one labelled card line, `decision_provenance["news"]`, the journal candidate's `news`, and `cards outcomes` `news_context` (status, tone, direction alignment). Never a gate; the LLM gate's prompt is unchanged so both attributions stay separate. `alpha news` is the cached research acquisition (history to 2015) for a later frozen entry. The loader now passes the `corporate_actions:` and `news:` YAML blocks (`entry_block: enforce` was previously ignored).
```

`CLAUDE.md`, after the corporate-actions paragraph:

```markdown
News as measured context ([news context](docs/news-context.md), desk item 4) fetches Benzinga headlines through the desk's Alpaca data keys once per universe scan for the ranked equity candidates (48 h window, market-wide lists excluded), scores the first `news.max_scored_per_scan` ranked symbols with a direction-free LLM call (tone, relevance, catalyst, one line), and carries one block per candidate on `LLMTradeEvaluation.news_context`, the card line "News (context, not a gate)", `decision_provenance["news"]` and the `scan_candidates_ranked` candidate's `news`. `cards outcomes` attributes it (`news_*` columns, deterministic `news_alignment`, the `news_context` summary) the way the LLM gate is attributed; nothing in `risk/`, `card_policy`, `card_stats` or admission reads it, and the gate's prompt never sees news. Fetch or scoring failures yield `unavailable`/`score_failed` blocks with type names only and never touch readiness or the scan. `alpha news` is the research-only cached acquisition. `load_config` passes the `corporate_actions:` and `news:` YAML blocks.
```

`docs/production.md`: one paragraph under the scan/operations section naming the card line, the two inspection commands and the cost bound. `docs/card-evidence.md`: one paragraph after the LLM-gate attribution text: news context is a fourth descriptive block (`news_context`) in `cards outcomes`; it never feeds `card_policy`. `docs/corporate-actions.md`: in the configuration section, one sentence that `load_config` now reads the `corporate_actions:` block (fixed 2026-10-13; before that the YAML block was ignored and the defaults ran).

- [ ] **Step 3: Run pre-commit and the full suite**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --all-files && env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider 2>&1 | tail -3`
Expected: hooks pass (re-add any Markdown the formatter changed); suite passes with the pre-existing 173 skips.

- [ ] **Step 4: Commit**

```bash
git add docs CLAUDE.md
git commit -m "News context: docs (news-context.md, card evidence, production, roadmap item 4 status, CLAUDE.md)"
```

---

## Self-review

- **Spec coverage:** §3.1 → Task 2; §3.2 → Task 3; §3.3 → Task 4 (reprice: field rides in `raw_response`, Task 4 round-trip test + Task 8 spec sentence); §3.4 → Task 5; §3.5 → Task 6; §3.6 (+ loader fix, YAML blocks, `news_live`) → Task 1; §3.7 → Task 7; §4 failure rows → Tasks 3, 4, 5 tests; §5 → each task's tests plus the source-text guard — **add** to Task 4's scan test file: `test_no_gate_or_policy_module_reads_news` asserting `"news" not in Path(module).read_text().lower()` for `execution/card_policy.py`, `research/setups/card_stats.py` and every file under `agentic_trader/risk/`; §6 → Task 8.
- **Placeholders:** none; the `_frame_for` helper in Task 6 is defined by instruction against an existing test (the implementer reads it) — acceptable because the plan cannot know the fixture's exact bars.
- **Type consistency:** `for_symbols(symbols, *, score_first)` (Tasks 3, 4); `unavailable_block(config, error_type)` (3, 4); `_news_line(block, *, markup, now=None)` (5); `news_alignment(direction, tone)` (6); `fetch_news_cached(..., pace=, chunk_symbols=)` and `build_news_source(config)` (7); `NewsConfig` field names identical in 1, 3, 4, 7.
- **Review Focus:** each of the five lines names its test and task above.
