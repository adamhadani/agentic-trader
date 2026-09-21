# Versioned trade lifetimes

Shared research and broker execution contract. This changes the experiment
being tested, so timed policies create new immutable alpha identities and require
fresh research. Existing definitions, positions and the rejected ETF campaign keep
their original meanings. Session alphas remain diagnostic-only.

## Two separate clocks

A timed execution policy declares integer elapsed-UTC seconds for:

1. **Resting entry lifetime**, measured from broker submission (the simulated
   submission bar in research). It bounds the working order's intent. Signal and
   approval freshness remain independent admission checks.
2. **Maximum holding intent**, measured from confirmed complete entry fill. Partial
   fills do not establish a complete owned position or reset either clock.

Deadlines are inclusive: at or after the deadline, request cancellation/closure at
the first eligible observation. These are operator/execution intents, not promises
of exchange cancellation or fills at an exact instant. Overnight/weekend elapsed
seconds count; a market close must still wait for an eligible regular session.

## Broker boundary and failure behavior

Keep GTC protection. Alpaca bracket cancellation can cancel the remaining group,
while exit legs activate only after the entire entry fills. A DAY bracket is not a
substitute for independently expiring the entry while preserving protection.
See [Alpaca order contracts](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

Persist an exact entry-order/client-ID cancellation intent before one DELETE.
Lookup confirms the result; a lost reply, timeout or restart must never replay the
mutation. A partial fill, changed identity, replacement or fill/cancel race requires
retained evidence and operator attention; do not release exposure or invent a fill.
Cancellation intents retain risk and fence fresh entry authorization/submission until the exact group is confirmed unfilled/terminal. A competing close on the same symbol is also fenced. Independent recovery grants only a bounded in-flight observation window; it never grants permission to resend.

Holding expiry uses the existing close service with a durable deterministic request
identity. It preserves the halt and requires exact broker quantity/direction.

A timer cannot prove cancellation success. Retain protection/risk and block new
risk when state is uncertain. No historical position receives a guessed lifetime.
No session activation gate is removed by implementing lifecycle machinery.

## Research boundary

Use the same elapsed-time deadline functions and versioned policy in minute replay.
Expire a pending entry before evaluating fills at/after its deadline. A held position
exits at the next eligible bar open after its holding deadline; gap/protective exits
already executable at that open take precedence. Full-size OHLC fills, immediate
cancellation and market exits remain explicit assumptions, not measured broker facts.
Do not add a timeout only to research or reinterpret prior experiment results.

Acceptance: immutable policy round trips and historical identity regression;
long/short deadline boundaries, cash timeline, weekend gaps and cost arithmetic;
real SDK cancellation timeout/fill races; durable replay/restart and independent
PostgreSQL contenders; no business action on historical policies; shared close
ownership and unchanged promotion/qualification gates.

## Research and operator interfaces

`alpha replay` accepts paired `--entry-lifetime-seconds` and `--holding-lifetime-seconds` (positive integers, maximum 31 days each). Frozen campaign protocols may declare `execution.lifetime` with the same fields and `version: elapsed_utc_v1`. The diagnostic lifetime-attribution harness additionally uses `elapsed_utc_v2`, where either deadline may be explicitly `null` while at least one remains positive; it never treats a missing value as an implicit long timeout. The limit is a validation bound, not a suggested strategy. Missing lifetime means the original policy; malformed/partial lifetime never falls back. Policy variation is a new charged experiment, not an amendment to old artifacts.

Intrabar fills are located only to their minute bar; simulated holding time starts at that bar's timestamp. Exact exchange fill time, cancellation latency, partial fills and queue priority are not known from OHLC. Existing protective open/gap exits take precedence over a simultaneous holding deadline. Overflow in descriptive annualization returns unavailable rather than destroying otherwise valid equity evidence.

`entry_cancellation` readiness reports unresolved durable intents. Close/cancel requests and transitions use the existing transactional notification outbox; no synthetic production messages are sent by tests/replay. An explicit halt remains until reviewed and resumed. Session activation is still blocked; this implementation alone supplies no qualification or live execution evidence.

Inspect durable cancellation evidence with `copilot db queue --kind entry_cancel` and `copilot db events --stream entry-cancel/COMMAND_ID`. These are read-only; unresolved intents have no resend operation.

## Native-daily one-session entries (semantics version 5)

`AlphaDefinition.semantics_version == 5` is the production native-daily contract:
no clock at all, and exactly one fixed entry-only lifetime.

- Requires `timeframe == "1d"` and `clock is None`.
- `execution` must be a `TimedAlphaExecutionPolicy` whose lifetime is exactly
  `TradeLifetimePolicy(resting_seconds=57_600, holding_seconds=None,
  version="elapsed_utc_v2")` — no holding deadline.
- The existing "timed execution requires a versioned clock" rule narrows to
  "timed execution requires version 3, 4 or 5"; versions 2-4 keep their exact
  rules unchanged.
- Version 5 places no constraint on `data_feed` or `adjustment`, exactly like
  version 2: discovery may run on a non-deployment feed, while qualification and
  promotion still separately require a raw Alpaca deployment feed.
- No field is added to `AlphaDefinition`, and the version-2 `clock` pop in
  `to_dict` is untouched, so every existing hash stays byte-exact. A version-5
  document serialises `"clock": null` plus a `lifetime`, so it cannot collide with
  a version-2 hash. A timed policy — including version 5 — is a new immutable
  identity requiring fresh research, holdout and qualification; no existing alpha,
  position or study is reinterpreted.

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

After this change, "timed execution is diagnostic-only" is enforced solely by the
version rules in `research/alpha/models.py`: versions 3 and 4 remain diagnostic;
version 5 is production-eligible because it has no clock and every production
fence keys on the clock.

**Deployment note.** Editing `scripts/launchd.sh` does not regenerate the installed
launchd plist (see [controlled restart](production.md#controlled-maintenance-and-restart)).
After deploying this change the operator must run
`./scripts/launchd.sh install-miner` and verify that
`~/Library/LaunchAgents/com.agentictrader.alphaminer.plist` contains
`--entry-policy gtc`; otherwise the scheduled ETF32 benchmark would silently adopt
the new `session` default for daily mining.

**Known limits.** The journaled RUN/manifest record does not itself state which
`--entry-policy` was requested; only each trial's persisted `definition`
(`execution`/`semantics_version`) and its `evidence.execution_model` label
(`gtc_limit_conservative_brackets_v2` or `session_limit_conservative_brackets_v1`)
record the policy it was actually evaluated under. Built-in (non-alpha) strategies
still submit GTC limit entries at the observed price: `TradeLifetimeService` only
reconciles signals carrying `alpha_policy.lifetime`, so this contract changes
nothing about built-in strategy execution.
