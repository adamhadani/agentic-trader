---
layout: default
title: Quantitative Strategies & Models - Cash-Plus Trading Copilot
---

# 📊 Quantitative Strategies & Econometric Models

This document details the mathematical and statistical formulations governing the quantitative screening engines and econometric analytics in the **Cash-Plus Trading Copilot**.

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
