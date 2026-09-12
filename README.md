# Cash-Plus Futures Copilot

An automated personal trading copilot designed for a "Cash-Plus" portfolio ($100,000 baseline cash generating money-market yield). The system runs scheduled quantitative scans across liquid micro futures contracts (`/MES`, `/MNQ`, `/MGC`, `/MCL`), evaluates setups via an LLM agent with real-time macro awareness, and delivers actionable swing-trade alert cards directly to a private Telegram chat (with interactive acknowledgment callbacks) for manual execution in Robinhood.

---

## 1. Core Risk & Portfolio Constraints
1. **Instrument Scope:** Exclusively Micro futures:
   - `/MES` (Micro S&P 500) - Multiplier: $5.00/pt, Tick: 0.25 ($1.25)
   - `/MNQ` (Micro Nasdaq-100) - Multiplier: $2.00/pt, Tick: 0.25 ($0.50)
   - `/MGC` (Micro Gold) - Multiplier: $10.00/pt, Tick: 0.10 ($1.00)
   - `/MCL` (Micro WTI Crude) - Multiplier: $100.00/pt, Tick: 0.01 ($1.00)
2. **Fixed Sizing:** Exactly 1 micro contract per signal. Total active open notional exposure across all concurrent positions must not exceed $60,000 (0.6x effective leverage on $100k cash).
3. **Reward-to-Risk (R:R):** Strictly $\ge 2.0$. Stop distance must be $\ge 1.5 \times \text{ATR}(14)$ to prevent premature stop-outs from noise.
4. **Macro Event Lockout:** Zero entry alerts permitted within 60 minutes before or 30 minutes after scheduled Tier-1 economic releases (CPI, PPI, FOMC rate decisions/pressers, Non-Farm Payrolls).
5. **Deduplication Rule:** Do not emit if a signal for the same contract + strategy exists within 12 hours.

---

## 2. Quantitative Screening Strategies
- **Strategy A: Trend-Pullback (Mean Reversion)**
  - Daily Invariant: Long if Close > EMA(50) > EMA(200); Short if Close < EMA(50) < EMA(200).
  - 4h Trigger: RSI(14) dip & recovery near the 20 EMA (within 0.5 * ATR(14)).
- **Strategy B: Volatility Squeeze Breakout**
  - Squeeze Condition: Bollinger Bands (20, 2.0) fully enclosed inside Keltner Channels (20, 1.5 ATR) for $\ge 5$ consecutive candles.
  - Breakout Trigger: Candle close outside Bollinger Bands with Volume surge $> 1.3 \times \text{SMA}(\text{Volume}, 20)$.

---

## 3. Configuration & Environment (`.envrc`)

Environment tokens and overrides are configured in `.envrc`:

```bash
# Telegram Bot Token & Chat ID
export TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"
export TELEGRAM_CHAT_ID="your_telegram_chat_id_here"

# LLM Provider Key & Model
export LLM_MODEL="openai/gpt-5.6" # or gemini/gemini-2.5-flash, anthropic/claude-3-5-sonnet-20241022
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY=""
export ANTHROPIC_API_KEY=""

# Optional Portfolio Overrides
export PORTFOLIO_CASH="100000"
export MAX_NOTIONAL_EXPOSURE="60000"
```

*Note: If Telegram credentials are not yet set, the copilot prints formatted alert cards directly to the terminal for local review.*

---

## 4. Usage & CLI Commands

All commands can be run with `uv run copilot <command>` or `uv run python -m agentic_trader.main <command>`:

### Check Portfolio Status & Recent Signals
```bash
uv run copilot status
```
Displays current cash base, active notional exposure, effective leverage, macro calendar context, and recent signals from the SQLite database.

### Run a One-Time Market Scan
```bash
# Live scan with LLM evaluation and alerting
uv run copilot scan

# Dry-run scan (evaluates setups without saving to DB or dispatching alerts)
uv run copilot scan --dry-run

# Run scan using deterministic risk rules (bypassing LLM calls)
uv run copilot scan --no-llm
```

### Send a Test Alert
```bash
uv run copilot test-alert
```
Dispatches a synthetic `/MES` trade signal card to verify Telegram connectivity and layout formatting.

### Start Background Daemon & Scheduler
```bash
uv run copilot daemon
```
Starts APScheduler running scans every 4 hours aligned with candle closes, and starts the Telegram Bot callback listener for real-time button clicks (`[ ✅ Acknowledge & Tracking ]` and `[ ❌ Dismiss Signal ]`).

---

## 5. Running the Test Suite
```bash
uv run pytest
```
Runs the 13 automated unit tests covering technical indicators, strategy screeners, SQLite deduplication and exposure tracking, and risk evaluator invariants.
