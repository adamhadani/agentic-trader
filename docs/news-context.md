# News as measured context

Desk item 4 ([roadmap](alpha-roadmap.md)); design and decisions in the
[spec](superpowers/specs/2026-10-13-news-context-design.md). News is labelled context on a suggestion
card, recorded for attribution. It is never a gate, a signal or a sizing input.

## What it is

- One bounded Benzinga headline fetch per scan that ranks candidates, through the desk's Alpaca data
  keys (no new credentials). Headlines and provider summaries only; article bodies are never
  requested (`include_content=False`).
- The fetch covers the ranked equity candidates' symbols over the last `news.window_hours` (48).
  Contracts containing `/` (futures, crypto pairs) and catalog drift cards get no symbols and no
  block.
- Scoring: the first `news.max_scored_per_scan` (6) ranked symbols that have at least one company
  item get one direction-free LLM call each (tone, relevance, catalyst, a one-line summary). Other
  symbols keep counts and headlines (`unscored`).
- Each candidate carries one block: on `LLMTradeEvaluation.news_context`, in the card line, in
  `decision_provenance["news"]` and in the `scan_candidates_ranked` journal candidate's `news`.
  `applied` is always `false`: nothing reads the block as an input.
- Scope. The news step runs on every scan that ranks candidates, including symbol-scoped operator
  scans and dry runs; the intraday job's non-equity contracts give no symbols and no fetch. The
  journal row and `cards outcomes` attribution cover universe scans only, so `/scan SYMBOL` and
  intraday cards carry the block but have no journal row.
- Nothing in `agentic_trader/risk`, `card_policy`, `card_stats` or admission reads news (source-text
  tests enforce it), and the LLM gate's prompt never contains news, so the gate's attribution stays
  separate.

Code: `data/news.py` (model, `AlpacaNewsSource`, `select_items`), `agent/news_context.py` (scorer,
service, block), `copilot._news_blocks`, `notifier/telegram_bot._news_line`,
`research/setups/outcomes.py` (attribution), `research/news.py` and `cli/commands/alpha_news.py`
(research acquisition).

## Block schema

Every key is always present; the block is JSON-safe.

| Key | Values |
| --- | --- |
| `status` | `ok` (scored), `no_news` (0 company items), `unscored` (items, beyond the scoring cap or no LLM key), `score_failed`, `unavailable` (fetch failed or timed out, or the step raised) |
| `as_of` | fetch `received_at`, ISO-8601; `None` when `unavailable` |
| `window_hours`, `n_items`, `n_market_wide` | ints. `n_items` counts the symbol's company items in the window (all of them, not only the newest kept); 0 when `unavailable` |
| `truncated` | bool, true when the provider still had a page token on the last page of a chunk |
| `headlines` | newest `max_headlines_recorded` of `{id, created_at, source, headline}` |
| `score` | `{tone, relevance, catalyst, one_line}` or `None` |
| `model`, `prompt_sha256` | the scorer's identity; `None` unless `status == "ok"` |
| `error_type` | exception type name or `None`; never exception text |
| `page_sha256` | SHA-256 of the last raw page observed for the fetch (fetch evidence, not a combined hash); `None` when `unavailable` |
| `applied` | always `false` |

Score vocabulary: `tone` negative, neutral or positive (for the company's shares, independent of any
trade direction); `relevance` company, sector or market; `catalyst` earnings, guidance, m_and_a,
regulatory, product, analyst, macro or other. `one_line` is clipped to 160 characters. Items tagged
with more than `max_symbols_per_item` (5) symbols are market-wide lists ("Companies reporting before
the bell"): excluded and counted in `n_market_wide`. The scorer runs at temperature 0 with a JSON
reply; `prompt_sha256` hashes its system prompt so a prompt change is visible in the journal.

## Card line

One line beside the corporate-action line, always labelled `News (context, not a gate):`. A missing
block renders nothing; the thesis text is untouched.

| Status | Text after the label |
| --- | --- |
| `ok` | `{n_items} items/{window_hours}h · tone {tone} · {catalyst} · "{one_line}" · latest "{headline}" ({source}, {age})` |
| `unscored` | `{n_items} items/{window_hours}h · not scored · latest "{headline}" ({source}, {age})` |
| `no_news` | `none in {window_hours}h` |
| `score_failed` | `{n_items} items/{window_hours}h · scoring failed ({error_type})` |
| `unavailable` | `unavailable ({error_type})`, or `unavailable` |

A truncated fetch appends ` · feed truncated`. Headlines and the one-liner are whitespace-collapsed,
clipped and HTML-escaped for Telegram. The terminal card (dry runs) renders the same line. A repriced
card keeps the scan-time block: the block rides in the card's stored `raw_response`
(`eval_res.model_dump_json()`), which `_replacement_card` re-validates, so `as_of` says when it was
fetched.

## Attribution

`cards outcomes` is the only reader. Per journaled universe-scan candidate it adds columns
`news_status`, `news_n_items`, `news_tone`, `news_catalyst`, `news_relevance` and `news_alignment`
(rows without a block show `absent`), and `summarize()` adds a `news_context` block printed next to
`llm_gate`.

`news_alignment` is deterministic from the candidate's direction and the symbol's tone, so the scorer
never sees the direction: `aligned` (LONG with positive, or SHORT with negative), `opposed` (the
reverse), `neutral` (tone neutral), `none` (no score).

Populations in the `news_context` summary differ. `blocks`, `by_status`, `scored` and `by_catalyst`
count over all journaled rows, mature and immature. `by_alignment` and `by_tone` (each with `n`,
`mean_r_cost` and `hit_rate`) use mature rows only, the same population the LLM-gate block uses. The
empty-frame summary carries the block with zeros.

This is descriptive. `card_policy` and `card_stats` do not read it, and no number here is a gate until
an operator-specified policy says so.

## Configuration

`news` in `AppConfig` (`NewsConfig`, `extra="forbid"`). The feature runs only with `news.enabled` and
Alpaca data keys (`news_live`). `news.enabled: false` removes the step entirely: no fetch, no block,
no card line, outcomes show `absent`. `load_config` passes the `news:` block (and, since 2026-10-13,
the `corporate_actions:` block).

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | compute blocks on scans |
| `window_hours` | `48` | item age window at scan time (max 168) |
| `max_items_per_symbol` | `12` | newest company items passed to the scorer |
| `max_symbols_per_item` | `5` | items with more symbols are market-wide lists, excluded and counted |
| `max_scored_per_scan` | `6` | ranked symbols scored per scan; the rest are `unscored` |
| `max_headlines_recorded` | `5` | headlines kept in provenance and the journal |
| `max_headline_chars` | `110` | clip on the card |
| `chunk_size` | `50` | symbols per provider request |
| `max_items_per_fetch` | `1000` | the SDK `limit` per chunk (`truncated` when the provider had more) |
| `max_requests_per_minute` | `100` | provider pacing, once per symbol chunk |
| `fetch_timeout_seconds` | `15` | whole-fetch bound; the socket deadline is `market_data.timeout_seconds` |
| `score_timeout_seconds` | `20` | per LLM call, enforced by the scorer; the service adds an outer guard |
| `model` | unset | scorer model; unset means `AppConfig.llm_model` |

Without an LLM key the scorer is absent and symbols with items are `unscored`.

## Failure behaviour

| Situation | Block | Card | Scan |
| --- | --- | --- | --- |
| Fetch fails or exceeds `fetch_timeout_seconds` | `unavailable` with the type name for every symbol | `unavailable (TimeoutError)` | continues unchanged |
| The whole step raises | `unavailable` with the type name | same | continues unchanged |
| LLM call fails, times out or returns invalid JSON | `score_failed` with the type name; headlines kept | counts and `scoring failed` | continues |
| 0 company items in the window | `no_news` | `none in 48h` | |
| Beyond `max_scored_per_scan` | `unscored` | counts and latest headline | |
| Truncated fetch | `truncated: true` | ` · feed truncated` | |
| `news.enabled: false` or no data keys | no block | no line | no fetch |
| Futures or crypto candidate, drift card | no block | no line | |

Nothing touches readiness, the halt state, ranking, the card budget, sizing, admission or the broker.
No exception text reaches a card, provenance or the journal; only type names.

## Operations

- No restart-visible component. The first live block appears on the next scan after deploy.
- Inspect the journal: `copilot db events --stream scan/<date> --limit 1` shows the `news` key per
  candidate. Inspect attribution: `copilot cards outcomes --days 30` prints `news_context`.
- Cost: at most `max_scored_per_scan` LLM calls (small prompts) and one news request per symbol chunk
  per scan.
- Measured 2026-10-13 (read-only probe, desk data keys): one source (`benzinga`); 1,003 items over 7
  days for the 159 universe names (134 to 222 per weekday); 131 of 159 names covered; 185 items tag
  more than five symbols (market-wide lists); headline median 84 characters; summaries empty for 48%;
  history reaches 2015-01-02; the 7-day universe pull took 3.3 s, so a 48 h window sits well inside the
  15 s bound.

## Research acquisition

`alpha news` is research only: no database, broker, Telegram or registry.

```bash
copilot alpha news --cohort config/research/spread/cohort-v1.json \
  --start 2016-01-01 --end 2026-10-01 --cache CACHE_DIR --output NEW_OUTPUT_DIR
```

`--symbol` is repeatable and combines with `--cohort`; `--chunk-symbols` (default 20) sets symbols per
request. The output directory must not exist; it receives `news.json.gz` and `manifest.json`
(per-symbol and per-month counts, request and content digests, the research environment).

Requests go per symbol chunk per calendar month. Each is bounded to `RESEARCH_MAX_ITEMS = 5000`; if the
provider still has a page token the window is split in half down to one day, and a single day that is
still truncated fails with a "page cap" `ValueError`. Each request is cached as a gzip JSON file keyed
by the SHA-256 of the request, holding the items (never bodies), the receipt and a content digest; a
reuse re-hashes and refuses a mismatch. Private writes (0700 directories, 0600 files). The source paces
once per symbol chunk, not per HTTP page; the SDK's own 429 retry covers bursts.

No study exists yet. A frozen news entry (for example post-news drift conditioned on tone) needs its
own catalog spec.

## Design decisions

1. **Alpaca news (Benzinga).** Same keys and bounded transport; the probe above shows usable coverage
   and a long history.
2. **Fetch at scan time, no worker.** News is only useful fresh and a 48 h window needs no staleness
   contract; a worker would need a readiness component and journal retention for a feed that changes on
   every poll. The only failure mode is `unavailable`.
3. **Direction-free score, deterministic alignment.** One call serves every candidate of a symbol and
   cannot be steered by the trade direction. The default 6 scored symbols exceeds
   `max_llm_evaluations_per_scan` (4), so every card that can be sent plus the nearest runners-up is scored.
4. **Out of the gate prompt.** If the gate saw news, `llm_gate` attribution would mix two inputs and
   news could never be measured alone. Both LLM calls are recorded separately.
5. **Carried on the evaluation**, following the `corporate_actions` precedent: no notification signature
   change, dry runs render it, reprice keeps it.
6. **Bounded journal payload.** A few headlines plus digests, never bodies; the research cache holds the
   bulk outside the database.
7. **Loader fix bundled.** `load_config` previously never passed `corporate_actions`, so that YAML block
   (including `entry_block: enforce`) was silently ignored.
8. **No study yet.** The acquisition exists so a later spec can start from measured counts.

Known limits: the LLM-gate fallback writes exception text into `thesis_summary` (pre-existing); news and
corporate-action symbol sets both miss dynamic-universe names beyond the scan's own candidates; no
schema migration (head stays `008_alpha_pipeline`).
