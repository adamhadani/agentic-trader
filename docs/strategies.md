---
layout: default
title: Quantitative Strategies & Models - Agentic Trader
---

> Runtime note (2026-09-16): research backtests and formulaic return streams do not
> produce live performance records. Broker reports use actual Alpaca fills and account
> valuations. Promotion allocation weights and the convex optimizer remain research
> metadata/library functionality, not live position sizing. See [development notes](development-notes.md).

# 📊 Quantitative Strategies & Econometric Models

This document summarizes screening defaults and research models. Effective parameters
come from validated configuration; these models do not guarantee predictive power.
GEX and pairs are analytical reports, not integrated multi-leg execution strategies.

---

## 1. Strategy A: Multi-Timeframe Trend-Pullback

### Objective
Capture high-probability trend-continuation entries during temporary counter-trend pullbacks, anchoring stops behind structural swing levels.

### Formulations
1. **Daily Trend Invariant**:
   - **Bullish Regime**: $\text{Close}_{\text{daily}} > \text{EMA}(50)_{\text{daily}} > \text{EMA}(200)_{\text{daily}}$
   - **Bearish Regime**: $\text{Close}_{\text{daily}} < \text{EMA}(50)_{\text{daily}} < \text{EMA}(200)_{\text{daily}}$
2. **4-Hour Trigger**:
   - Price must pull back near the 20-period EMA:
     $$|\text{Close}_{4\text{h}} - \text{EMA}(20)_{4\text{h}}| \le 0.5 \times \text{ATR}(14)_{4\text{h}}$$
   - RSI uses configured dip/recovery thresholds across three candles. Long:
     either of the previous two values is below `rsi_oversold_dip`, and the current
     value recovers to at least `rsi_oversold`. Short uses the corresponding
     `rsi_overbought_surge` and `rsi_overbought` thresholds.
3. **Levels & Exit Brackets**:
   - **Stop Loss**: Minimum stop distance floor of $1.5 \times \text{ATR}(14)$ behind the local swing low/high.
   - **Take Profit**: Configured for at least $2.0 \times \text{Risk Distance}$ ($R:R \ge 2.0$).

---

## 2. Strategy B: Volatility Squeeze Breakout

### Objective
Exploit volatility compression regimes (John Carter Squeeze) where trading ranges tighten prior to sharp, directional expansion.

### Formulations
1. **Band Definitions**:
   - **Bollinger Bands**: $\mu_{20} \pm 2.0 \times \sigma_{20}$
   - **Keltner Channels**: $\text{EMA}(20) \pm 1.5 \times \text{ATR}(14)$
2. **Squeeze Compression Condition**:
   - Squeeze is active when the upper Bollinger Band is below the upper Keltner Channel AND the lower Bollinger Band is above the lower Keltner Channel:
     $$\text{BB}_{\text{upper}} \le \text{KC}_{\text{upper}} \quad \text{and} \quad \text{BB}_{\text{lower}} \ge \text{KC}_{\text{lower}}$$
   - Compression must persist for at least $N \ge 5$ consecutive candles.
3. **Breakout Trigger**:
   - Candle closes outside the Bollinger Bands:
     - **Long Breakout**: $\text{Close} > \text{BB}_{\text{upper}}$
     - **Short Breakout**: $\text{Close} < \text{BB}_{\text{lower}}$
   - Volume confirmation surge:
     $$\text{Volume} \ge 1.3 \times \text{SMA}(\text{Volume}, 20)$$

---

## 3. Research: Options Gamma Exposure Estimates

### Objective
Analyze available Yahoo option chains across ETFs and futures proxies using
Black-Scholes assumptions. Calls-positive/puts-negative is a model convention;
the feed does not reveal dealer inventory. Missing counts/defaults are disclosed;
a real underlying quote is required.

### Formulations
1. **Black-Scholes Analytical Gamma**:
   $$\Gamma = \frac{N'(d_1)}{S \sigma \sqrt{T}}$$
   where $d_1 = \frac{\ln(S/K) + (r + \frac{\sigma^2}{2})T}{\sigma \sqrt{T}}$ with boundary safeguards for $T \to 0$.
2. **Dealer Gamma Exposure (GEX)**:
   - Aggregated in $ Millions per 1% move:
     $$\text{Call GEX}_K = \Gamma_K \times \text{OI}_K \times 100 \times S^2 \times 0.01 \times 10^{-6}$$
     $$\text{Put GEX}_K = -\Gamma_K \times \text{OI}_K \times 100 \times S^2 \times 0.01 \times 10^{-6}$$
3. **Structural Pinning Levels**:
   - **Call Wall**: Strike with peak call open interest (major overhead resistance).
   - **Put Wall**: Strike with peak put open interest (major downside support floor).
   - **Gamma Flip**: Interpolation across strike-level net GEX sign changes. This
     heuristic does not reprice the entire option book across hypothetical spot prices.

---

## 4. Research: Cointegration and Pairs Screening

### Objective
Screen price pairs for residual stationarity and spread signals. No automatic
paired-leg execution or position protection is implemented. The current code uses
OLS plus residual ADF; statistical calibration should be reviewed before using this
as a trading admission rule.

### Formulations
1. **Engle-Granger Two-Step Cointegration**:
   - **Step 1 (OLS)**: Regress $Y_t$ on $X_t$:
     $$Y_t = \beta X_t + \alpha + \epsilon_t$$
   - **Step 2 (ADF)**: Test residuals $\epsilon_t$ for unit-root stationarity via Augmented Dickey-Fuller test:
     $$\Delta \epsilon_t = \gamma \epsilon_{t-1} + \sum_{i=1}^p \phi_i \Delta \epsilon_{t-i} + e_t$$
   - Pair is cointegrated if ADF $p$-value $< 0.05$.
2. **Ornstein-Uhlenbeck Half-Life**:
   - Continuous-time / discrete AR(1) mean-reversion speed:
     $$\Delta \epsilon_t = \theta \epsilon_{t-1} + c + \eta_t$$
   - For $\theta < 0$, the half-life in trading bars is:
     $$T_{\text{half}} = -\frac{\ln(2)}{\theta}$$
   - Filters out non-stationary or sluggish pairs ($T_{\text{half}} > 60$ bars).
3. **Rolling Spread & $Z$-Score**:
   - Real-time spread: $S_t = Y_t - (\beta X_t + \alpha)$
   - Rolling $Z$-score over 30-day window:
     $$Z_t = \frac{S_t - \mu_{S, t}}{\sigma_{S, t}}$$
    - Signals:
      - $Z_t \le -2.0$: `BUY_SPREAD` (Long Y, Short $\beta X$)
      - $Z_t \ge +2.0$: `SELL_SPREAD` (Short Y, Long $\beta X$)
      - $|Z_t| \le 0.5$: `EXIT_SPREAD` (Mean-reverted, close position)

---

## 5. Formulaic alpha screeners and research

Read the [alpha-stack review](alpha-stack-review.md) for reproduced defects,
measurement results and the hardening/experiment plan. Current statistics and
promotion records do not establish a validated live strategy.

### DSL and discovery

The AST evaluator supports OHLCV/derived fields, arithmetic, comparisons and
registered rolling operators. It uses no Python `eval()`. However, validation lacks
operator type/arity/lag/resource contracts. Global `rank`/`scale` read the full
series, negative lags expose future bars, and missing observations may become zero.
`ts_argmax` returns the normalized position of the maximum in the window, not bars
since the maximum. These contracts must be repaired before expanding the grammar.

`AlphaMiner` samples six randomized templates and a seven-formula catalog. It is
not an evolutionary/genetic algorithm. Some catalog WorldQuant names refer to
adaptations rather than the corresponding published expressions. The CLI mines on
the first requested symbol and evaluates survivors on the others. Defaults are two
years of daily bars and 15 random candidates; launchd requests 25 each Saturday.
Generated definitions still specify 4h. The scheduled job does not auto-promote.

### Live decisions and lifecycle

`FormulaicAlphaStrategy` evaluates promoted definitions from the loaded registry.
It standardizes scores over 30 bars (minimum five) and emits LONG/SHORT candidates
at the configured entry threshold. It may substitute another timeframe when data
is missing, a documented defect. Research uses a different 50-bar normalization
and score-decay exits. Live execution instead follows evaluator sizing, configured
brackets, approval, entry admission and position monitoring; the definition's
`exit_threshold` is not an active live score-exit policy.

Candidates carry a logical strategy ID. Definitions can currently be overwritten
under that ID, so it is not an immutable strategy-version reference. External
YAML changes require restart; CLI and Telegram file reports can differ from the
running registry. `eligible_symbols: null` permits screening across configured
instruments; an explicit list restricts routing but does not prove validation.
Promotion weights are metadata and do not determine live capital allocation.

### Validation, orthogonality and optimization

Research reports rolling forward Rank IC, simulated returns and DSR. The 70/30
split's trailing segment is used for selection, so it is not an untouched test.
DSR currently receives annualized Sharpe with an observation-count formula; trial
accounting and some metric labels also need correction. Do not interpret displayed
DSR as the probability of a profitable live strategy.

The CLI uses pseudoinverse projection `candidate - A @ pinv(A) @ candidate` to
measure residual association with formulaic incumbents. It fits the whole sample
and currently compares to contemporaneous rather than forward returns. Rejected
or failed novelty evidence can still reach auto-promotion. This is an unresolved
admission gap, not a reliable orthogonality guarantee.

For weighted residualization, `M = I - X(X'WX)^+ X'W` satisfies `X' W M = 0`;
it generally is neither symmetric nor neutral under the unweighted `X' M` test.
Neutral residual scores do not imply neutral executed portfolio weights.

`ConvexAlphaPortfolioOptimizer` is a research-only SLSQP mean-variance/turnover
optimizer. It has gross, box, optional net-neutrality and factor bounds. It does
not maximize Sharpe, enforce a fully invested budget or automatically choose an
inverse-volatility fallback. Input validation, label alignment and independent
feasibility checks are incomplete. No CLI command or live sizing path applies its
weights. Existing execution risk caps continue to govern individual entries.
