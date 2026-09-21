# Session-bounded entries for native-daily alphas — design

Status: proposed, September 21, 2026. Sub-project C1 (first mining-funnel fix).
Evidence: [lifetime attribution result](../../alpha-lifetime-attribution-2026-09-21.md).

## Problem

An alpha entry is a limit order at the tick-rounded signal-bar close that rests
until filled (`entry_limit`, `OrderRequest.time_in_force` default GTC; the copilot
never overrides it). With a correct forecast the price moves away, the order does
not fill, and the resting order suppresses every later signal. With no forecast the
price wanders back and fills. In the 800-search study the planted forecast completed
**1.3 trades against 58–69 for the null** under this policy; bounding the resting
entry to one bar gave 62–75 trades, improved the planted route in 100% of paired
comparisons and left the null negative after costs. The same construction is used
live, and the miner evaluates every candidate under it, so discovery currently
rewards drift and selects against forecast skill.

The live remedy already exists and runs: `TradeLifetimeService` (scheduled every
minute) reads a lifetime from `signal.alpha_policy`, journals an `entry_cancel`
intent, cancels the exact unfilled entry and keeps GTC protection. A DAY bracket is
not a substitute: time in force applies to every leg, so protection would lapse at
the close. The only thing preventing use is one identity rule:
`Timed execution requires a versioned session clock` (`research/alpha/models.py`),
whose two clocks are session (v3, barred from qualification, activation and entry
risk) and synthetic fixed-daily (v4, `data_feed == "synthetic"`).

## Goal and non-goals

Let a production native-daily alpha carry an **entry-only** lifetime that means the
same thing in research and live, make mining use it, and measure whether the funnel
can then recognise a planted signal.

Non-goals: holding deadlines (measured harmful when imposed after selection);
repricing or market entries; built-in trend-pullback/squeeze strategies; session
(v3) activation; any change to `ValidationPolicy` (a new field would invalidate
every stored qualification), to gates, to lifetime trial accounting, or to the
broker/cancellation machinery.

## Contract: semantics version 5

`AlphaDefinition.semantics_version == 5` requires all of:

- `timeframe == "1d"` and `clock is None`;
- `execution` is a `TimedAlphaExecutionPolicy` whose lifetime is
  `TradeLifetimePolicy(resting_seconds=57_600, holding_seconds=None,
  version="elapsed_utc_v2")`.

The rule "timed execution requires a versioned clock" narrows to "timed execution
requires version 3, 4 or 5". Versions 2–4 keep their exact rules. Version 5 places
no constraint on `data_feed` or `adjustment`, exactly like version 2: nothing in the
contract depends on a provider calendar, discovery on a non-deployment feed and the
synthetic power harness must exercise the identical contract, and qualification and
promotion already require a raw Alpaca deployment feed.

**Why exactly 57,600 seconds.** Daily research cannot resolve time inside a bar, so
the only entry lifetime it can represent honestly is "the session the order was
submitted for". Research stamps the order at the entry bar's label (New York
midnight); label + 16 h is 16:00 New York, before the next label, so the order
expires at the next bar before its fill check: one session. Live measures from
`submitted_at`; broker admission requires an open regular session (09:30–16:00 New
York), so submission + 16 h falls between 01:30 and 08:00 the next day, before the
next open: one session. A value of 86,400 gives research one session but live two
partial sessions. Daylight-saving changes fall on non-trading days, so consecutive
daily labels are always at least 24 h apart and the arithmetic is unaffected. A
single named constant, `DAILY_ENTRY_LIFETIME_SECONDS = 57_600`, lives beside
`TradeLifetimePolicy`.

**Identity.** No field is added to `AlphaDefinition`, and the version-2 `clock` pop
in `to_dict` is untouched, so every existing hash stays byte-exact (pinned by the
existing regression). A version-5 document serialises `"clock": null` and a
`lifetime`, so it cannot collide with a version-2 hash. A timed policy is a new
immutable identity requiring fresh research, holdout and qualification; no existing
alpha, position or study is reinterpreted.

**Why nothing else changes.** Every production fence keys on `clock is not None`
(admission, promote, qualify, shadow, market-clock guard); version 5 has no clock,
so it passes them by construction. The simulator reads the lifetime from the
execution policy alone. The screener stamps `definition.execution.to_dict()` into
the signal, admission compares it byte-for-byte with the immutable definition, and
`TradeLifetimeService` already treats `holding_seconds=None` as disabled. After this
change "timed execution is diagnostic-only" is enforced solely by the version rules
in `models.py`; the docs must say so.

## Mining under the deployed policy

- `AlphaMiner.mine(..., execution: AlphaExecutionPolicy | None = None)`. When given,
  every catalog and generated definition is rebuilt with that execution and
  `semantics_version=5` before evaluation, so validation folds, selection and the
  journaled trials all describe the policy that will trade.
- `copilot alpha mine --entry-policy [session|gtc]`, default `session` for
  `--timeframe 1d` and rejected with a clear error for other timeframes (which keep
  `gtc`). `gtc` reproduces earlier benchmarks exactly.
- The scheduled ETF32 job passes `--entry-policy gtc` explicitly until an operator
  edits its configuration, so the benchmark never changes silently.
- `alpha test` accepts the same option so an ad-hoc expression is backtested under
  the policy it would trade with. Session/minute `alpha replay` is unchanged.

Decision recorded for review: the manual default is `session`, because the measured
GTC policy is adverse to correct forecasts; say so to reverse it.

## Confirmatory power run

Reuse the existing power study rather than extending the lifetime harness: it
already measures the required endpoint, and running it under the new policy gives a
like-for-like comparison with the [September 18 result](../../alpha-power-diagnosis-2026-09-18.md).

- `PowerProtocol` gains `entry_policy: Literal["gtc", "session"] = "gtc"`. The
  field is omitted from the protocol document when it is `gtc`, so every existing
  frozen protocol keeps its identity and still loads. With `session`,
  `select_power_candidates` mines with the version-5 execution policy; the known
  control and every generated trial are rebuilt under it by the miner, and all
  downstream measurement, family composition, bootstrap and summarisation code is
  unchanged because it reads the policy from each definition.
- `alpha power-plan --entry-policy session` freezes the confirmation protocol with a
  fresh root seed and a newly sourced read-only family snapshot; replicate counts,
  generators and search budget are unchanged.
- **Predeclared policy: session-bounded entry, no holding deadline.** Selection is
  performed under that policy, because the question is whether the funnel works
  when mining and execution agree. The September 18 run is the GTC reference.
- Endpoint: current-family full-policy acceptance per profile × effect × method ×
  route with the existing exact binomial bounds and Bonferroni allocation over the
  16 primary endpoints: null upper bound ≤ 5%, dense lower bound ≥ 80%, sparse
  lower bound ≥ 50%. Unavailable endpoints stay in denominators.
- Synthetic only; no runtime database, provider, broker or notifier is constructed
  by the study; it grants no qualification or promotion authority.

If it passes, mining campaigns become meaningful and paper probes may be enrolled.
If it fails, the per-criterion record identifies the next loss (search objective
versus one-bar IC, or DSR family composition), which become C2/C3.

## Error handling

Malformed or partial lifetimes never fall back to GTC (existing behaviour). A
version-5 definition with any other lifetime, a holding deadline, a clock, or a
non-daily timeframe is rejected at construction. `--entry-policy session` with an
intraday timeframe is a CLI error. Live cancellation uncertainty keeps its existing
contract: retain risk, fence new entries, recover by lookup only, never resend.

## Testing (TDD)

- Identity: v5 round-trips; the pinned v2 hash is unchanged; every illegal v5
  combination is rejected; a v2 timed definition is still rejected.
- Simulator on New-York-midnight daily labels: an unfilled entry expires at the next
  bar before its fill check; a Friday entry does not survive to Monday; an entry
  filled on its own bar is unaffected; GTC behaviour is byte-identical without a
  lifetime.
- Parity: for submission instants across 09:30–16:00 New York in both EST and EDT,
  `entry_deadline` precedes the next regular open; with 86,400 it does not (pins the
  reason for the constant).
- Miner: every trial definition carries the requested execution and version 5; the
  `gtc` path reproduces a stored benchmark run exactly; intraday + `session` errors.
- Admission and live: a v5 signal passes `_alpha_entry_rejection` when active;
  `TradeLifetimeService` cancels an unfilled v5 entry after the deadline and never
  schedules a close for it; SDK and PostgreSQL siblings of the existing
  `tests/integration/test_trade_lifetimes.py` cases.
- Power protocol: a `gtc` protocol document and identity are byte-identical to
  before; a stored September 18 protocol still loads; a `session` protocol mines
  version-5 definitions for the known control and every trial; a tiny CLI run
  constructs no runtime services.

## Documentation

`docs/alpha-trade-lifetimes.md` (version 5 contract and the 57,600 derivation),
`docs/alpha-pipeline.md`, `docs/cli-reference.md`, `docs/alpha-roadmap.md` (C1
delivered; next losses), `docs/alpha-lifetime-attribution-plan.md` (the power-study
confirmation supersedes the unimplemented P2 endpoint), `CLAUDE.md`/`AGENTS.md`.

## Observed, out of scope

- Built-in strategies appear to use the same GTC limit construction; measure before
  changing live strategy behaviour.
- A daily bar is treated as closed 24 h after its midnight label and signals are
  valid for four hours, so only scans between roughly 05:30 and 16:00 New York can
  yield an executable daily-alpha signal. Verify during paper-probe deployment.
