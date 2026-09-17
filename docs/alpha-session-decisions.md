# Versioned session decisions

A2b now gives **new** alpha definitions an explicit session decision clock, shared
by minute replay, formulaic screening and shadow scoring. This is the contract and
parity increment, not completed live migration. The normal scanner still acquires
native provider bars; it does not populate session snapshots. Session definitions
cannot qualify, activate or reserve new entry risk. The forward data observer
continues collecting receipt evidence without scoring or trading.

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
not grant shadow dates/decisions. This is diagnostic deduplication, not a durable
live decision/submission cursor. Qualification, registry activation and durable
entry admission explicitly reject session-clock versions, including session-derived
`1d` versions. The coarse simulator also rejects them.

Tests cover RED → GREEN clock/version and admission failures, exact existing hash,
delay/expiry boundaries, future receipt/reversed clocks, malformed calendars,
long-delay future perturbation, research/screening/shadow score parity, holiday
proposal expiry, persistent GTC orders and journal rebuild/no-credit behavior. Real
SDK loopback HTTP and disposable PostgreSQL exercise acquisition through screening
and journal persistence; these are integration fixtures, not actual paper fills.

## Next acceptance boundary

Wire bounded minute/calendar acquisition into a session-aligned decision worker,
with adequate causal history and actual receipts. Persist one decision identity per
version/symbol/closed candle before downstream processing; test restart, revised
bars, skipped windows and slow reads without replaying stale decisions. Reuse the
existing journal/entry services and keep native built-in strategy acquisition separate.
Then collect forward score and actual broker execution evidence. These remain
required before lifting any session/intraday promotion gate. Continue the
[alpha roadmap](alpha-roadmap.md); broader mining and A3 follow these foundations.
