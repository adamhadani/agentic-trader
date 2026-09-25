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
  `static_percentile: 0.25`, `static_universe: "config/config.yaml contracts (equities)"`;
- `trade`: `decision_time_et: "10:35"`, `entry_session_offset: 2`,
  `stop_atr_multiple: 2.0`, `atr_window: 14`, `target_r: 3.0`, `max_hold_sessions: 20`;
- `costs_bps_per_side: [0.0, 5.0]` (5.0 decides);
- `bootstrap`: `block_mean: 10`, `draws: 2000`, `seed: 20260925`, `ci: 0.90`, one-sided;
- `pass_rule` per leg (below); `secondary` diagnostics list (below);
- `sector_etf` map (copied from `setup-outcomes-v1.json`) for the sector-relative diagnostic.

## Data

### Earnings events

- Source: the Nasdaq earnings calendar endpoint already used by the earnings blackout
  (`NASDAQ_EARNINGS_CALENDAR_URL`, `agent/earnings.py`). One request per weekday in the
  decision window (≈2,700), paced at ≤1 request/second, bounded retries on GET only.
- Every raw page is saved under the run's private output directory with its SHA-256 and
  receipt time. Re-runs reuse saved pages (checked against their hashes) and never
  re-fetch a saved date.
- Parsed fields per row: `symbol`, `date` (the calendar date), `eps`, `eps_forecast`,
  `surprise_pct_reported`, `n_estimates`, `fiscal_quarter`. `marketCap` is ignored
  (current, not point-in-time). `time` is ignored (historically always
  `time-not-supplied`).
- One parser shared with the live blackout: extend `agent/earnings.py`'s row parsing to
  read EPS, forecast, surprise and estimate count (the blackout keeps using date and
  timing only). No duplicate parser.
- Surprise used by the rule is recomputed:
  `surprise_pct = 100 × (eps − eps_forecast) / |eps_forecast|`, defined only when both are
  present, `|eps_forecast| ≥ 0.05` and `n_estimates ≥ 1`. Nasdaq's own `surprise` field is
  a cross-check reported as a diagnostic (count of sign disagreements).
- Rows with the same symbol and date are de-duplicated (first row kept, count reported).

### Prices

- Alpaca SIP, adjustment `all`: daily bars (reaction, volatility, ATR, liquidity) and
  hourly bars (decision price and bracket labelling) for every event symbol, SPY, the
  sector ETFs and the static universe equities. Reuse the setup study's bar cache
  (`research/setups/runner.py`, `.npz` datasets with range claims).
- An event whose symbol has no bars covering its window is **missing**, not dropped
  silently: missing counts are reported by year and leg (survivorship/renames).

### Liquidity (point in time)

On the decision date: the symbol's median daily dollar volume (close × volume) over the
20 sessions ending D−1 must be at least the 25th percentile of the same statistic across
the static universe equities on that date, and the D−1 close must be ≥ $10. This mirrors
the live dynamic-universe rule (`liquidity_gate`, `min_price: 10`) on the study's own
feed.

## Event and trade rule

For a report with calendar date D (sessions on the observed NYSE calendar):

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
   price = the last regular-session hourly close before 10:35.
4. **Levels.** `ATR14` from daily bars through D+1. Long: stop = price − 2×ATR, target =
   price + 3×(2×ATR). Short mirrored.
5. **Label.** `label_bracket(levels, decision_at, hourly, max_hold_sessions=20,
   cost_bps_per_side=c)` (`research/setups/labels.py`): entry at the open of the first
   regular-session hourly bar at or after the decision, conservative same-bar tie
   (stop), gap fills at the open, TIMEOUT marks to the close of the 20th session.
   `IMMATURE` rows (insufficient bars) are dropped and counted.

## Statistics and pass rule (per leg)

Unit: `R_cost` per event at 5 bps per side (the 0 bps result is reported alongside).
Confidence intervals: stationary session-block bootstrap over decision sessions (block
mean 10, 2,000 draws, seed 20260925), one-sided 90%. Reuse the setup study's bootstrap
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

Surprise-only and reaction-only rules; a 60-session hold; reaction relative to the
sector ETF; results by year and by liquidity tercile; target/stop/timeout shares;
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
  events, bars, labels (`events.parquet`, `labels.parquet`), then `result.json`.
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

≈2,700 calendar requests (≈45–60 minutes paced) plus daily and hourly SIP bars for
roughly 2–3k symbols (≈1 hour), cached for re-runs.

## Caveats

- **Survivorship:** delisted names are absent from Nasdaq's history and often from
  current bar sets; this flatters the long leg and understates the short leg. Missing
  events are counted, not hidden.
- **Report time unknown historically:** entry waits until D+2, which forgoes any
  day-one drift and matches what the live lane could do with the same data.
- **Consensus and EPS values** are as served today, not guaranteed point-in-time.
- **Ticker changes** can lose events (counted as missing).
- **Hourly-bar entry** at the first bar at or after 10:35 follows the setup-study
  convention; it is not a fill model.

## Part 2 (outline only; not in this spec)

Built only for a passing leg: a live earnings-event source feeding the dynamic-universe
path (new `source`), an `EarningsDrift` strategy emitting candidates with a frozen
catalog identity, a time-exit policy on those cards enforced by the existing
lifetime/close services, probe-style caps, and tolerance of the unofficial calendar
endpoint's outages. It gets its own spec.
