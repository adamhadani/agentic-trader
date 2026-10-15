# Corporate actions

Desk item 3, part 1. The desk captures Alpaca corporate actions daily, journals them, and uses
them in three places: an entry rule, `/positions` notes and the account ledger's
unsupported-activity issue. A research command fetches the same data for a frozen cohort.
Design: [spec](superpowers/specs/2026-10-11-corporate-actions-design.md).

## Purpose

Three gaps existed on 2026-10-11.

- Live scans and the tap path read **raw** daily bars (`AlpacaDataProvider.fetch_bars` defaults to
  `adjustment="raw"`). A split inside a lookback distorts every indicator and the ATR, and nothing
  in the pipeline knew a split happened.
- A tracked position that crosses a split keeps its pre-split quantity, stop and target while the
  broker adjusts its own quantity and average price. A cash-merger target is dead money until the
  effective date. A name change breaks symbol matching.
- The account ledger marked every non-trade activity it does not model as "Unsupported activity
  type" and withheld realized totals without saying what happened.

The same data pays twice. It is the event source for a later dividend or split leg
([roadmap](alpha-roadmap.md), item 3), and it is the [architecture review](architecture-review.md)
P1 reconciliation gap. Part 2, the event-leg study, is a separate spec and a frozen catalog entry.
It waits for the counts the research acquisition below produces on the research cohort.

## Data

Alpaca's `/v1/corporate-actions` (data API, the keys the desk already uses) returns fourteen typed
shapes: forward, reverse and unit splits; cash and stock dividends; spin-offs; cash, stock and
stock-and-cash mergers; redemptions; name changes; worthless removals; rights distributions; reorganizations.

`CorporateAction.effective_on` is the first session whose prices reflect the action: `ex_date` for
splits (forward and reverse), stock and cash dividends, spin-offs and rights; `effective_date` for
mergers and unit splits; `process_date` for name changes, redemptions and worthless removals; `effective_date` for
reorganizations.

Measured on 2026-10-11 from the installed checkout (read-only probe):

- History reaches 2016 (XOM dividends from 2016-02-09).
- About 90 cash dividends per market day market-wide. Over 2026-10-01..10: 907 cash dividends,
  6 forward splits, 11 reverse splits, 7 name changes, 5 cash mergers, 6 stock mergers and
  3 spin-offs.
- Announced future actions are present: MSFT's 2026-11-19 ex-date was visible on 2026-10-11.
- The `start`/`end` filter matches **`process_date`** only. XOM's 2026-08-17 ex-date dividend is
  returned only for a window containing its 2026-09-10 payable/process date; NVDA's 2024-06-10
  split only for a window containing 2024-06-10. An ex-date horizon of N days therefore needs a
  `process_date` window well beyond N.

## Model and queries

`agentic_trader/data/corporate_actions.py`:

- `CorporateAction` is frozen and carries `id`, `kind`, `symbol` (the traded symbol the action
  affects), `related_symbol`, `effective_on`, the optional record, payable and process dates, the
  rates, and `special` and `foreign` flags.
- `is_structural` is true for every kind except `cash_dividend`.
- `reorganization` is a cash-and/or-stock reorganization of the symbol into `related_symbol`, effective on
  `effective_date`; it carries the optional `cash_rate` and the first stock movement's rates, records with
  several stock movements are refused, and `price_factor()` is `None`.
- `price_factor()` is `old_rate / new_rate` for forward and reverse splits, `1 / (1 + rate)` for a
  stock dividend, and `None` otherwise.
- `describe()` is the operator text, for example `forward split 10-for-1`, `cash dividend
  $0.98/share`, `cash merger by WMT at $11.50`.
- `normalize_actions` maps the provider payload to a tuple sorted by `(effective_on, symbol, id)`.
  An unknown type key, or a missing required date or rate, fails the whole normalisation with the
  type and field name and never the payload.
- `between(actions, symbol, start, end)` selects one symbol's actions by `effective_on`.
  `agentic_trader.risk.blocking_corporate_action` is the entry rule's predicate (it lives in `risk`, so the
  policy layer imports no provider code; `data` only types against it).
- `AlpacaCorporateActionsSource` chunks symbols (`chunk_size`), takes one SDK call per chunk under
  a request pacer, records a SHA-256 of each raw page, and fails the whole fetch if any chunk
  fails. Each call passes `limit=None`: alpaca-py treats `limit` as a cap on the whole aggregated
  result, so only without one does it follow `next_page_token` until the chunk is exhausted.
  `transport/alpaca.py:BoundedCorporateActionsClient` applies the desk's socket bounds.

## Daemon worker

`CorporateActionsWorker` runs in the daemon beside the card-statistics worker
(`service.run_corporate_actions_worker`).

- It polls every `poll_seconds`, does nothing inside a suggestion-scan window, and works once per
  New York date. A date is done when the worker has checked it in memory or the latest journaled
  check carries that `as_of`.
- Symbols are the configured universe, held equities (tracked signals and, on a brokerage account, the
  broker's own positions; no futures or crypto) and `corporate_actions.extra_symbols`. More than `max_symbols` fails the run with a
  `ValueError`; nothing is truncated.
- The window is `process_date` in `[today - lookback_days, today + lookahead_days]` (45 and 120
  calendar days by default). Dividends pay about 30 days after the ex-date, so ex-dates roughly 90
  days ahead are covered.
- A run appends one `corporate_actions_captured` event on stream `corporate-actions/capture` only
  when the content digest or the requested symbol set differs from the latest capture, and one
  `corporate_actions_checked` event on `corporate-actions/check` every run. The capture carries the
  sorted `symbols` it requested, so every consumer knows which names it covers. A capture above `max_actions` fails the run.
- A failed fetch or normalisation records nothing. The previous capture stays authoritative until
  it ages out, and the next poll retries.
- Readiness component `corporate_actions` has an age limit of `max_age_seconds` (four days by
  default, so weekends and holidays stay ready). A failure shows only the exception type name.

Inspect the latest capture with `copilot db events --stream corporate-actions/capture --limit 1`.
There is no schema change; the head stays `008_alpha_pipeline`.

## Entry rule

Rule id `corporate_action` (`agentic_trader/risk`, [risk policy](risk-policy.md#rule-table)).
`corporate_action_block(actions, symbol, today, *, pre_days, post_days)` refuses on the first
**structural** action on the symbol with `today - post_days <= effective_on <= today + pre_days`
(defaults 7 days ahead, 5 behind: raw bars straddle it). Cash dividends never refuse.

`corporate_actions.entry_block` selects the mode and ships `preview`.

| Mode | Evaluator (equities) | Tap gate |
| --- | --- | --- |
| `off` | step skipped, no provenance | nothing |
| `preview` | the card is unchanged; provenance and the card line record that it would have been refused | nothing |
| `enforce` | refuses with the rule's text | refuses with the same text |

- Stale (older than `max_age_seconds`) or missing evidence never refuses. The provenance records
  `{"status": "stale"}` or `{"status": "unavailable"}` plus the mode. An equity the latest capture
  did not request (a dynamic-universe name, a PEAD drift name, a `/scan SYMBOL` outside the universe;
  see [Known limits](#known-limits)) records
  `{"status": "uncovered", "mode": …, "as_of": …}` and never refuses either; only a covered symbol
  can be `ok`.
- Provenance key `corporate_actions` holds `status`, `mode`, `as_of`, `would_block`, `action`,
  `reason` and `note`. With no blocking action, a cash dividend with an ex-date inside `pre_days`
  is recorded as `note`.
- The card line is `🏷 Corporate action: NVDA forward split 10-for-1 effective 2026-06-10 —
  preview, card unchanged` (without the suffix when enforced), or `🏷 Ex-dividend 2026-11-19
  (cash dividend $0.98/share)` for a note. Nothing is printed otherwise.
- The refusal text is `Corporate action: {SYMBOL} {describe} effective {YYYY-MM-DD} ({in n
  day(s) | n day(s) ago | today}) — within the {pre}/{post}-day blackout.`
- The scan reads the store once per scan. Admission does not apply the rule, as with
  `earnings_blackout`. Nothing here touches the broker, the halt state or sizing.

## Positions notes

`/positions` (CLI and Telegram share the snapshot) adds one note per action on a broker position
with `effective_on` in `[today - post_days, today + notes_horizon_days]`:

- `XOM: cash dividend $0.99/share, ex 2026-11-12 (in 32 days), pays 2026-12-10.`
- `NVDA: forward split 10-for-1 effective 2026-06-10 (3 days ago); tracked quantity, stop and
  target predate it until reconciled.`

A stale or missing capture adds the single note `Corporate-action evidence stale.` or
`Corporate-action evidence unavailable.` while the feature is enabled; a held equity the capture did
not request gets `SYMBOL: corporate-action evidence does not cover this symbol yet.` until the next
run includes it. Notes are display only: the
tracked quantity, stop and target are not adjusted.

## Ledger

`AccountLedgerService` accepts the store. For an unsupported activity that names a symbol, a
structural action on that symbol within ten days of the activity date changes the issue to
`Unsupported activity type: {kind} ({describe} {SYMBOL} effective {date} in corporate-action
evidence)`. Without such an action the text is unchanged. The ledger **explains** and does not
resolve: realized totals stay withheld, because exact split and merger activity semantics need
observed paper activities and replay fixtures.

## Research acquisition

`research/corporate_actions.py:fetch_corporate_actions_cached(source, symbols, start, end,
cache_dir, *, pace)` writes `cache_dir/corporate-actions/<sha256 of the request>.json` with mode
0600 inside a 0700 directory. The request is the source, the format version (2: every chunk is read
to its last page, so no file written before that fix is reused), the sorted upper-case symbols and
the window. The file holds the normalised actions, the receipt (request and receipt times, page
count, page digests) and the content SHA-256. A reuse recomputes the digest, refuses a mismatch
with `does not match its recorded SHA-256`, and never calls the provider on a hit.

```bash
copilot alpha corporate-actions --symbol XOM --cohort COHORT.json \
  --start 2016-01-01 --end 2026-10-11 --cache CACHE_DIR --output NEW_PRIVATE_DIR
```

`--cohort` reads a JSON file with a `sectors` mapping (symbols per sector) or a `symbols` list;
`--symbol` is repeatable and the two combine. `--output` must not exist; it is created 0700. The
command writes `actions.json` and `manifest.json` (request hash, content SHA-256, counts by kind,
cache file, whether it was a cache hit, the research environment). The window filters on
`process_date`, so choose it with the filter in [Data](#data) in mind. Research only: no database,
registry, broker or Telegram, and no promotion credit. The
[`exdiv` catalog entry](apriori-alphas.md#entry-2-pre-ex-dividend-run-up)
(`config/research/apriori/exdiv-v1.json`) pins this acquisition's inputs: the process-date window
(`actions_window`) and the cohort file's SHA-256 (`cohort_sha256`). Its study manifest records the
cohort's path, digest and symbols, and its `result.json` records the acquisition's request and
content digests.

## Configuration

`corporate_actions` in `AppConfig` (`CorporateActionsConfig`). `load_config` reads the `corporate_actions:` YAML block (fixed 2026-10-13; before that the block, including `entry_block: enforce`, was ignored and the defaults ran).

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | run the worker and read captures; the worker also needs Alpaca data keys |
| `entry_block` | `"preview"` | `off`, `preview` or `enforce` for the entry rule |
| `pre_days` | `7` | a structural action effective this many calendar days ahead blocks (or would) |
| `post_days` | `5` | a structural action effective this many calendar days behind blocks (or would) |
| `notes_horizon_days` | `10` | `/positions` notes look this far ahead |
| `lookback_days` / `lookahead_days` | `45` / `120` | the `process_date` capture window |
| `max_symbols` / `chunk_size` | `600` / `100` | request bounds |
| `max_actions` | `5000` | capture bound |
| `max_requests_per_minute` | `60` | provider pacing |
| `poll_seconds` | `300` | worker poll |
| `max_age_seconds` | `345600` | readiness age limit and the staleness bound for every consumer |
| `extra_symbols` | `[]` | symbols beyond the universe and positions |

## Operations

1. Deploy through the usual controlled restart. The worker runs on its first poll outside a scan
   window and `/readyz` gains `corporate_actions`.
2. Confirm a capture: `copilot db events --stream corporate-actions/capture --limit 1`.
3. Run `entry_block: preview` for about a week. Cards carry the line and the provenance block; read
   how often `would_block` appears and for which kinds. Count the `uncovered` blocks per source as
   well: dynamic-universe cards (`dynamic_source` `most_actives` or `movers`), `/scan SYMBOL` cards
   (`dynamic_source: "operator"`) and PEAD drift cards (strategy `pead_long`). Those names are never
   refused, so the `uncovered` share of the card flow is the share the rule cannot see; weigh it
   before deciding on `enforce`.
4. Moving to `enforce` is an operator decision, the same `off -> preview -> enforce` path as
   `card_policy` and `book_sizing`.

## Known limits

- Live bars stay raw. The capture lets us count how often a universe name's lookback straddles a
  split; switching adjustment is a separate, measured change.
- No automatic adjustment of tracked quantities, stops or targets.
- Special dividends are treated as ordinary (annotated, never blocking).
- The ledger names the action and does not resolve it; totals stay withheld.
- The data depends on one provider, and the `process_date` filter semantics are a measured fact
  about it. If they change the worker still runs and the horizon shifts.
- Index inclusion is out of scope (paid constituent history).
- Coverage stops at the captured symbol set. Three card sources name symbols the worker never
  requests (unless held or listed in `extra_symbols`): dynamic-universe names (up to
  `universe.dynamic.max_symbols`, 20 by default, per scheduled scan), PEAD drift names
  (`research/apriori/pead_live.py`, Nasdaq reporters outside the universe) and `/scan SYMBOL` names
  outside the universe. They are always `uncovered` and never refused, even under `enforce`. The
  follow-up is to add recently scanned names (from `scan_candidates_ranked`) to the worker's
  symbol set.
