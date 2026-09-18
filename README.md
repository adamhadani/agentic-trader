# Agentic Trader

A quantitative screening and operator-approved trading desk. The current deployment
uses **Alpaca paper equities/ETFs**, PostgreSQL, Telegram and a single macOS launchd
daemon. Scans propose trades; operator approval enters a durable execution queue.

Python **3.14** · `uv` · SQLAlchemy/Alembic · `alpaca-py` · pytest · Ruff · mypy

## Start here

- [Operations and deployment](docs/production.md): ownership, schedules, restart and verification.
- [CLI reference](docs/cli-reference.md): commands and recovery tools.
- [Development handoff](docs/development-notes.md) and [assistant instructions](CLAUDE.md).
- [Architecture review and priorities](docs/architecture-review.md).
- [Active alpha research roadmap](docs/alpha-roadmap.md).
- [Forecast-to-fill tutorial comparison and allocation gaps](docs/forecast-to-fill-review.md).
- [Entry queue, broker events and outbox](docs/durable-execution.md).
- [Account activity ledger](docs/account-ledger.md).
- [Readiness alerts and retention](docs/operational-monitoring.md).
- [Alpaca contracts and integration coverage](docs/alpaca-integration-review.md).
- [Research strategies](docs/strategies.md), [historical roadmap](docs/roadmap.md),
  and [September 15 incident](docs/incident-2026-09-15.md).

## Runtime model

`EXECUTION_MODE=alpaca` with `ALPACA_PAPER=true` selects brokerage paper trading.
`EXECUTION_MODE=paper` selects the local simulator. They have separate state scopes.
Other broker adapters exist but require the fresh-admission contract before using
the entry queue. Alpaca crypto brackets and broker-backed slicing are unsupported.

One daemon owns:

- Four-hour swing scans and session-gated 15-minute intraday scans, relative to startup.
- Minute position reconciliation plus Alpaca trade-stream wakeups.
- Independent entry, notification and 60-second account-activity workers.
- Telegram commands, approval buttons and command-menu registration.
- `/metrics`, `/healthz` liveness and `/readyz` current-run freshness on port 9108.

The external launchd watchdog runs every 60 seconds. It restores missing/stopped
processes, persists sustained readiness incidents and queues alerts through the
existing outbox. It does not blindly restart an unready process. A separate weekly
alpha-miner job performs research without automatic promotion.

Positions use the broker's quantity, average entry, current price and unrealized
P&L from one snapshot. `/perf` separates reconciled account performance—including
partial/external executions and supported fees/income—from tracked full-close
statistics. Missing, stale or unreconciled evidence withholds realized totals.
`/status` shows configured risk capital and tracked exposure, not broker cash.

Risk policy comes from `config/config.yaml` and validated configuration. Defaults
include a $100k capital baseline, $60k total notional ceiling, per-class limits,
minimum R:R 2, and minimum stop distance 1.5 ATR. The baseline is a sizing input;
it does not imply the account earns modeled Treasury yield. Entry preflight
rechecks broker positions/orders, price age/drift, session, halt and risk capacity.

## Common operator commands

```bash
uv run copilot doctor --readiness   # passive current-run freshness; nonzero if unready
uv run copilot positions            # broker-backed positions
uv run copilot perf                 # read-only account refresh + performance
uv run copilot status               # configured risk budget and tracked exposure
uv run copilot scan                # scan and stage suggestions for approval
uv run copilot execute 4            # authorize signal 4; accepted does not mean filled
uv run copilot close 4 --dry-run     # preview a tracked close
uv run copilot close 4              # request fill-confirmed closure
uv run copilot flatten              # preview all current Alpaca positions
uv run copilot flatten --confirm    # close positions, preserving trading halt state
uv run copilot panic                # confirmation, persistent halt and emergency exits
uv run copilot resume               # clear halt only after unresolved-entry checks
uv run copilot db incidents         # persisted operational incident state
uv run copilot db outbox            # deliveries, retries and dead letters
uv run copilot db retention         # preview bounded healthy-observation compaction
```

Telegram supports `/status`, `/positions`, `/perf`, `/macro`, `/explain_macro`,
`/alphas`, `/gex`, `/pairs`, `/backtest`, `/scan`, `/close`, `/flatten`, `/panic`,
`/resume` and `/help`. `/macro` owns combined market context; `/regime` was removed.
The menu is registered in default, private-chat and operator-chat scopes at startup.

Normal equity closes require regular trading hours before canceling protection.
Outside those hours, replies and durable notices explain the refusal, report the
broker's next regular open in UTC, and confirm protection was left unchanged by the request.
Panic can queue emergency exits for the next session. A requested or accepted close
is not a confirmed liquidation. Inspect per-position outcomes and broker positions.

## Development setup

Inspect launchd registration before starting services. **Do not run a second daemon,
`listen`, or Compose stack alongside the installed poller.** Develop in a worktree
when changing supervisor scripts: the installed watchdog executes them every minute.

```bash
uv sync --dev
# On a new installation only: copy .envrc.example to .envrc and configure credentials/DB.
uv run copilot db upgrade head
uv run pre-commit install
uv run pytest
uv run pre-commit run --all-files
```

PostgreSQL is the runtime backend. SQLite is explicitly selected for tests and
sandboxes; there is no automatic failover. Tests strip credentials, use temporary
DBs, block Python/native network I/O and enforce DB guards. Real SDK HTTP/WebSocket
integration uses loopback only. For PostgreSQL integration, create an empty
disposable database whose name starts `test_`:

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres
```

Safe local checks:

```bash
uv run copilot scan --dry-run --no-llm --symbols IWM
uv run copilot test-alert
uv run python scripts/verify_runtime.py
```

Dry scans use an empty temporary simulator and no Telegram or broker mutations;
market-data calls remain possible. `test-alert` previews locally. Explicit sending
requires a separate test bot/chat. The runtime verifier reads broker/Telegram state,
checks current source revision and daemon freshness, and submits no orders/messages.
Keep credentials, chat identifiers, raw logs, DBs and verification output private.

## Deployment and research

The installed Mac desk uses `./scripts/launchd.sh status` and controlled restarts.
Pause watchdog then daemon before updating its checkout or applying migrations.
Verify startup revision, readiness, broker/report parity, stream and actual poll
freshness afterward. See the [operations runbook](docs/production.md).

Docker Compose is an alternative deployment with `postgres` and `copilot` services;
use `docker compose logs -f copilot`. Never start it alongside this desk's launchd
stack. Compose's PostgreSQL image is independent of the installed Homebrew version.

Research commands include `backtest`, `stress`, `gex`, `pairs`
and `alpha` mining/inspection/promotion. Research results are not live account P&L.
Research does not automatically change running strategy parameters. External config
edits require a restart; alpha registry changes load between scans. Allocation
weights/convex optimization are not yet integrated into live sizing.
The [alpha pipeline](docs/alpha-pipeline.md) documents causal validation, versioned
journal-backed promotion, shadow gates and research-only portfolio targets.
Historical YAML metrics do not authorize deployment. The [original review](docs/alpha-stack-review.md)
and [implementation evidence](docs/alpha-pipeline-implementation.md) separate defects,
regression verification and actual trading evidence.
The [original calibration study](docs/alpha-study-2026-09-16.md) exposed execution
coverage defects. The [fresh follow-up](docs/alpha-timeline-study-2026-09-16.md)
completed all 1,952 jobs; discovery power remains insufficient. Neither grants promotion credit.
The [return-timeline correction](docs/alpha-return-timeline.md) retains cash periods,
exposes feature coverage and requires current-policy evidence before alpha activation.
The [session/minute replay](docs/alpha-session-replay.md) diagnoses execution timing
and feed coverage using observed calendars and the shared bracket engine. Intraday
promotion still requires live-clock alignment and broker execution evidence.

Read docs as Markdown or preview with `cd docs && bundle install && bundle exec jekyll serve`.
Historical design notes and the reference PDF are source material, not runtime guarantees.

The daemon's [prospective session-data observer](docs/alpha-forward-observations.md)
records live REST availability and revisions for SPY/15m on the configured feed.
Use `copilot alpha forward --days 7` or Telegram `/alphas` for [recorded candidate outcomes and receipt timing](docs/alpha-forward-evidence.md); missing/truncated history stays explicit.

Inspect `copilot alpha status` for capture quality; it provides no promotion credit
and does not change strategy scans or activate mined alphas.

Session alpha research now uses [versioned receipt/delay/expiry contracts](docs/alpha-session-decisions.md).
The daemon now acquires receipt-stamped minutes and persists one diagnostic decision
per candidate/symbol/candle, including missed and interrupted outcomes. New session
versions remain blocked from promotion pending measured execution and qualification evidence.

Forecast research: `copilot alpha benchmark RUN_ID --method ridge --budget 5 --horizon 1`
compares causal predictions on saved discovery data. See the
[forecast benchmark guide](docs/alpha-forecast-benchmarks.md) and
[prioritized alpha roadmap](docs/alpha-roadmap.md). Diagnostic results do not authorize trading.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](docs/alpha-forecast-policy.md).

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](docs/alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


The [frozen ETF session campaign](docs/alpha-session-campaign-2026-09-17.md) completed all 81 attempts with full execution
coverage. None passed every rule: QQQ momentum's positive cost-stressed returns had
only 11 closed trades. Six predeclared SPY/QQQ hypotheses are designated for diagnostic
forward observation, not activation or qualifying shadow credit. Preserve the failed
triage result; timed order/holding lifetimes are implemented. The continuous and sector-panel campaigns are complete; measured forward/lifecycle evidence continues to accumulate.

Timed session research supports [immutable entry/holding lifetimes](docs/alpha-trade-lifetimes.md), shared with the broker lifecycle service. `alpha replay` accepts paired `--entry-lifetime-seconds` and `--holding-lifetime-seconds`; omitted limits preserve existing GTC behavior. Cancellation recovery, risk fencing and close intents reuse the execution journal/outbox. Session alphas remain diagnostic-only; the completed continuous study and subsequent sector-panel experiment retain separate frozen protocols.

The [continuous timed ETF campaign](docs/alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](docs/alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](docs/alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](docs/alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](docs/alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The [persistent-book follow-up](docs/alpha-persistent-book-2026-09-17.md) is also complete;
use the [current roadmap](docs/alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities) for next work. [Automatic Alpaca bar evidence](docs/market-data-evidence.md) now retains raw pages and normalization outcomes.

The [forecast contract hardening](docs/forecast-contract-hardening.md) records causal calibration,
trade participation bounds, forecast-error penalties and bounded provider reads. The
legacy `optimize`/`retune` and uncalibrated Kelly sizing paths have been retired.

The [persistent ETF book experiment](docs/alpha-persistent-book.md) uses the shared
panel journal and explicit adjusted-price accounting. It is research-only: reserve
the frozen matrix before access; no promotion or live portfolio execution.
[Verified feed access and alternatives](docs/research-data-sources.md) distinguish
recent-SIP entitlement from throttling; historical SIP remains usable.

Source-specific [volume calibration](docs/alpha-volume-calibration.md) uses the shared
daily study harness with frozen training/forward intervals and exact feed identity.
`alpha volume-study` is research-only. `/alphas` now provides a compact explanation
and status summary; use `alpha forward` and `alpha list` for full evidence/definitions.

Prospective alpha checks use [separately configured IEX workers](docs/alpha-iex-forward.md);
their diagnostic candidates and receipts grant no trading or promotion authority.

The [prospective equity universe](docs/alpha-equity-universe.md) adds a reproducible
300-name non-ETF candidate cohort with source evidence. It is a metadata snapshot;
liquidity screening, historical membership and forecast qualification remain separate.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](docs/alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

`alpha forecast-study` now separates explicit-horizon forecast evidence from deployment gates over the [screened equities and ETF controls](docs/alpha-panel-forecasts.md). It preserves past-only eligibility, mature training labels, all model outcomes and cost sensitivity; current membership remains survivor-conditioned development evidence.

`alpha forecast-controls` replays the [matched style and endpoint-evidence study](docs/alpha-forecast-controls.md) from verified retained artifacts. It uses the existing trial journal, makes no provider calls and cannot activate a strategy.

The [September 18 matched-control result](docs/alpha-forecast-controls-2026-09-18.md) reproduces Ridge but finds comparable simple-style performance and unknown 2023 endpoints. No active alpha was added; prospective evidence with fixed controls is next.
