# A priori alpha catalog, entry 1: post-earnings drift study (Part 1)

**Status:** design approved in conversation 2026-09-25; awaiting written-spec review.
**Scope:** research only. No live behaviour, schema, registry or Telegram change.

## Why

Every Telegram card comes from the two native strategies, whose measured edge is thin
([setup outcomes](../../setup-outcomes-2026-09-23.md),
[short-suppression test](../../setup-baserates-short-2026-09-23.md)). Mined alphas
cannot qualify: the search is broad and each symbol has few trades, so planted-signal
power is 0/64. An **a priori alpha** takes the opposite trade-off: one
literature-documented hypothesis per leg, frozen before any data is read, tested
across thousands of events pooled over many names. Post-earnings-announcement drift
(PEAD) is the first catalog entry. It is event-driven (independent of the price-pattern
strategies), yields a legible thesis per card, and produces many candidates during
earnings season.

Honest prior: recent work reports that PEAD has largely decayed in liquid US equities
(e.g. Martineau, 2022, "Rest in Peace Post-Earnings Announcement Drift"). A failed leg
is an acceptable, informative outcome.

## Decisions already made (operator, 2026-09-24/25)

| Topic | Decision |
| --- | --- |
| Gate | A frozen historical study gates cards; a passing leg later runs as a capped live probe (Part 2). |
| Universe | Any liquid US reporter from the Nasdaq calendar, screened like the dynamic universe. |
| Exit | Hybrid: protective stop, wide target and an N-session time exit, whichever comes first. |
| Signal | Surprise and price reaction must agree. |
| Legs | Long and short are tested separately; each has its own pass rule. |
| Approach | A small catalog lane (approach 1). Part 1 = this study; Part 2 = live lane, built only for a passing leg. |

## Catalog entry

`config/research/apriori/pead-v1.json` is committed before any data is read. Its
SHA-256 is recorded in the run manifest; a changed file is a new version (`pead-v2`),
never an edit. Fields:

- `id` (`"pead"`), `version` (1), `title`, `references` (literature, free text),
  `hypotheses` (prose per leg, as in `setup-baserates-short-v1.json`), `decision_rule`;
- `window`: `decisions: ["2016-03-01", "2026-07-31"]`, `bars_through: "2026-09-01"`,
  `recent_from: "2023-01-01"`;
- `feed: "alpaca:sip"`, `adjustment: "all"`;
- `event`: `min_abs_forecast: 0.05`, `min_estimates: 1`, `surprise_pct: 5.0`,
  `reaction_sigma: 1.0`, `vol_window: 20`, `benchmark: "SPY"`;
- `universe`: `min_price: 10.0`, `dollar_volume_window: 20`,
  `static_percentile: 0.25`, `liquidity_adjustment: "raw"` (the static universe is the
  configured equity contracts, recorded in each result; see "Liquidity");
- `trade`: `decision_time_et: "10:35"`, `entry_session_offset: 2`,
  `stop_atr_multiple: 2.0`, `atr_window: 14`, `target_r: 3.0`, `max_hold_sessions: 20`,
  `secondary_hold_sessions: 60`;
- `costs_bps_per_side: [0.0, 5.0]` (5.0 decides);
- `bootstrap`: `block_mean: 10`, `draws: 2000`, `seed: 20260925`, `ci: 0.90`, one-sided;
- `pass_rule`: `min_events: 300`, `min_recent_events: 100`, `trim_fraction: 0.01`;
- `max_failed_calendar_fraction: 0.02`, `max_empty_session_fraction_per_year: 0.10`,
  `calendar_request_interval_seconds: 1.0`.

Version 1 was amended once, before any data was read, after the final code review
(2026-09-25): `liquidity_adjustment` and `max_empty_session_fraction_per_year` were added
and the surprise is rounded to 9 decimals. Its SHA-256 changed accordingly; it has never
been run.

## Data

### Earnings events

- Source: the Nasdaq earnings calendar endpoint already used by the earnings blackout
  (`NASDAQ_EARNINGS_CALENDAR_URL`, `agent/earnings.py`). One request per weekday in the
  decision window (≈2,700), paced at ≤1 request/second, bounded retries on GET only.
  A page is accepted only with HTTP 200, a JSON object body and Nasdaq's own
  `status.rCode == 200`; a 200 response with another (or no) `rCode` is a soft error,
  retried and then recorded as a failed date, never saved as an empty day.
- Every raw page is saved under the run's private output directory with its SHA-256 and
  receipt time. Re-runs reuse saved pages (checked against their hashes) and never
  re-fetch a saved date.
- Fail closed on a gappy sample: more than `max_failed_calendar_fraction` (2%) failed
  dates, or, in any calendar year, accepted pages with zero rows on more than
  `max_empty_session_fraction_per_year` (10%) of that year's requested observed trading
  sessions (an empty session page cannot be told apart from a truncated one; empty
  weekend/holiday pages do not count). Empty session pages per year are reported.
- Parsed fields per row: `symbol`, `date` (the calendar date), `eps`, `eps_forecast`,
  `surprise_pct_reported`, `n_estimates`, `fiscal_quarter`. `marketCap` is ignored
  (current, not point-in-time). `time` is ignored (historically always
  `time-not-supplied`).
- One parser shared with the live blackout: extend `agent/earnings.py`'s row parsing to
  read EPS, forecast, surprise and estimate count (the blackout keeps using date and
  timing only). No duplicate parser.
- Surprise used by the rule is recomputed:
  `surprise_pct = 100 × (eps − eps_forecast) / |eps_forecast|`, defined only when both are
  present, `|eps_forecast| ≥ 0.05` and `n_estimates ≥ 1`, rounded to 9 decimal places so
  cent-granular EPS lands exactly on the ±5% boundary (`100 × (0.21 − 0.20)/0.20` is
  `4.99999999999999` in binary floating point). Nasdaq's own `surprise` field is
  a cross-check reported as a diagnostic (count of sign disagreements).
- Rows with the same symbol and date are de-duplicated (first row kept, count reported).

### Prices

- Alpaca SIP, adjustment `all`: daily bars (reaction, volatility, ATR) for every event
  symbol, SPY and the static universe equities, fetched once per symbol over the whole
  window. The liquidity gate instead reads **raw (unadjusted)** daily bars, fetched the
  same way for every event symbol and static equity (not SPY) and cached separately
  (`bars_raw`): see "Liquidity". Hourly bars (decision price and bracket labelling) only for symbols
  with at least one liquid event, over that symbol's event span. Reuse the setup study's
  bar cache (`research/setups/runner.py`, `.npz` datasets with range claims). The bar
  evidence store is not used (the cache files and their digests are the study's bar
  artifact; the raw evidence for thousands of symbols would be many gigabytes).
- Symbols outside `^[A-Z]{1,5}$` (share classes, units, warrants) are counted as
  unsupported, not fetched.
- An event whose symbol has no bars covering its window is **missing**, not dropped
  silently: missing counts are reported by year and leg (survivorship/renames).

### Liquidity (point in time)

As the live gate computes it at the decision: `as_of` = D+1, the last session completed
before the D+2 decision. The symbol's `median_dollar_volume(daily, as_of)` (median
close × volume over the 20 completed sessions through `as_of`) must be at least
`static_reference(static_daily, 0.25, as_of=as_of)` over the static universe equities
(configured contracts whose asset class is equity), and the `as_of` close must be ≥ $10.
Both functions are the live dynamic-universe code (`screeners/dynamic_universe.py`),
reused, not reimplemented. A date without a reference (fewer than its minimum names) is
skipped and counted.

The gate (the `$10` floor, the symbol's median dollar volume and the static reference)
uses **raw (unadjusted) daily bars**, as a live scan saw prices at the time. With
adjustment `all`, historical closes are divided by every later split (2016 NVDA is ≈$1)
and inflated by later reverse splits, so an adjusted floor and, slightly, an adjusted
dollar-volume screen would depend on future corporate actions — lookahead. Reaction,
σ, ATR, levels and labels keep the adjusted bars. A symbol without raw bars is counted
`no_daily_bars`; one without a raw `as_of` close is counted `no_raw_close`.

The result records the static universe actually used (`universe.static_symbols`, sorted,
and the SHA-256 of their newline-joined list), the configured static equities without raw
bars with their failure reasons, and the number of reference names per evaluated `as_of`
date (min/median/max and the full per-date map).

## Event and trade rule

For a report with calendar date D (sessions on the observed NYSE calendar):

0. **Session date.** D must be an observed trading session; a report dated on a
   non-session day is skipped and counted.
1. **Reaction.** `r_i = close(D+1)/close(D−1) − 1`, `r_m` likewise for SPY.
   `σ_i` = standard deviation of daily log returns over the 20 sessions ending D−1.
   `z = (r_i − r_m) / (σ_i × √2)`. The window covers both pre-open and after-close
   reports because the report time is unknown.
2. **Legs.**
   - Long: `surprise_pct ≥ +5` and `z ≥ +1`.
   - Short: `surprise_pct ≤ −5` and `z ≤ −1`.
   - Control (for P2): every liquid event with the needed prices, regardless of surprise
     and reaction, labelled once as a long bracket and once as a short bracket.
3. **Decision.** Session D+2 at 10:35 New York, the live suggestion-scan clock. Decision
   price = the close of the last regular-session hourly bar that ends at or before 10:35
   on D+2 (the 09:00 bar).
4. **Levels.** `ATR14` = the simple mean of the 14 true ranges ending D+1 (daily bars). Long: stop = price − 2×ATR, target =
   price + 3×(2×ATR). Short mirrored.
5. **Label.** `label_bracket(levels, decision_at, hourly, max_hold_sessions=20,
   cost_bps_per_side=c)` (`research/setups/labels.py`): entry at the open of the first
   regular-session hourly bar at or after the decision, conservative same-bar tie
   (stop), gap fills at the open, TIMEOUT marks to the close of the 20th session.
   `IMMATURE` rows (insufficient bars) are dropped and counted.

## Statistics and pass rule (per leg)

Unit: `R_cost` per event at 5 bps per side (the 0 bps result is reported alongside).
Confidence intervals: stationary session-block bootstrap over decision sessions (block
mean 10, 2,000 draws, seed 20260925). As in the short-suppression test, `ci90` is the
[5th, 95th] percentile interval and a criterion "holds" when its lower bound is above
zero (a one-sided test at the 5% level). Reuse the setup study's bootstrap
(`session_block_bootstrap`, the paired variant in `research/setups/baserates.py`).

A leg **passes** only if all hold:

- **P1 edge.** Mean `R_cost` > 0 and the CI lower bound > 0.
- **P2 signal adds value.** Mean `R_cost(leg) − mean R_cost(control, same side)` > 0,
  CI lower bound > 0, with a paired draw that resamples whole sessions once for both
  means (as S2 in the short-suppression test).
- **P3 robustness.** The 1%-trimmed mean `R_cost` (each tail) > 0, and the mean `R_cost`
  over decisions from 2023-01-01 is > 0.
- **P4 sample.** At least 300 labelled events overall and at least 100 from 2023-01-01.

Decision rule: a passing leg becomes eligible for Part 2 as a capped paper probe. A
failing leg stays off; the result is written up and the catalog entry is marked
`failed` for that leg. No threshold is revisited after the run; any change is a new
version with a new, non-overlapping window.

### Descriptive diagnostics (never decisive)

Surprise-only and reaction-only rules; a 60-session hold; results by year and by liquidity tercile; target/stop/timeout shares;
missing-bar counts by year; Nasdaq-vs-recomputed surprise sign disagreements; the
distribution of events per session (earnings-season clustering).

## Code layout

- `agentic_trader/research/apriori/__init__.py`
- `catalog.py`: `AprioriEntry` (frozen, validated) loaded from JSON with its SHA-256;
  rejects unknown fields.
- `earnings_history.py`: paced historical calendar acquisition, raw-page artifact store,
  parsed `EarningsReport` rows (using the shared parser from `agent/earnings.py`).
- `pead.py`: event construction (reaction, liquidity, legs, control), levels, labelling
  via `label_bracket`, statistics, pass rule, result assembly.
- CLI: `copilot alpha apriori-study PROTOCOL --output NEW_PRIVATE_DIRECTORY`
  (`cli/commands/`), refusing an existing non-empty output directory, as other studies do.
- Run order: write `protocol.json` (copy) and `manifest.json` (entry id/version, SHA-256,
  code revision, `authorizes_promotion: false`) **before** any provider access; then
  events, bars, labels (`events.csv.gz`, `labels.csv.gz`; no parquet engine is a
  dependency), then `result.json`. More than 2% failed calendar dates, or more than 10%
  empty trading-session pages in any calendar year, fails the run closed
  (`status: failed`) rather than testing on a gappy sample. Missing labels are counted
  by reason, year and leg.
- No database writes, no registry or trial credit, no notifications, no broker access.

## Documentation (same PR; no docs-only PR)

- `docs/apriori-alphas.md`: the catalog index (entry, version, status per leg, links).
- `docs/apriori-pead-<run date>.md`: the result write-up (protocol, scale, P1–P4 table
  per leg, diagnostics, caveats, decision).
- `docs/alpha-roadmap.md`:
  - the catalog lane and this result;
  - a later-work item (operator, 2026-09-25): research how quant practice overcomes
    sparse per-symbol trade histories in alpha mining (pooled/cross-sectional panel
    estimation, hierarchical or shrinkage estimators, meta-labeling, event pooling,
    cross-asset transfer) and design ways to unblock the mining funnel's 0/64 power.
- `CLAUDE.md`: one short paragraph pointing to the catalog and its research-only status.

## Testing

- Parser: small hand-made fixture pages (a normal row, missing EPS, negative forecast,
  tiny forecast, `N/A` estimates, duplicate rows), shared by the blackout's tests.
- Acquisition: pacing, hash-checked reuse, GET-only bounded retries, no re-fetch of
  saved dates — with a fake transport; no network in tests.
- Event rule: reaction window across a weekend/holiday, σ from pre-event sessions only
  (no lookahead), liquidity percentile on the decision date, `$10` floor, leg boundaries
  exactly at ±5% and ±1σ, missing-bar accounting.
- Levels/labels: long and short levels from ATR; delegation to `label_bracket`.
- Statistics: P1–P4 on synthetic frames with known answers, including a trimmed-mean
  failure and a recent-window failure; paired control draw.
- CLI: manifest written before any provider call; refuses a non-empty output directory.

## Runtime estimate

≈2,700 calendar requests (≈45–60 minutes at one per second); one adjusted daily request
per event symbol (several thousand, ≈45 minutes at 150 requests/minute) and one raw daily
request per event symbol and static equity for the liquidity gate (≈45 minutes more);
hourly bars in one-year chunks for the symbols with a liquid event (≈2k symbols, ≈2–3
hours). Roughly 5–6 hours end to end, all cached for re-runs.

## Caveats

- **Survivorship:** delisted names are absent from Nasdaq's history and often from
  current bar sets; this flatters the long leg and understates the short leg. Missing
  events are counted, not hidden.
- **Report time unknown historically:** entry waits until D+2, which forgoes any
  day-one drift and matches what the live lane could do with the same data.
- **Consensus and EPS values** are as served today, not guaranteed point-in-time.
- **Ticker changes** can lose events (counted as missing).
- **No sector-relative reaction:** Nasdaq rows carry no sector and there is no
  point-in-time sector for non-universe names, so the reaction is measured against SPY
  only.
- **Hourly-bar entry** at the first bar at or after 10:35 follows the setup-study
  convention; it is not a fill model.

## Part 2 (outline only; not in this spec)

Built only for a passing leg: a live earnings-event source feeding the dynamic-universe
path (new `source`), an `EarningsDrift` strategy emitting candidates with a frozen
catalog identity, a time-exit policy on those cards enforced by the existing
lifetime/close services, probe-style caps, and tolerance of the unofficial calendar
endpoint's outages. It gets its own spec.
