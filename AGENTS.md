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

- **Paper probes:** `probe` is a third registry list, valid only in the `…/alpaca:paper`
  scope. `probe_block_reason` is the single liveness rule for snapshot, sweep and
  admission. Probes are risk-capped, expiring, renewable only while unkilled (−4R
  from first enrolment, sticky per version), and earn no shadow, holdout or promotion
  credit. Never make `active` depend on the account being paper; leaving `probe`
  never touches the broker.

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

- Follow the [active alpha roadmap](docs/alpha-roadmap.md) for research changes. `alpha calibrate` artifacts are synthetic diagnostics, never promotion credentials; preserve attempt history and predeclare statistical acceptance criteria.
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

- Semantics version 5 is the production native-daily contract: no clock, exactly
  `DAILY_ENTRY_LIFETIME_SECONDS = 57,600` resting and no holding deadline, so
  research (bar label) and live (`submitted_at`) both mean one regular session.
  Never relax it to a configurable value, add a holding deadline to it, or re-fence
  it on `semantics_version`; the miner's `--entry-policy session` default and the
  scheduled `--entry-policy gtc` pin are deliberate.

- Pair research-semantic changes with bounded actual-data experiments per the [roadmap cadence](docs/alpha-roadmap.md#research-delivery-cadence-and-progress-september-17). Continuous replay chunks only acquisition: never reset features, pending orders or positions at chunk boundaries. Preserve inclusive SDK/exclusive internal range semantics, failed receipts and promotion gates.

- The [IC review](docs/alpha-information-coefficient.md) distinguishes single-symbol rolling IC from cross-sectional IC. Existing ICIR is unannualized and overlapping; never apply IID t-statistics or cross-sectional reference thresholds directly. New metric semantics require versioned evidence and predeclared tests/studies.

- [Panel studies](docs/alpha-sector-panel.md) use explicit native daily calendars/targets and per-fold IC evidence. Never intersect away missing members, rank by future availability, annualize with an inferred 252, or relabel price proxies as fills. Reserve the whole matrix and exclude all members/benchmark/warmup before acquisition; only research screens may consume their outputs.

- Forecast calibration must verify outcome availability at its cutoff. Pooling and
  shadow allocation require matching target/feed/price/clock/currency contracts.
  Bound traded notional separately from holdings; nonzero pending orders currently
  block shadow allocation. Preserve immutable solver input/output evidence.
- Legacy `optimize`/`retune` and uncalibrated Kelly sizing are retired. Use the
  journal-backed research pipeline; do not introduce replacement config-export shims.
- Read fallback deadlines never replay unfinished reads; shared worker capacity is
  bounded, while transport adapters still require real socket deadlines. See
  [forecast contract hardening](docs/forecast-contract-hardening.md).

- Alpaca bar reads retain [raw page evidence](docs/market-data-evidence.md) before SDK
  parsing/cleaning. Inject the shared evidence store in production composition; keep
  per-request context isolation, immutable page hashes and failure references. Never
  treat successful capture as complete/fresh market coverage or prune evidence silently.

The [persistent ETF book experiment](docs/alpha-persistent-book.md) uses the shared
panel journal and explicit adjusted-price accounting. It is research-only: reserve
the frozen matrix before access; no promotion or live portfolio execution.
[Verified feed access and alternatives](docs/research-data-sources.md) distinguish
recent-SIP entitlement from throttling; historical SIP remains usable.

- [Volume calibration](docs/alpha-volume-calibration.md) binds symbol/feed/timeframe/
  adjustment/clock and training evidence. Preserve past-only fitting and exact profile
  identity; no SIP conversion factor, automatic live threshold change or intraday
  reuse without seasonality. Both real-data and synthetic diagnostics grant no promotion.

- Research worker feed comes from its injected policy, never the trading feed. Filter
  session candidates by exact feed before enrollment/budgeting; retain/report other-feed
  evidence. New-feed controls need new immutable identities. See [IEX forward](docs/alpha-iex-forward.md).

- Equity universe snapshots retain current asset UUIDs and directory evidence; never
  infer common-stock subtype/sector from names or backdate current membership. Bind
  follow-up research to the snapshot hash and freeze/charge before prices. Preserve
  missing/excluded members and all failed attempts. See [equity universe](docs/alpha-equity-universe.md).

- Daily equity liquidity screens must bind the complete verified snapshot and frozen
  acquisition policy. Use shared `AlphaPanelService`/`DailyStudyInputs`; retain every
  member checkpoint. Unknown acquisition or invalid source evidence withholds selection;
  legitimate empty bars mean missing coverage. Current IEX volume is not consolidated
  capacity, and current-cohort historical development is not point-in-time validation.

- [Panel forecast studies](docs/alpha-panel-forecasts.md) keep every frozen member on the expected calendar. Fit preprocessing only on training rows with strictly mature labels. Eligibility and basket weights cannot depend on future outcomes; missing held outcomes withhold full-path P&L. Current-cohort historical diagnostics cannot qualify an alpha or relax older complete-book contracts.

- [Retained forecast controls](docs/alpha-forecast-controls.md) use hash-bound parent artifacts and the shared daily journal. Reserve/exclude before retained-price access. Future endpoint evidence cannot change decisions; unknown held outcomes withhold curves. Preserve original training semantics and distinguish artifact-read receipts from provider observations.

- Alpaca entry risk uses the existing account ledger checkpoint and persistent cash-flow-adjusted high-water mark. Never default missing/stale risk to zero or reset it on restart. All tiers share the drawdown cap; final admission pins the fingerprint under trading → ledger lock order and rechecks lease expiry after waits. Read [account risk](docs/account-ledger.md#cash-flow-adjusted-drawdown-and-entry-admission).

- [Entry capacity](docs/entry-capacity.md) uses typed broker account/book/quote evidence
  and exact protection identities. Final admission rechecks freshness and all current
  reservations under the trading lock. Use the lesser of mandate and observed equity;
  missing funding/borrow/protection evidence blocks new risk. Planned stop risk is not
  a gap-loss or CVaR guarantee. Keep research gate-power diagnostics separate from promotion.

- [Power diagnosis](docs/alpha-power-ablation-plan.md) reuses immutable statistical
  measurements and the existing artifact runner. Freeze selection before holdout,
  keep current-family provenance distinct from historical sensitivity, and stop
  before validation after mechanical development failures. Synthetic output never
  grants promotion/shadow credit or charges the live trial ledger.

- [Factor controls](docs/alpha-factor-controls.md) reuse the retained-panel workflow.
  Preserve strictly prior residual fits, original parent forecasts, common causal
  support and unknown held outcomes. Charge/exclude before any parent read; source
  hashes and assumed daily completion are not historical receipt/PIT evidence.

- Native-daily panel forecasts use actual receipts after the New York day ends.
  Preserve fixed H20 calendar anchors, strict mature training, immutable missed
  residual innovations and first outcomes. Its journal claims/state CAS cannot
  replay expired captures or authorize trading; see [daily panel](docs/alpha-daily-panel.md).
  Independent baseline companions reuse that capture/fit and have separately
  counted, charged immutable enrollments pinned by the journal. Validate artifacts
  against those pins before outcome price reads. Configuration removal is not
  retirement; partial artifact writes cannot publish partial success or advance state.
