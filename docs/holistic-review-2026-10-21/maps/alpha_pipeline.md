# Alpha pipeline map: DSL, registry, shadow, promotion, forward evidence, forecasts

Reviewed read-only at `db82817` (worktree `agentic-trader-intraday`). Paths are relative to the
repo root. "Code does" means current behaviour; "Recommend" marks proposals.

## 1. Purpose and scope

`agentic_trader/research/alpha/` turns a hypothesis into an evidence-backed trading permission.
It covers a bounded causal expression language, immutable strategy versions, discovery (catalog,
random and genetic), purged validation with charged trials, a one-use holdout qualification, a
journal-backed registry (`active` / `shadow` / `probe`), shadow forecasts, and several prospective
diagnostic workers. It is the only code path by which a mined or literature formula can produce
a Telegram card. Today it produces **no** mined cards: there are zero active alphas, the weekly
miner is retired (`scripts/launchd.sh:21`), and the only live output of the alpha machinery is the
a priori catalog probe lane (PEAD drift), which belongs to `research/apriori`.

## 2. Components and contracts

### 2.1 Expression language

- `compile_expression` (`dsl.py:94-176`) parses a Python AST and enforces at most 4,096 chars,
  128 nodes and depth 16, constants of magnitude ≤ 1e6, integer powers ≤ 5, known fields and
  operators only, positional args only, and a composed lookback ≤ 2,520. It also checks units as a
  `(price_exp, volume_exp)` pair (`dsl.py:28-48`, `87-91`, `120-170`). The result is cached and
  hashable (`CompiledExpression`, `dsl.py:70-75`).
- Fields (`dsl.py:28-48`): OHLCV, `vwap`, the derived `returns`, `hl_spread`, `oc_spread` and
  `open_gap` (`dsl.py:186-199`), plus per-lane event, short-volume and insider fields that are
  hard-coded into the same global dict. No market, sector, benchmark or residual field exists.
- Operators (`operators.py:108-170`): about 30 single-series rolling operators (`ts_rank`, `ts_corr`,
  `ts_slope`, `ts_residual`, `ts_mad`, `decay_linear`, `zscore` clipped at ±3, `ema`, `cond`,
  `clip`, …). Undefined values stay NaN and division by zero gives NaN (`operators.py:78-81`).
  `realized_vol` is an alias of `ts_std` (`operators.py:162`). `log` is declared dimensionless
  (`operators.py:125`).
- Cross-sectional operators (`cross_sectional_rank`, `cross_sectional_zscore`, `group_neutralize`,
  `beta_adjust_scores`) live outside the grammar in `panel.py:19-37,132-159`. The language is
  single-instrument by design (`dsl.py:1-5`). The only production import of `group_neutralize` is
  `research/setups/features.py:27`.

### 2.2 Strategy contract

- `AlphaDefinition` (`models.py:49-181`) is content-addressed: `version_id` hashes the AST dump plus
  every trading field except name, description and origin (`models.py:127-135`). There are four
  semantics versions. Version 2 is fixed-duration GTC. Version 3 is a session clock. Version 4 is
  synthetic. Version 5 (`models.py:28,107-115`) is native daily, unclocked, with a one-session
  resting entry.
- Signal semantics (`strategy.py:144-161`): the raw score is z-scored against its own trailing
  `normalization_window` (default 30) and compared with `±entry_threshold`. Every per-symbol alpha
  therefore means "unusually high or low for this symbol relative to its last 30 bars". It cannot
  express a level or a cross-sectional rank.
- Execution (`strategy.py:31-65,164-226`): a limit at the tick-rounded signal close, a
  structural/ATR stop (1.5 ATR, 10-bar swing), a 2R target and a 1.5R trailing ratchet. The ATR
  is a finite simple true range. `AprioriBracketPolicy` is a separate catalog kind.
- `closed_alpha_bars` (`clock.py:36-80`) is the single live and shadow observation boundary. For
  fixed clocks it rejects a closed bar older than one bar duration (`clock.py:47-50`). A daily bar
  "closes" at its label + 1 day (`market/bars.py:23,98`).

### 2.3 Discovery

- `AlphaMiner.mine` (`miner.py:314-482`) evaluates seven catalog definitions (`catalog.py:6-77`,
  "adapted WQ" formulas written for 4h) and then random templates (`miner.py:63-211`) or
  `TypedGeneticSearch` (`search.py:60-184`: 17 seeds, 12 wrapper operators, windows 3-60, and an
  archive diversified by operator set).
- Genetic candidates take the defaults `direction="bi_directional"` and `entry_threshold=1.5`
  (`miner.py:389-395`, `models.py:57-58`). Fitness is the validation-fold bracket Sharpe minus
  0.01 × the expression length (`miner.py:434`).
- Metrics (`miner.py:213-289`): fold simulations, a local-family DSR (`miner.py:441-446`), and a
  rolling 20-row time-series Spearman IC against **one-bar** close-to-close returns
  (`miner.py:221-223`, `metrics.py:26-69`). A one-bar OLS-HAC calibration is also fitted.
- `ValidationPolicy` (`validation.py:19-51`): 20% holdout, three expanding folds, 1-bar label and
  embargo. Gates: Sharpe ≥ 1, DSR ≥ 0.95, IC ≥ 0.01, at least 10 trades, drawdown ≤ 20%.
- `simulate_execution` (`simulation.py:174-311`) holds one position at a time on 100% of equity
  (`simulation.py:233`), fills stop-first, and fills GTC or one-session limits on daily OHLC.

### 2.4 Qualification

`AlphaPromotionService.qualify` (`promotion.py:70-96`) requires a single-symbol universe equal to
the run's symbol (`promotion.py:88`). It consumes the holdout before computing anything
(`promotion.py:91`, `storage/alpha.py:203-258`), then runs `measure_statistical_evidence` and
`assess_statistical_measurements` (`promotion.py:197-460`).

The gates are conjunctive:

- intraday and recursive-feature blockers;
- validation Sharpe, local DSR, IC and trade count;
- every validation fold positive;
- **family-adjusted** validation DSR;
- holdout Sharpe, trades, **family DSR**, drawdown, doubled-cost return and a block-bootstrap lower
  bound;
- a frozen incumbent snapshot;
- incremental novelty, a Pearson residual-IC p-value with no HAC correction
  (`orthogonalization.py:110-122,194-222`);
- the raw Alpaca deployment data contract (`promotion.py:474-480`).

The family trial count is the lifetime `family/all` count. The variance is the per-timeframe
Sharpe list (`storage/alpha.py:185-201,239-251`).

### 2.5 Registry, shadow and probes

- `AlphaRepository._change` (`storage/alpha.py:280-369`) is a CAS on the registry generation.
  Promotion requires all of the following (`storage/alpha.py:308-355`):
  - an unclocked definition with an Alpaca feed;
  - a passing qualification under the current policy, no older than 45 days
    (`config.py:827`);
  - **an active set identical to the mining-time incumbent snapshot**;
  - at least 20 shadow sessions and 10 triggered decisions per symbol;
  - no other alpha that owns the symbol.
- `snapshot()` (`storage/alpha.py:674-707`) feeds `install_alphas` in each scan
  (`agent/copilot.py:757-760`). `FormulaicAlphaStrategy` (`screeners/formulaic.py:60-161`) emits
  a `ScreenerCandidate` with `alpha_version`, `alpha_policy` and `setup_quality=|z|/3`. The
  evaluator rebuilds the alpha's own bracket (`agent/evaluator.py:238-250`). Book sizing exempts
  alpha and probe cards (`execution/book_sizing.py:325`).
- `AlphaShadowService.observe` (`shadow.py:69-159`) runs per static contract in each non-dry scan
  (`agent/copilot.py:1062-1065`). `record_forecast` counts sessions and nonzero decisions
  (`storage/alpha.py:720-738`). Calibrated forecasts are blended (`forecasts.py:219-266`, weight
  1 at `shadow.py:115`) and journaled as `combined_shadow_forecast` (`shadow.py:145-154`). Nothing
  reads them back.
- Probes (`probe.py:33-116`, `storage/alpha.py:417-504`) enforce paper scope only, a daily
  unclocked definition, holdout Sharpe > 0, cost-stressed return > 0, at least 5 holdout trades,
  a −4R sticky kill, a $100 risk cap, at most 3 probe slots, and a single symbol owner.

### 2.6 Forecasts and IC

`ForecastTarget` (`targets.py:20-46`), `ForecastContract` and `ForecastCalibration` (OLS with a HAC
covariance and a **fixed** 0.5 shrinkage, `forecasts.py:52-171`) carry horizon, feed, adjustment,
clock and currency. `information.py` implements a versioned cross-sectional Rank IC with HAC for
the panel studies. The legacy miner IC is not migrated (`docs/alpha-information-coefficient.md:52`
onward).

### 2.7 Prospective and diagnostic workers (live in the daemon)

The following run as daemon workers but are journal sinks with no path to orders
(`cli/commands/service.py:35-39,242,263-296`):

- `SessionObservationService` (SPY/QQQ 15m receipts);
- `SessionDecisionService` (`decisions.py`; version-3 IEX controls, which can never qualify:
  `promotion.py:85-86`, `storage/alpha.py:310-311`);
- `DailyPanelService` (64 equities plus 9 ETF proxies, four frozen arms, H20 outcomes;
  `docs/alpha-daily-panel.md:3-6`).

### 2.8 Frozen study harnesses and legacy backtest (dormant)

`study.py`, `power_*`, `lifetime_*`, `calibration.py`, `panel_study.py`, `panel_forecast*`,
`persistent_*`, `volume*`, `factor_*`, `forecast_controls*`, `retained_*` and `baselines.py` are
one-shot predeclared experiments. In total there are 63 modules and about 14,500 LOC in
`research/alpha`, 40 `alpha` CLI commands in a 2,208-line `cli/commands/alpha.py`, and 69
`docs/alpha-*` files.

`backtest/engine.py` is a third bracket simulator. It is driven by yfinance (`engine.py:5,74-97`),
defaults to `load_config()` (`engine.py:54`), and is still constructed by the copilot
(`agent/copilot.py:4484`) and the backtest/stress CLIs.

### 2.9 Seams

| Consumes | Produces |
| --- | --- |
| Alpaca raw IEX/SIP or yfinance bars via `cli/commands/alpha.py:175-213`; registry snapshot; scan `ContractMarketData` | `domain_events` + `alpha_projections` (runs, versions, holdouts, qualifications, registry, forecasts, probes); NPZ artifacts; `ScreenerCandidate`s for active/probe versions; `RegistrySnapshot.catalog_probes` for apriori drift cards |
| `research/setups/labels.py` (not used by this lane's simulator) | `ConvexAlphaPortfolioOptimizer` reused only as book-sizing's shadow cross-check (`execution/book_sizing.py:27,423-470`) |
| Pooled lane (`research/pooled/formula.py:25`) imports the DSL evaluator only | Pooled ledger in the same repository (`storage/alpha.py:1035-1214`), never `family/all` |

## 3. Data flow

1. **Acquire.** `alpha mine` reserves the trials before provider I/O (`cli/commands/alpha.py:365-372`).
   It downloads raw bars (`:189`), writes a `DatasetManifest` hash, and snapshots the active
   incumbents into the manifest (`:400`).
2. **Discover.** Catalog definitions run first, then search (`miner.py:376-440`). Every trial is
   registered as an immutable version (`cli/commands/alpha.py:410-411`). `record_run` adds the
   trials to `family/all` and their per-bar Sharpes to `family/1d/…` (`storage/alpha.py:185-201`).
3. **Qualify.** The operator picks a finalist. `begin_holdout` consumes a per-symbol interval: any
   overlap with a previously consumed interval on that symbol is refused for every formula and
   timeframe (`storage/alpha.py:221-234`). The decision is recorded immutably.
4. **Shadow.** `alpha shadow` needs no qualification (`storage/alpha.py:280-369`). The scan observes
   the version, and valid closed-bar scores accrue sessions and decisions. Stale daily bars
   (Mondays and post-holiday days, `clock.py:47-50`) record `valid: false`.
5. **Promote or probe.** `promote` reaches `active` through the checks in §2.5. `probe` reaches the
   capped paper lane. A catalog leg reaches `probe` through `enrol_catalog_probe`.
6. **Card.** The next scan installs the snapshot. The formulaic screener fires when |z| ≥ threshold
   and the definition's own bracket is applied. Admission re-fences the registry at reservation
   and at submission (`docs/alpha-pipeline.md:31-39`). The card goes out as Telegram
   `🧪 PAPER PROBE` or as a normal card.
7. **Side branches.** Calibrated forecasts are written to `forecast/…` and combined, and are never
   evaluated. The session and daily-panel workers write their own artifacts.

Routes to a live card, in practice:

- **(a)** DSL → active: blocked by the gates (§6, G2–G3).
- **(b)** DSL → probe: reachable in principle, but no qualified candidate exists.
- **(c)** Catalog → probe: live (PEAD). This is the only working route.
- **(d)** Pooled formula → card: **no route exists** (§6, G1).

## 4. Evidence of what has been tried

| Experiment | Result | Source |
| --- | --- | --- |
| Per-symbol ETF32 miner, 62 weekly runs | No finalist; retired 2026-10-14 | `scripts/launchd.sh:21` |
| Weekly miner's effect on the family | It kept "charging 512 trials a week to the family that deflates every future qualification" | `docs/alpha-roadmap.md:440` |
| 32-name equity campaign, 1,024 charged trials | Best validation SR 3.34, best DSR 0.890; zero leaders met both gates | `docs/alpha-equity-mining-2026-09-19.md:24-31` |
| Power diagnosis, 800 searches | Null bounds met. **0/64** planted-effect acceptance (dense and sparse). GTC pending orders blocked about 90% of pulses (453 of 498 bars pending). Holdout trades about 1.3-1.45 against a 10-trade minimum. Both family-DSR gates failed in every positive run. Dropping any single criterion produced no passes. | `docs/alpha-power-diagnosis-2026-09-18.md:23-28,39-56,65-69` |
| Lifetime attribution | GTC limits "select against a correct forecast": the planted route made 1.3 trades, the null 58-69. A one-session entry restores conversion. | `docs/alpha-lifetime-attribution-2026-09-21.md:3-7,56-66` |
| Version-5 confirmatory power run | Named "the next action"; no result doc exists | `docs/alpha-roadmap.md:61-69` |
| A1b/A2a calibration | Lifetime gates accepted 0/64 planted cases. The research-only holdout comparator accepted 12-28/64. | `docs/alpha-stack-survey-2026-09-18.md:97-101` |
| Pooled lane, fixed sets | 64 formula-cohort pairs, no finalist. Example v4 edges: seven of eight negative; leg mean about +0.17R is just the label base rate. | `docs/alpha-pooled-short-v1-2026-10-20.md:135`; `docs/alpha-pooled-checks-v4-2026-10-07.md:37-57` |
| Pooled power | Detection 0.84 at 0.08R and 0.99 at 0.15R per pick (cohort v2) | `docs/alpha-pooled-checks-v4-2026-10-07.md:25-27` |
| Native setup features | `setup_quality` is uninformative. `vol_20` (ρ +0.049, Holm p 0.004) and `sector_rel_mom_60` (ρ +0.028, Holm p 0.02) survive. Prospective evaluation window 2026-10-21 → 11-04. | `docs/setup-outcomes-2026-09-23.md:67-80`; `docs/alpha-roadmap.md:300-303,630` |
| Bracket counterfactual | Every arm trails SPY by 0.10-0.15R | `docs/bracket-counterfactual-2026-10-18.md:6` |
| Research/live signal parity | 0 of 15,470 entry-direction disagreements | `docs/alpha-pipeline-implementation.md:80-82` |

## 5. Strengths worth preserving

- **The causal, bounded language.** It is an AST whitelist with unit checks, NaN-preserving
  operators, no forward fill and prefix-tested causality. Mutation and crossover must
  recompile (`search.py:130-161`).
- **Immutable, content-addressed identity** with explicit semantics versions (`models.py:127-135`).
  Old hashes stay byte-identical.
- **Charge-before-read accounting** and **consume-before-compute** holdouts
  (`cli/commands/alpha.py:365-372`, `storage/alpha.py:203-258`). Every criterion records
  pass/fail/unavailable with its reason (`promotion.py:279-288`). Missing evidence fails closed.
- **One shared strategy contract and clock** for research, live screening and shadow
  (`strategy.py`, `clock.py`, `formulaic.py:79-104`), with measured parity.
- **Registry CAS, admission fencing, and a probe lane** with a risk cap, a sticky kill, paper scope
  and owner exclusivity. This lane is the right shape for incubating anything new.
- **Predeclared power and null checks**, plus honest negative write-ups. This is rarer, and more
  valuable, than any single alpha.

## 6. Gaps, weaknesses, risks and inconsistencies

**G1. The actual mining lane has no deployment contract.** The pooled lane is where mining now
happens. Its formulas are dimensionless scores on **adjusted** bars, read at D-1 and ranked
cross-sectionally into a top decile (`research/pooled/formula.py:1-7,100-108`). The registry can
only hold a per-symbol definition:

- `eligible_symbols == (symbol,)` (`promotion.py:88`);
- time-series z-thresholding (`strategy.py:150-161`);
- **raw** adjustment (`promotion.py:476-477`).

Probe enrolment likewise needs a per-symbol daily definition (`storage/alpha.py:427-432`). The
pooled docs defer this to an unbuilt "Part 2 probe spec" (`docs/alpha-pooled-mining.md:1016-1017,1365-1368`).
Even a pooled finalist could not reach Telegram without new design work.

**G2. Per-symbol qualification is mathematically near-unreachable.** The holdout DSR is deflated by
the lifetime family count (`promotion.py:370,388`; `storage/alpha.py:239-251`), even though the
holdout tests one finalist frozen before the test. With the frozen family (7,065 trials, variance
0.002876; `docs/alpha-study-protocol.md:73-74`), the expected null maximum is a per-bar Sharpe of
0.203. Clearing DSR ≥ 0.95 on a 252-bar holdout then needs a per-bar Sharpe of about 0.31, roughly
**4.9 annualized** (computed from `metrics.py:72-131`).

The same family also deflates validation (`promotion.py:351-366`). This double-counts selection and
explains the 0/64 power result. Any new research charge raises the bar for every future candidate.

**G3. Throughput traps in the registry.**

- Holdout consumption is keyed by **symbol only** (`storage/alpha.py:221`). With a 5-year lookback,
  one qualification attempt locks that symbol's last year for every other formula and timeframe.
- Promotion requires the global active set to equal the mining-time incumbent snapshot
  (`storage/alpha.py:322-326`; snapshot taken at `cli/commands/alpha.py:400`). Promoting or
  demoting **any** alpha, on any symbol, permanently strands every other qualified candidate,
  which then cannot be re-qualified while its holdout stays consumed. The novelty test itself
  only uses symbol-relevant incumbents (`promotion.py:167-172`).
- Qualification expires after 45 days (`config.py:827`). Ten triggered decisions take about 75
  sessions at the two-sided |z| ≥ 1.5 rate of roughly 13%, or about 150 sessions one-sided. The
  "qualify then shadow" order therefore cannot finish in time. The required order (shadow first)
  is undocumented.

**G4. The per-symbol lane's objectives, labels and units disagree.**

- Search optimizes multi-bar bracket Sharpe on full capital (`miner.py:434`; `simulation.py:233`).
- The IC gate uses an overlapping one-bar time-series IC (`miner.py:221-223`; `metrics.py:50-69`).
- Novelty uses a non-HAC Pearson p-value (`orthogonalization.py:110-122`).
- Calibration predicts one-bar returns (`miner.py:249-266`).
- Live cards are sized in R.
- Three bracket engines disagree:
  - `simulation.py` uses a daily-OHLC limit at the signal close, which the docs call
    adverse-selecting;
  - `research/setups/labels.py:176-189` uses hourly RTH bars and enters at the first bar open at or
    after the decision; pooled cubes and `cards outcomes` share it;
  - `backtest/engine.py` uses yfinance.

  Research P&L for a would-be alpha is therefore not measured the way its cards would be.

**G5. DSL expressiveness is narrow where the literature says the signal is.**

- No in-expression cross-sectional, industry or market-relative operators or fields (`dsl.py:1-5,28-48`).
  The best-documented equity signals are residual and industry-relative momentum, beta-adjusted
  reversal and sector-neutral volatility.
- No explicit mask or NaN operator. `insider-v1.json` encodes "only when" as
  `insider_buy_rel / (insider_buy_rel > 0)`.
- No `ts_skew`, `ts_quantile`, `ts_cov` or `ts_beta`.
- `log(close)` passes `require_dimensionless` (`operators.py:125`, `pooled/formula.py:49-52`),
  even though it ranks names by price level.
- New data fields are added by editing a global dict (`dsl.py:32-47`), with no as-of or visibility
  metadata.
- The per-symbol lane cannot express levels at all: every signal goes through the 30-bar
  time-series z (`strategy.py:144-152`).
- Genetic candidates default to two-sided trading (`miner.py:389-395`). Their fitness therefore
  includes ETF shorts against a rising 2021-2026 tape, even though native shorts were clearly
  negative (`docs/setup-outcomes-2026-09-23.md:61-63`). Borrow is not modelled.

**G6. Shadow counts activity, not quality.** The gate counts sessions and nonzero decisions
(`storage/alpha.py:331-343,726-738`), not outcomes. Calibrated and combined forecasts are written
and never scored (no consumer of `combined_shadow_forecast` beyond `shadow.py:149`). Decay, cost
and capacity monitoring is still a to-do (`docs/alpha-pipeline.md:422-423`). The combination's
standard error is a worst-case sum (`forecasts.py:260`) with a fixed 0.5 shrinkage
(`forecasts.py:63`), and the single-owner rule means there is usually only one contributor.

**G7. Daily signals are lost on Mondays and after holidays.** `clock.py:47-50` rejects a daily
bar more than one day past its fixed close. Friday's bar is about 2.4 days old at Monday 10:35,
so active and probe daily alphas cannot fire, and shadow records `stale_closed_bar`. That is about
20% of sessions. The behaviour is acknowledged (`docs/alpha-pipeline.md:75-76`) but not fixed,
although session calendars exist (`market/bars.py:112`).

**G8. Probe guidance is not enforced.** The roadmap says not to enrol GTC probes
(`docs/alpha-roadmap.md:68-69`), and GTC entries are proven adverse-selecting. Code accepts any
unclocked daily definition (`storage/alpha.py:427-428`), including version-2 GTC.

**G9. Mining has no cadence and no forward accrual.** The miner is retired and pooled campaigns
are manual and need operator approval. No worker scores frozen candidate formulas prospectively,
so a formula's forward evidence starts only after promotion. The daily panel has no predeclared
decision rule or activation lane (`docs/alpha-stack-survey-2026-09-18.md:122-127`).

**G10. Complexity and dormancy.** Most of the 14,500 LOC is in single-use frozen harnesses (§2.8).
Three simulators exist. Of the live workers, the session decisions worker evaluates version-3
controls that can never qualify. CLAUDE.md has become a running results log. Changing a shared
kernel (simulation, journal, DSL) now carries the burden of every frozen harness.

**G11. Survivorship and adjustment.** Per-symbol research runs on raw bars (`cli/commands/alpha.py:189`).
Splits and distributions therefore appear as returns in features and simulated P&L, while
pooled scores use adjusted bars. Cohorts are current membership. The survivorship spike found
Alpaca bars cover delisted names but its asset master is not a dated delisting list
(`docs/survivorship-spike-2026-10-20.md:3-12`).

**G12. Performance.** Python-lambda rolling operators are about 100× slower than vectorized ones:
measured `ts_rank` 7.0 ms against `ts_mean` 0.07 ms per 2,600-bar series (`operators.py:15-19,138-152`).
That makes genetic, pooled and check-B searches costly and discourages a scheduled cadence.

## 7. Improvement opportunities (recommendations)

**I1. Make a cross-sectional ranker the deployment unit (fixes G1; directly affects card
quality).** Add a versioned `RankerDefinition`: a dimensionless DSL score on as-of-adjusted bars,
plus a direction, evaluated at the scan's decision time over the scan's ranked native candidates
or eligible universe. Deploy it through the existing `card_policy` pattern (`off → preview → enforce`)
to reorder or withhold cards, and as a probe kind for one card per session under the probe cap.

Its evidence is the pooled lane's session-paired edge in R on `label_bracket`, the same label as
`cards outcomes`. `vol_20` and `sector_rel_mom_60` become its first candidates when their
2026-10-21 → 11-04 window closes.

Expected effect: the alpha work finally reaches the one or two cards a day. Ranking claims need
far less power than standalone strategies, because the control is the same day's candidate set.

**I2. Version a validation policy v2 with selection-correct holdout inference (fixes G2-G3).**

- Keep the family DSR (or a Romano-Wolf / Hansen-SPA family test via the `arch` package already
  used, `docs/alpha-study-protocol.md:20-23`) for **selection** on validation.
- Test the frozen finalist on the holdout with a one-sided block-bootstrap or HAC test, Holm-adjusted
  across holdouts consumed in a declared campaign. This is the A1b comparator, which already
  recovered 12-28/64 against 0/64.
- Key holdout consumption by (symbol, hypothesis family) and compare incumbents per symbol.
- Measure trades and returns in R per trade, not full-capital Sharpe.

Expected effect: measurable power while keeping the null bounds. The confirmatory power run
should be the acceptance test for v2.

**I3. One labeler (fixes G4).** Re-base `simulate_execution` on `research/setups/labels.py` semantics
(hourly RTH, entry at the decision time or at a declared limit offset), or derive both from one
kernel. Retire `backtest/engine.py` or rebuild it on that kernel. Version the miner's IC and novelty
on `information.py` (HAC, horizon-matched).

Expected effect: research results, probe P&L and `cards outcomes` become directly comparable.

**I4. DSL v2 (fixes G5 and G12).**

- Add typed cross-sectional operators: `cs_rank`, `cs_zscore`, `cs_neutralize(x, sector)` using
  observed group membership.
- Add market-relative fields: `mkt_ret`, `sector_ret`, `beta_60`, `resid_ret`.
- Add `where(cond, x)`, `ts_skew`, `ts_quantile`, `ts_cov` and `ts_beta`.
- Make `log` keep units unless it is differenced.
- Use a field registry with unit, source and as-of visibility.
- Compile to vectorized kernels (numpy, bottleneck or numba), as Qlib's expression engine does.
- Seed fixed sets from the Chen-Zimmermann *Open Source Asset Pricing* signal library and the
  Jensen-Kelly-Pedersen global factor definitions rather than hand-picked formulas.

Expected effect: the language can state the signals with the strongest replication record, such
as residual momentum (Blitz, Huij & Martens 2011) and industry momentum (Moskowitz & Grinblatt
1999), and searches get about 10-100× cheaper.

**I5. A prospective formula scorer with sequential stopping (fixes G6 and G9; gives the requested
cadence).** A daemon worker, like `DailyPanelService`, would score every frozen fixed-set formula
and probe candidate at 10:35 each session. It would label picks with `label_bracket` and decide
probe eligibility with a pre-registered, always-valid test (confidence sequences, Howard et al.
2021). Score shadow forecasts the same way and report decay.

Expected effect: evidence accrues daily without re-mining used history, which is the cadence the
operator asked for.

**I6. Small, high-leverage fixes.**

- Calendar-aware daily freshness using `TradingSession`, so Mondays work (G7).
- Require `semantics_version == 5` for DSL probes (G8).
- Default genetic candidates to `long`, or search direction and threshold explicitly (G5).
- Document or enforce "shadow before qualify" (G3).

**I7. Prune (fixes G10).** Tag completed frozen harnesses (the pattern already exists, e.g.
`research/power-diagnosis-v1-20260918`) and delete them from `main`, keeping reproducibility by tag.
Fold the dormant per-symbol miner into the I2/I3 decision: either revive it on v2 or retire it
explicitly. Move dated results out of CLAUDE.md into the roadmap.

**I8. Set prior expectations.** Hou, Xue & Zhang (2020) and Chordia, Subrahmanyam & Tong (2014)
find most anomalies weak in liquid, value-weighted, recent samples, which is exactly the cohort
used here. McLean & Pontiff (2016) find about 50% post-publication decay. Novy-Marx & Velikov
(2016) find costs erase high-turnover anomalies.

Expected effect: long-only liquid-name decile edges of 0.08-0.15R per pick are optimistic MDEs.
Prefer conditioning information such as events, insider and short-flow data, and ranking within
setups, over standalone factors. Use Bailey et al.'s PBO and CPCV for search-selection
diagnostics, and Gu, Kelly & Xiu (2020) as the panel-ML baseline.

## 8. Open questions

- **Registry contents.** What is in the production registry right now (active, shadow, probe and
  legacy YAML shadows), and how large have `family/all` and the 1d Sharpe list grown? I could not
  run the CLI.
- **Per-symbol lane.** Is the per-symbol DSL lane meant to be revived (version 5, confirmatory
  power run) or retired? Nothing records that decision.
- **Pooled multiplicity.** How would a pooled finalist's multiplicity, which lives in the pooled
  ledger, map onto qualification? Code defines no rule.
- **Daily panel.** What decision rule turns daily-panel H20 outcomes, due mid-October, into any
  action?
- **Daily bar timestamps.** I did not verify the exact Alpaca daily-bar timestamp convention on
  live data. The Monday rejection holds for any midnight-anchored label, because the gap is
  at least two days.
