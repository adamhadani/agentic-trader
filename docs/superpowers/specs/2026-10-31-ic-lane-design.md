# IC lane: rank IC machinery and the `composite-ic-v1` development gate

Date: 2026-10-31. Status: design, built under the operator's standing design trust ("go with your recommended approaches i trust your judgement"). Calls marked *(my call)* are mine, not the operator's.

Governing documents: [alpha directions report](../../alpha-directions-2026-10-30/report.md) §2.3, §3 (program rules R1–R6 and direction 3), §4 and §6; [research window ledger](../../research-window-ledger.md); [information coefficient contract](../../alpha-information-coefficient.md).

## 1. Why

Every pooled lane so far asks one question of one formula: does the top decile earn more than the rest under a long bracket? That has produced 64 pass/fail verdicts and no source. Three things in the review follow from that:

1. **Nothing combines signals.** Every practitioner source treats a single signal as a weak input to a combination.
2. **A top-decile edge under a bracket is a weak statistic.** Rank IC uses the whole cross-section, and it measures the thing a combination adds up.
3. **A batch should be judged by its mean, not its best member.** A fixed set whose eight formulas average nothing does not get to keep its one good-looking member.

The operator deferred Sharadar on 2026-10-30, so the review's fresh 2007–2016 era is unavailable and direction 2 is parked. The operator approved $0 work and was told this comes next, with the warning that anything it finds stays a hypothesis.

**What this delivers at $0:**

- a rank IC statistic with checks, which is the yardstick every later data source has to beat;
- a decisive answer on the existing record: read by IC, do the 64 tested pairs say anything different?
- a go/no-go on `composite-ic-v1` on the development window.

**The asymmetry to keep in view.** The development window is already read and its cohorts are survivors. A failure closes the composite, so the era is never spent on it. A pass only keeps it alive as a hypothesis.

## 2. Goals

1. Forward and residual return arrays from the next open, at 1, 5, 20 and 60 sessions.
2. A rank IC statistic: session-block bootstrap as primary, Newey-West as a cross-check, and a tear sheet.
3. Checks A-IC (planted effect) and C-IC (negative control), which must pass before the gate reads anything.
4. A zero-trial re-score of the 64 pairs on known windows: it reproduces the campaigns' recorded edges and applies a batch-mean rule.
5. `composite-ic-v1`, frozen and hashed, and its development gate.
6. A $0 sector map (SEC SIC codes mapped to Fama-French 12), because the report's SIC came from Sharadar.
7. New risk operators in the DSL, `ts_beta` and `ts_resid_std`, and a `cross_sectional_regress_out` panel kernel.

## 3. Non-goals and anti-goals

- **No cards, probe, registry entry, shadow or promotion credit, broker order or account access, Telegram or database access.** Nothing here runs in the daemon. No schema or migration.
- **No read of the reserve.** Nothing reads a price or label after 2023-12-29 (ledger W2 stays unused). A test poisons those prices and asserts every output is unchanged.
- **No era read and no slice decay check.** Both need the parked Sharadar work or a revised D6.
- **No search.** Fixed formulas only, and no free-form "score this expression" command: a tool for unlogged reads would break rule R1.
- **A failure would be** a composite that passes because of a leak, a target misaligned so that IC is structurally zero, or a result written up as more than a hypothesis.

**Left out on purpose *(my call)*:**

- **A string grammar for `cs_*` operators.** The report's answer to the operator's first thought lists them. The kernels exist and the composite calls them directly, and no formula is waiting for a string form. The report also says "no for more search". The first candidate that needs one can add it.
- **`ts_skew` and `ts_cov`.** No member uses them. `ts_cov` as an operator would also add nothing to `ts_beta`.
- **The MI-R2 label-span fix.** It changes cube identities and invalidates cached artefacts, and IC does not read bracket labels. It stays on the list for the next cube.
- **`atr_pct`.** Only the slice's economic gate used it.
- **Fundamental themes.** They need SF1 and the audit, and the report's declared fallback is price themes only.
- **Vectorising the DSL's rolling operators.** Six members over 1,000 names does not need it.

## 4. Inputs

All from the existing pooled cache (`~/agentic-trader-research/pooled-cache-v1`), via the same `build_cube_inputs(..., require_cached=True)` the campaigns use, so no bars are fetched.

**The one provider call.** `build_cube_inputs` reads the trading calendar from Alpaca before it checks the cache, and that calendar sets every `D−1` read and `D+h` index. The lane reuses the existing research client `_AprioriClients` (`cli/commands/alpha.py`), which calls `load_config()` and needs the Alpaca keys, rather than deriving a second calendar that would have to match the cube's exactly *(my call)*. It makes no other provider call and opens no database.

| Input | Source |
|---|---|
| Adjusted daily bars | `CubeBuild.adjusted`; truncated to `<= window_end` before anything else runs |
| Eligibility and dollar volume | The cube's `eligible` and `dollar_volume` arrays, `[S, N]`, for the protocol's sessions |
| SPY | The cached `SPY_1d` bars, digest-checked, truncated the same way |
| Decisive cohort | v3, through protocol `cohort3-daily-v1` |
| Secondary cohort | v2, through `campaign-v4`, reported only |
| Sectors | §6 |

The window is `discovery[0]` → `selection[1]` of the protocols, 2016-08-01 → 2023-12-29.

## 5. Arrays

Sessions `S` and symbols `N` follow the cube. A score for decision session `D` is read from completed bars at `D−1`, the same read as `evaluate_prepared`.

**Forward return,** for `h ∈ {1, 5, 20, 60}`:

```
r_h[D, i] = open[D + h, i] / open[D, i] − 1
```

It is NaN if either open is missing, or if session `D + h` falls after `window_end`. So decisions near the end of the window drop out at longer horizons, and no reserve price is read.

**An hourly-scored batch enters a session later.** Those formulas read bars through 10:00 on `D`, so `D`'s open is before the score exists, and using it would leak. For them, `r_h[D, i] = open[D + 1 + h, i] / open[D + 1, i] − 1`. A test pins this. The hourly cache could supply a 10:00 price on `D`, but one entry convention keeps all eight batches comparable, so I use the next open *(my call)*. It blunts the two intraday batches by one session; the results say so.

**`beta_60`** is `ts_beta(returns, mkt_ret, 60)`, read at `D−1`.

**`mkt_ret`** is a new DSL field, joined per symbol through `ScoreBook.with_fields`. It is SPY's return between the symbol's own consecutive bars, so a gap in the symbol compounds SPY over the same gap.

**Residual target:**

```
e_h[D, i] = r_h[D, i] − beta_60[D, i] · spy_r_h[D]
```

`spy_r_h` is SPY's open-to-open return over the same sessions. `e_h` is then demeaned per session within each sector, over names with a finite value. A NaN beta makes `e_h` NaN. **The decisive target is `e_20`.** Raw `r_h` is reported beside it.

**`log_adv`** is the log of the cube's `dollar_volume`; a non-positive value is NaN.

## 6. Sector map

**Why FF12 *(my call)*.** The report's "SIC sector" came from Sharadar. SIC itself is free from the SEC. Twelve published groups are mechanical, large enough to z-score inside, and not my own grouping.

**Acquisition.** `alpha ic sectors` maps each cohort ticker to a CIK with `company_tickers.json`, then reads `sic` from `https://data.sec.gov/submissions/CIK{cik:010d}.json`:

- paced at 2 requests a second, `insider_filings.py`'s rate, with `EDGAR_USER_AGENT` from the environment as that module reads it;
- HTTPS, host allow-list `www.sec.gov` and `data.sec.gov`, no redirects followed to any other host. `insider_filings.py` checks one host and has no `submissions` fetch, so this is new code that follows its pattern;
- **the user agent never appears in a file, a manifest or printed output**: it holds an email address;
- GET only, with one retry on `httpx.TransportError` as in that module; a failed name is recorded with the type name of its error.

**Frozen outputs:**

- `config/research/ic/ff12-sic-v1.json`: the eleven range lists from Ken French's `Siccodes12.zip`, SHA-256 `d801141acf039f2e06e6d4d9ba2b3992e9747a1d82fabd53ef21da4a3af79fff`, with anything else in `Other`. A boundary test covers SIC 3693 (`Hlth`), 3694 (`BusEq`), 3692 (`BusEq`), 2830 (`Hlth`), 2829 (`Chems`) and 9999 (`Other`).
- `config/research/ic/sectors-v1.json`: `{symbol: {cik, sic, group}}` over the union of cohorts v2 and v3, with `as_of`, per-symbol failures and the FF12 digest. It is public SEC data and holds no personal information, so it is committed like the cohort files.

**Limits, stated in the docs.** SIC is current, not point-in-time, so it carries a small look-ahead. SEC SIC codes rarely change.

**Unmapped names.** A name with no SIC gets `unknown` and is **excluded from the IC and the composite**, because it cannot be sector-neutralised. The gate and checks refuse to run if fewer than 90% of the decisive cohort's names are mapped *(my call)*. There is no fallback source and the floor is not relaxed: below it, the lane stops and the result doc says why. The cached `company_tickers.json` already covers 100% of both cohorts' tickers (measured 2026-10-31), so only a blank `sic` can cost coverage.

## 7. The IC statistic

**Per-session IC.** Spearman correlation across names with finite score, finite target and `eligible` true, using average ranks for ties. A session with fewer than 30 pairs, or a constant score or target, is NaN and counted as unavailable. This is the convention in `docs/alpha-information-coefficient.md`. The lane needs a vectorised kernel for the check loops, and a test pins it to `research/alpha/information.py:cross_sectional_ic` within 1e-12 on a random panel with NaNs and ties, so there is one definition.

**Inference.** Unavailable sessions are dropped first and counted.

- **Primary:** the mean IC with a stationary session-block bootstrap, using the existing `bootstrap_draws`. Mean block length is `2h`, 2,000 draws, a seed from the protocol. `t = mean / se_boot`.
- **Cross-check:** Newey-West at `2h` lags through `summarize_expected_values` and `ICPolicy`. Lags count retained sessions, since unavailable ones are already dropped. It is reported and never decides.

The report asks only for more than `h − 1` lags. `2h` for both is my call, and C-IC tests it.

**Tear sheet.** One JSON document and a text rendering, per horizon and per target (raw, residual):

- mean IC, se, bootstrap t, Newey-West t, ICIR, share of positive sessions, counts of evaluated and unavailable sessions;
- mean IC by calendar year and by four equal consecutive blocks;
- mean return by score decile and the top-minus-bottom spread, in percent;
- median names per session, lag-1 score rank autocorrelation, and top-decile turnover.

## 8. `composite-ic-v1`

**The rule is the report's, applied to the authors' published tables.** For each JKP price theme, take the factor from the earliest-published paper in JKP's appendix, with JKP's sign, where computable from the bars. The tables are `GlobalFactors/Cluster Labels.csv` (SHA-256 `22c7fdd214218d1210ab9922efed59bf607538098cc06b6f2c5f326d2e6cf64b`) and `Factor Details.xlsx` (`4c579e4dcb93eed0897941d8f5f84e9cc784a2d3fc696be68a29a24e68206141`) from `bkelly-lab/ReplicationCrisis`, read 2026-10-31 for member selection only. **No IC has been computed by anyone on this machinery.**

| Theme | Member | DSL expression | Sign | Paper |
|---|---|---|---|---|
| Momentum | `ret_3_1` | `roc(delay(close, 21), 42)` | +1 | Jegadeesh and Titman (1993) |
| Momentum | `ret_6_1` | `roc(delay(close, 21), 105)` | +1 | Jegadeesh and Titman (1993) |
| Momentum | `ret_9_1` | `roc(delay(close, 21), 168)` | +1 | Jegadeesh and Titman (1993) |
| Momentum | `ret_12_1` | `roc(delay(close, 21), 231)` | +1 | Jegadeesh and Titman (1993) |
| Short-term reversal | `ret_1_0` | `roc(close, 21)` | −1 | Jegadeesh (1990) |
| Low risk | `ivol_capm_252d` | `ts_resid_std(returns, mkt_ret, 252)` | −1 | Ali, Hwang and Trombley (2003) |

**Why four momentum members.** All four come from the same earliest paper, and the rule has no tie-break. Taking all of them adds no choice, and the within-theme mean stops them outweighing the others.

**How the low-risk member was reached.** Walking the theme by publication year:

1. `beta_60m` (Fama and MacBeth, 1973): not computable. It needs 60 months of bars and the cache starts in 2016-01.
2. `beta_dimson_21d` (Dimson, 1979): skipped. It is a beta, and step 3 of the construction regresses out `beta_60`, which removes most of what it is.
3. `turnover_126d` (Datar, Naik and Radcliffe, 1998): not computable. It needs shares outstanding.
4. `ivol_capm_252d` (Ali, Hwang and Trombley, 2003): taken.

The neutraliser skip is an addition to the report's rule *(my call)*. Without it the low-risk theme would be noise after neutralising. Survivorship probably works against this member, since the high-volatility names that blew up are missing.

**Excluded by the report:** Amihud, `short_ratio_z`, PEAD, insider, seasonality.

**Construction, per session, over eligible names with a known sector, member by member:**

1. Multiply by the sign, z-score across names, clip at ±3.
2. Z-score within sector. A sector with fewer than 5 finite names gives NaN for those names that session.
3. Regress out `beta_60` and `log_adv` with an intercept (`cross_sectional_regress_out`), on rows where all three are finite, requiring at least 30 rows. Keep the residual.
4. Z-score the residual across names, so each member has the same scale before averaging *(my call; the report's list leaves it open)*.

**Then combine, per name:**

5. A name needs at least 60% of the members finite: 4 of 6.
6. Mean of the available members within each theme, then mean across the themes that have one. A theme of four members has the same weight as a theme of one, and nothing is fitted.

**Frozen file.** `config/research/ic/composite-ic-v1.json`, validated by a Pydantic model with `extra="forbid"`, holds the members, signs, citations, the source digests, the skipped-member reasons, the construction constants, the target, the gate, the check settings, and the pins for the sector file and the two cohort protocols. Commands take its SHA-256 and write it to every manifest. A change is a new version.

### 8.1 The gate

Report rule, on cohort v3, `e_20`, 2016-08-01 → 2023-12-29:

> **Pass:** mean IC > 0 with bootstrap `t ≥ 2`, and IC > 0 in at least 3 of 4 equal consecutive blocks of the evaluated sessions.

- `alpha ic gate` writes its manifest before computing, like every other lane.
- It refuses unless A-IC and C-IC passed for the same composite digest, sector digest and clean code revision.
- Cohort v2 and the other horizons are reported, and do not decide.

| Result | What it means | What follows |
|---|---|---|
| **Fail** | The composite is closed. | The era is never read for it. The IC tools stay. It weakens the case for Sharadar's price lane; the event lane is unaffected. |
| **Pass** | A hypothesis, on a read window and a survivor cohort. | No card, no probe. The era read needs Sharadar. A fixed prospective read on W11 would need its own spec. Say this plainly in the result doc. |

### 8.2 Checks

Both run on the gate's own cohort, target and window, through the gate's code path. Both are research reads of the development window and are in the ledger.

**C-IC, the negative control.**

- **Decisive:** a **fixed within-sector name permutation**, applied to the composite for all sessions, 200 replicates, one seed each. The score keeps its real persistence and sector shape but loses name identity. This is harder than R4's wording, because it keeps the autocorrelation that makes an overlapping target dangerous.
- **Reported:** R4's literal version, permuting within session and sector, 100 replicates. Shuffling every session removes the persistence, so it would pass almost regardless *(my call to make the fixed one decisive)*.
- **Pass:** the gate rule accepts at most 10% of the fixed-permutation replicates.

**A-IC, the planted effect.**

- **Decisive base:** the same fixed-permutation composite, again keeping real persistence and factor shape. For each of 50 replicates at planted `λ`:

  ```
  score = sqrt(1 − λ²) · z(base) + λ · z(rank(e_20))
  ```

- **Pass:** at `λ = 0.03` the gate rule accepts at least 70% of replicates. IC 0.03 is about where a card nears break-even by the report's arithmetic, and 70% is the low end of the report's own power range.
- **Reported:** power at `λ = 0.02`, the report's figure, and at `0.04`, plus the same for the report's literal base, a pure AR(1) score with `φ = 0.97` per name. An i.i.d.-noise base understates daily IC variance, because it has no factor exposure, so it would overstate power *(my call)*.
- This check exercises the gate's code path end to end, but it is **not** an alignment check. The plant and the read share one target array, so a misdated array is invisible to it, and on an overlapping 20-session target a shift of up to about ten sessions still passes. Alignment rests on the exact-formula tests in the arrays task and on a spot check of real cells against raw bars before the read.

**If A-IC fails, the gate does not run.** The result doc then says the window cannot decide this question. If C-IC fails, the statistic is wrong, and it is fixed and re-run before anything else.

## 9. The 64-pair re-score

**Scope.** The eight fixed sets run so far, 8 formulas each: `campaign-v4`, `intraday-v1`, `events-v1`, `short-v1`, `cohort3-daily-v1`, `cohort3-intraday-v1`, `cohort3-events-v1`, `cohort3-short-v1`. Insider sets never ran, so they are out. Inputs come from `score_book` per protocol, with digest-pinned event and short inputs.

**Zero-trial.** The formulas and windows were already spent, and this charges nothing and journals nothing: no database. The record is its ledger row.

**Rehearsal (R2), per set and before any IC.** For each pair, recompute the discovery `edge_mean` and `n_sessions` through the campaign's own scoring path, and compare with the campaign's `formulas.jsonl` in `~/agentic-trader-research/pooled-campaign-*`. `edge_mean` must match within 1e-9 and `n_sessions` exactly. The pointers to the eight directories go in a frozen file with the digests of their `formulas.jsonl`.

The campaigns ran at older revisions, so a set may drift. A set with any mismatch is `not_reproduced`: it records its mismatches, gets **no IC read and no batch verdict**, and the other sets continue. The command exits non-zero if any set did not reproduce, and the results count the pairs covered. A code fix is allowed only if it changes no hypothesis, and each fix is logged in the results (R2).

**The IC read.** Per pair: `e_20` IC with the same statistic, over discovery and selection separately, with the expression as written. Sectors come from §6; `unknown` is excluded as in §6. Tear-sheet fields go in the output directory.

**The batch-mean rule** *(my definition; the report names the rule without defining it)*:

> A batch (one fixed set on one cohort) **carries** only if its mean IC over its eight formulas is greater than 0 on discovery **and** on selection. No member of a batch that does not carry goes anywhere, however good it looks.

Carrying is not a pass. It only makes a member eligible for a later spec that tests incremental IC over the composite. Selection is the out-of-sample half, which is what *Finding Alphas* ch. 15 judges.

**Also reported:** the mean and standard deviation of the 64 t-statistics against the existing "mean −0.06, sd 1.25" for the edge statistic, and where the known names landed: `shorts_retreat`, `quiet_momentum`.

**Order.** The composite file is committed before the re-score runs, so no IC on this machinery exists when the members are frozen.

## 10. Commands

`agentic_trader/cli/commands/alpha_ic.py`, group `alpha ic`, registered like `alpha_spread`. Each writes a new private output directory, refuses one that exists, and writes its manifest before computing. Each requires a clean revision (`require_clean_revision`) except `sectors`, which runs before its output is committed.

| Command | Does |
|---|---|
| `alpha ic sectors` | Acquisition (§6) |
| `alpha ic checks` | A-IC and C-IC (§8.2) |
| `alpha ic gate` | §8.1 |
| `alpha ic rescore` | §9 |

Code lives in a new package `agentic_trader/research/ic/`, with tests in `tests/research/ic/`. Like the other research lanes it uses no database, broker orders or Telegram; `load_config()` is called only by the calendar client (§4).

## 11. DSL additions

Additive only, in `research/alpha/operators.py`: existing formulas and their identities do not change.

- `ts_beta(y, x, d)`: rolling covariance over the variance of `x`, full window required, dimensionless, NaN when the variance is zero.
- `ts_resid_std(y, x, d)`: the rolling standard deviation of the OLS residual of `y` on `x`, computed as one fit per window, `ddof=2`, with tiny negative variance floored at 0. It has `y`'s units.
- `mkt_ret`: a dimensionless field in `FIELD_UNITS` with the comment pattern of the other lanes, present only when a frame carries it.

`research/alpha/panel.py` gets `cross_sectional_regress_out(panel, regressors)`, beside the existing kernels and validating the same way.

## 12. Docs and ledger

- **`docs/research-window-ledger.md`:** rows for the checks, the re-score and the gate are added **in one commit before the first read**, with the frozen composite and sector files, naming window, cohort, and that this is a go/no-go on development data with no α claim. The header counts update. The three commands then run at that one clean revision, because the gate requires the checks' exact revision. A code fix after that means a new revision and a re-run of the checks.
- **`docs/alpha-ic-lane.md`:** the contract, including the limits above.
- **`docs/alpha-ic-lane-results-YYYY-MM-DD.md`:** results, with the hypothesis-only statement first.
- **`CLAUDE.md`** gets a short pointer, and **`docs/alpha-roadmap.md`** and **`docs/alpha-directions-2026-10-30/README.md`** get status updates. The README still says direction 1 is awaiting merge; fix that too.

## 13. What's left to the builder

File layout inside `research/ic/`, the internal shape of the tear sheet, how SPY is read through the existing cached-bar helpers, the Newey-West plumbing through `ICPolicy`, and the plumbing that loads each protocol's inputs, provided the behaviour above holds.
