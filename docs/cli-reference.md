---
layout: default
title: CLI Command Reference - Agentic Trader
---

# 💻 CLI Command Reference Manual

The `copilot` CLI provides a unified command hierarchy for production daemon execution, day-to-day desk monitoring, quantitative backtesting, parameter optimization, and database migrations.

---

## 1. Production Daemon & Services

### `copilot daemon`
Runs the continuous steady-state trading service.
```bash
uv run copilot daemon
```
- **Components Executed**:
  - APScheduler 4-hour swing scans and session-gated 15-minute intraday scans, relative to startup.
  - 1-minute position reconciliation and bracket take-profit / stop-loss reconciler.
  - Alpaca WebSocket `TradingStream` wakeups (no latency guarantee).
  - Interactive two-way Telegram bot listener.
  - Embedded Prometheus metrics and health check HTTP server on `0.0.0.0:9108`.

### `copilot listen`
Runs the two-way interactive Telegram bot in isolation without running the quantitative scheduler or position monitor.
```bash
uv run copilot listen
```

---

## 2. Day-to-Day Operator Runbook

### `copilot status`
Displays configured risk capital, tracked notional exposure, leverage utilization and macro calendar events. It does not report broker cash.
```bash
uv run copilot status
```

### `copilot positions`
Displays the broker account snapshot: actual average entry, quantity, current price and unrealized P&L, plus tracked stops/targets. CLI and Telegram share the same builder. Source/time and tracking mismatches are shown; failures are not reported as empty positions.
```bash
uv run copilot positions
```

### `copilot scan`
Triggers an immediate quantitative universe scan across configured assets.
```bash
# Live scan with LLM evaluation and alerting
uv run copilot scan

# Dry-run scan (evaluates setups using temporary storage and no production writes/alerts)
uv run copilot scan --dry-run

# Run scan using deterministic rules (bypassing LLM)
uv run copilot scan --no-llm

# Filter by asset class
uv run copilot scan --asset-class futures
uv run copilot scan --asset-class equities

# Specific symbol override
uv run copilot scan --symbols /MES,/MNQ,SPY

# Bypass session hours and market closure check (e.g. testing off-hours)
uv run copilot scan --bypass-session-filter --dry-run
```

### `copilot execute <signal_id>`
Authorizes a signal through the durable entry queue. Fresh admission supports Alpaca equities and local simulation; other adapters fail closed until they implement that contract.
```bash
uv run copilot execute 4
```

### `copilot close <signal_id> [--price PRICE] [--dry-run]`
Close a tracked position without halting trading. Alpaca verifies the exact entry,
position quantity/direction/cost basis, cancels symbol orders, waits for their
terminal states and released quantity, then submits a market close with a durable
client order ID. P&L uses confirmed fills. `--price` is optional and only affects
simulation/manual adapters. `--dry-run` reads the position without changing orders.

```bash
uv run copilot close 5 --dry-run
uv run copilot close 5
```

### `copilot flatten [--confirm] [--dry-run]`
Preview all current broker positions by default. `--confirm` submits coordinated
closes; `--dry-run` always takes precedence. This preserves the existing halt state:
it neither halts trading nor clears a prior panic. Scans and recommendations continue
subject to the existing risk/session/deduplication checks.

```bash
uv run copilot flatten --dry-run
uv run copilot flatten --confirm
```

Telegram equivalents: `/close 5`, `/flatten`, `/flatten dry-run`, `/flatten confirm`.
Flatten includes broker-only positions without inventing tracked trade history. Actual executions still enter reconciled account-level performance.
Ambiguous tracked ownership or partial quantities are reported for reconciliation.
Orders on position symbols are cancelled; unfilled entries in other symbols remain.
Each position has its own result: a rejected close does not stop other closes.
Equity closes outside regular hours are refused before protective orders are changed.
The command supports the Alpaca authoritative-position adapter; other adapters
continue to use their existing single-position close behavior.
See [close operations and recovery](production.md#coordinated-close-and-flatten).

### `copilot panic [--confirm] [--reason "TEXT"]`
Persists a trading halt, requests order cancellation and market exits. Pending/rejected exits remain visible; acceptance is not confirmed liquidation.
```bash
# Prompt for interactive confirmation
uv run copilot panic

# Immediate execution (bypass confirmation prompt)
uv run copilot panic --confirm --reason "Extreme volatility circuit breaker"
```

### `copilot resume`
Clears persistent emergency trading halt and restores universe scans and operator-approved entry flow, after unresolved-submission checks.
```bash
uv run copilot resume
```

### `copilot test-alert`
Previews a non-actionable `[TEST]` message locally. Add `--send` with dedicated `TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` to test a separate bot/chat; no signal or broker call is created.
```bash
uv run copilot test-alert
```

### `copilot gex [symbol]`
Estimates GEX and strike-based gamma flip/walls from Yahoo option chains and model assumptions. It does not observe dealer inventory.
```bash
# Standard ASCII report
uv run copilot gex SPY

# Specify expiration depth
uv run copilot gex QQQ --expirations 5

# JSON output
uv run copilot gex /MES --json
```

### `copilot pairs`
Screens cross-asset pairs for cointegration (Engle-Granger test), Ornstein-Uhlenbeck mean-reversion half-life, and rolling spread $Z$-score arbitrage signals.
```bash
# Scan default institutional pairs
uv run copilot pairs

# Evaluate a specific pair
uv run copilot pairs --pair SPY/QQQ

# Screen all pairwise combinations among a list
uv run copilot pairs --symbols SPY,QQQ,IWM,GLD

# Customize lookback and thresholds
uv run copilot pairs --lookback 252 --p-value 0.05 --z-entry 2.0 --z-exit 0.5

# Raw JSON output
uv run copilot pairs --json
```

### `copilot metrics`
Dumps a point-in-time Prometheus exposition snapshot or launches a standalone HTTP metrics server.
```bash
# Dump Prometheus exposition text to stdout
uv run copilot metrics

# Launch standalone metrics server on custom port
uv run copilot metrics --serve --port 9109
```

### `copilot explain-macro`
Generates an educational, executive tutorial briefing synthesizing latest published macroeconomic observations (10Y-2Y yield curve slope in bps, High Yield OAS credit spreads, VIX volatility context, 5Y/10Y TIPS inflation breakevens, and Copilot risk sizing). Powered by LLM synthesis with an exhaustive deterministic rule-based fallback.
```bash
uv run copilot explain-macro
```

---

## 3. Quantitative Research & Backtesting

### `copilot backtest`
Runs an offline historical backtest with transaction friction, slippage, Cash-Plus attribution, and optional Monte Carlo simulation.
```bash
# Run across default futures universe
uv run copilot backtest

# Backtest specific symbols and strategy
uv run copilot backtest --symbols SPY,QQQ,IWM --strategy trend_pullback --lookback 2y

# Run with Monte Carlo simulation (1,000 runs)
uv run copilot backtest --symbols /MES --monte-carlo --mc-sims 1000

# Trailing stop policy options:
# --trailing-stop-mode: none, breakeven_and_trail, chandelier_atr (default)
uv run copilot backtest --symbols SPY --trailing-stop-mode chandelier_atr --trail-trigger-r 1.5 --trail-atr-multiple 1.5

# Retail breakeven test (moving stop to entry at 1.0R):
uv run copilot backtest --symbols /MES --trailing-stop-mode breakeven_and_trail --breakeven-trigger-r 1.0

# Frictionless benchmark
uv run copilot backtest --no-friction
```

### Retired research commands

`optimize` and `retune` have been removed, including scheduled retuning and config
export. Their separate simulator/trial accounting was unreliable. Use journal-backed
`alpha mine`, `alpha benchmark`, panel/replay and explicit qualification instead.
See [research contracts](forecast-contract-hardening.md).

`alpha portfolio SNAPSHOT.json --output REPORT.json` requires explicit matching
forecast/risk contracts and persists an immutable private input/output audit. It is
shadow-only and rejects nonzero pending orders.

### `copilot stress`
Performs tail-risk evaluation by replaying strategy execution through historical macro crises or simulating instantaneous cross-asset factor shocks.
```bash
# Replay all historical crisis scenarios
uv run copilot stress --scenario all

# Specific crisis replay
uv run copilot stress --scenario 2008_gfc
uv run copilot stress --scenario 2020_covid
uv run copilot stress --scenario 2022_inflation

# Instantaneous parametric factor shocks
uv run copilot stress --scenario shock
```

### `copilot eval`
Runs Promptfoo benchmark evaluations of trade decision prompts against hard risk invariants.
```bash
uv run copilot eval
```

---

## 4. Alpha discovery and promotion (`copilot alpha`)

The [alpha pipeline guide](alpha-pipeline.md#commands-and-cadence) is the canonical
command and contract reference. `catalog` lists hypotheses; `list`, `status`,
`inspect VERSION_ID` and `export` read journal state. `inspect CATALOG_ID` displays
a definition without downloading data or inventing a performance result.

```bash
uv run copilot alpha calibrate --seeds 10 --bootstrap-samples 499
uv run copilot alpha study-plan --seed 10472909262026 --output /private/path/new-protocol.json
uv run copilot alpha study config/research/a2a-v1.json --output /private/path/new-study-directory
uv run copilot alpha replay 'delta(close,3)' --symbol SPY --interval 15m --start 2024-11-27 --end 2024-11-29 --output /private/path/new-replay
uv run copilot alpha mine --symbol SPY --feed alpaca --interval 1d --lookback 5y --iterations 25 --method random
uv run copilot alpha mine --universe etf32 --feed alpaca --method genetic --iterations 9 --max-seconds 120
uv run copilot alpha benchmark RUN_ID --method ridge --budget 5 --horizon 1
uv run copilot alpha benchmark RUN_ID --method single --budget 7 --horizon 5
uv run copilot alpha panel-study config/research/sector-panel-v1.json --output /private/path/new-panel
uv run copilot alpha qualify RUN_ID VERSION_ID
uv run copilot alpha shadow VERSION_ID --generation N
uv run copilot alpha promote VERSION_ID --generation N
uv run copilot alpha demote VERSION_ID --generation N
uv run copilot alpha portfolio /private/path/observed-snapshot.json --output /private/path/shadow-report.json
uv run copilot alpha import config/promoted_alphas.yaml
uv run copilot alpha test --symbol NVDA --interval 1d -- '-1.0 * delta(ts_rank(volume, 10), 5)'
```

`mine` persists all trials and leaves holdout untouched. `qualify` consumes the
frozen holdout once. Promotion requires exact version, generation, passing evidence,
deployment data contract and observed shadow history. Import is shadow-only;
`--auto-promote`, allocation metadata and symbol/timeframe-changing promotion flags
are removed. Demotion changes future screening only; use the shared close/flatten
commands separately when liquidation is intended. Portfolio solving is shadow-only.

`calibrate` is synthetic-only, with no runtime config, DB or network access. It writes
a private report, never promotion evidence. Explicit family count/variance parameters
define the comparison scenario. See [calibration contracts](alpha-pipeline.md#synthetic-calibration)
and the [active research roadmap](alpha-roadmap.md).

`study-plan` freezes settings without evaluating observations. `study` runs the
[predeclared comparison](alpha-pipeline.md#predeclared-calibration-studies), retaining
all replicates in a new private directory. Failed or missing replicates cannot pass
the criteria; even a complete passing study is not a promotion credential.

`replay` captures observed exchange sessions and raw minute bars, then runs the
shared execution engine on completed regular-session observations. It charges one
real research attempt and excludes the inspected period from fresh holdouts before
provider access, including failed diagnostics. Results earn no qualification or
shadow credit; intraday promotion remains blocked. See [session replay](alpha-session-replay.md).

## 5. Database Schema Migrations & Administration (`copilot db`)

Manage PostgreSQL runtime or explicitly selected SQLite sandbox schema versions and database maintenance via Alembic and administration subcommands:
```bash
# Apply all pending database migrations to latest revision
uv run copilot db upgrade head

# View current database revision
uv run copilot db current

# View migration history
uv run copilot db history

# Roll back one migration revision
uv run copilot db downgrade -1

# Clear a disposable sandbox; refused when durable events/work exist
uv run copilot db clear
uv run copilot db clear --yes  # bypass interactive confirmation
```

### Global Database Override Options
Commands interacting with storage accept options or environment variables to redirect to alternate database files:
- `--db-name <name>`: Resolve a named SQLite sandbox under `data/<environment>/` (or `DB_NAME` env var).
- `--db-path <path>`: Explicit filesystem path to database (or `DB_PATH` env var).

## Configuration, test isolation and audit

`--db-path` accepts an explicit SQLite path or database URL. `--db-name` explicitly
selects a named SQLite sandbox and overrides inherited DB selection. Production
uses `.envrc` and the runtime YAML; tests use separate settings without dotenv.
See [development notes](development-notes.md#configuration-and-isolated-development).

```bash
uv run copilot db audit --signal-id 5 --limit 50
uv run copilot test-alert                 # local preview
# Explicit opt-in, separate test destination only:
uv run copilot test-alert --send
```

`scan --dry-run` uses an empty temporary portfolio and a simulated broker, disables
Telegram, and skips monitoring. `/perf` separates reconciled account performance
from tracked full-close statistics; neither is account-day return. `/healthz` is
liveness; `/readyz` is passive freshness. Active DB/LLM probes are CLI `doctor` only;
HTTP `/healthcheck` is removed. Do not launch `listen` alongside the daemon.


### Options data quality

`gex --json` emits only JSON on stdout; progress goes to stderr and failures return
nonzero. Missing option-chain counts and modeled numeric defaults appear in
`data_quality_notes`; invalid underlying prices fail instead of using fixed ETF
prices. GEX is a Yahoo option-chain estimate, not brokerage P&L or observed dealer
inventory. `--expirations` defaults to the configured options policy.

### Telegram macro command consolidation

Use `/macro` for VIX classification, Treasury curve, credit, inflation, published
data dates and combined trading filters. `/regime` has been removed.
`/explain_macro` explains the macro indicators; `copilot explain-macro` is its CLI
counterpart. Telegram `/backtest` uses configured `backtest.lookback` when omitted.
Conversational positions use the same broker report as `/positions` and the CLI.

## Durable execution and diagnostics

Entries approved through CLI or Telegram enter the same persistent FIFO and
reserve portfolio capacity. `execute` can return queued, rejected, accepted or
unconfirmed; accepted does not mean filled. Conditions are rechecked before POST;
changed conditions require a new scan/approval. `resume` refuses unresolved entries.
`listen` also runs the entry/outbox workers but does not schedule scans/reconciliation.

- `copilot doctor --readiness`: passive daemon freshness report; nonzero if unready.
- `copilot db queue`: inspect persisted entry requests/outcomes.
- `copilot db events [--stream NAME] [--limit N]`: immutable workflow/broker events.
- `copilot db orders [--rebuild]`: inspect/replay order views; no broker mutations.
- `copilot db outbox [--retry JOB_ID]`: inspect delivery jobs or explicitly requeue a dead letter.

See [workflow guarantees, limits and configuration](durable-execution.md).

## Account activity performance

`copilot perf` refreshes read-only broker accounting and uses the same performance
renderer as Telegram `/perf`. Account-wide realized/unrealized values and
tracked full-close metrics are separate.

- `copilot db ledger`: cached reconciliation status.
- `copilot db ledger --sync`: read-only activity import; no orders/messages.
- `copilot db ledger --rebuild`: replay journal projections; no broker requests.
- `copilot db activities`: private raw fill/cash evidence with exact IDs.

See [account ledger](account-ledger.md) for unsupported activities, freshness and
reconciliation rules. A failed reconciliation never becomes zero profit.

## Operational incidents and maintenance

- `copilot doctor --monitor`: stateful supervisor pass using the passive readiness
  contract; persists debounced incidents, queues alerts and runs due compaction.
  May deliver one existing outbox job when endpoint/delivery freshness fails.
- `copilot db incidents [--rebuild]`: inspect/replay incident lifecycle; no resending.
- `copilot db retention [--apply]`: preview by default; apply one bounded batch of
  redundant old healthy-observation compaction. Financial events/dead letters stay.

See [monitoring semantics and defaults](operational-monitoring.md). `--monitor`
and `--readiness` are mutually exclusive. Active `doctor` probes are separate.

`alpha replay` creates a new session-clock version with `--decision-delay-seconds`
(default 60) and `--max-lateness-seconds` (default 120). Expired proposals cannot
become new orders at a later session; already-submitted GTC orders persist. See
[session decision contracts](alpha-session-decisions.md) and their diagnostic-only limits.

Alpha benchmarks are [forecast diagnostics](alpha-forecast-benchmarks.md) with private
artifacts and charged trials; they do not simulate orders or authorize promotion.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](alpha-forecast-policy.md).

### Timed session replay

`copilot alpha replay EXPRESSION --symbol SPY --start YYYY-MM-DD --end YYYY-MM-DD --entry-lifetime-seconds 300 --holding-lifetime-seconds 86400` declares both elapsed-UTC lifetimes in a new immutable execution policy. Both options are required together, each 1–2,678,400 seconds. Omit both to preserve the original GTC contract. Entry age starts at simulated submission; holding age starts at simulated full fill. These flags do not change current positions, promote alphas, or place orders. See [trade lifetimes](alpha-trade-lifetimes.md).

Inspect durable cancellation evidence with `copilot db queue --kind entry_cancel` and `copilot db events --stream entry-cancel/COMMAND_ID`. These are read-only; unresolved intents have no resend operation.

### Native daily panel diagnostics

`alpha panel-study PROTOCOL --output DIRECTORY` requires a new output directory and
a complete frozen plan/screen JSON. It charges the full hypothesis/fold/IC/cost matrix
and excludes member/benchmark/warmup intervals before acquisition. It retains source
arrays, receipts, manifests, IC series, basket weights/costs and a separate screen
report. Failed acquisition/coverage exits nonzero without refunding attempts; existing
outputs are never overwritten. This is intentional research persistence, not a dry
run or permission to submit a basket. See [panel contracts](alpha-sector-panel.md).

### Persistent ETF book research

`copilot alpha book-study PROTOCOL.json --output NEW_DIRECTORY` runs the frozen
[monthly book comparison](alpha-persistent-book.md), charges every comparison before
acquisition and retains daily cash/inventory/cost evidence. No orders, notifications,
registry changes or promotion credit. See [data access](research-data-sources.md).


`copilot alpha volume-study PROTOCOL.json --output NEW_DIRECTORY` runs the
[frozen source-volume study](alpha-volume-calibration.md). Fit per-symbol daily
relative-volume profiles on prior data and retain later event rates, exact profile
identities, raw receipts and every trial/failure. Both SIP and IEX protocols are in
`config/research/volume-*-v1.json`. It changes no feed, strategy or registry state.
Telegram `/alphas` is a compact status/glossary; CLI `alpha forward --days 7` and
`alpha list` retain detailed metrics and immutable definitions.

### Prospective equity universe

```bash
uv run copilot alpha universe-snapshot config/research/prospective-equity-300-v2.json \
  --output /private/path/new-equity-cohort
```

This [metadata-only command](alpha-equity-universe.md) charges one diagnostic attempt
before reads, retains raw provider evidence and selects a reproducible current cohort.
`--previous /private/path/older/snapshot.json` retains observed additions/removals/changes.
Output must be new. Current membership, unknown instrument subtype and unscreened
liquidity cannot establish historical eligibility or alpha qualification. The actual
300-name cohort is recorded in the linked report; invoking again is another attempt.

### Current-cohort liquidity screen

```bash
uv run copilot alpha liquidity-study config/research/equity-liquidity-iex-v1.json \
  --universe /private/path/cohort/snapshot.json --output /private/path/new-liquidity
```

The frozen protocol contains exact `plan` and `acquisition` policies. The snapshot
hash binds every candidate; prices are read only after the diagnostic charge and
all observation exclusions persist. Results retain every candidate and selected
asset UUID, input hashes, raw evidence, per-member checkpoints and any shortfall.
A provider/validation failure withholds the full selection. A completed but undersized
selection exits nonzero with its evidence intact. No orders, notifications, observer,
activation or qualification are created. See [equity research](alpha-equity-universe.md).


### Synthetic power diagnosis

```bash
uv run copilot alpha power-plan --seed FRESH_INTEGER \
  --family-snapshot /private/path/family.json --output /private/path/new-protocol.json
uv run copilot alpha power-study /private/path/new-protocol.json \
  --family-snapshot /private/path/family.json --output /private/path/new-power-study
```

Replace `FRESH_INTEGER` with a new explicit nonnegative integer below `2**128`.
The [power protocol](alpha-power-ablation-plan.md) binds the snapshot hash, seeds,
bootstrap, policy and finite budget. The runner requires a new output directory,
retains selection before holdout and stops before validation after development
calculation failures. A missing snapshot leaves current-family endpoints unavailable;
there is no historical substitution. Complete scientific rejection exits successfully;
incomplete evidence exits nonzero. No runtime DB, provider, notifier or broker is
constructed; no trial is charged to production and no synthetic result can promote.


### Retained factor controls

```bash
uv run copilot alpha factor-controls config/research/equity-factor-controls-iex-v1.json \
  --parent /private/path/original-forecast-study --output /private/path/new-factor-study
```

The [factor contract](alpha-factor-controls.md) freezes six arms, three original
annual folds and 1/5 bp costs: 54 charged comparisons. The parent directory must
match the exact hash-bound protocol. The shared research journal reserves the
budget and excludes all parent members before reads. Retained files supply data;
there are no provider calls, supervised refits, orders, notifications or promotion.
An incomplete/failed study exits nonzero while retaining its charge and artifacts.
