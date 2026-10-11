# Corporate-actions ingestion (desk item 3, part 1) — design

Date: 2026-10-11. Author: the assistant under the operator's standing instruction to take design
decisions and present them in the PR. Status: approved for planning by that instruction; merge needs
the operator's explicit approval.

## 1. Why

The desk-direction plan ([roadmap](../../alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8),
item 3) puts a second event leg after the PEAD probe reports and says to ingest corporate actions
first, because the same data pays twice: it is the event source for a dividend or split leg, and it
is the architecture review's P1 reconciliation gap ("corporate actions or stock transfers make account
reconciliation unsupported").

What the desk has today, measured on 2026-10-11:

- Live scans and the tap path read **raw** daily bars (`AlpacaDataProvider.fetch_bars` defaults to
  `adjustment="raw"`). A split inside a 60-session lookback distorts every indicator and the ATR;
  nothing in the pipeline knows a split happened.
- A tracked position that crosses a split keeps its pre-split quantity, stop and target while the
  broker adjusts its own quantity and average price; `/positions` reports the mismatch as a bare
  "tracked quantity X; broker quantity Y", and the trailing-stop replacement would be priced on the
  wrong scale. A cash-merger target is dead money until the effective date; a name change breaks
  symbol matching.
- The account ledger (`accounting/ledger.py`) accepts `DIV`/`INT`/fees/transfers and marks every
  other non-trade activity "Unsupported activity type", withholding realized totals with no
  explanation of what happened.
- Research has only the Nasdaq earnings calendar as an external event source; there is no
  hash-identified corporate-action history for a frozen catalog entry.

The data exists on the plan the desk already pays for. Alpaca's `/v1/corporate-actions` (data API,
same keys) returns thirteen typed shapes (forward/reverse/unit splits, cash and stock dividends,
spin-offs, cash/stock/stock-and-cash mergers, redemptions, name changes, worthless removals, rights
distributions). Measured on 2026-10-11 from the installed checkout: history back to 2016 (XOM
dividends from 2016-02-09), about 90 cash dividends per market day market-wide, announced future
actions present (MSFT's 2026-11-19 ex-date was visible on 2026-10-11), and the `start`/`end` filter
matches **`process_date`** only (the payable date for cash dividends, the ex-date for splits), so an
ex-date horizon of N days needs a `process_date` window well beyond N.

## 2. Scope

**In this PR (part 1):**

1. A typed corporate-action model and an Alpaca source with bounded transport and page evidence.
2. A daily daemon worker that captures the desk's symbols (configured universe, tracked positions,
   configured extras) over a rolling window and journals one capture event per content change, with
   a readiness component.
3. Consumers of the capture: a `corporate_action` entry rule in `agentic_trader/risk` (preview by
   default), the matching card line and provenance block, `/positions` notes, and a named reason in
   the ledger's unsupported-activity issue.
4. A research-side cached acquisition and CLI that writes a hash-identified event file for a later
   catalog entry.
5. Documentation: `docs/corporate-actions.md`, the risk-policy rule table, production operations,
   the architecture-review P1 row, CLAUDE.md, the roadmap item 3 status; and the bundled pairs v2
   results document (no docs-only PRs).

**Out of scope (named so they are not implied):** switching live bars to split-adjusted series
(a behaviour change for every strategy; the capture lets us first count how often a universe name
has a split in its lookback); exact split or merger accounting in the ledger (realized totals stay
withheld; the issue is explained, not resolved); automatic adjustment of tracked quantities, stops or
targets; the event-leg study itself (part 2, its own spec and frozen catalog entry); index inclusion
(paid constituent history); any schema migration (head stays `008_alpha_pipeline`).

## 3. Components

### 3.1 Model and source — `agentic_trader/data/corporate_actions.py`

- `CorporateActionKind(StrEnum)`: the thirteen Alpaca types, singular (`forward_split`,
  `reverse_split`, `unit_split`, `cash_dividend`, `stock_dividend`, `spin_off`, `cash_merger`,
  `stock_merger`, `stock_and_cash_merger`, `redemption`, `name_change`, `worthless_removal`,
  `rights_distribution`). An unknown type key from the provider is a `ValueError` naming the key.
- `CorporateAction` (pydantic, `frozen=True, extra="forbid"`): `id`, `kind`, `symbol` (the traded
  symbol the action affects: `symbol`, `acquiree_symbol`, `old_symbol` or `source_symbol` in the
  provider's shape), `related_symbol: str | None` (acquirer, new or alternate symbol),
  `effective_on: date` (the first session whose prices reflect the action: `ex_date` for splits,
  stock and cash dividends, spin-offs and rights; `effective_date` for mergers and unit splits;
  `process_date` for name changes, redemptions and worthless removals), `record_date`,
  `payable_date`, `process_date` (optional dates), `rate`, `old_rate`, `new_rate`, `cash_rate`
  (optional floats), `special: bool = False`, `foreign: bool = False`, `source: Literal["alpaca"]`.
  Derived: `is_structural` (every kind except `cash_dividend`), `price_factor()` (forward/reverse
  split: `old_rate / new_rate`; stock dividend: `1 / (1 + rate)`; otherwise `None`),
  `describe()` (operator text, e.g. `forward split 10-for-1`, `cash dividend $0.98/share`,
  `cash merger by WMT at $11.50`).
- `normalize_actions(data: Mapping[str, Sequence[Mapping]]) -> tuple[CorporateAction, ...]` from the
  provider's `{plural_type: [records]}` payload, sorted by `(effective_on, symbol, id)`. Missing
  required dates or rates fail the whole normalisation (`ValueError` with the type and field name,
  never the payload).
- `CorporateActionsPage`: `actions`, `requested_at`, `received_at`, `symbols: int`, `window`,
  `pages: int`, `page_sha256: tuple[str, ...]` (digest of each raw page's JSON; no credentials,
  no query strings).
- `CorporateActionsSource(Protocol)`: `fetch(symbols, start, end) -> CorporateActionsPage`.
- `AlpacaCorporateActionsSource(client, *, pacer, chunk_size=100)`: chunks symbols, one SDK call
  per chunk under `observe_responses` for page digests, `limit=1000`; a failed chunk fails the fetch
  (no partial result). `transport/alpaca.py` gains `BoundedCorporateActionsClient(BoundedTransport,
  CorporateActionsClient)` beside the other bounded clients.
- Pure queries used by every consumer: `actions_for(actions, symbol)`, `upcoming(actions, symbol,
  today, horizon_days)`, `recent(actions, symbol, today, lookback_days)`.

### 3.2 Storage — `agentic_trader/storage/corporate_actions.py`

`CorporateActionsStore(store: WorkflowStore)` on the existing event store, no projection table, no
migration:

- `record_capture(capture) -> dict`: appends one event of the new `EventKind.CORPORATE_ACTIONS_CAPTURED`
  on stream `corporate-actions/capture` **only when** `capture["content_sha256"]` differs from the
  latest capture's; returns the current capture either way. The payload holds `captured_at`,
  `as_of` (New York date), `symbols_count`, `symbols_sha256`, `window` (`process_date` start/end),
  `actions` (normalised, sorted), `counts_by_kind`, `content_sha256` (over the sorted actions),
  `pages`, `requested_at`, `received_at`. Bounded at `max_actions` (5,000); beyond it the run fails.
- `latest_capture() -> dict | None` (`store.events("corporate-actions/capture", limit=1)`).
- `CorporateActionsLookup`: a frozen read model built from a capture plus a freshness verdict
  (`status: "ok" | "stale" | "unavailable"`, `as_of`, `actions`), constructed once per scan or
  report and passed down; no consumer reads the store on its own.

### 3.3 Worker — `agentic_trader/data/corporate_actions_worker.py`

`CorporateActionsWorker(source, store, symbols, *, config, clock)` with `run_once() -> str`
returning `"captured"`, `"unchanged"` or `"skipped: <reason>"`:

- Runs once per New York date, never inside a suggestion-scan window (reuses `in_scan_window` from
  the card-statistics worker), polled every `poll_seconds`.
- Symbols: the configured universe (`config.universe.symbols`), tracked active equity positions
  (`db.get_active_positions()`, contracts without a `/` prefix) and `extra_symbols`; capped at
  `max_symbols` (`ValueError` beyond it, so the operator sees it rather than a silent truncation).
- Window: `process_date` in `[today − lookback_days, today + lookahead_days]` (defaults 45 and 120
  calendar days; dividends pay within about 30 days of the ex-date, so ex-dates roughly 90 days
  ahead are covered).
- Failure: any chunk failure or normalisation error fails the run; nothing is recorded, readiness
  turns not ready with the exception type name, and the next poll retries. Partial captures never
  exist.
- Readiness: `HealthComponent.CORPORATE_ACTIONS`, age limit `config.corporate_actions.max_age_seconds`
  (default four days, spanning weekends and holidays like `card_stats`); `observe(component, True)` on
  `captured`/`unchanged`, `observe(component, False, type_name)` on failure. Enabled flag threaded
  like `card_stats_enabled`.
- Wiring in `cli/commands/service.py` beside `run_card_stats_worker`; the source is built from the
  same Alpaca keys the data provider uses (`alpaca_key_id` / `alpaca_secret_key` in `AppConfig`).

### 3.4 Config — `CorporateActionsConfig` on `AppConfig` as `corporate_actions`

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `True` | run the worker and read captures |
| `entry_block` | `"preview"` | `off` / `preview` / `enforce` for the entry rule |
| `pre_days` | `7` | a structural action effective within this many calendar days ahead blocks (or would block) |
| `post_days` | `5` | a structural action effective within this many calendar days behind blocks (or would block): raw bars straddle it |
| `notes_horizon_days` | `10` | `/positions` notes look this far ahead |
| `lookback_days` / `lookahead_days` | `45` / `120` | the `process_date` capture window |
| `max_symbols` / `chunk_size` | `600` / `100` | request bounds |
| `max_actions` | `5000` | capture bound |
| `poll_seconds` | `300` | worker poll |
| `max_age_seconds` | `345600` | readiness age limit and the staleness bound for every consumer |
| `extra_symbols` | `[]` | symbols beyond the universe and positions |

Validation: positive bounds, `pre_days`/`post_days` ≥ 0, symbols match `[A-Z][A-Z0-9.]{0,9}`, no
duplicates. `entry_block` uses the existing three-mode literal idiom (`BookSizingMode`,
`CardPolicyMode`).

### 3.5 Entry rule — `agentic_trader/risk/rules.py`

`corporate_action_block(actions: Sequence[CorporateAction], symbol: str, today: date, *, pre_days: int,
post_days: int) -> Rejection | None`, rule id `RiskRule.CORPORATE_ACTION`: the first structural
action on `symbol` with `today − post_days ≤ effective_on ≤ today + pre_days` refuses with
`Corporate action: {SYMBOL} {describe()} effective {YYYY-MM-DD} ({in n day(s) | n day(s) ago | today}) —
within the {pre}/{post}-day blackout.` Cash dividends never refuse. The one predicate behind the
evaluator gate, the tap gate and re-evaluate; admission does not apply it (as with
`earnings_blackout`).

Evaluator (`agent/evaluator.py`, a new step 2c after the earnings blackout, equities only): the scan
builds one `CorporateActionsLookup` and passes it in. `status != "ok"` → no refusal, provenance
`corporate_actions: {"status": "stale" | "unavailable"}`. Otherwise the rule runs: in `enforce` a
rejection is `_rejected(reason, RiskRule.CORPORATE_ACTION, …)`; in `preview` the card is unchanged
and provenance records `{"status": "ok", "mode": "preview", "would_block": true, "action": {...}}`;
with no blocking action, any **cash dividend** with an ex-date within `pre_days` is recorded as
`{"status": "ok", "note": {...}}` so the card can say so. The tap path
(`TradingCopilot._tap_gate_reason`) and re-evaluate apply the same predicate in `enforce` mode only,
from the lookup current at tap time; a stale or unavailable capture never refuses a tap.

Card line (`notifier/telegram_bot.py`, beside `_book_line`): `🏷 Corporate action: NVDA forward split
10-for-1 effective 2026-06-10 (in 3 days) — preview, card unchanged` / `🏷 Ex-dividend 2026-11-19
($0.98/share)`; nothing when the block has no action or note.

### 3.6 Positions notes — `TradingCopilot.get_positions_report`

With a fresh lookup, each broker position gets one note per action with `effective_on` in
`[today − post_days, today + notes_horizon_days]`: `XOM: cash dividend $0.99/share, ex 2026-11-12,
pays 2026-12-10.` or `NVDA: forward split 10-for-1 effective 2026-06-10 (3 days ago); tracked
quantity, stop and target are pre-split until reconciled.` A stale or unavailable capture adds the
single note `Corporate-action evidence {stale | unavailable}.` when the feature is enabled. Notes
render through the existing `notes` field in both the CLI table and the Telegram HTML.

### 3.7 Ledger — `accounting/ledger.py`

`reconcile(activities, snapshot, *, cash_tolerance, corporate_actions: Sequence[CorporateAction] = ())`:
for an unsupported activity that names a symbol, if a structural action on that symbol has
`effective_on` within ten days of the activity's date, the issue reads `Unsupported activity type:
{kind} ({describe()} {SYMBOL} effective {date} in corporate-action evidence)`; otherwise the current
text. Totals stay withheld. `AccountLedgerService` takes an optional `CorporateActionsStore` and
passes the latest capture's actions; without one, behaviour is unchanged.

### 3.8 Research acquisition — `agentic_trader/research/corporate_actions.py` and CLI

`fetch_corporate_actions_cached(source, symbols, start, end, cache_dir, *, pace) -> tuple[tuple[CorporateAction, ...], dict]`
writes `cache_dir/corporate-actions/<sha256 of the request>.json` (normalised actions and receipt)
and verifies the recorded content digest on reuse, mirroring the bar cache. CLI
`copilot alpha corporate-actions --symbol X … | --cohort config/research/…json --start YYYY-MM-DD
--end YYYY-MM-DD --output NEW_PRIVATE_DIR` writes `actions.json` and `manifest.json` (request hash,
content SHA-256, counts by kind, code revision). Research only: no database, registry, broker or
Telegram.

## 4. Failure behaviour

| Situation | Worker | Entry rule | `/positions` | Ledger |
| --- | --- | --- | --- | --- |
| Provider chunk fails / normalisation error | run fails, nothing recorded, readiness false (type name) | last good capture while fresh | last good capture while fresh | last good capture |
| No capture yet / capture older than `max_age_seconds` | — | never refuses; provenance `unavailable`/`stale` | one evidence note | plain unsupported text |
| Capture exceeds `max_actions` or symbols exceed `max_symbols` | run fails (`ValueError`) | as above | as above | as above |
| `enabled: false` | not started; readiness has no component | step skipped, no provenance | no notes | plain text |

Nothing here touches the broker, the halt state, sizing or admission.

## 5. Testing

- Normalisation: fixtures for all thirteen shapes (the public 2024 AAPL/NVDA/WMT/CMG/XOM sample and
  synthetic records for the rarer kinds), the `effective_on` mapping per kind, `price_factor`,
  `describe`, unknown type key and missing date failures.
- Source: a fake SDK client returning pages; chunking, page digests, a failing chunk fails the fetch.
- Store: first capture appends; identical content does not; changed content appends; `latest_capture`
  ordering; the `max_actions` bound.
- Worker: once per New York date, skips inside a scan window, symbol union and cap, window arithmetic,
  readiness observations on success and failure.
- Rule: boundaries at `−post_days`, `0`, `+pre_days`, one day outside each; cash dividends never
  refuse; reason text.
- Evaluator: `off`/`preview`/`enforce` on a blocking action, a dividend note, stale and unavailable
  lookups; provenance shape. Tap gate: enforce refuses, preview does not, stale does not.
- Card line, positions notes, ledger issue text, research cache round-trip and digest check, config
  validation, `/readyz` component with age limit.
- Full suite, pre-commit, no migration diff.

## 6. Operations

After the controlled restart the worker runs on its first poll outside a scan window;
`/readyz` gains `corporate_actions`; `copilot db events --stream corporate-actions/capture --limit 1`
shows the capture. `entry_block` ships `preview`: cards carry the line and provenance for about a
week, then the operator decides on `enforce` (the same `off → preview → enforce` path as `card_policy`
and `book_sizing`).

## 7. Design decisions

1. **One capture event per content change, no projection table.** Keeps schema head 008, bounds
   storage to a few hundred events a year, and gives every consumer the same read model. Costs a
   ~150 KB payload per change; if the universe grows past the bound, the worker fails visibly.
2. **Window on `process_date`, measured not assumed.** The provider filters on `process_date`;
   `lookahead_days` 120 covers dividends' ex-dates about 90 days ahead. If Alpaca changes the filter
   semantics the worker still works, the horizon merely shifts.
3. **Preview by default, stale never refuses.** Matches the desk's `preview → enforce` convention and
   the card-policy rule that partial, stale or missing evidence never withholds. Costs: for about a
   week a card across a split is only annotated.
4. **Structural actions block; cash dividends annotate.** A regular dividend moves the price by about
   the dividend and the bracket survives; a split, merger or name change invalidates the bar history
   or the instrument. Special dividends are treated as ordinary (annotated) in part 1.
5. **The ledger explains, it does not resolve.** Exact split/merger activity semantics need observed
   paper activities and replay fixtures (the architecture review's words); guessing them would break
   the "never infer" contract. The issue text names the action so the operator knows why totals are
   withheld.
6. **Raw bars stay raw.** The capture first measures how often the lookback of a universe name
   straddles a split; switching adjustment is a separate, measured change.
7. **Research acquisition reuses the normaliser and the bar cache's digest discipline** so part 2's
   catalog entry can pin the event file's SHA-256 in its manifest exactly as PEAD pins the Nasdaq pages.

## 8. Part 2 (separate spec)

Candidate event legs from this data, each a frozen a priori catalog entry with its own A/C-style
checks where they apply: the ex-dividend day effect and dividend-month premium (Elton–Gruber 1970;
Lakonishok–Vermaelen 1986; Hartzmark–Solomon 2013), post-split drift (Ikenberry–Rankine–Stice 1996),
and the post-reverse-split short (Kim–Klein–Rosenfeld 2008). The choice waits for the counts the
part-1 acquisition produces on the research cohort over 2016–2026.
