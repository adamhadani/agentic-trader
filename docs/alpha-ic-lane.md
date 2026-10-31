# IC lane: rank IC machinery and the `composite-ic-v1` development gate

Design: [spec](superpowers/specs/2026-10-31-ic-lane-design.md); governing documents: the
[alpha directions report](alpha-directions-2026-10-30/report.md) (direction 3, program rules R1-R6), the
[research window ledger](research-window-ledger.md) and the
[information coefficient contract](alpha-information-coefficient.md). The spec is the authority; this
file is the contract the commands implement, with the limits stated.

## 1. Status and what a result means

**Reads done 2026-10-31: `composite-ic-v1` failed its gate and is closed** ([results](alpha-ic-lane-results-2026-10-31.md)). The tools stay.
A re-run is a declared second look.

- **Research only.** No card, probe, registry entry, shadow or promotion credit, broker order or account
  access, Telegram or database access, schema or migration. Nothing runs in the daemon. **Nothing here
  produces a card.**
- **A pass is a hypothesis**, on a window every earlier lane has read, over a cohort of survivors. It
  confers no card, no probe and no α claim. The era read needs the parked Sharadar work; a fixed
  prospective read on W11 would need its own spec.
- **A fail closes the composite.** The era is never read for it. The IC tools stay. A fail weakens the
  case for Sharadar's price lane; the event lane is unaffected.
- **No read of the reserve.** Nothing reads a price or label after 2023-12-29 (ledger W2 stays unused);
  a test poisons those prices and asserts every output is unchanged.
- **No search.** Fixed formulas only. There is no free-form "score this expression" command, because a
  tool for unlogged reads would break rule R1.

## 2. Frozen files

All under `config/research/ic/`; each command takes the composite's SHA-256 and writes it to its manifest.
A change to any file is a new version.

| File | SHA-256 |
| --- | --- |
| `composite-ic-v1.json` | `63269ca1ba2a2bf7437a8c979646948007f5a671c680efaa9b71b301077d63b4` |
| `rescore-v1.json` | `961f60f9fcebbe716a482d3a545e73e693f6ef80a204aa989b430a538776c8ec` |
| `sectors-v1.json` | `56bc70fb52ce541aa2e4292673e81ebbc42df18bcf53484e6907cb52d4689574` |
| `ff12-sic-v1.json` | `4fac501b2baffd0792188f514e0b93fe41bd195f6c09c66b288e23befe83bcb5` |

The composite file also pins both cohort protocols and the sector file. The composite was committed before
the re-score runs, so no IC on this machinery existed when its members were frozen.

## 3. Inputs and the one provider call

Inputs come from the existing pooled cache (`~/agentic-trader-research/pooled-cache-v1`) through
`build_cube_inputs(..., require_cached=True)`, so no bars are fetched. The window is 2016-08-01 to
2023-12-29 and every array is truncated to it before anything computes. The decisive cohort is v3
(`cohort3-daily-v1`); cohort v2 (`campaign-v4`) is reported and never decides.

**The one provider call:** `build_cube_inputs` reads the trading calendar from Alpaca before it checks the
cache, and that calendar sets every `D-1` read and `D+h` index. The lane reuses the research client
`_AprioriClients` (`load_config()`, Alpaca keys) rather than deriving a second calendar that would have to
match the cube's. It makes no other provider call and opens no database. `alpha ic sectors` separately calls
the SEC (section 5).

## 4. Arrays

A score for decision session `D` is read from completed bars at `D-1`. For `h` in {1, 5, 20, 60}:
`r_h[D, i] = open[D+h, i] / open[D, i] - 1`, NaN if either open is missing or session `D+h` falls after
the window end. Decisions near the end therefore drop out at longer horizons.

- **Hourly-scored batches** read bars through 10:00 on `D`, so they enter one session later:
  `open[D+1+h] / open[D+1] - 1`. This blunts the two intraday batches by one session, and the results say so.
- **Residual target:** `e_h = r_h - beta_60 * spy_r_h`, where `beta_60` is `ts_beta(returns, mkt_ret, 60)` at
  `D-1`. It is then demeaned per session within each sector **over eligible names with a finite value** and
  is NaN for non-eligible names, so the target is sector-neutral on the set the IC scores. The decisive target
  is `e_20`; raw `r_h` is reported beside it.
- **`log_adv`** is the log of the cube's `dollar_volume`; a non-positive value is NaN.

## 5. Sector map

SEC SIC codes mapped to Fama-French 12 (`ff12-sic-v1.json`, from Ken French's `Siccodes12.zip`), because the
review's "SIC sector" came from Sharadar. `alpha ic sectors` maps ticker to CIK with `company_tickers.json`
and reads `sic` from the SEC submissions API: paced at 2 requests a second, HTTPS to `www.sec.gov` and
`data.sec.gov` only, no cross-host redirects, GET only with one retry on a transport error, a failed name
recorded by error type name. `EDGAR_USER_AGENT` comes from the environment and never appears in a file,
manifest or output.

**Limits:** SIC is current, not point-in-time, so it carries a small look-ahead; a wrong sector costs noise,
not sign. A name with no SIC is `unknown` and is excluded from the IC and the composite. The gate and checks
refuse to run if fewer than 90% of the decisive cohort's names are mapped; there is no fallback and the
floor is not relaxed.

## 6. The IC statistic

Per-session IC is Spearman across names with finite score, finite target and `eligible` true, using average
ranks for ties. A session with fewer than 30 pairs, or a constant score or target, is unavailable and
counted. A test pins the vectorised kernel to `research/alpha/information.py:cross_sectional_ic` within
1e-12, so there is one definition.

- **Primary:** mean IC with a stationary session-block bootstrap (`bootstrap_draws`, mean block length `2h`,
  2,000 draws, seed from the composite file); `t = mean / se_boot`.
- **Cross-check:** Newey-West at `2h` lags, reported and never deciding.
- **Tear sheet:** per horizon and per target (raw, residual): mean IC, se, bootstrap and Newey-West `t`, ICIR,
  share of positive sessions, evaluated and unavailable counts; mean IC by calendar year and by four equal
  consecutive blocks; mean return by score decile and top-minus-bottom spread; median names per session,
  lag-1 score rank autocorrelation, top-decile turnover.

**`t` calibration** (measured, 600 simulated series per row, n = 1,790, triangular overlap of a target of
horizon `h`; final review, 2026-10-31):

| h | Block (sessions) | Pure overlap: `t` sd | `t >= 2` accepts | With a slow IC regime: `t` sd | `t >= 2` accepts |
| --- | --- | --- | --- | --- | --- |
| 1 | 2 | 1.00 | 3.0% | 4.38 | 30.0% |
| 5 | 10 | 1.10 | 4.2% | 2.23 | 18.8% |
| **20** | **40** | **1.18** | **5.0%** | **1.45** | **9.0%** |
| 60 | 120 | 1.25 | 5.8% | 1.38 | 7.3% |

So at h = 20, `t >= 2` behaves like a one-sided 5% test, which matches R1. A slow drift in the IC mean
inflates short horizons far more, so **`t` is not comparable across horizons**: horizons are compared by
means and block means, not by `t`. **C-IC validates h = 20 only, on the composite only**, and the results
quote its `t` sd. The regime row is a synthetic assumption.

## 7. `composite-ic-v1`

Six members, one per rule from the earliest-published paper in each JKP price theme that the bars can
compute (tables from `bkelly-lab/ReplicationCrisis`, read for member selection only):

| Theme | Member | Expression | Sign |
| --- | --- | --- | --- |
| Momentum | `ret_3_1`, `ret_6_1`, `ret_9_1`, `ret_12_1` | `roc(delay(close, 21), 42 / 105 / 168 / 231)` | +1 |
| Short-term reversal | `ret_1_0` | `roc(close, 21)` | -1 |
| Low risk | `ivol_capm_252d` | `ts_resid_std(returns, mkt_ret, 252)` | -1 |

Skipped, with reasons in the file: `beta_60m` (needs 60 months of bars), `beta_dimson_21d` (a beta; the
construction regresses out `beta_60`), `turnover_126d` (needs shares outstanding). The neutraliser skip is an
addition to the report's rule. Excluded by the report: Amihud, `short_ratio_z`, PEAD, insider, seasonality.

**Construction, per session, over eligible names with a known sector, member by member:** multiply by sign,
z-score, clip at 3; z-score within sector (fewer than 5 finite names gives NaN); regress out `beta_60` and
`log_adv` with an intercept (at least 30 rows) and keep the residual; z-score again. **Combine:** a name needs
at least 4 of 6 members; mean within each theme, then mean across themes. Nothing is fitted.

### The low-risk leg and the volatility confound

The low-risk member carries two large, opposite, name-linked biases in a rank statistic (synthetic worlds with
zero arithmetic alpha, final review, 2026-10-31; per-seed noise about 0.004, sizes depend on assumed
volatility):

- **Rank IC against volatility.** Twenty-session returns compound, so a high-volatility name has a lower
  median; a rank target favours low volatility even at zero alpha. Composite rank IC +0.012 to +0.016 with no
  selection, growing with horizon (+0.003, +0.006, +0.012, +0.023 at h = 1, 5, 20, 60).
- **Survivor selection.** High-volatility names reach a liquidity band by rising, which pushes the other way.

| World | Low-risk theme, rank IC | Composite, rank IC | Composite, Pearson | Top minus bottom, % per 20 sessions |
| --- | --- | --- | --- | --- |
| No selection, 600 names | +0.030 | +0.016 | -0.001 | -0.16 |
| No selection, 1,000 of 4,000 (two seed sets) | +0.026, +0.027 | +0.012 | -0.001, -0.004 | -0.10, -0.25 |
| Control: median-zero returns | -0.006 | -0.003 | -0.032 | -1.50 |
| Control: no volatility dispersion | -0.005 | +0.0005 | -0.001 | -0.08 |
| v3-shaped band 401-1,400 (two seed sets) | +0.002, +0.008 | +0.0005, +0.007 | -0.020, -0.013 | -0.71, -0.49 |
| v2-shaped top 450, strong price-liquidity link | -0.014 | -0.006 | -0.027 | -0.83 |
| v2-shaped top 450, weak link | -0.003 | -0.002 | -0.022 | -0.73 |

What follows:

- **Cohort v2 is not a replication.** The two cohorts are pushed apart mechanically by about 0.01 of composite
  rank IC with no alpha. It is reported beside v3 and never decides.
- **The arithmetic spread is biased too.** A negative top-minus-bottom beside a positive rank IC is expected on
  this cohort with no effect.
- **C-IC cannot see any of this.** Its fixed permutation breaks name identity by design and averaged within
  0.001 of zero in the worlds it was run in. The gate therefore reports a decomposition (section 8).
- Regressing on `beta_60` while the target subtracts the same beta adds noise and leans slightly against a
  pass; it is not a route to a false pass.

## 8. The gate

`alpha ic gate`, cohort v3, `e_20`, 2016-08-01 to 2023-12-29:

> **Pass:** mean IC > 0 with bootstrap `t >= 2`, and IC > 0 in at least 3 of 4 equal consecutive blocks of the
> evaluated sessions.

It writes its manifest before computing and refuses unless A-IC and C-IC passed for the same composite
digest, sector digest and clean code revision. Cohort v2 and the other horizons are reported and do not decide.

**Reported beside the decision, on both cohorts, none of it deciding:** each theme's rank IC with `t` and its
arithmetic (Pearson) IC; the composite excluding the low-risk theme with `t`, and the composite's Pearson IC;
the composite lagged 252 sessions (in the synthetic worlds it tracked the mechanical part of the IC);
`min_detectable_ic = min_t * se`, and `power_at` 0.02 and 0.03, computed as `Phi(ic/se - min_t)` from the
read's own `se`.

**Declared reading, fixed before the read:**

> A pass whose composite excluding low risk does not reach `t >= 2` is recorded as **carried by the low-risk
> leg**, and it does not strengthen the case for Sharadar's price lane.

This is a reading, not a decision rule; whether it should become one is the operator's call.

### Power statement (R3)

`min_detectable_ic` is a **50%-power** level: a true IC equal to it passes half the time. At 80% power a fail
rules out IC 0.03 only if `se <= 0.0106`, that is `min_detectable_ic <= 0.021`. Above that, the results say
that a fail could not rule out IC 0.03 and the closure rests on the report's rule, not on evidence of absence.
For the report's range of `se` (this is arithmetic, not a measurement):

| se | Power at IC 0.02 | Power at IC 0.03 |
| --- | --- | --- |
| 0.0105 | 46% | 80% |
| 0.016 | 23% | 45% |

Conversion to card units, using the report's arithmetic (9% residual volatility, top-decile z 1.75, 30 bp
round trip, 8.5% implied stop):

| IC | Gross per pick | Net per pick | Per card |
| --- | --- | --- | --- |
| 0.02 (the shrunk effect) | 0.315% | +0.015% | +0.002R |
| 0.03 | 0.472% | +0.172% | +0.020R |
| 0.04 | 0.630% | +0.330% | +0.039R |

This is an arithmetic conversion, **not a measured `net_excess_r_v1`**, which would need a bracket replay with
per-name cost and a beta-matched control. It ignores the survivor bias on arithmetic spreads (section 7), and
R5's economic gate is untouched.

## 9. Checks

Both run on the gate's own cohort, target and window through the gate's code path, and are research reads of
the development window (ledger L40).

- **C-IC (negative control), decisive:** a fixed within-sector name permutation of the composite, all
  sessions, 200 replicates. It keeps persistence and sector shape and loses name identity, which is harder
  than R4's wording. **Pass:** the gate rule accepts at most 10% of replicates. **Reported:** R4's literal
  version (permute within session and sector, 100 replicates), which removes persistence and would pass almost
  regardless.
- **A-IC (planted effect), decisive:** on the same permuted base, 50 replicates of
  `score = sqrt(1 - l^2) z(base) + l z(rank(e_20))`. **Pass:** at `l = 0.03` the gate rule accepts at least
  70%. **Reported:** `l` = 0.02 and 0.04, and the report's AR(1) `phi = 0.97` base.
- **A-IC is a plumbing check, not a power statement.** Permuting names removes the IC variance that factor
  returns put into a real composite, so its power is overstated (in a synthetic factor world `t` stayed near
  10, about three times the real figure at high exposure). The honest measure is the read's `min_detectable_ic`
  and `power_at`.
- **A-IC is not an alignment check.** The plant and the read share one target array, so a misdated array is
  invisible to it, and on an overlapping 20-session target a shift of up to about ten sessions still passes.
  Alignment rests on the exact-formula tests and on the spot check below.
- **Alignment spot check (real cells, computes no IC):** before the checks, a read-only script outside the
  repository draws 200 seeded (session, name) cells in the window and compares `returns[1]`, `returns[20]` and
  `spy_returns[20]` with `open[D+h]/open[D]-1` read directly from the cached bar frames. Each cell must match
  within 1e-12 or be NaN for a stated reason.
- **If A-IC fails the gate does not run**, and the result doc says the window cannot decide this question.
  If C-IC fails the statistic is wrong; it is fixed and re-run before anything else.

## 10. The 64-pair re-score

Scope: the eight fixed sets run so far, 8 formulas each (`campaign-v4`, `intraday-v1`, `events-v1`,
`short-v1`, `cohort3-daily-v1`, `cohort3-intraday-v1`, `cohort3-events-v1`, `cohort3-short-v1`); the insider
sets never ran. Inputs come from `score_book` per protocol with digest-pinned event and short inputs.

**Zero-trial:** it charges nothing and journals nothing, with no database; its record is its ledger row. The
formulas and their discovery window were already spent, but **selection is new for 63 of the 64 pairs**; only
`shorts_retreat` on cohort v3 ever reached it. That is allowed because W1 is development only. It also means
**any batch that carries has no unread selection window left**, so a later spec needs the era or a
prospective read.

**Rehearsal (R2), per set and before any IC:** recompute each pair's discovery `edge_mean` and `n_sessions`
through the campaign's own scoring path and compare with the campaign's `formulas.jsonl` (pinned by digest).
`edge_mean` must match within 1e-9 (`EDGE_TOLERANCE`, recorded in the manifest) and `n_sessions` exactly. A
set with any mismatch is `not_reproduced`: it records its mismatches and gets no IC read and no verdict, the
other sets continue, and the command exits non-zero. Each code fix is logged in the results and may change no
hypothesis. The rehearsal covers the edge path only; the IC path rests on the pinned kernel test and the
spot check.

**The read:** per pair, the `e_20` IC with the same statistic over discovery and selection separately, with the
expression as written, so a negative-momentum formula stays negative as the campaigns traded it. One fixed
within-sector permutation placebo per pair and window goes through the same read; the summary gives placebo
`t` mean and sd beside the real ones. The earlier "sd 1.25" is not a null: the null itself gives about 1.18
at h = 20.

**Batch-mean rule** (this lane's definition of the report's undefined rule): a batch (one set on one cohort)
**carries** only if its mean IC over its eight formulas is above 0 on discovery **and** on selection. A formula
with fewer than 250 evaluated sessions in a window (`min_sessions`, about 12 independent 20-session periods) is
left out of that window's mean and counted; per-window minimum and median session counts are reported.

- **Carrying is not a pass.** Under a global null about a quarter of batches carry (two positive means), about
  2 of 8, and the batches are correlated. It only makes a member eligible for a later spec that tests
  incremental IC over the composite.
- **Sets hold opposing members** (`quiet_momentum` and `quiet_reversal`; `shorts_*` and `squeeze`; `oc_spread`
  and `open_gap - oc_spread`; `momentum_12_1` and `reversal_5`), so a near-zero batch mean does not mean every
  member is null. That is what the rule intends.
- **Windowed formulas** such as `earnings_age <= 20` rank names inside the window only, where the campaign
  compared picks with all eligible cells. IC session counts therefore sit beside the rehearsed `n_sessions`;
  the campaign had no 30-pair minimum. Short batches can look like full-window means, hence the session counts.
- **Raw formulas take the volatility bias of section 7 unneutralised.**
- Also reported: mean and sd of the 64 `t` values against the recorded edge statistic (mean -0.06, sd 1.25;
  recomputed from `formulas.jsonl` as -0.063 and 1.257), and where `shorts_retreat` and `quiet_momentum` landed.

## 11. Commands and DSL additions

Package `agentic_trader/research/ic/`, commands in `agentic_trader/cli/commands/alpha_ic.py` (group `alpha ic`),
tests in `tests/research/ic/`. Each command writes a new private output directory, refuses one that exists and
writes its manifest before computing; each requires a clean revision except `sectors`.

| Command | Does |
| --- | --- |
| `alpha ic sectors` | Acquisition (section 5) |
| `alpha ic checks` | A-IC and C-IC (section 9); exits non-zero on a failed check |
| `alpha ic gate` | Section 8; a gate fail is a completed read, exit 0 |
| `alpha ic rescore` | Section 10; exits non-zero if a set did not reproduce |

New DSL names, additive only (existing formula identities do not change): `ts_beta(y, x, d)` (rolling
covariance over the variance of `x`, full window, NaN at zero variance); `ts_resid_std(y, x, d)` (rolling
standard deviation of the OLS residual of `y` on `x`, one fit per window, `ddof=2`, in `y`'s units); and
`mkt_ret`, a dimensionless field **present only when a frame carries it** (SPY's return between a symbol's
own consecutive bars). `cross_sectional_regress_out(panel, regressors)` joins the panel kernels. All are
research-only and no registry or probe consumes them.

- `ts_beta` uses the `matching_ratio` unit rule: the two arguments must have matching units and the result is
  dimensionless. It is a unit check only, so `ts_beta(close, close, 60)` compiles as dimensionless (it is
  identically 1). A manually imported registry formula using these operators would compile; one using
  `mkt_ret` fails `Missing required fields` and is skipped.
- `ts_resid_std` is a realized-volatility operator outside the pooled `forbidden_operators` list. A future
  protocol's frozen list must name it.

## 12. Revision discipline

The checks, the re-score and the gate run at one clean revision, because the gate requires the checks' exact
revision. A code fix after the checks means a new revision and a re-run of the checks. **A re-run after a gate
read is a declared second look.** The rows are in the ledger (L40-L42) before the first read.

## 13. Limits and what was left out

- **A read window and survivors.** W1 is already read by every pooled lane, and both cohorts are current
  members, not point-in-time. No clean confirmation window exists without the era (Sharadar deferred 2026-10-30).
- **Current SIC** (section 5) and **the volatility confound** (section 7).
- **The decisive result stays inside this lane's declared alpha:** development only, `t >= 2` at h = 20
  behaving as about 5% one-sided (section 6).
- **No era read and no slice decay check:** both need the parked Sharadar work or a revised D6.
- **Left out on purpose:** a string grammar for `cs_*` operators (the composite calls the kernels directly and
  no formula waits for one); `ts_skew` and `ts_cov` (no member uses them); the MI-R2 label-span fix (it
  changes cube identities and IC does not read bracket labels); `atr_pct`; fundamental themes (they need SF1
  and the audit; the declared fallback is price themes only); vectorising the DSL's rolling operators.

## 14. Results

| Slot | Value |
| --- | --- |
| Alignment spot check | Passed: 200 cells, no mismatch |
| A-IC | Passed, as a plumbing check (power 100%, overstated by construction) |
| C-IC | Passed: 3.0% accepted (limit 10%), `t` sd 1.13 |
| Re-score (64 pairs, both windows) | All eight sets reproduced; 2 of 8 batches carry, the chance rate; no pair reaches `t >= 2` in both windows |
| Gate, cohort v3 `e_20` | **Fail:** mean IC +0.0116, `t` 1.53, 3 of 4 blocks; power at IC 0.03 is 97.5% |
| Cohort v2, reported | Mean IC +0.0148, `t` 1.50, 4 of 4 blocks |
| Result doc | [2026-10-31](alpha-ic-lane-results-2026-10-31.md) |
