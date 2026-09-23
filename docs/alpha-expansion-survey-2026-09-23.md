# Alpha expansion survey — September 23, 2026

**Status:** decision record. It ranks ways to widen the alpha funnel and fixes the
workstream order the operator approved on September 23. Progress is tracked in the
[alpha roadmap](alpha-roadmap.md#alpha-expansion-workstreams-september-23). The
external literature below was gathered by research agents; claims from 2026 preprints
are leads, not verified results.

## Why the funnel is narrow

Operator breadth is **not** the binding constraint. The per-instrument DSL
(`research/alpha/dsl.py`, `operators.py`) already has rolling mean/EMA/std/max/min/
argmax/argmin/rank/correlation, delta/delay, `decay_linear`, zscore, slope, residual,
MAD, clip and conditionals. It also enforces unit checks and structural causality.
The typed genetic search (`search.py`) already keeps a quality-diversity archive and
a parsimony penalty, and family-adjusted DSR uses the lifetime trial ledger.

The constraints are structural:

1. **Per-symbol statistics.** `alpha mine` evaluates one instrument's time series.
   Qualification needs 10 trades, family-adjusted DSR, a one-use holdout **keyed by
   symbol** and 20 shadow sessions per symbol. That leaves few independent
   observations per hypothesis, which is consistent with the planted-signal power
   result of 0/64 ([power diagnosis](alpha-power-diagnosis-2026-09-18.md)). Holdouts
   are consumed per symbol, so repeated campaigns exhaust them.
2. **No activation lane for cross-sectional work.** Panel Ridge/boosted forecasts,
   factor controls and the daily panel worker are research-only. The convex
   allocator is shadow-only.
3. **All cards come from two hand-written strategies** (trend pullback, squeeze
   breakout). There are zero active mined alphas, and `setup_quality` is an
   unvalidated heuristic.
4. **Data is OHLCV only.** There are no earnings dates, fundamentals or news. A card
   can be bracketed straight through an earnings gap.

## External evidence (summary)

- **Operators.** WorldQuant Alpha101 and Qlib Alpha158 are the reference sets.
  Missing from our per-instrument DSL:
  - cross-sectional `rank`/`zscore` and sector neutralization;
  - residual/beta against a benchmark or sector ETF;
  - `ts_skew`/`ts_kurt`, `ts_cov`, `signedpower`;
  - up/down-day counts.

  The first two families only make sense on a panel.
- **Search.** The most consistent 2023–2025 idea is to optimize a *set*: reward a
  candidate's marginal contribution to a combined pool, with a correlation penalty.
  This comes from AlphaGen (KDD 2023), AlphaForge (AAAI 2025), AlphaQCM (ICML 2025)
  and RiskMiner (ICAIF 2024).

  LLM-agent miners (Alpha-GPT, AlphaAgent KDD 2025, RD-Agent-Quant) and RL/MCTS
  papers are almost all evaluated on CSI300/500 A-shares. They rarely report
  DSR/PBO, and AlphaEval (2025) found many reported gains shrink under a common
  protocol. Probability of backtest overfitting (CSCV) is a cheap complement to our
  DSR and holdouts.
- **Time-series foundation models** (TimesFM 2.5, Chronos-2, Moirai 2 (non-commercial
  licence), TTM, Toto, TiRex). 2025–26 evaluations find no reliable zero-shot or
  fine-tuned edge on return direction. On realized volatility and spreads they are
  competitive but do not clearly beat HAR/GARCH.
- **Deep cross-sectional models** (MASTER, StockMixer, FactorVAE, HIST; Kelly et al.
  cross-asset attention). Gains over LightGBM-on-Alpha158 are small (about +0.01 to
  0.03 IC), unstable across splits and mostly on A-shares. MASTER ran at about 300
  names, so our universe size is plausible. Gu, Kelly and Xiu (RFS 2020) remain the
  robust US result: modest nonlinear gains at large N.
- **Stat-arb.** PCA/ETF-residual mean reversion (Avellaneda & Lee) is real but has
  decayed. Deep-learning stat-arb (Guijarro-Ordóñez, Pelger & Zanotti) is the most
  relevant US cube result. Alpaca paper shorting is limited to easy-to-borrow names.

## Ranked recommendations

Recommendations are ranked by the north star: one or two reasonable suggestions per
session.

1. **Cross-sectional panel lane ("cube v1").** Build the causal stocks × time ×
   features panel over the scan universe:
   - cross-sectional operators: rank, zscore, sector-demean, residual versus SPY or
     the sector ETF;
   - an Alpha158-lite feature library;
   - Ridge and LightGBM benchmarks;
   - **time-interval** holdouts instead of per-symbol ones;
   - IC/ICIR/turnover;
   - a pool-contribution search objective.

   The output is one calibrated forecast per name per day in `ForecastContract`
   units. Panel sample size raises statistical power without relaxing any gate.
2. **Shadow ranking of today's cards.** Record the panel forecast beside
   `setup_quality` on every card candidate and compare both against realized R.
   Meta-labeling (a take-profit-before-stop classifier on replayed native setups) is
   the follow-on. This is the fastest route to better cards and needs no new
   execution contract.
3. **Earnings awareness.** A deterministic entry blackout when an announcement falls
   inside the hold window. Post-earnings drift is a later candidate source and needs
   surprise data.
4. **Dynamic universe.** Alpaca asset list plus the existing liquidity screen plus
   movers/most-actives, added to the suggestion scan's list only. The intraday job
   stays limited to `non_universe_contracts`. History mining beyond about 300 names
   needs point-in-time membership (for example Sharadar or Norgate); today's list is
   not survivorship-safe.
5. **Residual reversal as a single-leg feature** before two-leg pairs. Two legs need
   a new execution contract: atomic legs, partial fills, shared protection and
   easy-to-borrow checks.
6. **Deep learning and foundation models only after panel IC exists.** First, a
   foundation-model volatility feature compared with HAR (`arch` is already a
   dependency). Then a small MASTER/StockMixer-style model.

   Not now: foundation models for return direction, or a full LLM-agent miner. LLM
   hypothesis proposals into the same charged trial ledger are a later option.

## Downstream fit

Every source (DSL alpha, panel model, residual reversal, later deep models) emits
per-name, per-date forecasts in one `ForecastContract`. A combiner (IC-weighted,
ridge-shrunk) feeds three consumers:

- card ranking;
- card generation (ATR bracket, probe caps, existing risk reservation);
- allocator sizing (roadmap step B).

Gates do not change. **Open operator decision:** a 1–5 session panel horizon versus
semantics v5, which has no holding deadline. The options are bracket-only exits or a
new timed semantics version using the existing lifetime service. Advisory exit cards
(A2) pair naturally with a forecast that has flipped or decayed.

## Workstreams (approved September 23)

| Workstream | Scope | Why this order |
| --- | --- | --- |
| WS1 | Earnings blackout gate + card note | Small; protects cards immediately |
| WS2 | Panel lane v1 + shadow card ranking | Largest research yield; spec → plan first |
| WS3 | Dynamic universe (liquidity screen + movers) | Widens candidates once ranking exists |

Later, operator-gated: residual reversal feature; the volatility-feature study; the
pool-contribution search objective and PBO diagnostic; a panel-driven card source
with its execution policy; a small deep cross-sectional model.
