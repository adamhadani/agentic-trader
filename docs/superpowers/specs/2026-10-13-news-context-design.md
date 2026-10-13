# News as measured context (desk item 4) — design

Desk-direction item 4 ([roadmap](../../alpha-roadmap.md), "News as measured context"): wire a news
feed and attach LLM news scoring to suggestion cards as labelled context, attributed the way the
LLM gate is attributed in `cards outcomes`. Never a gate or signal until the attribution says so.
Designed 2026-10-13 under the operator's standing trust to take design decisions; decisions and the
measured facts behind them are in §7.

## 1. Why

- The desk's cards carry OHLCV-derived context only; the agreed definition of a reasonable
  suggestion ([desk direction, October 8](../../alpha-roadmap.md)) asks for broader context,
  honestly labelled. A card that says "4 news items in 48 h, tone negative, catalyst earnings" lets
  the operator read a setup against what the market is reacting to, and lets the outcomes report
  measure whether that context carries information.
- The LLM gate's attribution contract already exists: every evaluated card records `llm_verdict`,
  `cards outcomes` decomposes selection, LLM gate, market exposure and fill slippage, and nothing
  derived from it is a gate except the operator-configured `card_policy`. News joins that contract
  as a fourth descriptive block, so the question "do cards with aligned news do better?" is answered
  by the same report over the same journal rows.
- A research-side acquisition of the same feed (history to 2015) is the only way to get a sample
  faster than two cards a day; it is the event source a later frozen catalog entry would need.

## 2. Scope

**In this PR:**

1. A typed news model and an Alpaca news source (Benzinga via the desk's existing data keys) with
   bounded transport and page evidence; selection of a symbol's recent, company-specific items.
2. A per-scan news context service: one bounded fetch for the ranked candidates' symbols, LLM tone
   scoring for the first `max_scored_per_scan` ranked symbols, a per-candidate block carried on the
   evaluation, in card provenance and in the `scan_candidates_ranked` journal; the card line.
3. `cards outcomes`: `news_*` columns and a descriptive `news_context` block (by status, tone and
   direction alignment). `card_policy`, `card_stats` and every risk rule stay untouched.
4. `NewsConfig` as `news` on `AppConfig`, read by `load_config`; the same loader fix for the
   `corporate_actions` block, which `load_config` never passed (so YAML overrides were silently
   ignored — the preview-week `enforce` decision depends on this).
5. A research-side cached acquisition and CLI (`alpha news`) writing a hash-identified headline
   file for a later catalog entry; no study.
6. Documentation: `docs/news-context.md`, card-evidence pointer, production operations, the roadmap
   item 4 status, CLAUDE.md; this spec and the plan.

**Out of scope (named so they are not implied):** any gate, rule, signal or sizing input derived
from news (nothing in `agentic_trader/risk`, `card_policy` or admission reads it); news text in the
LLM gate's prompt (it would contaminate the gate's attribution — §7); a daemon worker, readiness
component or `/positions` notes (news is fetched at scan time, §3.3); blocks for `/scan SYMBOL`,
the intraday job or drift cards; full article bodies (`include_content` stays false); a `/news`
Telegram command; a frozen news study or catalog entry (its own spec); any schema migration (head
stays `008_alpha_pipeline`).

## 3. Components

### 3.1 Model and source — `agentic_trader/data/news.py`

- `NewsItem` (frozen pydantic): `id: int`, `headline: str`, `summary: str` (may be empty),
  `source: str`, `url: str | None`, `created_at: datetime` (UTC), `symbols: tuple[str, ...]`
  (upper-case, as tagged by the provider). `updated_at` is dropped (measured identical to
  `created_at` on every item in the probe).
- `NewsPage` (frozen dataclass): `items: tuple[NewsItem, ...]`, `requested_at`, `received_at`,
  `symbols: tuple[str, ...]`, `window: tuple[datetime, datetime]`, `pages: int`, `page_sha256: str`
  (SHA-256 over the raw pages in order), `truncated: bool` (the provider still had a page token
  when `max_items` was reached).
- `NewsSource` Protocol: `fetch(self, symbols: Sequence[str], start: datetime, end: datetime) -> NewsPage`.
- `AlpacaNewsSource(client, *, chunk_size: int, max_items: int, clock)` — `client` is a
  `BoundedNewsClient(BoundedTransport, NewsClient)` with `raw_data=True` (one-line subclass in
  `transport/alpaca.py`, same socket deadline and GET-only retry as the other bounded clients).
  Symbols are sent in chunks of `chunk_size` as the comma-joined string the SDK expects, with
  `NewsRequest(start, end, sort="desc", include_content=False, limit=max_items)`; a set `limit`
  caps the whole paginated result (SDK fact, same as corporate actions) and a remaining
  `next_page_token` marks `truncated`. Raw pages are observed through `client.observe_responses`
  for `pages`/`page_sha256`. Items are de-duplicated by `id` across chunks. Any chunk failure or
  normalisation error fails the whole fetch (no partial pages).
- `select_items(page, symbol, *, now, window_hours, max_items, max_symbols_per_item) -> NewsSelection`
  (frozen dataclass: `items` newest first, `n_items`, `n_market_wide`): items tagged with
  `symbol`, `created_at` in `[now − window_hours, now]`; items tagged with more than
  `max_symbols_per_item` symbols are market-wide lists ("Companies reporting before the bell") and
  are excluded and counted, not shown; the newest `max_items` remain.

### 3.2 Scoring — `agentic_trader/agent/news_context.py`

- `NewsScore` (frozen pydantic, strict): `tone: Literal["negative", "neutral", "positive"]`,
  `relevance: Literal["company", "sector", "market"]`,
  `catalyst: Literal["earnings", "guidance", "m_and_a", "regulatory", "product", "analyst", "macro", "other"]`,
  `one_line: str` (≤ 160 characters, trimmed; HTML-escaped at render time).
- `NewsScorer(*, model: str, api_key: str | None, timeout_seconds: float, complete=litellm.acompletion)`
  with `async score(symbol: str, items: Sequence[NewsItem]) -> NewsScore`. One call,
  `temperature=0`, `response_format={"type": "json_object"}`, `timeout=timeout_seconds`. The user
  message lists `created_at`, `source`, `headline` and `summary` per item (newest first); the
  system prompt asks for tone of the news **for the company's shares**, independent of any trade
  direction, and for JSON only. `prompt_sha256` = SHA-256 of the system prompt text (recorded, so a
  prompt change is visible in the journal). `complete` is injectable for tests; the scorer never
  raises into the scan: failures surface as the block's `status`.
- `NewsContextService(source, scorer, config: NewsConfig, *, clock)` with
  `async for_symbols(symbols: Sequence[str], *, score_first: int) -> dict[str, dict[str, Any]]`:
  one `fetch` in `asyncio.to_thread` under `asyncio.wait_for(config.fetch_timeout_seconds)`; then
  `select_items` per symbol; scoring runs with `asyncio.gather` for the first `score_first` symbols
  (in the order given, which is rank order) that have ≥ 1 item, each call bounded by the scorer's
  timeout. Returns one block per requested symbol (schema below). The service is per scan; it keeps
  no state and never calls the provider outside `for_symbols`.

**Block schema** (every key always present; JSON-safe, no NaN):

| Key | Values |
| --- | --- |
| `status` | `ok` (scored), `no_news` (0 items), `unscored` (items, beyond `score_first`), `score_failed`, `unavailable` (fetch failed or disabled source) |
| `as_of` | fetch `received_at` ISO-8601, or `None` when `unavailable` |
| `window_hours`, `n_items`, `n_market_wide` | ints (`n_items` counts selected items; 0 when `unavailable`) |
| `truncated` | bool |
| `headlines` | newest `max_headlines_recorded` of `{id, created_at, source, headline}` |
| `score` | `NewsScore` dict or `None` |
| `model`, `prompt_sha256` | the scorer's identity (`None` when not scored) |
| `error_type` | exception type name or `None` — never exception text |
| `page_sha256` | fetch evidence or `None` |
| `applied` | always `False`: mirrors `llm_verdict`'s shape and states that nothing read it |

### 3.3 Scan integration — `agent/copilot.py`, `agent/evaluator.py`

- After ranking and before the send loop (beside the other `*_by_rank` lists), `run_scan` computes
  `news_by_rank`: the ranked candidates' equity symbols (contracts without a `/` prefix, in rank
  order, de-duplicated), `score_first = config.news.max_scored_per_scan`,
  `blocks = await news_service.for_symbols(...)`, then `news_by_rank[i] = blocks.get(symbol)` (a
  futures candidate gets `None`). When `news.enabled` is false or no Alpaca data keys exist, the
  service is `None` and every entry is `None` (no block, no line, no column value). The step is
  wrapped so any exception yields `unavailable` blocks with the type name; it never raises into
  the scan and never touches the budget, ranking or candidate order.
- `RiskEvaluator.evaluate_candidate(..., news_context: Mapping[str, Any] | None = None)` attaches
  the block to `LLMTradeEvaluation.news_context: dict[str, Any] | None` after the LLM reply is
  parsed (`data.pop("news_context", None)` first, exactly like `corporate_actions`), on every path
  (LLM, deterministic, fallback). The prompt template and `build_system_prompt` do not change: a
  test asserts the rendered prompt contains no headline text.
- `TradingCopilot._news_block(evaluation)` (static, like `_llm_block`) returns the block as-is
  (already JSON-safe) or `None`; it is written to provenance as `decision_provenance["news"]`, to
  the journal candidate as `"news"` via a new `news_by_rank` keyword of `_journal_scan_ranking`,
  and rides to the notification inside `eval_res` (no `send_signal_alert` signature change).
  The dry-run path renders the same line through `format_terminal_card`.
- Reprice: `_replacement_card` passes `news_context=old_provenance.get("news")` into the
  re-evaluation so the replacement card keeps the scan-time block (its `as_of` says when).
- `cards outcomes` is the only reader. `card_stats`, `card_policy`, `agentic_trader/risk`,
  admission and the tap gate do not import or read the block (test: `card_policy.decide` and
  `compute_card_stats` sources contain no `news`).

### 3.4 Card line — `notifier/telegram_bot.py`

`_news_line(block, *, markup) -> str` beside `_corporate_action_line`, appended to both
`evidence_block` expressions (alert and terminal cards). One line, always labelled
`News (context, not a gate):`

| Status | Text after the label |
| --- | --- |
| `ok` | `{n_items} items/{window_hours}h · tone {tone} · {catalyst} · "{one_line}" · latest "{headline}" ({source}, {age})` |
| `unscored` | `{n_items} items/{window_hours}h · not scored · latest "{headline}" ({source}, {age})` |
| `no_news` | `none in {window_hours}h` |
| `score_failed` | `{n_items} items/{window_hours}h · scoring failed ({error_type})` |
| `unavailable` | `unavailable ({error_type})` or `unavailable` |

`age` is "2h ago"/"1d ago" from `created_at` to the card's time; `truncated` adds " · feed truncated".
Headlines and `one_line` are HTML-escaped when `markup` is true and clipped to
`max_headline_chars` (default 110). A missing block renders nothing. The thesis text is untouched.

### 3.5 Outcomes — `research/setups/outcomes.py`

- `_row()` flattens the candidate's `news` block into `news_status`, `news_n_items`, `news_tone`,
  `news_catalyst`, `news_relevance`, `news_alignment`; `_COLUMNS` gains them. `news_alignment` is
  deterministic: `aligned` (LONG × positive or SHORT × negative), `opposed` (the reverse),
  `neutral` (tone neutral), `none` (no score). Rows without a block get `None`/`"absent"`.
- `summarize()` adds `news_context`: `{"blocks": n, "by_status": {status: n}, "scored": n,
  "by_alignment": {alignment: {"n", "mean_r_cost", "hit_rate"}}, "by_tone": {...},
  "by_catalyst": {catalyst: n}}` over the same mature rows the LLM-gate block uses; the empty-frame
  dict carries the block with zeros. Everything is descriptive, printed next to `llm_gate`.

### 3.6 Config — `NewsConfig` on `AppConfig` as `news`

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `True` | compute blocks on universe scans |
| `window_hours` | `48` | item age window at scan time |
| `max_items_per_symbol` | `12` | newest items kept per symbol (scoring input) |
| `max_symbols_per_item` | `5` | items tagged with more symbols are market-wide lists: excluded, counted |
| `max_scored_per_scan` | `6` | ranked symbols scored by the LLM per scan (the rest `unscored`) |
| `max_headlines_recorded` | `5` | headlines kept in provenance and the journal |
| `max_headline_chars` | `110` | clip on the card |
| `chunk_size` | `50` | symbols per provider request |
| `max_items_per_fetch` | `1000` | the SDK `limit` (aggregate cap; `truncated` when hit) |
| `fetch_timeout_seconds` | `15` | whole-fetch bound (`wait_for`); per-request socket deadline is `market_data.timeout_seconds` |
| `score_timeout_seconds` | `20` | per LLM call |
| `model` | `None` | scorer model; `None` means `AppConfig.llm_model` |

Validation: positive bounds, `extra="forbid"`, `max_scored_per_scan ≥ 1`, `window_hours ≤ 168`.
`load_config` passes `news=NewsConfig(**(cfg_dict.get("news") or {}))` and
`corporate_actions=CorporateActionsConfig(**(cfg_dict.get("corporate_actions") or {}))`.
`config/config.yaml` gains commented `news:` and `corporate_actions:` blocks at their defaults
(behaviour-neutral). `news_live(config)` mirrors `corporate_actions_live` (enabled + Alpaca keys).

### 3.7 Research acquisition — `agentic_trader/research/news.py` and `alpha news`

- `fetch_news_cached(source, symbols, start, end, cache_dir, *, pace, chunk_symbols=20) -> (items, manifest)`
  mirroring `fetch_corporate_actions_cached`: request dict `{source: "alpaca-news", format: 1,
  symbols, start, end}` → `cache_dir/news/<request_sha256>.json` holding `items` (id, headline,
  summary, source, url, created_at, symbols — no content), `content_sha256`, timestamps, pages and
  page digests; reuse re-hashes and refuses a mismatch; private writes (0700/0600, temp + rename).
  Requests are issued per symbol chunk per calendar month with `limit=None` (full pagination); a
  month whose last page still has a token is split in half (down to one day) so no window is ever
  truncated; every request is paced.
- `alpha news --symbol/--cohort --start --end --cache --output` writes `news.json.gz` and
  `manifest.json` (counts per symbol and per month, request/content digests, `research_environment()`),
  refuses an existing output directory, and uses no database, broker or Telegram.

## 4. Failure behaviour

| Situation | Block | Card | Scan | Outcomes |
| --- | --- | --- | --- | --- |
| Fetch fails or exceeds `fetch_timeout_seconds` | `unavailable` + type name for every symbol | "unavailable (TimeoutError)" | continues, nothing else changes | counted under `by_status` |
| LLM call fails/times out/invalid JSON | `score_failed` + type name; headlines kept | counts + "scoring failed" | continues | counted |
| 0 items in window | `no_news` | "none in 48h" | — | `alignment none` |
| Beyond `max_scored_per_scan` | `unscored` | counts + latest headline | — | counted |
| `truncated` fetch | block flag | " · feed truncated" | — | — |
| `news.enabled: false` or no data keys | no block (`None`) | no line | no fetch | `absent` |
| Futures candidate | no block | no line | — | `absent` |

Nothing here touches readiness, the halt state, ranking, budget, sizing, admission or the broker.
No exception text reaches a card, provenance or the journal; `error_type` is a type name.

## 5. Testing

- `tests/data/test_news.py`: normalisation from raw pages (symbols upper-cased, UTC, empty
  summary), de-duplication across chunks, `truncated`, chunk failure fails the fetch, page digest
  stability; `select_items` window edges, market-wide exclusion, newest-first cap.
- `tests/agent/test_news_context.py`: scorer with an injected `complete` (valid JSON, invalid
  tone → `score_failed`, timeout → `score_failed`, 0 items → no call); service with a fake source:
  rank-ordered `score_first`, `unscored`, fetch failure → `unavailable` for all; blocks JSON-safe.
- `tests/agent/test_evaluator*.py`: `news_context` attached on LLM, deterministic and fallback
  paths; a reply containing `news_context` is popped; rendered prompt contains no headline text.
- `tests/agent/test_copilot_scan*.py`: `news_by_rank` aligned to `ranked`, futures → `None`,
  provenance `news`, journal `news`, disabled → no blocks, service exception → `unavailable`.
- `tests/notifier/test_telegram_bot*.py`: `_news_line` per status, HTML escaping, clipping, age,
  missing block → "".
- `tests/research/setups/test_outcomes*.py`: columns, alignment per direction × tone,
  `news_context` summary, empty frame.
- `tests/test_config*.py`: `NewsConfig` validation; `load_config` reads `news:` and
  `corporate_actions:` from a YAML fixture (regression for the loader gap).
- `tests/research/test_news_cached.py` + `tests/cli/test_alpha_news.py`: cache hit/miss, mismatch
  refusal, month splitting on a token, manifest counts, output refusal.
- Source-text guards: `card_policy.py`, `card_stats.py` and `agentic_trader/risk/*` contain no
  `news`.

All tests use fakes; network is blocked by the suite.

## 6. Operations

- No restart-visible component: the first live block appears on the next universe scan after
  deploy; `copilot db events --stream scan/<date> --limit 1` shows the `news` key per candidate;
  `copilot cards outcomes --days 30` prints `news_context`.
- Cost: ≤ `max_scored_per_scan` LLM calls per scan (≈ 1.5k tokens each) and one news request per
  scan; measured fetch for 159 symbols over 7 days took 3.3 s, so a 48 h window is well inside the
  15 s bound.
- The research acquisition is operator-run (`alpha news`), paced, cached, outside scan windows.

## 7. Design decisions

1. **Feed: Alpaca news (Benzinga).** Read-only probe 2026-10-13 with the desk's data keys: one
   source (`benzinga`); 1,003 items over 7 days for the 159 universe names (134–222 per weekday,
   14–23 per weekend day); 131/159 names had ≥ 1 item, median 6 per covered name, SPY 162 and NVDA
   140 at the top; 448/1,003 items tag exactly one symbol, 185 tag more than five (market-wide
   lists); headlines median 84 chars, summaries empty for 48%; article bodies are HTML of ~2.7k
   chars median (not fetched); history reaches 2015-01-02 (AAPL March 2019: 158 items, JNJ 29);
   `updated_at` equals `created_at`. No new credentials, same bounded transport.
2. **Fetch at scan time, no worker.** News is only useful fresh, and the scan already does bounded
   provider reads; a worker would need a readiness component, journal retention for a feed that
   changes every poll, and a staleness contract for a 48 h window. One bounded request per scan
   with `unavailable` as the only failure mode is simpler and honest. A daemon worker becomes
   worthwhile only if `/positions` news notes are wanted (not asked).
3. **Score per symbol, direction-free; alignment computed deterministically.** One LLM call serves
   every candidate of a symbol, the score cannot be steered by the trade direction, and the
   outcomes report derives `aligned/opposed` itself. Scoring the first `max_scored_per_scan` ranked
   symbols (default 6 > `max_llm_evaluations_per_scan` 4) covers every card that can be sent plus
   the nearest runners-up; the rest keep counts and headlines (`unscored`).
4. **Out of the gate prompt.** If the gate saw the news, `llm_gate` attribution would mix two
   inputs and the news block could never be measured on its own. The two LLM calls stay separate
   and both are recorded.
5. **Carried on the evaluation.** `LLMTradeEvaluation.news_context` follows the `corporate_actions`
   precedent: no notification-signature change (a missing kwarg dead-letters a card), reprice
   carries it explicitly, dry runs render it.
6. **Bounded journal payload.** The journal keeps `max_headlines_recorded` headlines plus digests,
   never bodies; `domain_events` is retained forever, so the per-scan block stays a few hundred
   bytes per candidate. The research cache holds the bulk, outside the database.
7. **Loader fix bundled.** `load_config` never passed `corporate_actions`, so the YAML block the
   corporate-actions docs describe had no effect; adding `news` to the loader without fixing that
   would repeat the gap. Both are one line each, covered by a regression test, behaviour-neutral
   with the shipped defaults.
8. **No study in this PR.** A frozen news entry (e.g. post-news drift conditioned on tone) needs
   its own catalog spec; the acquisition exists so that spec can start from measured counts.

Follow-ups recorded, not done here: the LLM-gate fallback writes the exception text into
`thesis_summary` (pre-existing, contrary to the type-name-only rule for incidents); the
corporate-actions and news symbol sets both miss dynamic-universe names, so those candidates are
`uncovered`/have counts only.
