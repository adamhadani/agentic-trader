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

## 5. Strategy E: Formulaic Alpha DSL & Promoted Strategy Screener

### Objective
Provide a flexible domain-specific language (DSL) for formulaic alpha modeling (inspired by WorldQuant 101 Alphas and academic factor literature), dynamic genetic mining, statistical overfitting validation, and production trade execution.

### DSL Formulation & Safe AST Parsing
The alpha DSL uses a safe Abstract Syntax Tree (AST) evaluator without `eval()` or code generation risks:
1. **Mathematical & Transform Operators**:
   - $\text{sign}(x)$, $\text{abs}(x)$, $\log(x)$, $\text{scale}(x)$ (sum-normalized absolute weights), $\text{rank}(x)$, $\text{zscore}(x)$.
2. **Time-Series Operators**:
   - $\Delta(x, d) = x_t - x_{t-d}$
   - $\text{SMA}(x, d)$: Simple moving average over window $d$.
   - $\text{TS\_Rank}(x, d)$: Rolling percentile rank of the current value within the last $d$ bars.
   - $\text{TS\_Corr}(x, y, d)$: Rolling Pearson correlation between series $x$ and $y$ over $d$ bars.
   - $\text{TS\_Std}(x, d)$: Rolling sample standard deviation.
   - $\text{TS\_Argmax}(x, d)$: Number of bars since maximum value was achieved over lookback $d$.
   - $\text{Decay\_Linear}(x, d)$: Linearly weighted moving average assigning weight $w_i = d - i$.

### Production Execution (`FormulaicAlphaStrategy`)
Promoted alphas persisted in `config/promoted_alphas.yaml` are loaded into `StrategyRegistry`:
1. **Rolling Normalization**:
   Raw formula scores are standardized using a rolling 30-bar Z-score:
   $$Z_t = \frac{\alpha_t - \mu_{\alpha, 30}}{\sigma_{\alpha, 30}}$$
2. **Threshold Triggers**:
   - **Long**: $Z_t \ge \theta_{\text{entry}}$
   - **Short**: $Z_t \le -\theta_{\text{entry}}$
3. **Dynamic ATR Risk Management**:
   Every generated candidate is stamped with an immutable strategy ID (e.g. `alpha_wq_006`) and paired with dynamic ATR(14) brackets:
   - **Stop Loss**: $1.5 \times \text{ATR}(14)$ beyond entry.
   - **Take Profit**: $\ge 2.0 \times \text{Risk Distance}$ ($R:R \ge 2.0$).

### Statistical Overfitting Gating (DSR & Rank IC)
To penalize multiple testing and measure out-of-sample association (without eliminating overfitting):
1. **Spearman Rank Information Coefficient**:
   $$\text{Rank IC} = \text{corr}_{\text{rank}}(\alpha_t, R_{t+1})$$
   $$\text{IC\_IR} = \frac{\mu_{\text{IC}}}{\sigma_{\text{IC}}}$$
2. **Deflated Sharpe Ratio (DSR)**:
   Accounts for the number of tested trials $N$, sample length $T$, skewness $\gamma_3$, and kurtosis $\gamma_4$:
   $$\text{DSR} = \Phi\left(\frac{(\widehat{\text{SR}} - \text{SR}^*) \sqrt{T-1}}{\sqrt{1 - \widehat{\gamma}_3 \widehat{\text{SR}} + \frac{\widehat{\gamma}_4 - 1}{4}\widehat{\text{SR}}^2}}\right)$$
   The mining DSR threshold defaults to 0.85 and is configurable. CLI auto-promotion
   is explicit; the scheduled miner does not enable it. These thresholds do not
   establish future profitability.

### Signal Orthogonalization Pipeline
The mining report checks residual association against the incumbent signal
subspace using a pseudoinverse projection. It is not a universal promotion gate:
$$\alpha_{\text{ortho}} = \alpha_{\text{cand}} - A A^+ \alpha_{\text{cand}}$$
1. **Linear Independence**: Projects away the supplied incumbent subspace within numerical precision;
   this is a sample calculation, not a guarantee of future independence.
2. **Residual Predictive Power**: Evaluates whether the orthogonal residual maintains incremental alpha against forward returns $r$:
   $$\text{IC}(\alpha_{\text{ortho}}) = \frac{\langle \alpha_{\text{ortho}}, r \rangle}{\|\alpha_{\text{ortho}}\| \|r\|}$$
   The report uses a default residual-IC threshold of 0.015 to label novelty;
   inspect the CLI qualification and promotion path before treating that label as a gate.
3. **Factor Annihilator Operator ($M_X$)**:
   Multi-factor projection operator neutralizing market beta and sector risk:
   $$M_X = I_N - X(X^T W X)^{-1} X^T W, \quad X^T M_X = 0$$

### Convex Alpha Portfolio Optimizer
Optimal portfolio weights across promoted alphas and asset universes are solved via convex quadratic/nonlinear programming (`scipy.optimize.minimize(method='SLSQP')`):
$$\max_{w} \quad w^T \mu - \frac{\lambda}{2} w^T \Sigma w - \gamma \sum_{i=1}^N \sqrt{(w_i - w_{0,i})^2 + \epsilon^2}$$
Subject to:
- Gross leverage: $\sum_{i=1}^N |w_i| \le L_{\max}$
- Box limits: $w_{\min} \le w_i \le w_{\max}$
- Factor bounds: $b_{\text{lower}} \le X^T w \le b_{\text{upper}}$
Smoothed turnover uses $\epsilon = 10^{-6}$ in the research optimizer. Solver
convergence and useful allocations require validation; these weights are not used
by the live sizing path.

### Multi-Asset Universe Routing (`eligible_symbols`)
Each formulaic alpha can be targeted to specific market universes where its factor dynamics are statistically validated:
- `eligible_symbols: null` $\rightarrow$ Eligible screening across configured contracts, still subject to approval/risk.
- `eligible_symbols: ["NVDA", "AMD"]` $\rightarrow$ Eligible screening restricted to high-beta semiconductor equities.
- `eligible_symbols: ["QQQ", "SPY"]` $\rightarrow$ Broad index ETF regime momentum.
