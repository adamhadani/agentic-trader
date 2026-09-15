---
layout: default
title: Quantitative Strategies & Models - Agentic Trader
---

> Runtime note (2026-09-15): research backtests and formulaic return streams do not
> produce live performance records. Broker reports use actual Alpaca fills and account
> valuations. Promotion allocation weights and the convex optimizer remain research
> metadata/library functionality, not live position sizing. See [development notes](development-notes.md).

# 📊 Quantitative Strategies & Econometric Models

This document details the mathematical and statistical formulations governing the quantitative screening engines and econometric analytics in the **Agentic Trader**.

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
   - Relative Strength Index (Wilder's RSI-14) must indicate temporary oversold/overbought condition:
     - **Long**: $\text{RSI}(14) \le 40.0$ with upward tick.
     - **Short**: $\text{RSI}(14) \ge 60.0$ with downward tick.
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

## 3. Strategy C: Options Implied Volatility Surface & GEX

### Objective
Analyze real-time option chains across index ETFs (`SPY`, `QQQ`, `IWM`) and futures proxies to infer market maker positioning, structural support/resistance walls, and volatility regimes.

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
   - **Gamma Flip**: Price level where total net dealer gamma crosses zero ($+\text{GEX} \leftrightarrow -\text{GEX}$).

---

## 4. Strategy D: Cointegration & Statistical Pairs Arbitrage

### Objective
Identify stationary, mean-reverting linear combinations of cross-asset prices and generate statistical arbitrage spread trades.

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
To eliminate curve-fitting and multi-testing p-hacking:
1. **Spearman Rank Information Coefficient**:
   $$\text{Rank IC} = \text{corr}_{\text{rank}}(\alpha_t, R_{t+1})$$
   $$\text{IC\_IR} = \frac{\mu_{\text{IC}}}{\sigma_{\text{IC}}}$$
2. **Deflated Sharpe Ratio (DSR)**:
   Accounts for the number of tested trials $N$, sample length $T$, skewness $\gamma_3$, and kurtosis $\gamma_4$:
   $$\text{DSR} = \Phi\left(\frac{(\widehat{\text{SR}} - \text{SR}^*) \sqrt{T-1}}{\sqrt{1 - \widehat{\gamma}_3 \widehat{\text{SR}} + \frac{\widehat{\gamma}_4 - 1}{4}\widehat{\text{SR}}^2}}\right)$$
   Alphas are only eligible for auto-promotion if $\text{DSR} \ge 0.85$ (typically $\ge 0.95$ for high-confidence institutional deployment).

### Signal Orthogonalization Pipeline
Before promoting an alpha candidate into production, it is checked for collinearity against the incumbent promoted strategy subspace via **Gram-Schmidt Residualization**:
$$\alpha_{\text{ortho}} = \alpha_{\text{cand}} - \sum_{k=1}^K \frac{\langle \alpha_{\text{cand}}, \alpha_k \rangle}{\|\alpha_k\|^2} \alpha_k$$
1. **Linear Independence**: Guarantees $\langle \alpha_{\text{ortho}}, \alpha_k \rangle = 0$ for all existing portfolio alphas.
2. **Residual Predictive Power**: Evaluates whether the orthogonal residual maintains incremental alpha against forward returns $r$:
   $$\text{IC}(\alpha_{\text{ortho}}) = \frac{\langle \alpha_{\text{ortho}}, r \rangle}{\|\alpha_{\text{ortho}}\| \|r\|}$$
   Candidates with residual $\text{IC} < 0.015$ are rejected as redundant linear repackagings.
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
Hyperbolic $L_1$ turnover smoothing ($\epsilon = 10^{-6}$) guarantees continuous differentiability and numerical stability without derivative stalls.

### Multi-Asset Universe Routing (`eligible_symbols`)
Each formulaic alpha can be targeted to specific market universes where its factor dynamics are statistically validated:
- `eligible_symbols: null` $\rightarrow$ Universal execution across all configured contracts.
- `eligible_symbols: ["NVDA", "AMD"]` $\rightarrow$ Targeted execution restricted to high-beta semiconductor equities.
- `eligible_symbols: ["QQQ", "SPY"]` $\rightarrow$ Broad index ETF regime momentum.
