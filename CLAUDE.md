# CLAUDE.md

Guidelines and reference commands for AI coding assistants working in the `agentic-trader` repository.

---

## 1. Project Overview
The **Cash-Plus Futures Copilot** is an automated personal trading copilot for a $100k cash portfolio. It runs quantitative 4-hour candle scans across liquid micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`), evaluates candidates with an LLM agent with real-time macro calendar awareness, and dispatches actionable alert cards with interactive buttons to Telegram for manual execution in Robinhood.

---

## 2. Common Commands

### Virtual Environment & Dependency Management
- Manage environment with `uv`. Python 3.12+ (managed locally as 3.14).
- Sync dependencies: `uv sync`
- Install pre-commit hooks: `uv run pre-commit install`

### Running the Application (`copilot` CLI)
- Status: `uv run copilot status`
- Scan (live): `uv run copilot scan`
- Scan (dry-run, no alerts/db updates): `uv run copilot scan --dry-run`
- Scan (deterministic rule fallback, no LLM call): `uv run copilot scan --no-llm`
- Test alert: `uv run copilot test-alert`
- Daemon (scheduler + Telegram listener): `uv run copilot daemon`
- Telegram listener only: `uv run copilot listen`
- Prompt evaluation: `uv run copilot eval`

### Testing, Linting & Quality Control
- **Pre-commit checks (mandatory before any commit)**:
  `uv run pre-commit run --all-files`
- Run unit tests:
  `uv run pytest`
- Run linting:
  `uv run ruff check --fix`
- Run formatting:
  `uv run ruff format`
- Run type checking:
  `uv run mypy agentic_trader`
- Run Promptfoo evals:
  `npx -y promptfoo eval -c evals/promptfooconfig.yaml --no-cache`

---

## 3. Architecture & Key Directory Layout
- `agentic_trader/config.py` & `config/config.yaml`: Multipliers, tick sizes, risk ceilings, and env var loader.
- `agentic_trader/data/market_data.py`: Multi-timeframe bar data fetcher (Daily, 4h, 1h via `yfinance`).
- `agentic_trader/screeners/`:
  - `indicators.py`: Vectorized technical indicators (EMA, RSI-Wilder, ATR, Bollinger, Keltner, Squeeze).
  - `strategies.py`: Quantitative screening algorithms (Strategy A: Trend-Pullback; Strategy B: Squeeze Breakout).
- `agentic_trader/agent/`:
  - `calendar.py`: Economic calendar parser and macro lockout logic.
  - `evaluator.py`: LiteLLM trade evaluator, schema validation, and invariant enforcement.
  - `prompts.py`: System prompt and structured few-shot evaluation prompts.
- `agentic_trader/storage/db.py`: SQLite persistence (`data/copilot.db`), deduplication, and active notional exposure tracking.
- `agentic_trader/notifier/telegram_bot.py`: Alert card formatting, Telegram inline callback dispatcher, and bot commands (`/status`, `/scan`, `/help`).
- `evals/`: Promptfoo configuration and evaluation benchmarks.
- `scripts/`: macOS `launchd.sh` daemon supervision script.

---

## 4. Hard Domain Invariants & Business Rules
When writing or modifying logic, NEVER violate these core constraints:
1. **Instrument Scope**: Micro futures ONLY: `/MES`, `/MNQ`, `/MGC`, `/MCL`. No standard contracts, no crypto, no equities.
2. **Fixed Sizing**: Exactly 1 micro contract per signal.
3. **Notional Exposure Ceiling**: $\le \$60,000$ total active open notional exposure across all concurrent positions (0.6x effective leverage on $100k cash). Reject any candidate where `current_notional + candidate_notional > $60,000`.
4. **Risk-to-Reward Ratio**: Strictly $R:R \ge 2.0$.
5. **Structural Stop Distance**: Minimum stop distance $\ge 1.5 \times \text{ATR}(14)$. Anchor behind swing highs/lows with this floor.
6. **Macro Lockout**: NO entry alerts within $[-60\text{m}, +30\text{m}]$ of Tier-1 releases (CPI, PPI, FOMC, NFP).
7. **Signal Deduplication**: No duplicate signal for the same contract + strategy within 12 hours.

---

## 5. Coding Style & Guidelines
- **Python Version**: Modern Python 3.12+ features (use `X | None` instead of `Optional[X]`, builtin generics `list[T]`, `dict[K, V]`).
- **Imports**: Clean top-level imports sorted by Ruff / isort. Avoid inline imports inside async functions (`PLC0415`).
- **Async Execution**: Use `asyncio.create_subprocess_exec` rather than blocking `subprocess.run` inside async code (`ASYNC221`).
- **Type Safety**: Full type annotations on all function signatures and public class attributes. Strict Mypy adherence.
- **Pydantic**: Use Pydantic v2 models for structured schemas and LLM payloads.
- **Secrets & Git**: Never commit `.envrc` or credentials. Keep `.envrc` in `.gitignore` and maintain clean placeholders in `.envrc.example`.
- **Pre-commit**: Always run `uv run pre-commit run --all-files` before finalizing changes. All hooks must pass.
