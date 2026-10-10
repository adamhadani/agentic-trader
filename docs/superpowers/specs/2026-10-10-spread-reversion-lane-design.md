# Spread-reversion lane (pairs v1) — design

Date: 2026-10-10. Desk-direction item 2 ([roadmap](../../alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8)).
Status: approved for implementation under the operator's standing "go with your recommended
approaches" instruction (2026-10-09); design decisions are listed in §11 for review with the PR.

## 1. Why

Three daily-bar lanes (DSL mining, pooled genetic, pooled fixed-set) found nothing on this cohort,
and the native funnel's own entries have no drift: the October 8 diagnostic and its October 10
refresh show runner-up target rates of 23–30% against the 33% a 2R bracket needs, with no stop,
target or time-exit geometry that turns the same entries positive. Every lane so far has tested a
*directional* label. Mean reversion of a cointegrated spread is a different label: market-neutral by
construction, with a literature record (Gatev, Goetzmann and Rouwenhorst 2006; Do and Faff 2010) of
decaying but positive returns after cost.

The repo already has `agentic_trader/pairs` (Engle-Granger, half-life, z-score) but it is
display-only (`/pairs`, `copilot pairs`): it fits β, α and the z-score window on the same bars it
reports, uses the plain ADF p-value on an estimated residual (liberal), and reads whichever provider
answers first. It cannot serve as research evidence. This lane adds a predeclared, causal protocol
with its own power and null checks and a one-use confirmation window; it leaves the display code
alone apart from a documentation caveat.

## 2. Scope and non-goals

In scope (one PR):

- `agentic_trader/research/spread/`: protocol models, panel acquisition from cached SIP daily bars,
  rolling formation/trading schedule, Engle-Granger pair fitting, spread trading simulation, lane
  evaluation, the study executor, check A (power) and check C (null).
- `alpha spread-power`, `alpha spread-null`, `alpha spread-study` CLI commands.
- Frozen files `config/research/spread/cohort-v1.json` and `config/research/spread/pairs-v1.json`.
- A lane-generic one-use confirmation interval in `AlphaRepository` (`{lane}/confirmation`).
- Docs: `docs/alpha-spread-lane.md`, CLAUDE.md paragraph, roadmap status (item 1 bracket closure,
  item 2 shipped), `docs/strategies.md` pairs caveat, CLI reference.

Out of scope (explicitly):

- Baskets (Johansen, PCA-residual): a v2 protocol after pairs v1 reports.
- Any probe, registry entry, Telegram card, order or `/pairs` behaviour change. A probe would need a
  two-leg execution contract (atomic legs, shared protection, borrow checks) that does not exist;
  the study result is `authorizes_promotion: False` and the operator decides what follows.
- Intraday data, point-in-time membership, borrow costs, short-sale availability.

## 3. Cohort (`config/research/spread/cohort-v1.json`)

```json
{"id": "spread-cohort", "version": 1,
 "survivorship": "config/config.yaml universe equities (mega_caps + research_cohort) that are members of the pooled cohort v2; sector tags from config.yaml; listed and active on 2026-10-10; not point-in-time",
 "market": "SPY",
 "sectors": {"technology": ["AAPL", ...], "financial_services": [...], ...}}
```

127 equities in 11 sectors (technology 26, financial_services 19, consumer_cyclical 15,
industrials 15, healthcare 14, communication_services 9, consumer_defensive 9, energy 8,
basic_materials 8, real_estate 2, utilities 2), giving 927 same-sector candidate pairs. Pairs are
same-sector only (the economic prior for a common stochastic trend); cross-sector pairs are never
tested. The identity is the SHA-256 of the file bytes, pinned by the protocol as `cohort_sha256`
and recorded in every manifest; a changed file is a new version.

Known bias: membership is today's (survivorship). Survivors are less likely to have diverged for good,
which flatters reversion. The confirmation window (2024–2026) is least exposed; the docs say so.

## 4. Protocol (`config/research/spread/pairs-v1.json`)

Frozen, `extra="forbid"`, hashed. Values below are the protocol; they are not tuned.

| field | value |
| --- | --- |
| `feed` / `adjustment` | `alpaca:sip` / `all` (split and dividend adjusted; spreads span up to 18 months) |
| `bars` | from 2016-01-04 through 2026-08-31 (the pooled cache range) |
| `windows.discovery` | 2017-01-03 → 2023-12-29 (trading windows) |
| `windows.confirmation` | 2024-01-02 → 2026-07-31 (one use, journaled) |
| `schedule` | formation 252 sessions, trading 126 sessions, non-overlapping tiles |
| `formation.coint_max_lag` | 1 (Engle-Granger `coint`, trend `c`, no autolag) |
| `formation.p_value` | 0.05 |
| `formation.half_life_sessions` | [5, 42] |
| `formation.hedge_ratio_abs` | [0.25, 4.0] |
| `formation.top_pairs` | 20 (ranked by the cointegration t-statistic, most negative first) |
| `formation.min_eligible_names` | 60 |
| `trading.z_entry` / `z_exit` / `z_stop` | 2.0 / 0.5 / 4.0 |
| `trading.fill` | `next_open` |
| `costs_bps_per_side` | [0, 5, 10]; `decision_cost_bps` 5 |
| `bootstrap` | stationary, block mean 10 sessions, 2000 draws, seed 20261010 |
| `pass_rules` | min_closed_trades 100, min_windows 8, min_positive_window_fraction 0.6, max_abs_market_beta 0.2, trim_fraction 0.01 |
| `confirmation_rules` | min_closed_trades 30, min_windows 4 (otherwise identical) |

### 4.1 Schedule

Sessions are the exchange calendar (Alpaca calendar) restricted to the bar range. Starting at the
stage's first session, trading windows of 126 sessions tile forward; each needs the 252 sessions
before it as formation. A trading window that would cross the stage end is dropped (whole windows
only; the discovery tiling never reads a confirmation session). The confirmation tiling restarts at
2024-01-02; its formation windows reach back into 2023, which is estimation on the past and allowed.
Expect about 13 discovery and 5 confirmation windows.

### 4.2 Eligibility and formation (per window, causal)

A symbol is eligible for a window when it has a close on every formation session (eligibility
uses only the past); a trading-window gap is handled in simulation (§4.3). Fewer than 60 eligible
names in any window fails the stage closed (`coverage_failed`).

For each same-sector pair (Y, X) with both names eligible, on formation log closes:

1. OLS `log Y = α + β log X + e`.
2. Engle-Granger test `statsmodels.tsa.stattools.coint(log Y, log X, trend="c", maxlag=1, autolag=None)`
   (MacKinnon cointegration p-values, which account for the estimated β; the display module's plain
   ADF p-value does not).
3. Half-life from the residual AR(1) (`Δe_t = c + θ e_{t−1}`; half-life = −ln 2 / θ when θ < 0).
4. Keep when `p < 0.05`, `5 ≤ half-life ≤ 42`, `0.25 ≤ |β| ≤ 4`, and the residual std `σ_f > 0`.
5. Rank kept pairs by the `coint` t-statistic ascending; take the first 20 (`top_pairs`). A name may
   appear in several pairs. Y is the alphabetically-earlier symbol; the regression direction is fixed,
   never chosen by fit.

Frozen per pair for the trading window: `α, β, σ_f` (formation residual mean is 0 by OLS).

### 4.3 Trading (per selected pair, trading window only)

`z_t = (log Y_t − α − β log X_t) / σ_f` at each trading-session close.

- **Entry** (flat only): `z_t ≥ 2.0` → short Y / long X; `z_t ≤ −2.0` → long Y / short X. Fill at
  the next session's open. No entry on the window's last session.
- **Exit**: first close with `|z| ≤ 0.5` (reverted) or `|z| ≥ 4.0` (stop), filled at the next
  session's open; or the window's last session, filled at that session's close (`window_end`).
- **Gaps**: a trading session where either leg lacks a bar is skipped for signals; if the pair is open
  the position is marked with the last available closes. A pair missing a bar on the window's last
  session is closed at its last available close.
- **Weights** at entry: dollar weights `w_Y = 1/(1+|β|)`, `w_X = |β|/(1+|β|)` (gross 1), fixed shares
  thereafter. Trade gross return `R = s_Y w_Y (P_Y^exit/P_Y^entry − 1) + s_X w_X (P_X^exit/P_X^entry − 1)`
  with leg signs `s ∈ {+1, −1}`. Net of cost `c` bp per side: `R − 2c/10⁴` (two legs in, two out,
  gross 1). Re-entry after an exit is allowed within the window.
- **Daily marks**: while open, the pair's daily return is the weighted leg return from the previous
  mark (entry open on the fill day). Costs are charged on the fill days.

### 4.4 Lane series and evaluation (per stage)

Committed-capital convention (Gatev): each window allocates 1/20 of capital to each selected pair;
unfilled slots and flat pairs earn 0. The lane's daily return is the sum of the pairs' daily
returns divided by 20, concatenated across the stage's windows (disjoint, so no overlap).

Pass rules at `decision_cost_bps` (5):

- **S1** lane mean daily return > 0 with the stationary-bootstrap (block 10, 2000 draws) 90% CI lower
  bound > 0.
- **S2** closed-trade mean net return > 0 and 1%-trimmed mean > 0.
- **S3** ≥ 100 closed trades across ≥ 8 trading windows, and ≥ 60% of windows with positive mean
  closed-trade net return.
- **S4** |OLS β| of the lane daily return on the market's (SPY) daily close-to-close return ≤ 0.2.

Descriptives (never gates): per-cost means, annualised Sharpe, hit rate, mean holding sessions,
exit-reason counts, per-window table, per-sector table, estimated vs realised half-life, selected-pair
counts and eligible names per window, S4 with a HAC(5) standard error.

### 4.5 Decision

- Discovery fails any of S1–S4 → `failed_discovery`; the confirmation window is not read or consumed.
- Discovery passes → the study journals the confirmation interval (§6), runs the identical rule on the
  confirmation tiling, and reports `confirmed` when S1, S2, S4 and the relaxed S3 hold, else
  `failed_confirmation`.
- Every result carries `authorizes_promotion: False`. A `confirmed` result makes a probe *specifiable*,
  not permitted.

## 5. Checks (both required at the same protocol, cohort and clean code revision)

**A — power** (`alpha spread-power`, synthetic only, no provider or cache access). Ten seeds. Each
builds a synthetic panel shaped like the cohort (same sector sizes, 2016–2023 sessions): log prices
= market factor + sector factor + idiosyncratic random walk (daily vols 1.0%, 0.7%, 1.2%), opens =
previous close × a 0.3%-vol overnight factor. Thirty planted same-sector pairs replace Y with
`α + β X + e`, `β ∈ [0.6, 1.6]`, `e` an OU process with half-life 20 sessions and innovation std 0.8%
(stationary std ≈ 3.1%). The full discovery pipeline runs. Pass when discovery passes S1–S4 in ≥ 8 of
10 seeds; planted-pair recall per window is reported.

**C — null** (`alpha spread-null`, real cached bars). Ten seeds. Each symbol's close-to-close log
returns and overnight gaps are circularly shifted by its own random multiple of 63 sessions
(seeded) and the price paths rebuilt from the first close, so marginal dynamics survive and every
contemporaneous relation (correlation, cointegration) is destroyed. The full discovery pipeline runs
on each. Pass when discovery passes in ≤ 1 of 10 seeds (the whole pipeline's false-acceptance rate,
selection included).

`spread-study` refuses without passing A and C directories whose `cohort_sha256`,
`protocol_sha256` and `environment.runtime.revision` match its own; it refuses a dirty or unknown
revision and uncommitted files under `agentic_trader`, `config` or `tests`.

## 6. Journal and artifacts

- Manifest before any I/O: `protocol.json` (protocol + sha256), `manifest.json` (ids, hashes,
  environment, journal identity, `authorizes_promotion: false`), then bars.
- No `family/all` trial charge (the protocol is one frozen hypothesis, like the a priori catalog).
- `AlphaRepository.consume_lane_confirmation(lane, *, protocol_sha256, cohort_sha256, interval, detail)`
  writes `{lane}/confirmation` under the alpha lock; any overlapping interval already consumed for that
  lane is refused. `lane` matches `^[a-z][a-z0-9-]{1,31}$` and is never `pooled` (which keeps its own
  campaign-bound method). The study calls it once, after discovery passes and before reading a single
  confirmation session; the journal must be the declared `--journal-scope` on PostgreSQL, checked
  before any bar read.
- Output directory is new and private (`--output`, 0o700, refuse if exists); results via
  `save_json_report`; trades as `trades.csv.gz`, lane series as `lane.csv.gz`.
- Bars come from the pooled cache (`--cache ~/agentic-trader-research/pooled-cache-v1/bars`, which
  already holds the 127 names and SPY, adjusted `all`, 2016-01-01 → 2026-09-01) through the generic
  `_fetch_cached` / `_claim_cache_range`; a name missing from the cache is fetched within the claimed
  range. No `BarEvidenceStore` (same reasoning as the pooled lane).

## 7. Architecture

```
agentic_trader/research/spread/
  protocol.py   SpreadCohort, SpreadProtocol (+ nested models), LoadedSpreadProtocol,
                load_spread_cohort, load_spread_protocol, same_sector_pairs
  panel.py      SpreadPanel(closes, opens, sessions), build_spread_panel (cache/fetch),
                shift_panel (null), synthetic_panel (power)
  schedule.py   Window(formation, trading), stage_windows
  formation.py  PairFit, fit_pair, select_pairs
  trading.py    Trade, simulate_pair, lane_series
  evaluate.py   evaluate_stage (S1–S4 + descriptives)
  study.py      run_stage, execute_spread_study, execute_spread_power, execute_spread_null, spread_gate
agentic_trader/cli/commands/alpha_spread.py   spread-power, spread-null, spread-study
agentic_trader/storage/alpha.py               consume_lane_confirmation
config/research/spread/{cohort-v1,pairs-v1}.json
tests/research/spread/
```

Pure functions take frames and return dataclasses; only `panel.build_spread_panel` and the CLI
touch providers, the cache or the journal. Everything heavy runs in `asyncio.to_thread`.

## 8. Known limits (documented in `docs/alpha-spread-lane.md`)

Survivorship (§3); today's adjustment factors applied to history; no borrow cost or availability;
fills at the next open with a flat 5 bp per side and no impact; daily bars only; β frozen for six
months; multiple comparisons inside formation are controlled only by the out-of-sample trading
window and the top-20 cap; dependence across simultaneously open pairs is handled by the lane-series
block bootstrap, not by trade-level tests; the market-neutrality gate uses one factor (SPY).

## 9. Testing

Unit: protocol validation (ordering, hash pinning, forbid extras); schedule tiling (whole windows,
stage boundary, formation reach-back); `fit_pair` on a constructed cointegrated pair (recovers β,
half-life, passes) and on independent random walks (fails at the nominal rate over many seeds, loose
bound); `simulate_pair` on a hand-built z path (entry fill at next open, exit on band, stop, window
end, gaps, cost arithmetic, weights, re-entry); `lane_series` arithmetic (1/K, flat days 0);
`evaluate_stage` on hand-built series (each S-rule flips independently); `shift_panel` preserves
marginal returns as a multiset and changes cross-correlation; `synthetic_panel` plants recoverable
pairs; executor writes the manifest before `build()` and records failures; the study refuses dirty
revisions, mismatched checks and reads no confirmation session before the journal call (fake
repository); `consume_lane_confirmation` refuses overlaps and the `pooled` lane (SQLite journal
fixture). CLI smoke through `CliRunner` with a fake panel builder.

## 10. Operations after merge

Controlled restart (installed checkout to the merged revision), then from the installed checkout with
`.envrc` sourced read-only, outside the scan windows:

```
copilot alpha spread-power config/research/spread/pairs-v1.json --output ~/agentic-trader-research/spread-power-v1-YYYYMMDD
copilot alpha spread-null  config/research/spread/pairs-v1.json --cache ~/agentic-trader-research/pooled-cache-v1/bars --output ~/agentic-trader-research/spread-null-v1-YYYYMMDD
copilot alpha spread-study config/research/spread/pairs-v1.json --power DIR --null-check DIR --cache ~/agentic-trader-research/pooled-cache-v1/bars --journal-scope SCOPE --output ~/agentic-trader-research/spread-study-v1-YYYYMMDD
```

Estimated runtime: 927 pairs × 18 windows of `coint(maxlag=1)` ≈ seconds per window; a stage in
under five minutes, checks under an hour each. The results document is bundled with the next
workstream PR (no docs-only PRs).

## 11. Design decisions taken under the standing instruction

1. Pairs only in v1; baskets deferred (YAGNI, and pairs alone answer "does spread reversion exist here").
2. Same-sector pairs from the sector-tagged scan universe ∩ pooled cohort (127 names) rather than all
   452 pooled names: an economic prior, 927 instead of ~100k tests, and names a future probe could trade.
3. Gatev-style non-overlapping 252/126 formation/trading tiles with frozen β, α, σ_f; no rolling z-window
   inside the trading window (fully causal and simple to audit).
4. `statsmodels.coint` (MacKinnon cointegration p-values) rather than the display module's plain ADF.
5. Fixed `maxlag=1`, no autolag: predeclared and ~50× cheaper, making checks A and C affordable.
6. Lane-series block bootstrap as the primary test (handles overlapping pairs) plus trade-level and
   per-window rules; a SPY-beta gate because market neutrality is the lane's claim.
7. Adjusted (`all`) bars rather than raw: spreads over 18 months must not break on splits/dividends.
8. No `family/all` trial charge (one frozen hypothesis, catalog precedent); a lane-generic one-use
   confirmation interval instead of widening the pooled campaign ledger.
9. Null check by per-symbol circular shifts of returns and gaps, rebuilt into price paths, rather than
   per-name demeaning (demeaning does not remove spread predictability).
10. Reuse the pooled bar cache directory directly; no new feed, store or evidence artifacts.

Costs if wrong: (2) misses cross-sector pairs; (5) loses some size against serially-correlated
residuals; (9) the null may be slightly harsher than reality (it also destroys common factors), which
biases toward a *lower* false-acceptance rate — reported, not hidden.
