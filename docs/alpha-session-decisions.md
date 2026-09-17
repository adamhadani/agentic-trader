# Versioned session decisions

New alpha definitions have an explicit session decision clock shared by replay,
formulaic screening, shadow scoring and the daemon's **diagnostic forward worker**.
The worker acquires bounded raw-minute history with actual receipts and durably
consumes each candidate/symbol/candle window. Normal trading scans still acquire
native bars. Session versions cannot qualify, activate or reserve new entry risk:
actual forward and broker execution evidence remain necessary.

## Immutable meaning

- Existing `semantics_version: 2` definitions retain their exact serialized documents,
  version hashes and fixed-duration clock. A regression pins the pre-change hash.
- `semantics_version: 3` requires a raw Alpaca feed and a `clock` object containing
  `bar_layout: rth_open_v1`, `decision_delay_seconds` and `max_lateness_seconds`.
  Clock changes create new version IDs. No automatic conversion of old definitions.
- `SessionClockPolicy` in `market/bars.py` owns the window calculation. The default
  eligibility interval is **[close + 60 seconds, close + 180 seconds)**: 60 seconds
  of assumed delay followed by a 120-second decision window. These are diagnostic
  assumptions pending forward measurements, not claims about provider latency.
- Delay permits 0–86,400 integer seconds; lateness permits 1–86,400. A long delay
  evaluates the newest eligible historical prefix, excluding later observed values.
  Newer eligible decisions supersede older ones, including a newer no-signal score.

## Receipt and execution boundaries

`SessionBars` retains its observed calendar. Validation checks aligned, unique,
ordered, timezone-aware starts/closes and compatible signal/execution metadata.
`SessionSnapshot` binds the requested symbol and actual request/receipt times. Receipt must follow every
completed observation and the request; future receipts cannot score now. The caller
must supply real transport times—constructing this type does not establish provenance.
The shared `research/alpha/clock.py` selects the eligible prefix for both screening
and shadow. Session screening warms up from its versioned ATR/swing windows and
causal score availability; fixed-clock screening retains its existing minimum. A native DataFrame cannot substitute for a session snapshot, even if its
values look similar. Feed, adjustment, layout and stale/missing data fail closed.

Replay version `observed_session_minutes_v2` uses the clock inside the immutable
alpha definition. `ReplayPlan` no longer owns a second independent timing policy.
The obsolete internal `SessionReplayPolicy` API is removed. Historical v1 artifacts
remain untouched and cannot be relabelled as v2 evidence.

A proposal expires before submission if no regular-session execution minute exists
inside its window. A final Wednesday signal cannot silently become a new Friday
order after Thanksgiving. The default short window also expires session-derived daily signals before the next
open; it is not a qualified overnight-entry policy. An order already submitted while eligible retains its
GTC lifecycle across closed sessions; expiry does not cancel pending broker orders.
Results retain availability, expiry, eligible minute, supersession and expiry flags.
An absent future execution minute at the dataset boundary remains censored, rather
than being falsely reported as an observed expiry. Actual receipts and broker/operator
latency remain distinct from replay's historical availability assumption.

```bash
uv run copilot alpha replay 'delta(close,3)' \
  --symbol SPY --interval 15m --start 2024-11-27 --end 2024-11-29 \
  --decision-delay-seconds 60 --max-lateness-seconds 120 \
  --output /private/path/new-session-run
```

This is a new, charged diagnostic attempt that excludes the inspected period before
provider access. It is not an exact rerun of the frozen September 16 v1 experiment.
It sends no messages or orders. Do not spend real research evidence just to repeat
the fixture integration suite.

## Evidence and activation boundary

Session shadow scores are retained as `valid: false`, reason
`session_clock_diagnostic`, with their actual close and observation times. Repeated
same-candle diagnostics deduplicate through the existing journal, and rebuild does
not grant shadow dates/decisions. Qualification, registry activation and durable
entry admission explicitly reject session-clock versions, including session-derived
`1d` versions. The coarse simulator also rejects them.

Tests cover RED → GREEN clock/version and admission failures, exact existing hash,
delay/expiry boundaries, future receipt/reversed clocks, malformed calendars,
long-delay future perturbation, research/screening/shadow score parity, holiday
proposal expiry, persistent GTC orders and journal rebuild/no-credit behavior. Real
SDK loopback HTTP and disposable PostgreSQL exercise acquisition through screening
and journal persistence; these are integration fixtures, not actual paper fills.

## Durable diagnostic worker

`SessionDecisionService` receives configuration, repository, read-only session source,
clock and private artifact directory. The daemon composes it with dedicated bounded
SDK readers through the same lifecycle as the data observer. Blocking reads, scoring
and artifact writes run outside asyncio; shutdown drains work before closing clients.
No broker mutation, notifier, second execution queue or new database schema is involved.

Desk `alpha_pipeline.decisions` enables SPY/QQQ, a 30-second poll, 14 calendar days of
history, at most 12 matching-feed session definitions and 128 consumed windows per poll.
Its explicit `feed: alpaca:iex` is independent of the trading feed. Only registered
version-3 candidates with that exact feed and explicitly eligible symbols are observed.
Other-feed versions and their evidence remain intact; reports flag them as
`feed_not_configured`. [IEX controls](alpha-iex-forward.md) have new identities,
not a relabeling of the original SIP cohort. With no such candidates,
the worker is healthy and idle; that is not a forward sample. Insufficient history,
missing minutes and contract mismatches remain unavailable. There is no fallback feed.

- Initial enrollment starts now, without backfilling a decision already available.
  Registry-generation changes conservatively re-enroll and retain the intervening gap.
- Observed calendars enumerate due windows; a changed previously observed calendar
  fails closed without moving the cursor. Gaps beyond the bounded calendar horizon
  are retained as unknown intervals rather than fabricated missed candles.
- Cursor CAS and immutable `(version_id, symbol, closed_at)` claims share one journal
  transaction under the existing alpha lock. Competing clients cannot claim twice.
- Expired windows become `missed` without price reads. Overlapping older windows are
  `superseded`. Claimed work becomes `scored` or `unavailable`, with actual transport and
  completion timestamps. Commit checks the clock after acquiring the database lock
  and fences registry changes. No failure triggers a retry of that window.
- An uncompleted claim becomes `interrupted` after expiry, including after restart
  or candidate removal. Late completions retain separate forensic evidence and cannot
  replace that outcome. Bar revisions never rescore an already consumed candle.
- Claims, cursors, pending indexes, outcomes and late evidence replay from existing
  `domain_events` into `alpha_projections`. Price exposure is excluded before reads;
  forward scoring adds zero search trials and zero qualified shadow credit.
- Private manifests bind the definition, clock, policy, calendar and runtime. Raw
  arrays, input hashes, receipt times and result hashes permit reconstruction. The
  journal's final status is authoritative if registry/expiry changed after artifact writing.

`alpha status` includes `latest_session_decision` and pending claims. `/readyz` adds
current-run `alpha_decisions` progress; it means the worker progresses, including idle
and unavailable results, not that prices are complete or a strategy is qualified.
`alpha_session_decisions_total` uses bounded symbol/timeframe/feed/status labels;
structured logs include the durable decision ID and artifact hash. Inspect all outcomes,
not only the latest success. `copilot alpha forward --days 7` and `/alphas` share the
[bounded evidence report](alpha-forward-evidence.md): recorded outcomes, missing/gap
warnings, receipt timing and diagnostic score directions. No routine Telegram messages are emitted.

## Evidence and next acceptance boundary

Tests cover initial enrollment, receipts/score parity, missing data, late/reversed clocks,
registry changes, repeated/revised candles, missed windows, unknown gaps, restart and late
completion fencing, event-loop responsiveness and client-draining shutdown. Real SDK
loopback HTTP plus independent PostgreSQL clients exercise contention and journal replay.
These fixtures are not real paper fills or elapsed forward sessions.

Collect real forward decisions and review publication/score availability, then compare
broker acknowledgments and fills through existing execution services. Keep all session
promotion/entry gates until those contracts and statistical qualification are satisfied.
Independent candidate reads are currently bounded but not shared; if measured latency
requires batching, group acquisition while retaining separate claims and receipt lineage.
Research/live portfolio attribution remains separate from this diagnostic milestone.
