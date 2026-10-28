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

## `session_count_v1`

A second, session-counted holding policy (`SessionLifetimePolicy`,
`agentic_trader/execution/lifetime_policy.py`) for definitions that hold for a
fixed number of trading sessions rather than elapsed seconds — currently the
[a priori PEAD catalog probe](apriori-alphas.md#part-2-live-paper-probe)'s
`apriori_bracket_v1` bracket. It pairs a plain elapsed-seconds resting deadline
(`resting_seconds`, identical in kind to `TradeLifetimePolicy`'s) with a
session-counted holding deadline:

- The regular session the entry actually fills in is **session 1** — exactly how
  the setup study's `label_bracket` counts holding sessions, so the live exit and
  the research label agree. A fill stamped up to `SESSION_FILL_GRACE` (60 s) after
  that session's close (a closing-auction print at 16:00:00.250, or just after an
  early close) still counts as that session. `holding_sessions` sessions later, the position
  closes at `close_time_et` (New York, default `"15:45"`) — or, on an early
  close, 15 minutes before that session's close, whichever is earlier
  (`SESSION_CLOSE_BUFFER`).
- The broker's own trading calendar is required to count sessions and locate
  each one's close; there is no synthetic weekday fallback. The service reads
  `ceil(holding_sessions × 7 / 5) + 14` calendar days from the fill date
  (`holding_calendar_span_days`), so a longer policy never falls short by design.
  `TradeLifetimeService` passes `sessions=None` when the calendar call itself
  fails, and `SessionLifetimePolicy.holding_deadline` raises
  `SessionEvidenceError` (a fill outside the regular session, or a calendar
  shorter than `holding_sessions`) otherwise; either way `assess_lifetime`
  returns `LifetimeAction.REVIEW`, never a guessed deadline. A `REVIEW` halts
  new risk exactly like every other lifecycle uncertainty in this document;
  `/resume` does not waive it.
  A calendar read that raises is a failed evidence read, not a `REVIEW`
  decision: it is retried each sweep and reviewed only after
  `execution.lifetime_evidence_review_after_failures` (5) consecutive failed
  sweeps of that signal. A `REVIEW` decision still halts immediately.
- Every existing `elapsed_utc_v1`/`elapsed_utc_v2` policy, hash and position is
  unchanged: `lifetime_from_dict` dispatches on the document's own `version`
  field, and `SessionLifetimePolicy` is a distinct dataclass with its own
  `version: "session_count_v1"` — it can never round-trip as, or collide with,
  a `TradeLifetimePolicy` document.

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
submitted for". The research half holds because `validate_sampling`
(`agentic_trader/research/alpha/validation.py`) rejects any daily frame whose
consecutive labels are closer than 24 h, so label + 57,600 s always precedes the
next label whatever the label convention is — 00:00 UTC under the `alpha mine`
default `--feed yfinance`, or New York midnight under Alpaca: one session either
way. Live measures from `submitted_at`; broker admission requires an open regular
session (09:30–16:00 New York), so submission + 16 h falls between 01:30 and 08:00
the next day, before the next open: one session. A value of 86,400 gives research
one session but live two partial sessions. A single named constant,
`DAILY_ENTRY_LIFETIME_SECONDS = 57_600`, lives beside `TradeLifetimePolicy`.

**Live precondition.** A version-5 alpha only submits an entry when a scan lands
while (a) the previous daily bar is complete, (b) the signal is within its
four-hour validity (`config.execution.signal_max_age_seconds`, default 14,400 s),
and (c) the regular session is open (`AlpacaBroker.entry_market_context` requires
the broker clock's `is_open`). Verify the scan schedule satisfies this before
enrolling any version-5 paper probe.

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

## Native entries

`TradeLifetimeService` resolves a signal's entry lifetime through `entry_lifetime_of`:
`alpha_policy["lifetime"]` when declared, else the native marker in
`decision_provenance["entry_lifetime"]`. A new native card records that marker
(`native_entry_lifetime_marker`, a `NativeEntryLifetimePolicy`, version `session_close_v1`)
only when `execution.native_entry_lifetime` is `session` (shipped value; code default
`off`), `execution_mode` is `alpaca` and the broker executes the card's asset class (a
futures card under alpaca gets no marker and no card line). Cards recorded before deploy
carry no marker and are not bounded. The marker written before the session-close bound
(the daily `elapsed_utc_v2` policy, 57,600 s, no holding) reads as the same native
policy; any other marker shape is malformed and goes to review. Only the marker resolves
to the native policy; an `alpha_policy` lifetime never does, so alpha, PEAD/catalog and
probe entries keep their own semantics.

An unfilled marked entry is due at the **earlier** of its submission session's regular
close + 5 minutes (16:05 New York, 13:05 on an early close, from the strict broker
calendar for the New York date of the broker's `submitted_at`) and `submitted_at +
57,600 s` (the one-session outer bound). The close is inside the window
`scripts/awake.sh` keeps the host awake (09:15–16:30 New York), so a stale GTC entry is
cancelled in its own session instead of overnight, when the host may sleep and the entry
could fill at the next open. If the calendar is unavailable or its read fails, the outer
bound alone applies (nothing halts and nothing is cancelled early); a submission outside
a regular session goes to review. The due entry is cancelled once through the existing
`entry_cancel` path. The card says "Unfilled entry is cancelled at the session close."

A working partial fill of a marked entry rests like its unfilled remainder until the
deadline ("within resting lifetime": no halt, no review); at or after the deadline it goes
to review and halts. A terminal partial, and every alpha, catalog or probe partial, is
reviewed at once as before. Once a marked entry fully fills (`executed_at` set) it has
nothing left to time: reconciliation and `/resume` checks make no broker read for it, so a
transient read error cannot halt (the same holds for any policy without a holding
deadline). If its recorded fill is at or after the deadline, the cancel was missed (late
reconciliation or a sleeping host): one `entry_lifetime_missed` event on
`signal/<id>/lifetime` and one notice, no halt, decided from the journaled order
projection and `executed_at` only.

The bound is a service cancel, not DAY time-in-force: Alpaca uses one TIF for the whole
bracket, so DAY would also expire the protective legs after a fill. It does not reuse
`alpha_policy`, which belongs to alpha identity and qualification.

A cancel (deadline or operator) resolves one of three ways:

- **Confirmed no fill** (complete, zero-fill terminal group by exact read): `accepted`,
  the signal becomes `FAILED` and capacity is released.
- **Filled before the cancel** (the broker refused the DELETE, so the DELETE call itself
  errored, for example 422 not cancelable or a lost reply, and a fresh exact read shows
  the full entry quantity filled with the protective exits intact by the shared
  `protective_stop` capacity rule): work status `rejected` with reason
  `filled_before_cancel`. The position is open and protected; no halt.
- **Anything else** (partial fill, replacement, incomplete group, failed lookup, or an
  accepted DELETE whose parent then reads filled: polled within the owner's window, then
  this): `unknown`, new risk halts and the reservation is retained.

## Known limits

The journaled RUN/manifest record now also carries the requested
`entry_policy` (`"session"` or `"gtc"`) at run level; each trial's persisted
`definition` (`execution`/`semantics_version`) and its `evidence.execution_model`
label (`gtc_limit_conservative_brackets_v2` or `session_limit_conservative_brackets_v1`)
independently record the policy it was actually evaluated under. Built-in
(non-alpha) strategies submit GTC limit entries at the observed price; since the
native-entry change (see [Native entries](#native-entries)) only new native cards
recorded with `execution.native_entry_lifetime: session` in alpaca mode carry a
session-close bound, and signals recorded earlier stay unbounded. The simulator
grants a session-bounded entry the entire next session including its opening
print; live, the order is submitted inside the session and sees only the
remainder of it and can never take the open. The research fill rate is therefore
an upper bound. The September 18 GTC reference shares this bias, so the paired
comparison remains valid, but forward/probe execution evidence is required before
attributing any improvement to live trading.
