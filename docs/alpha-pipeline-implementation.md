# Alpha pipeline implementation log

This work implements the ordered roadmap in [alpha-stack-review.md](alpha-stack-review.md).
The isolated branch is `feat/alpha-pipeline`; paper deployment is a separate final verification step.

## Acceptance order

1. Causal bounded DSL, honest missing observations and common research/live scoring.
2. Immutable strategy contracts, matching timeframe and exit semantics; explicit execution assumptions.
3. Purged validation, untouched holdout, per-observation statistics, reproducible trial/data provenance.
4. Transactional promotion and immutable active snapshots using the existing event journal.
5. Aligned residual analysis and fail-closed convex portfolio targets; combined alpha forecasts in shadow mode.
6. Versioned research universe, bounded genetic search and comparable statistical baselines.
7. Integrated dry runs, documentation, PR review, CI, merge, controlled restart and freshness verification.

Research evidence and live operational evidence must be recorded separately. New candidates need real
out-of-sample and shadow evidence; synthetic regression tests are not evidence of trading profitability.

## TDD record

- Causality tests added before implementation: prefix/future-perturbation invariance, operator contracts,
  undefined values, missing fields, aligned observations and scalar broadcasting.

### Engineering progress (not deployment evidence)

- Causal DSL: RED 33 failures / 8 passes; GREEN 41 passes.
- Immutable contract/shared scoring + bracket simulator: 18 passed.
- Purged-fold/untouched-holdout/DSR/reference tests: 6 passed.
- Journal CAS/concurrency/replay/holdout-consumption: 4 passed.
- Convex optimizer changed to CVXPY/Clarabel after safety tests: 14 passed including 100 assets and permutation checks.
- Schema 008 adds one replayable alpha aggregate projection and optional signal attribution fields.
- Historical YAML imports are shadow-only, with no inferred historical versions. Deployment evidence is separate below.

## Review corrections

Follow-up failing tests exposed and then fixed impossible market-style entry fills
in a limit-order desk, lost exit P&L after a missing score, holdout reuse via moving
the terminal date or changing feeds/timeframes, wrong-feed shadow credit, stale
portfolio forecasts and risk-hidden opposing reservations. GTC pending orders,
conservative entry/exit ordering, bounded-history ATR and tick-rounded entry limits
now have shared contracts. Qualification history is immutable per run; later fresh
periods may produce another decision without rewriting previous evidence.

Intraday session semantics remain an explicit **activation blocker**: mixed-session
bars cannot validate regular-session bracket fills. The simulator labels such
results diagnostic and qualification rejects them. This requires a finer execution
timeline, not an optimistic fill assumption. Recursive EMA also requires shared
initialization before promotion. These are recorded in the architecture follow-up.

Additional checks cover LLM protection overrides, shadow CPU offloading, registry
acknowledgment after restart/change, observed shadow decision minimums, overlapping
sector/class caps and required factor observations. The four legacy YAML records
are imported only as unqualified shadow hypotheses during deployment.

## Market-data and numerical experiments — 2026-09-16

Read-only calls used Alpaca SIP/raw observations. No order API, production notifier
or production database was used by the experiment process. Raw arrays and reports
remain outside Git.

- All **32 ETFs** returned 5y daily observations, at least **1,252 bars** each.
  This establishes current feed coverage, not historical tradability/borrow/spread.
- Four legacy formulas were evaluated across **13 formula/symbol cases** using
  actual 4h/15m sampling over 1y. No input/evaluation errors. **11/13** diagnostic
  trailing-split Sharpes were negative. These runs do not consume a new pipeline
  qualification holdout, prove profitability, or satisfy intraday session evidence.
  Their observed periods must not subsequently be described as untouched.
- A small equal-budget smoke comparison used SPY 5y daily data, seed 20260916,
  **five trials each** for random, genetic, Ridge and histogram-gradient boosting.
  All four completed and left their final holdout untouched. Best validation SRs
  were approximately 1.12, 1.12, 0.76 and 0.50 respectively. This single-symbol,
  single-seed exercise validates the harness; it does not rank discovery methods.
- The frozen original five-symbol/7-formula prefix harness produced **0 / 15,470**
  entry-direction disagreements (pre-fix baseline: 1,087). This is signal parity,
  not execution/P&L parity.
- Twenty zero-mean synthetic mining runs returned **zero discovery finalists**.
  This small diagnostic is not a calibrated false-discovery estimate.
- Constrained solves at 3/10/50/100 instruments all returned independently checked
  optimal solutions, including net and factor bounds; no fallback allocation.

## Deployment checklist

Engineering tests, CI and operational verification are recorded independently.
The installed daemon remains at its previous revision until the reviewed merge,
PostgreSQL backup/migration and controlled restart. Final deployment evidence is
appended after those steps; process liveness alone is insufficient.

A final admission review reproduced four lifecycle defects: demoted, unversioned or
policy-mismatched alpha suggestions could reserve risk, and demotion during preflight
could precede an unchecked submission. The shared transaction now fences registry
state at reservation and submission commit. Independent PostgreSQL clients exercise
both sides of the commit boundary; committed submissions remain lookup-only.

### Final local verification

- All pre-commit hooks passed: formatting, secret/size checks, lockfile, Ruff,
  mypy (129 application modules) and impacted regression tests.
- **60 integration tests passed** using real SDK HTTP/WebSocket and disposable
  PostgreSQL, including independent-client alpha registry/submission fencing.
- A real-data `scan --dry-run --no-llm --symbols IWM --bypass-session-filter`
  completed with an empty simulated portfolio, zero emitted alerts, no broker
  execution and no Telegram delivery. Market/macro observations were read-only.
- The installed original service remained healthy throughout isolated development;
  deployment acknowledgment requires the merged revision and new run checks.
