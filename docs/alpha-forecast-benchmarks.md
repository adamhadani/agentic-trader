# Forecast-component benchmarks

Implemented September 17, 2026, as an A3 prerequisite while A2b collects forward
session evidence. This describes the forecast-only core of `alpha benchmark`; old benchmark
artifacts retain their old bracket-simulation meaning and are not reinterpreted.

## Purpose

The calibration study exposed a mismatch: a next-observation predictor may be
useful even when an unrelated limit-entry/bracket policy rarely trades it.
`alpha benchmark` measures prediction quality separately. An explicit optional
[timing/cost screen](alpha-forecast-policy.md) adds diagnostic payoff statistics.
Neither path simulates broker orders, registers a tradable definition, consumes a
qualification holdout, earn shadow credit or authorize promotion.

```bash
uv run copilot alpha benchmark RUN_ID --method single --budget 7 --horizon 1
uv run copilot alpha benchmark RUN_ID --method ridge --budget 5 --horizon 5
uv run copilot alpha benchmark RUN_ID --method boosted --budget 5 --horizon 5
```

`RUN_ID` identifies an existing frozen discovery dataset. Output goes to a new
private artifact directory; `--output` can select it explicitly. An existing directory
is refused. A failed calculation exits nonzero and retains its charged attempt.

## Scientific contract

- `ForecastTarget` identifies the input timeframe, horizon in **observed bars** and
  explicit endpoints. The default `observed_close_to_close_v1` label is: `close[t+h] / close[t] - 1`. This is an endpoint
  prediction target, not an executable same-close fill or a total-return series.
  Raw stock splits/dividends and session gaps need separate economic treatment.
- `forecast_components_v2` freezes the feature library, method, seed, parameter
  budget, selected DSL features, optional cost policy and validation policy in a hashed plan before loading prices. Changing
  horizon/method/parameters is another charged experiment.
- Only the source discovery prefix is evaluated. The full artifact is loaded/hashed
  for integrity; holdout values never enter features, labels, fitting or metrics.
  Source policy, feed/adjustment, manifest and exact holdout boundary must agree.
- Expanding purged folds use the **target's** horizon. Training labels mature before
  validation with the configured embargo. Each model and scaler is fitted anew on
  preceding complete observations. Validation labels mature inside their own fold.
- `single` calibrates one economic DSL feature at a time with ordinary least squares
  (budget bounded by the selected feature count; default seven-feature library). Ridge fits standardized
  combinations. Boosted trees test nonlinear interactions with early stopping disabled.
  The fixed library is in `research/alpha/baselines.py`; no new DSL operator is required.
- Each fold compares predictions with its training-label mean on identical finite
  support. Report RMSE, baseline RMSE, `1 - model_MSE / baseline_MSE`, time-series
  Spearman rank IC, sample counts and missing-prediction coverage. Undefined values
  remain unavailable. Positive skill means lower squared error than that comparator.
- Pooled metrics are descriptive, alongside each fold. They are **not** significance
  tests, cross-sectional IC, probability of profitability, calibrated uncertainty or
  evidence that overlapping labels are independent. No model is automatically selected.

## Architecture and evidence

Pure typed plans/results and numerical evaluation live in `research/alpha/baselines.py`;
`targets.py` owns outcome endpoints and `forecast_policy.py` owns the optional stateless
daily payoff screen.
`AlphaBenchmarkService` owns application orchestration, injects the existing
`AlphaRepository`, and offloads loading/fitting/artifact I/O. CLI only composes these.
No schema, queue, registry or compatibility alias is added.

Before evaluation, the service publishes a private manifest, reserves the entire
trial budget in the existing journal, and excludes the discovery interval from
future holdout reuse. It preserves the original holdout boundary. Successful runs
retain per-observation forecasts, realized labels, training means and fold IDs in
immutable NPZ artifacts, with hashes and fold statistics in `result.json`.
Failures retain their type and private error detail. Journal diagnostics carry the
plan and artifact hash; replay reconstructs them without fitting or trading.
Forecast trials increase lifetime attempts, **not** the strategy-Sharpe variance sample.
A crash leaves its reservation visible; automatic recovery/cancellation and per-trial
streaming checkpoints belong to A5 and are not claimed here.

Tests cover planted predictions, missing/constant observations, matched-support
metrics, future/holdout perturbations, target purging, immutable artifact failure,
CLI execution, actual Alpaca SDK pagination over TCP and SQLite/PostgreSQL replay.
Unit controls establish mechanics; they do not establish calibrated search power.

## Bounded first comparison (predeclared before reading results)

Use the existing September 16 five-year daily Alpaca discovery snapshots for **SPY,
QQQ and IWM**, keeping each source holdout untouched. Choose the latest saved five-year dataset per symbol by journal event order, without inspecting performance.
Metadata inspection found these snapshots predate the return-timeline policy and
lack the newer manifest clock field. Register their unchanged, hash-verified raw data
under new **dataset-only** source records (zero formula trials), explicitly retaining
the original run IDs and holdout boundaries. Do not migrate/reinterpret the old
performance evidence or relax the benchmark's current-policy checks.
For horizons **1 and 5** observed bars, run seven single-feature calibrations, three
Ridge regularization settings and three boosted-tree settings, seed **20260917**.
That is **78 charged trials**, no new download, no parameter search after observing
these results, no promotion and no claim of an independent final test. These periods
have already been used for discovery. Record every completion/failure and fold skill;
use findings to choose the next *predeclared* experiment, not relax a gate.

The [completed comparison](alpha-forecast-comparison-2026-09-17.md) retains all 78
trials and three modest forecast leads. No promotion follows.

## What remains before paper admission

A3 still needs versioned deployable forecast components, causal incumbent-relative
combination and nested selection, uncertainty/decay evidence, and an explicit execution
policy suited to the chosen forecast horizon. A2b acquisition/durable scheduling and
actual broker execution observations remain prerequisites for session-clock admission.
The [roadmap](alpha-roadmap.md) owns these dependencies and the wider search programme.


## Automatic diagnosis before lead selection

New benchmark output includes `forecast_diagnostics_v1` in each trial and fold.
This is additive descriptive evidence; forecast target/plan identity, model fitting,
trial charges, decisions and qualification gates are unchanged. Historical reports
remain intact. The [open-gap postmortem](alpha-open-gap-postmortem-2026-09-17.md)
motivated these summaries:

- Complete row/paired counts and separate unavailable prediction/target/baseline
  counts. Distribution summaries share identical finite paired support.
- Prediction, target and training-mean distributions (mean, population standard
  deviation, 5th/50th/95th percentiles); positive-versus-nonpositive directional
  accuracy alongside the always-positive reference. Zero is explicitly nonpositive.
- Signed squared-error reduction, positive/negative contributions, and the five
  largest positive/negative contributors with **decision** timestamps. The target's
  maturity time remains governed by its horizon; a timestamp is not an execution time.
- Largest/top-five contribution shares are relative to *net* error reduction and
  may exceed 100% when other observations offset gains. They are unavailable when
  net improvement is nonpositive. Removing a contribution from the displayed sum
  is attribution only, never a refit or deletion from evaluation.
- Total folds, folds with positive skill and folds with unavailable skill. These
  descriptive counts do not create a new acceptance threshold or significance test.

Each fitted fold retains feature order, a hash of its exact finite training features
and labels, estimator class/parameters, and a reconstruction reference to the frozen
plan, source prefix and recorded software environment. Single/Ridge models also
retain standardization and coefficients/intercept in original feature units.
Boosted models use the reproducible reconstruction recipe; no unsafe pickle or
second model registry is introduced.

Every optional payoff scenario/fold reports eligible/entered/skipped counts,
exposure fraction, mean net returns on entered days and the comparator's skipped
days, full-clock mean excess return, turnover legs, gross/net compounded return,
cost drag in **percentage points**, and net/gross terminal-wealth ratio. Missing
values remain unavailable; policy timelines must remain complete and finite.
Cash-only periods stay in the denominator. Skipped comparator returns are not
fabricated model trades. These summaries are included in the existing CLI JSON and
private result artifact; existing journal hashes and replay remain authoritative.

Tests include one-observation-concentrated and broadly distributed planted examples,
undefined/negative evidence, independent cost attribution, reconstruction of linear
predictions, future/holdout invariance, and real SDK -> SQLite/PostgreSQL artifact and
journal replay. A read-only attribution run against the original four open-gap
outputs reproduced the postmortem; it neither refitted models nor charged new trials.
