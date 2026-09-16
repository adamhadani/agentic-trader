---
layout: default
title: Architecture review — September 2026
---

# Architecture review — September 16, 2026

## Assessment

The central architecture is sound for an operator-approved paper desk: application
services coordinate business transitions, broker adapters own remote contracts,
repositories own transactional concurrency, and one outbox owns durable delivery.
The main structural debt is still `TradingCopilot`, which combines composition,
orchestration, reporting and research. Avoid adding new responsibilities there.

The subsequent [alpha-stack review](alpha-stack-review.md) records the mining,
DSL, validation, promotion and portfolio-construction findings and stress tests.
The [alpha pipeline](alpha-pipeline.md) implements its causal, shared-policy, journal and
qualification foundations. Remaining empirical/portfolio gates are tracked there.
The historical priority was to repair research/live parity, causality, confidence-statistic and promotion
gaps before increasing search volume or connecting optimized weights to trading.
The previously ranked work below remains deferred, not completed or superseded.

## Boundaries and mechanisms

| Concern | Mechanism and reason |
| --- | --- |
| Entry admission | Durable FIFO, atomic reservations and fenced preflight leases. A committed submission never expires/replays; exact client-ID lookup resolves uncertainty. |
| Closing | Per-symbol exclusive intent plus broker cancellation/revalidation. Its lifecycle differs from entry admission; sharing broker mutations through a generic retry queue would be unsafe. |
| Broker evidence | Versioned `domain_events`, exact identities, replayable order/activity projections. Account reconciliation uses Decimal and broker cost basis, without guessed tax lots. |
| Operator traces | `audit_events` records commands, transport outcomes, valuations and startup identity. This complements replayable domain facts; it is not a second execution state machine. |
| Critical notifications | Transactional outbox, fenced claims, bounded retries and preserved dead letters. HTTP retries handle individual delivery attempts; the outbox survives process failure. Neither repeats a trading action. |
| Health | One passive `/readyz` contract; external watchdog persists debounced incidents and uses the same journal/outbox. Active `doctor` probes remain CLI-only. |
| Retention | Bounded compaction of redundant old healthy observations, preserving failures/recovery boundaries/latest observations and all financial evidence. |
| Data resilience | Explicit market-data/calendar provider fallbacks. Broker valuations and fills have no alternative-feed/zero fallback. SQLite is explicit test storage, never runtime DB failover. |

Application services receive dependencies. Short DB transactions never span remote
calls. Protocol/SDK conversion belongs in adapters; policy belongs in validated
config; rendering belongs in presentation/transport modules. No Redis/pubsub or
second job infrastructure is justified for the current single-destination outbox.

## Changes from this review

- Removed active `/healthcheck` HTTP diagnostics. It could run migrations and
  external calls and expose connection details. `/readyz` is passive; active CLI
  diagnostics redact DB destinations and Telegram identities/errors. The Finnhub
  probe now calls its exchange-holiday provider instead of mislabeling a ForexFactory
  lookup as authenticated Finnhub success.
- Added an external operational monitor with a pure incident state machine,
  transactional projection/journal/outbox writes, stale-observation fencing and
  delivery-aware reminders. Replay never resends a notification.
- Added bounded healthy-observation compaction with independent maintenance locking.
  Financial evidence, notification deduplication records and dead letters stay intact.
- Removed duplicated report field aliases/post-init copying, a cointegration alias,
  an economic-calendar alias, research metrics alias, unused broker forwarding method
  and a screener type re-export. Callers now use canonical fields/modules; collection
  fields use dataclass factories. Deterministic levels are a dataclass, not a tuple
  with duplicate attributes. Removed fixed leverage wording and the unused sizing
  wrapper that substituted $100; parametrized sizing tests now exercise real prices
  and notional caps.
- Active `doctor` no longer constructs the whole copilot. Supervisor DB construction
  runs off the asyncio loop. No generic retry wrapper was introduced around trades.
- Consolidated current docs and distinguished historical designs from present behavior.
  Live status risk capital, account performance, research returns and broker cash
  have different meanings and are now described explicitly.

## Remaining findings, ranked

| Priority | Finding and use case | Recommendation / tradeoff |
| --- | --- | --- |
| P1 | Corporate actions or stock transfers make account reconciliation unsupported; per-signal partial exits remain conservative. | Add exact typed activity semantics and replay fixtures, then explicit ownership/allocation/protection models. Keep totals withheld and sliced trading disabled until complete; never assign by symbol. |
| P1 | Missing macro enrichment permits volatility-only evaluation; daily feeds have no maximum-age admission policy. The economic-calendar fetch can also turn a provider error into an empty event list. | Define per-feed age/calendar policy and fail/size decisions before increasing automation. Strict gates improve safety but can block valid trades on provider holidays/outages. |
| P1 | Live trailing uses recorded risk distance, while research supports ATR/high-water marks; replacement uncertainty has limited durable requested/acknowledged modeling. | Unify policy inputs and persistent stop intent, retaining exact broker confirmation and thesis/risk. Avoid changing existing protective orders during an incidental refactor. |
| P2 | `TradingCopilot` still constructs several services and returns some transport-specific strings. Constructors run migrations. | Extract composition/bootstrap and typed report/reconciliation services incrementally. Explicit migration startup needs coordinated changes to every CLI/daemon path; do not leave a compatibility fallback. |
| P2 | Heavy research shares executor capacity with trading I/O; Telegram handlers serialize. | Add a bounded research job service with cancellation/status and separate capacity. Do not enable blanket concurrent trade handlers. |
| Resolved in alpha pipeline | Timeframe filtering formerly followed conflict resolution and signal rows lacked timeframe/version. | Filtering now precedes arbitration; schema 008 persists optional timeframe/version/policy/provenance. Historical rows remain unknown. Regression and replay evidence live in [alpha implementation](alpha-pipeline-implementation.md). |
| Resolved foundation; portfolio execution gated | Schema 008 replaces YAML ownership with journal-backed immutable versions and CAS activation, installed between scans. | Forecast combination and constrained portfolios stay shadow-only until partial-fill attribution, plan revalidation and protection ownership are implemented. |
| P2 | Monitoring depends on the same host/DB/Telegram destination; local files and audit/financial history still grow. | Add an independent external alert destination and backup/restore/log-rotation policy. Archive durable evidence only with tested replay and deduplication preservation. |
| P3 | Some symbol/contract aliases and dictionary/SDK shape handling remain; older tests can exercise implicit provider failure paths. | Consolidate typed boundary models when touching those services and use explicit provider fixtures. Preserve SDK transport contracts, not unnecessary internal aliases. |

## Testing and review evidence

Safety fixtures are function-scoped; credentials and native network/DB guards
apply before collection and per test. Parametrized tests exercise state transitions,
races, recovery, unknown submissions, order ownership and projection replay.
Integration uses the actual Alpaca SDK over loopback HTTP/WebSocket plus disposable
PostgreSQL. New monitoring tests exercise passive HTTP → incident transaction →
Telegram SDK HTTP delivery, including permanent failure/dead letters; PostgreSQL
checks independent observers and maintenance/admission locks.

Source tests do not prove deployed broker or Telegram freshness. Use the current
run/revision verifier, `/readyz`, account reconciliation and actual poll metrics
following a controlled single-daemon restart. See [operations](production.md),
[monitoring](operational-monitoring.md) and [Alpaca coverage](alpaca-integration-review.md).


## Alpha pipeline architecture follow-up

The implementation reuses application services, pure domain functions, the existing
journal, transactional projections and execution FIFO. No second notification bus,
order queue or runtime YAML fallback was added. Scientific runs/qualification and
operational registry acknowledgments are distinct evidence. New DSL/config contracts
are validated; simulation assumptions are explicit and fail-closed gates cover
missing, stale, mismatched and reused data.

Next priorities: session-correct fine-bar intraday execution replay (intraday
promotion is blocked meanwhile); deployment-feed shadow/forward evidence and paper fill review;
point-in-time eligibility and leave-cohort-out validation; semantic expression
clustering; daily decay/capacity reports; versioned portfolio rebalance plans with
post-rounding/partial-fill risk and protection attribution. Preserve the other
operational priorities above. Do not enable RL or shared-position alpha execution
merely because the new infrastructure passes regression tests.

The ordered research work queue now lives in [alpha-roadmap.md](alpha-roadmap.md), including calibration before broader search. This document continues to own the operational priorities above.

[A1b evidence](alpha-study-2026-09-16.md) identified feature-dependent return
coverage: flat periods with undefined scores were dropped from simulation
statistics, as were some pending orders' same-bar exit losses. Implemented
[A2a](alpha-return-timeline.md) defines the execution clock separately
from feature availability, with compatible variance samples and policy fencing.
The [fresh study](alpha-timeline-study-2026-09-16.md) has no unavailable comparisons
and passes null-search criteria, but positive-control power remains insufficient.
Session-correct finer-bar replay remains A2b, followed by A3 objective alignment.
Preserve the original incomplete study and gates;
do not treat missing comparisons as null rejections or loosen trade-count requirements.
