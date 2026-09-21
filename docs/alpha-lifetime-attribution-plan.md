# Alpha lifetime attribution: proposed design

**Status — September 21, 2026: mechanistic run complete ([result](alpha-lifetime-attribution-2026-09-21.md)); the confirmatory full-policy endpoint below is not yet implemented or evaluated.** Earlier status — September 18, 2026: contract implemented; experiment not frozen. The
shared independent lifetime policy (`elapsed_utc_v2`), diagnostic fixed-daily clock
(`fixed_daily_v1`), and pure paired P0/P1/P2 replay harness are implemented and
covered by focused tests. No executable protocol, fresh seed, family snapshot or
full run is frozen for this study. The matrix below remains the intended contract
to review and freeze. It grants no qualification or deployment authority and does not change production thresholds. The
[roadmap](alpha-roadmap.md#current-priorities-after-the-whole-stack-survey) owns ordering.

## Question and bounded comparison

The [800-search diagnosis](alpha-power-diagnosis-2026-09-18.md) found a functioning
planted predictor, but GTC pending entries suppressed about 90% of later known-control
opportunities. Search selection and joint statistical gates caused separate losses.
Determine whether entry expiry and holding-horizon alignment improve conversion
before changing the search objective, DSL or acceptance rules.

| Policy | Resting entry | Holding intent | Paired interpretation |
| --- | --- | --- | --- |
| P0 | Original GTC | Original protective exits, no time expiry | Reference |
| P1 | 86,400 elapsed UTC seconds | Original protective exits, no time expiry | P1−P0 isolates entry expiry |
| P2 | 86,400 elapsed UTC seconds | 86,400 elapsed UTC seconds | P2−P1 isolates holding expiry conditional on bounded entry |

Keep limit construction, stops, targets, trailing protection, friction, forecast
horizon, search objective and all gates unchanged. No lifetime grid, repricing or
market-entry replacement is included. Expiry is an intent evaluated at the first
eligible observation, not a guaranteed fill. Weekends count. Synthetic fills remain
located at bar start; protective open/gap exits retain priority over holding expiry.
The current generator has next open equal to prior close and UTC business-day
timestamps. Its next-open holding exit can align the planted one-bar return, but
does not establish real exchange timing or a broker fill.

## Required shared contracts

This cannot run through existing configuration alone:

- `AlphaDefinition` permits timed execution with either the version-3 session clock
  or the diagnostic-only version-4 fixed-daily clock; `simulate_strategy` still
  rejects version-3 session-clock definitions and accepts only the fixed-daily
  diagnostic clock.
- `TradeLifetimePolicy` version `elapsed_utc_v1` requires both deadlines to be
  positive. Version `elapsed_utc_v2` represents P1's disabled holding expiry with
  an explicit `None` while retaining the same exact deadline functions.

Never substitute a very large timeout or bypass clock validation inside the study
runner. Preserve existing lifetime serialization, alpha version-2/3 hashes and
numerical results. Prove P0 parity with the original simulator behavior. Reuse the
shared deadline functions and `simulate_execution` state machine; keep the new
clock diagnostic-only until its qualification/execution evidence is supported.
The synthetic clock must not impersonate the receipt-qualified New York native-day
clock of the [prospective collector](alpha-daily-panel.md).

The implementation is exposed through two isolated CLI stages. `alpha lifetime-plan
--seed SEED --output PATH` writes a new immutable protocol. `alpha lifetime-study
PROTOCOL --output DIRECTORY` runs it through the existing private artifact lifecycle,
checkpointing each discovery selection before paired replay. Both commands are
synthetic-only and never open the runtime database, market provider, broker or
Telegram notifier.

## Intended matrix and evidence accounting

Retain the earlier dense/sparse generators and null/positive effect sizes, 2,500
observations, four development replicates per profile/effect, and 128 null plus 64
positive validation replicates per profile. Paired random/genetic searches retain
16 original expression attempts: **400 datasets and 800 baseline searches**.
Use a fresh root seed and new versioned namespaces, separating development,
validation, search and bootstrap roles while preserving declared data pairing.

Persist the known control and original GTC discovery-selected winner before any
holdout access. Do not reselect winners under P1/P2. Recompute both discovery folds
and holdout for these two routes under each new policy identity: the measurement
API requires the matching definition's discovery evidence. Old GTC discovery
metrics cannot be combined with timed-policy holdout results.

The intended conservative reservation is **20 attempts per search bundle**:
16 original expressions plus four targeted route×policy attempts. Freeze and test
the exact accounting and variance-sample treatment, including coincident
known/winner identities, before running. Cache identical measurements without
treating paired routes as independent samples. Do not silently retain a 16-attempt
family after testing extra policies. Synthetic attempts remain in immutable study
artifacts; they never increment the production research ledger.

Freeze a newly sourced current-family snapshot without resetting lifetime history.
Within each dataset/method, all three policy assessments use the same prior plus
declared bundle count and the same pooled discovery Sharpe observations. Replicates
remain separate counterfactuals against that prior. Retain descriptive count/variance
sensitivities; missing source evidence remains unavailable rather than becoming an
empty family or a historical scalar.

## Primary endpoint boundary

**P2 current-family full-policy acceptance is the sole confirmatory policy.** Retain
the original **16 profile×effect×method×route endpoints**, one-sided exact binomial
bounds with Bonferroni allocation `.05/16`, and reference criteria: null upper bound
≤5%, dense positive lower bound ≥80%, sparse positive lower bound ≥50%. P0/P1 are
mechanistic comparisons; results cannot select a different primary policy afterward.

Making all three policies primary would produce 48 endpoints. With 128 null
replicates, even zero accepts gives an adjusted upper bound of about 5.223%, above
the 5% criterion; at least 134 null replicates would be needed. Any such scope change
requires a new predeclared budget. Unavailable endpoints remain in denominators.

## TDD and delivery boundary

Reuse `power_study` selection/seeds/family composition, `power_trace` diagnostics,
immutable promotion measurements/criteria and `study_artifacts.execute_study`.
Extend their contracts; do not add a second simulator, lifecycle or journal.

Before freezing, tests must establish:

- Old identities/results remain exact; independently disabled deadlines work;
  expiry precedes same-time fills, including long/short and weekend boundaries.
- Protective-gap precedence, fees, complete cash timelines and boundary censoring
  remain correct. Expired entries cannot keep suppressing subsequent opportunities.
- Future perturbations cannot change discovery selection or earlier intents;
  trace on/off results match. Variant discovery/holdout identities agree.
- Paired seeds, all attempt charges, shared family evidence, one bootstrap per
  unique measurement and unavailable denominators survive artifacts/replay.
  Mechanical development failures stop validation before access.
- A small actual CLI/artifact integration constructs no runtime database, provider,
  broker or notifier. Shared lifetime changes also pass existing SDK cancellation,
  close/recovery and PostgreSQL race regressions.

Retain paired occupancy, suppressed pulses, timely/delayed entries, expirations,
completed trades, censoring, net/fee returns, predictor IC and every criterion.
Keep the constant-exposure comparison explicitly a no-fill/no-fee forecast proxy.
Improved conversion alone is not profitability; continued known-route gate failure
and frozen-winner failure diagnose different next steps. Only after review, tests
and a frozen protocol should fresh development and validation be run.
