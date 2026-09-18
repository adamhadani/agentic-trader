# Prospective daily equity panel

This diagnostic follows the [factor comparison](alpha-factor-controls-2026-09-18.md)
with forecasts saved **before their future entry and outcome prices exist**. It is
separate from the formula/bracket promotion lane. It cannot place orders, activate
alphas, earn qualification/shadow credit or send trade recommendations.

## Frozen experiment

The [protocol](../config/research/prospective-equity-panel-iex-v1.json) binds the
previously selected 64 equities and nine explicit sector ETF proxies, the selection
identity and factor-study parent. Names are not substituted after missing data.
This is a current-cohort prospective experiment, not historical point-in-time
membership or a fresh independent test of the already inspected model selection.

Four fixed arms use common past-only support: reversal60/volatility20 percentile
rank blend, volatility20, that blend plus residual momentum, and Ridge. Each has
at most unit gross exposure, zero net target exposure and eight names per tail,
with the shared boundary-tie policy. Minimum common breadth is 16. Missing future
prices never determine eligibility or initial weights.

Ridge refits daily with its fixed three features and regularization 100. Its window
is the last 504 **mature predictor sessions**, requiring at least 126 distinct
sessions and 500 rows. The scaler fits on training rows only. Labels must end
strictly before the decision date; source receipts must precede the actual fit
cutoff. The 2021 history start supplies feature warmup and H20 label maturity.
Exact training support and fitted coefficients/scaler remain in each artifact.
This receipt-qualified refit differs from the historical frozen Ridge comparator;
it is four newly charged hypotheses, not a rewrite of earlier evidence.

Residual innovations use the shared nine-proxy regression with 126 strictly prior
returns. Initial historical bootstrap is known only at its actual receipt time.
After campaign start, missed innovations remain missing, including when the first
successful capture is delayed. Later price revisions cannot repair or recompute
previous innovations. Residual state and its forecast share one atomic parent-state
transition; the 252-session, skipped-21-session score retains its established rule.

### Independent baseline portfolios

The [baseline companion protocol](../config/research/prospective-equity-baselines-iex-v1.json)
adds three separately charged portfolios: rank blend, volatility20 and Ridge. It
binds the unchanged parent campaign by its exact hash. These portfolios share
past-only baseline support: the same 61-session history and finite Ridge, reversal
and volatility values, without requiring residual history. Percentile ranks and
weights are recomputed on that support. A broader-support result is not a paired
increment against a portfolio on the narrower primary support.

One acquisition, feature calculation, Ridge fit and residual update serve both
comparisons. Each has its own sealed forecasts and statuses. The original four-arm
forecast remains unchanged, including abstentions. Missing residual innovations
are never repaired, and no previous baseline portfolio is reconstructed for credit.

Companion enrollment charges **three additional hypotheses exactly once** under the
existing alpha lock. Its database-observed enrollment must precede the native day
boundary; the first declared decision date also applies. Each decision pins the
eligible immutable companion protocols before price reads. Later enrollment cannot
enrich that decision, and expired claims cannot be replayed.

If either comparison scores, one maturity-window acquisition evaluates all frozen
portfolios against the same labels. A primary abstention does not suppress baseline
outcomes. Unknown endpoints stay unknown; an abstained portfolio has no basket
payoff, including no fabricated zero return. Companion results never upgrade the
primary comparison's status or grant promotion authority.

## Availability and outcomes

Alpaca native daily bars follow the New York calendar day. Extended-hours trades
can update daily volume, so regular-session close is insufficient for this protocol.
The collection window starts at **00:30 New York time on the following calendar day**
and ends at 03:00 or five minutes before the next regular open, whichever is earlier.
This is a conservative acquisition policy, not an assertion that the provider will
never revise a bar. See [Alpaca aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq).

Enrollment must precede the native day boundary. A worker cannot backdate enrollment,
source receipts, fit time or commit time. Friday's decision is collected on Saturday;
exchange holidays, DST and early closes use the observed calendar. The next regular
session's daily open and the 20th subsequent session's daily close define H20.
Future exchange schedules are allowed; future price observations are not predictors.

The first maturity-window outcome acquisition is terminal. Entry and exit for each
symbol come from one newly captured all-adjusted data vintage. Zero-volume or
missing endpoints remain unknown; subsequent corrections cannot replace the primary
result. These are **adjusted-price payoff proxies**, not auction executions, borrow
availability, financing, spreads or fills. Separate per-side costs of 1/5 bp are
sensitivity scenarios, not measured trading costs.

Daily Rank IC observations overlap at H20. Economic baskets are scheduled on every
20th exchange session from the fixed campaign start, including dates whose capture
failed. Missing observations never reset the anchor or become zero returns. Do not
compound overlapping daily H20 returns as a unit-gross strategy or use IID significance.

## Architecture and operations

- `daily_plan.py` owns the immutable protocol and session windows.
- `daily_forecasts.py` owns pure receipt-aware math, reusing the existing estimator,
  feature, Rank IC, factor-regression and basket kernels.
- `daily_observations.py` coordinates injected read-only sources and private artifacts.
- `storage/alpha_daily.py` uses the existing alpha lock, `domain_events` and
  `alpha_projections`. No migration, new queue or delivery mechanism is added.
- The existing daemon owns the worker and its SDK clients. Its shared polling loop
  owns startup/shutdown; blocking SDK, filesystem and math operations run off-loop.
  Cancellation drains an in-flight read before closing its client.

Parent enrollment charges four hypotheses exactly once; the baseline companion
adds three through its own immutable reservation. All inspected member intervals
are excluded before price acquisition. A claim commits before I/O; a crash or expiry
cannot replay it. Completion samples database wall time after acquiring the journal
lock. A late result is retained, but cannot advance residual state or gain prospective
credit. Calendar revisions cannot shift frozen entry/exit dates or the basket schedule.

`alpha_pipeline.daily_panel` is disabled by default in the configuration model and
requires an explicit frozen `protocol_path` when enabled. The desk's campaign runs
September 18, 2026–September 17, 2027. Research acquisition uses the shared
`alpha_pipeline.daily_research` bounds/pacing. Full captures are bounded to 900 seconds;
readiness permits 1,200 seconds between durable capture completions or completed
worker checks. A progress callback runs only after the journal commits each result;
it does not issue healthy heartbeats during a stuck read. A poll can contain both
a new decision and a matured outcome.

`alpha_daily_panel` readiness proves current-run worker progress, including a healthy
idle check; it does **not** prove a usable forecast, fresh mature outcome or profitable
alpha. `alpha status`, `alpha forward` and the shared Telegram forward report show
separate campaign/decision/outcome evidence. Read those counts and private artifacts
alongside readiness. Restart applies changed worker configuration; an enrolled
protocol is immutable and any changed model/contract needs a new campaign identity.

Optional `comparison_protocol_paths` enroll bounded, unique companion documents on
startup. They are enrollment inputs, not activation switches: removing a path does
not remove its persisted enrollment from future eligible decisions or cancel pending
outcomes. The existing campaign remains the owner of all these observations. Changing
the **parent** campaign still requires explicit retirement/outcome draining, which
is not implemented; companion enrollment does not solve multi-campaign scheduling.

CLI/Telegram reports keep primary and baseline counts separate. Companion denominators
come from enrollments pinned to loaded decisions/outcomes; missing summaries and
truncation remain explicit. The `alpha_daily_panel_comparisons_total` counter counts
committed companion summaries separately from primary terminal counts. A failed
artifact write retains forensic files and a failure reference, without committing
partial successes or advancing residual state.
If storage cannot save the failure evidence either, the claim remains uncommitted
and expires; the worker reports failure and cannot replay that capture.

The collector retains source frames, actual per-symbol receipts, calendar, model,
weights, residual state and first outcomes under the private research artifact root.
The database holds hashes/references and bounded summaries. Neither raw data nor
runtime logs belong in Git. A campaign reference outside that root or with a changed
file hash fails closed.

## Validation and next decisions

Tests cover native-day/DST/early-close clocks, strict receipt and label boundaries,
future perturbations, training-only scaling, missing endpoints, revision-resistant
residual state, concurrency, database lock-wait expiry, replay, rollback, bounded
reporting and event-loop/cancellation behavior. Real SDK HTTP and disposable
PostgreSQL integration are separate from synthetic unit coverage. Deployment and
first actual forecast evidence are recorded separately in the private operational
review bundle and PR deployment record. A passing test is not an observed forecast.

No positive result or promotion follows merely from deploying this collector. After
mature observations arrive, assess available and expected counts, paired incremental
Rank IC with dependence-aware uncertainty, nonoverlapping cost sensitivity, exposure
and concentration. Authoritative instrument history, corporate actions and borrow,
measured execution and an explicit portfolio ownership/execution lane remain gates.
The next research priority is the [proposed lifetime-attribution design](alpha-lifetime-attribution-plan.md).
Its clock/deadline extensions and experiment protocol are not yet implemented or frozen.

### Initial collector validation — September 18, 2026

The full local suite passed 2,206 tests (133 opt-in skips). Focused math, shared
regression and actual SDK/SQLite tests passed 111 cases, including an empty native
daily SDK response; the shared boundary now preserves numeric missing values instead
of object-typed columns. Dedicated daily-journal tests passed 41 SQLite/PostgreSQL
cases, including real after-lock expiry. The separate real report-loader checks
passed on both databases. The full real SDK HTTP/WebSocket and disposable
PostgreSQL integration suite passed 418 tests.

A synthetic 73-symbol × 1,451-session first-bootstrap probe took 23.385 seconds on
this desk. It used no provider or runtime database and is a sizing check, not observed
production latency or alpha performance.

The baseline extension adds exact primary-document parity checks against the prior
implementation, all 231 missing-innovation boundaries, shared-fit/source/outcome
assertions, immutable enrollment races and separate report denominators. Fault
injection covers partial forecast/outcome/state writes and a self-consistently
resealed artifact with the wrong enrolled protocol. Restart tests preserve pending
outcomes and future eligible comparisons after configuration removal, without
recharging trials. Both artifact-validation paths must let the event loop progress.
Suite and deployment results for each revision are retained in its PR.

### Known support limitation

The primary comparison deliberately shares the residual model's complete-history
requirement. One missed residual innovation at session j makes its rolling feature
unavailable on sessions j+21 through j+251: **231 later decisions**. Because this is
a matched four-arm comparison, the same loss can abstain all four primary arms.
Per-stage support masks distinguish this from missing Ridge/style predictors.

The independent baseline companion now isolates those three portfolios from this
residual-history requirement. The 231-session limitation still applies to the
primary comparison. Actual forward coverage remains to be measured; an implemented
collector is not an observed forecast or a qualified strategy.
