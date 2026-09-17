# Working in agentic-trader

Read [CLAUDE.md](CLAUDE.md), [development notes](docs/development-notes.md), and
[operations](docs/production.md) before changing runtime behavior.

- This checkout runs the launchd Alpaca **paper** daemon and Telegram poller.
  Inspect registration first. Never start a second daemon/poller alongside it.
- Preserve existing work; inspect `git status` before editing. User-authorized
  fixes may include a controlled restart after tests and operational verification.
- Use Python 3.14 and `uv`. Run `uv run pre-commit run --all-files` before commits.
- Tests use temporary SQLite, explicit fixture config, stripped credentials, and
  blocked network I/O. PostgreSQL tests require `--run-postgres` plus a disposable
  `TEST_POSTGRES_URL` whose database name starts `test_`. Never relax DB guards.
- Inject configuration and storage before constructing services. Bare `AppConfig`
  cannot implicitly select a runtime DB. Dry scans use an empty simulated portfolio.
- Broker identity, actual fills, and conditional DB updates govern trade closure.
  Never match an exit by symbol alone or infer fill price from an order request.
- Keep credentials, chat identifiers, raw runtime logs, DBs, and incident snapshots
  out of Git. Use redacted audit evidence and the shared report builder for debugging.
- Reuse domain enums/constants; put operator-adjustable policy in validated config.
  Preserve external wire formats and frozen migration literals.
- Update docs for changed behavior. Record tests and deployed verification separately;
  a healthy process is not proof of broker, data-feed, or Telegram freshness.

- Keep blocking I/O/CPU work off the asyncio loop. Use shared transport retry and
  observation mechanisms; never retry a trade handler to recover a Telegram reply.
  Validate event-loop responsiveness and shared-state races when adding threads.

- `/macro` owns the unified market-context report; `/regime` has been removed.
  Use the evaluator's combined snapshot and configured thresholds. Never fabricate
  quote, P&L or macro observations to make a response look complete.

- Route Alpaca closes through `PositionCloseService`: persist an exclusive intent,
  confirm symbol-order cancellation, revalidate the broker snapshot, then submit
  once with its client ID. Recover uncertain outcomes by lookup, never replay.
  `/flatten` previews by default and does not change the trading halt. Dry-run
  commands must not cancel/submit orders or send synthetic production messages.

- Preserve real SDK HTTP/WebSocket integration coverage. Mutation responses can be
  ambiguous: never retry POST/PATCH/DELETE automatically. Resolve stop replacements
  by exact IDs and confirm working price before DB updates; preserve thesis/risk.
  Review [Alpaca contracts and coverage](docs/alpaca-integration-review.md).

- Entry authorization uses `EntryExecutionService` and the durable FIFO. Reserve
  risk atomically; only a valid preflight token may commit `submitting`. Never
  expire/replay a broker submission. Recovery looks up the original client ID.
- Critical notification intents share the signal/exit/stop transaction. Outbox
  retries delivery only; Telegram delivery is at least once. Preserve dead letters.
- Order projections replay `domain_events`; do not fabricate fills or silently
  reset signal IDs while journal/work exists. Read [durable workflows](docs/durable-execution.md).
- `/readyz` and `doctor --readiness` are passive current-run freshness checks.
  Keep TCP/WebSocket and PostgreSQL integration verification separate from the
  optional in-process SDK transport used in restricted environments.

- Read [alpha pipeline](docs/alpha-pipeline.md) before mining/promotion or portfolio
  changes. Keep shared causal scoring/brackets and exact timeframe/feed semantics.
  Persist every trial; consume holdout intervals before evaluation. Activation uses
  immutable versions, fresh qualification/shadow evidence and registry CAS; import
  historical YAML as shadow only. Combined portfolio targets cannot submit orders.
  Preserve current single-owner execution until fill attribution/protection supports more.

- Account performance uses the [activity ledger](docs/account-ledger.md): exact
  activity IDs, Decimal cash/inventory reconciliation and signed broker cost basis.
  Preserve account binding, fenced refreshes, revisions/retractions and replay.
  Unsupported/stale/unreconciled evidence must withhold account realized P&L;
  tracked full-close metrics are a separate subset. Never infer partial ownership.

- Develop supervisor edits in an isolated worktree: launchd executes repository
  scripts every minute. Pause watchdog then daemon before updating its checkout.
- Reuse the [operational monitor](docs/operational-monitoring.md) and outbox for
  readiness alerts. Replay never sends notifications; preserve stale-probe fencing.
  Only redundant old healthy observations may be compacted. Retain all financial,
  incident, work/deduplication and dead-letter evidence. HTTP health is passive.

- Follow the [active alpha roadmap](docs/alpha-roadmap.md) for research changes. Calibration artifacts are synthetic diagnostics, never promotion credentials; preserve attempt history and predeclare statistical acceptance criteria.
- Session replay uses observed exchange calendars and complete raw minute coverage,
  with the shared bracket engine. `alpha replay` journals a real research attempt
  and excludes inspected periods before provider access. Do not impute missing
  prices, conflate session-derived/native bars, or remove the intraday promotion
  gate before live-clock and execution evidence agree. Read [session replay](docs/alpha-session-replay.md).

- Forward session observations share the replay adapter/clock and existing journal.
  Preserve actual request/receipt times, raw failures/revisions and inspected-date
  exclusions; never backdate availability or grant promotion/shadow credit. Collector
  readiness is distinct from data completeness. See [forward observations](docs/alpha-forward-observations.md).

- New session alpha clocks are immutable definition identity; preserve version-2 hashes.
  Require receipt-stamped session snapshots and shared delay/expiry selection. Session
  decisions use durable version/symbol/candle claims with registry/cursor CAS. Never
  replay failed/expired claims or rewrite decisions after revisions. Session versions
  remain diagnostic-only until execution and qualification evidence are complete. See [session decisions](docs/alpha-session-decisions.md).

- Forecast benchmarks use explicit target horizons and purged training labels; retain
  plans/trial charges/artifacts through the existing diagnostic journal. Forecast
  metrics are not strategy P&L or qualification evidence. See
  [forecast benchmarks](docs/alpha-forecast-benchmarks.md).

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](docs/alpha-forecast-policy.md).

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](docs/alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


- The [ETF session campaign](docs/alpha-session-campaign-2026-09-17.md) has no complete
  triage passes. Its six predeclared SPY/QQQ hypotheses are diagnostic controls, not
  active/qualified alphas. Preserve the frozen protocol, trial history and rejection
  reasons; entry/holding lifetime changes require a new shared execution contract.

- Forward evidence reporting reuses canonical alpha projections and the [shared report builder](docs/alpha-forward-evidence.md). Exclude aliases/late results; preserve missing counts, truncation and cursor gaps. Receipt lag includes polling/delay, and diagnostic score directions are never fills or qualified shadow credit.

- [Timed alpha policies](docs/alpha-trade-lifetimes.md) share elapsed-UTC deadlines with broker execution. Never retrofit historical policies, release entry risk before exact cancellation-group confirmation, replay a cancellation, or retry a failed deterministic holding close. Use the existing journal/outbox and `PositionCloseService`; session activation remains gated.

- Pair research-semantic changes with bounded actual-data experiments per the [roadmap cadence](docs/alpha-roadmap.md#research-delivery-cadence-and-progress-september-17). Continuous replay chunks only acquisition: never reset features, pending orders or positions at chunk boundaries. Preserve inclusive SDK/exclusive internal range semantics, failed receipts and promotion gates.

- The [IC review](docs/alpha-information-coefficient.md) distinguishes single-symbol rolling IC from cross-sectional IC. Existing ICIR is unannualized and overlapping; never apply IID t-statistics or cross-sectional reference thresholds directly. New metric semantics require versioned evidence and predeclared tests/studies.
