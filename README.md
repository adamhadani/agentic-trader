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

## 3. System Prerequisites & Homebrew Dependencies

On macOS, install the required development tools and linters using [Homebrew](https://brew.sh/):

```bash
# Core package manager & Python runner
brew install uv

# Dockerfile linter (enforced in pre-commit)
brew install hadolint

# SQLite CLI for database inspection
brew install sqlite

# Node / npx (required for running Promptfoo evals via `copilot eval`)
brew install node

# Optional: Shell environment manager for .envrc
brew install direnv

# Optional: Docker Desktop (for containerized runs)
brew install --cask docker
```

---

## 4. Configuration & Environment (`.envrc`)

Copy `.envrc.example` to `.envrc` and fill in your secrets:

```bash
cp .envrc.example .envrc
# Edit with your API keys:
direnv allow  # or source .envrc
```

Key environment variables:
```bash
# Telegram Bot Configuration
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"

# LLM Provider Key & Model (LiteLLM format)
export LLM_MODEL="openai/gpt-5.6" # or anthropic/claude-3-5-sonnet, gemini/gemini-2.5-flash
export OPENAI_API_KEY="sk-..."

# Optional: LangSmith LLM Tracing & Observability
export LANGCHAIN_TRACING_V2="false"
export LANGSMITH_API_KEY="" # or LANGCHAIN_API_KEY
export LANGCHAIN_PROJECT="futures-copilot"

# Optional Portfolio Overrides
export PORTFOLIO_CASH="100000"
export MAX_NOTIONAL_EXPOSURE="60000"

# Broker Execution Mode ("paper" [default], "tradovate", or "manual")
export EXECUTION_MODE="paper"

# Tradovate Credentials (required if EXECUTION_MODE="tradovate")
export TRADOVATE_ENVIRONMENT="demo"  # "demo" or "live"
export TRADOVATE_API_KEY=""
export TRADOVATE_API_SECRET=""
export TRADOVATE_USERNAME=""
export TRADOVATE_PASSWORD=""
export TRADOVATE_ACCOUNT_ID=""
```

*Note: If Telegram credentials are not set, the copilot prints formatted alert cards directly to the terminal for local review.*

---

## 5. CLI Usage & Subcommands

Run commands via `uv run copilot <command>` or `copilot <command>` (if the virtualenv is active):

### Check Portfolio Status & Signals
```bash
uv run copilot status
```
Displays current cash base, active notional exposure, effective leverage, macro calendar context, and recent signals from SQLite.

### View Active Positions
```bash
uv run copilot positions
```
Displays all currently tracked positions, live quotes, stop/target prices, and mark-to-market unrealized P&L.

### Execute a Trade Signal
```bash
uv run copilot execute <signal_id>
```
Executes an approved signal via the configured broker (`PaperBroker` fills against live quotes; `TradovateBroker` sends native OCO bracket orders). Enforces portfolio risk invariants before submission.

### Manually Close an Active Position
```bash
uv run copilot close <signal_id> [exit_price]
```
Closes an open position at the broker, records realized P&L in SQLite, releases notional exposure, and emits an exit alert card.

### Run a Market Scan
```bash
# Live scan with LLM evaluation and alerting
uv run copilot scan

# Dry-run scan (evaluates setups without saving to DB or dispatching alerts)
uv run copilot scan --dry-run

# Run scan using deterministic structural rules (bypassing LLM calls)
uv run copilot scan --no-llm
```

### Send a Test Alert
```bash
uv run copilot test-alert
```
Dispatches a synthetic `/MES` trade signal card to verify Telegram formatting, broker execution button, and terminal display.

### Run Background Daemon
```bash
uv run copilot daemon
```
Starts APScheduler running scans every 4 hours aligned with candle closes, monitors active positions every 15 minutes for take-profit/stop-loss triggers, and starts the two-way Telegram Bot listener.

### Telegram Two-Way Interaction
When the daemon or listener is running, you can message the bot directly in Telegram:
- `/start` or `/help` - Command menu and risk invariants
- `/status` - Current active exposure, open notional, leverage, and last 5 signals
- `/positions` - Tracked positions and real-time unrealized P&L
- `/close <id> [price]` - Manually close a tracked trade
- `/scan` - Trigger an immediate quantitative scan across micro futures
- **Interactive Inline Buttons**:
  - `[ 🚀 Execute (Paper) ]` / `[ 🚀 Approve & Execute ]` -> Runs pre-execution risk checks, submits order to broker, stores broker order ID, and activates position tracking.
  - `[ ❌ Dismiss Signal ]` -> Marks signal as `DISMISSED`.

To test Telegram listening in isolation without running the scanner scheduler:
```bash
uv run copilot listen
```

---

## 6. LLM Prompt Evaluations (Promptfoo)

We use [Promptfoo](https://www.promptfoo.dev/) for deterministic benchmark evaluations of our trade evaluation prompts against hard risk invariants.

Run the test suite anytime:
```bash
uv run copilot eval
# or directly:
npx -y promptfoo eval -c evals/promptfooconfig.yaml --no-cache
```

`copilot eval` automatically maps your configured production `LLM_MODEL` (e.g. `openai/gpt-5.6`) to Promptfoo, testing 4 critical scenarios:
1. Valid Bullish Trend-Pullback on `/MES` (verifies approval, $R:R \ge 2.0$, stop $\ge 1.5 \times \text{ATR}$).
2. Macro Event Lockout during Tier-1 CPI (verifies rejection).
3. Poor Risk-to-Reward / Overhead Resistance (verifies rejection).
4. Portfolio Notional Exposure Ceiling Violation ($>\$60\text{k}$ total open exposure, verifies rejection).

---

## 7. Background Deployment & Supervision

### macOS `launchd` (Recommended for Local Mac)
To keep the copilot running continuously during trading hours with automatic restarts:
```bash
./scripts/launchd.sh install   # Installs ~/Library/LaunchAgents/com.agentictrader.copilot.plist and starts daemon
./scripts/launchd.sh status    # Check if launchd is running the service
./scripts/launchd.sh logs      # Tail data/copilot.log in real time
./scripts/launchd.sh stop      # Temporarily pause service
./scripts/launchd.sh uninstall # Unload and remove plist
```

### Docker & Docker Compose
To run containerized with persistent SQLite storage:
```bash
# Build and run in detached mode
docker compose up -d

# View container logs
docker compose logs -f

# Stop container
docker compose down
```

---

## 8. Development & Quality Assurance

This repository enforces strict code quality via `pre-commit`:

```bash
# Install git hooks
uv run pre-commit install

# Run all checks manually
uv run pre-commit run --all-files

# Run pytest unit tests
uv run pytest
```
Included pre-commit hooks:
- **ruff**: Linter and formatter
- **mypy**: Type checking across the codebase
- **pytest**: Automated unit tests
- **uv-lock**: Lockfile consistency checks
- **pre-commit-hooks**: File sanitization, trailing whitespace, secrets detection
