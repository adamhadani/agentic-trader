# Wider scan universe and a daily suggestion budget — design

Status: implemented September 22, 2026 (designed September 21). Serves the desk's overarching goal: **one or
two reasonable trade suggestions per market session in Telegram**, executable on the
Alpaca paper account.

## Problem

The live scan covers 13 instruments with two built-in strategies and fires every four
hours measured from daemon start. Fifteen suggestions were recorded in a week, twelve
of them on one day; the last two sessions produced none, and the daemon log shows no
candidates evaluated at all. Supply is limited by trigger rate, not by gates: a
`PENDING` card reserves nothing, and `max_concurrent_positions` and the stop-risk
budget are enforced only at Execute (`execution/admission.py`).

## Evidence (throwaway spike, September 21)

The real `StrategyEngine` and indicator code were replayed on truncated history for the
last 25 sessions at two fixed scan times (10:35 and 14:35 New York), deduplicated to
one candidate per symbol, strategy and session. yfinance bars; no risk, macro or LLM
gates, so these are upper bounds on cards.

| Universe | Names | Mean per session | Median | Sessions with none | Sessions with ≥ 2 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Today's equities and ETFs | 9 | 0.76 | 0 | 14 / 25 | 6 |
| ETF32 | 32 | 1.92 | 1 | 4 | 12 |
| Mega-caps | 65 | 4.64 | 4 | 0 | 23 |
| ETF32 + mega-caps | 97 | 6.56 | 5 | 0 | 23 |

Of 164 candidates, 124 were trend-pullback on the 4-hour frame and 39 squeeze breakout
on the 1-hour frame; 110 long and 54 short; 122 appeared at the morning scan. With
about six candidates a session, a budget of two is always fillable with selection
headroom, so ranking quality matters more than raw supply.

## Decisions taken with the operator

- Universe: **ETF32 + about 65 mega-caps + the 64-name screened research cohort**
  (about 160 names). The cohort aligns live scanning with the research panel; its
  thinner names are handled by the data-quality gate below.
- Paper caps: `max_concurrent_positions` 4 → **8** and `sizing.max_trade_notional_cap`
  $30,000 → **$7,500**; the $60,000 notional ceiling, asset-class caps and the 2 %
  aggregate stop-risk budget are unchanged.
- Budget scope: `--no-budget` records every approved candidate; dry runs always run
  without a budget.

## Goal and non-goals

Deliver a steady, bounded flow of ranked suggestions from the existing strategies over
a wide universe, without degrading the daemon.

Non-goals: new strategies; mined alphas or probes; session-bounded entries for built-in
strategies (follows the entry-expiry work); backtester changes; any change to
admission, brackets, sizing formulae, halt behaviour or the macro lockout.

## Design

### 1. Universe (`config.py`, `config/config.yaml`)

A new validated `universe:` key holds named groups of entries `{symbol, sector}`.
`load_config` expands each into an equity `ContractConfig` (`asset_class: equity`,
default tick and multiplier) unless `contracts:` already defines that symbol, which
wins. Validation at load: no duplicate symbol across groups; symbols match
`^[A-Z][A-Z.]{0,5}$`; no symbol that `market/session.py:_resolve_provider` would route
to the crypto provider (it matches by `BTC`/`ETH`/`SOL`/`DOGE` prefix); at most 250
names. `sector` becomes the symbol's correlation group, merged with the configured
`correlation_groups`, because an unlisted symbol currently matches no group and escapes
`max_correlated_positions` entirely. Futures stay in `contracts:` as they are.

Sectors are authored once from yfinance metadata and reviewed by eye; a name whose
sector is unknown joins no correlation group.

### 2. Session-aligned suggestion scans (`cli/commands/service.py`)

Two APScheduler **cron** jobs in `America/New_York`, default 10:35 and 14:35
(`scheduler.suggestion_scan_times_et`), running `run_scan(asset_class="equity")` only on
trading days (existing `CompositeMarketCalendar`). The times follow completed 09:30 and
13:30 hourly bars, and both leave a card's four-hour validity inside the regular
session that broker admission requires. The existing interval scan is kept for futures
but no longer fetches equities when the equity session is closed; today it would spend
every overnight scan fetching the whole universe to have each candidate rejected by the
per-candidate session gate. The 15-minute intraday job has no symbol scope of its own
today; it scans every configured contract. It is therefore restricted to the contracts
configured explicitly under `contracts:` (`AppConfig.non_universe_contracts`), so
widening the universe does not multiply its request rate.

The interval swing scan continues to cover the whole universe whenever it lands inside
the equity session; the budget governs what it may send.

### 3. Fetch scaling and visibility (`data/market_data.py`, `agent/copilot.py`)

- Suggestion scans call `fetch_data(include_fifteen_min=False)`: the built-in
  strategies read daily, 1-hour and resampled 4-hour frames only. Two calls per name.
- Fetches run with bounded concurrency (`market_data.scan_concurrency`, default 8) and a
  request pacer (`market_data.max_requests_per_minute`, default 150, below the 200 per
  minute IEX limit). About 320 calls take a little over two minutes.
- A symbol whose fetch fails or returns an empty frame is **counted and logged**, never
  silently dropped; the scan summary reports `scanned`, `failed` and the first failures.
- `scan_duration_seconds` histogram and a `scan_completed` log event with counts.
- The unused `MarketDataFetcher._cache` field is removed rather than half-implemented.

### 4. Data-quality gate (`agent/copilot.py`)

Before strategies run, a name must have at least `scan.min_bar_coverage` (default 0.8)
of SPY's count of hourly bars with positive volume over the last 10 sessions in the
same scan. This is feed-agnostic; an absolute dollar-volume threshold is wrong here
because IEX bars report IEX-only volume. Excluded names are counted in the summary.

### 5. Suggestion budget (`agent/copilot.py`, `screeners/`, `storage/db.py`)

The scan changes from "record each approved candidate inline" to three phases:

1. **Collect** every candidate that survives conflict resolution, deduplication and the
   deterministic evaluation (`evaluate_candidate(use_llm=False)`), which already applies
   sizing, exposure, correlation, macro and session gates.
2. **Rank** by `setup_quality`, a new `ScreenerCandidate` field in [0, 1] that each
   strategy computes from quantities it already has:
   - trend-pullback: mean of trend strength (`(EMA_fast − EMA_slow) / EMA_slow`, capped
     at 10 %), proximity (`1 − distance_to_trigger / (tolerance × ATR)`), and RSI
     recovery (`(RSI_now − RSI_extreme) / 15`, capped);
   - squeeze breakout: mean of squeeze length (`bars / 20`, capped) and volume expansion
     (`(volume / SMA20 − 1.3) / 1.7`, capped).
   Ties break by symbol for determinism. Alpha and probe candidates keep their place in
   the ranking through `|alpha_score|` mapped to [0, 1] by `min(|z| / 3, 1)`.
3. **Send** the top candidates subject to `scan.max_cards_per_scan` (default 1),
   `scan.max_cards_per_session` (default 2) and at most one card per correlation group
   per session. Only these are re-evaluated with the LLM and recorded, so LLM cost is
   flat in universe size. If the LLM rejects one, the next-ranked candidate takes its
   place, up to `scan.max_llm_evaluations_per_scan` (default 4).

The per-session count is durable and derived, not stored: signals with
`timestamp >=` the start of the current New York trading day, in this environment and
execution mode, excluding quarantined rows. A restart cannot reset the budget.
Ranking before recording also removes today's arbitrary behaviour where exposure
budget is consumed in alphabetical scan order. Operator-initiated `copilot scan` and
`/scan` bypass `max_cards_per_scan` but respect the per-session limit unless
`--no-budget` is passed.

`setup_quality` is a transparent prioritisation heuristic, **not validated alpha**. It
is stored in `decision_provenance` with the rank and the number of candidates it beat,
so its usefulness can be measured against realised outcomes later.

### 6. End-of-session digest

One durable outbox notice after the last suggestion scan of the day: candidates found,
cards sent, runners-up with their scores, names excluded by the coverage gate, fetch
failures and scan durations. A quiet day still tells the operator the desk is alive.

### 7. Paper caps (`config/config.yaml`)

`portfolio.max_concurrent_positions: 8`, `sizing.max_trade_notional_cap: 7500`. No code
change. Total notional, asset-class caps and the stop-risk budget are unchanged.

## Error handling

A failed fetch excludes that name from this scan only and is reported. If SPY itself
cannot be fetched the coverage gate is skipped and the digest says so. An LLM outage
falls back to the existing deterministic approval, so cards still go out. The cron jobs
use `coalesce` and `max_instances=1`; a scan still running at the next trigger is
skipped and logged. The trading halt continues to block scans entirely.

## Testing (TDD)

Config expansion and every validation failure; correlation group merge. Pacer and
bounded concurrency with a fake provider and clock; failure accounting. Coverage gate
including the SPY-missing case. `setup_quality` bounds and monotonicity per component.
Budget: ranking order, per-scan and per-session limits across two scans, restart
(derived count), one-per-group, LLM-reject fallback, operator bypass, halt. Scheduler:
jobs registered at the configured New York times and skipped on non-trading days.
Digest content. Existing scan, dedup, probe-tagging and account-risk tests pass
unmodified. Deployment verification is separate evidence.

## Rollout

Deploy with the full universe but `max_cards_per_session: 2`. Watch the first two
sessions' digests for scan duration, failures and coverage exclusions before changing
anything. Rollback is a config edit (`universe: {}` restores today's 13 instruments).

## Implementation notes (September 22)

Deviations from the design above, ruled during execution and now the built behaviour:

- The coverage gate applies to **strategy scanning only**. A thin name is still fetched
  and still recorded by alpha-shadow observation; only `scan_contract` is skipped for it.
- The LLM evaluation budget (`max_llm_evaluations_per_scan`) applies only when the LLM
  is in use. A `--no-llm` scan and a dry run are bounded by the card budget alone.
- Send-phase failures are counted as scan errors and do **not** consume the card budget:
  a card that was never recorded leaves `remaining_session` untouched.
- The interval swing scan remains. It covers the universe whenever it lands inside the
  equity session and can therefore spend the session budget before the digest is
  published; it is not a second suggestion schedule, and its cards count the same.
- The digest is in-memory per daemon run (`_session_scan_stats`, trimmed to the current
  ET date). A mid-session restart truncates it; the *budget* itself is unaffected,
  because it is derived from the signals table.
- `summary["approved"]` counts deterministic approvals eligible for ranking, not cards
  sent. The budget, the send-phase LLM evaluation and send failures all thin it down.

Post-review fix wave (September 22):

- Every summary carries `scope` (`asset_class`, `timeframe`, `restricted`). The digest
  aggregates **universe suggestion scans only** — no timeframe filter and no symbol
  restriction — so the 15-minute intraday job and `--symbols` scans are excluded; if
  only excluded scans ran, the digest says no suggestion scans ran. The digest also
  reports the scanned and insufficient totals, and a non-empty selection that scanned
  nothing degrades `scan` readiness instead of reporting healthy.
- An empty `scheduler.suggestion_scan_times_et` is now valid and disables the cron
  suggestion scans (and therefore the digest); all other validation is unchanged. The
  intraday job likewise registers nothing when no explicit contracts are configured,
  because `run_scan(symbols=[])` would otherwise select the whole universe.
- The copilot's read pool is sized `2 * scan_concurrency + DEFAULT_READ_WORKERS`: the
  primary leg can hold every scan slot for its full timeout while the fallback leg and
  the one-minute position monitor still need slots.
- The evaluator's correlation check and Execute admission remain deliberately
  different: admission counts any position in the group, the evaluator only
  same-direction ones, because `test_correlation_group_opposite_direction_allowed`
  pins the opposite-direction hedge as allowed at suggestion time. Sector groups now
  cover every equity, so the gap is bounded by `portfolio.max_correlated_positions: 2`
  in the paper config and documented in production.md rather than closed in code.

## Follow-ups (not in this change)

Session-bounded entries for built-in strategies; evaluating `setup_quality` against
realised R; universe-wide mined alphas over the same names; fixing the crypto-prefix
provider heuristic rather than validating around it; the interval scan's drift from
candle closes.
