---
layout: default
title: Architecture review — September 2026
---

# Architecture review — 2026-09-15

## Assessment

The package boundaries are useful: broker adapters, storage, screeners, risk,
research, presentation and transport have separate modules and tests. The largest
remaining coupling sits in `TradingCopilot`, which combines orchestration, execution,
report building, trailing stops, research commands and dependency construction.
PostgreSQL is the runtime backend; SQLite is an explicitly selected test/development
backend, not a production failover mechanism.

## Changes made during this review

- Config is loaded at an explicit boundary, without import-time secrets or ambient
  overrides of constructed models. Storage requires an explicit target/config.
- DB, broker, data fetcher and notifier can be injected before copilot construction.
  The unused `FuturesCopilot` alias and main-module class reexports were removed;
  callers import the canonical class from `agent/copilot.py`.
- Same-signal execution claims and position closure use conditional SQL updates.
  Polling/stream reconciliation share a lock; a losing close sends no duplicate alert.
- The broker owns cost basis and valuation. Exact order ownership and actual fills
  govern realized P&L. Unverified Alpaca closes and quarantined rows are excluded.
- Runtime environments, audit vocabulary, halt keys, broker tolerances, retry defaults
  and shared data defaults are centralized. Alpaca protocol states use SDK enums;
  stream retry timing is validated config. External payload keys, historical migrations,
  mathematical identities and explicit test examples retain literals intentionally.
- Test config/secrets are isolated at collection and per-test setup. Function-scoped
  temporary DBs prevent state sharing; native-driver guards cover libpq/libcurl.
  PostgreSQL is opt-in against a `test_` database. Regression variations use parametrization.
- JSON UTC logs retain run IDs through Alembic; persistent audit records include
  source revision, order IDs, fill evidence, valuations and notification outcomes.

## Remaining findings, in priority order

| Priority | Finding / consequence | Recommendation |
| --- | --- | --- |
| P1 | `execute_signal_by_id` reads portfolio exposure before claiming. Two different pending signals can both pass the aggregate limit. `SUBMITTING` does not reserve capacity. | Add a transactional portfolio reservation covering quantity/notional/class budgets, plus approval-time session/macro/risk revalidation. Use a deterministic broker client-order ID for ambiguous submission recovery. |
| P1 | Complete fills work; partial entry/exit combinations, cancellations, replacements, sliced plan IDs and externally created broker trades lack a fill ledger. | Add immutable order/fill tables keyed by broker account/order/execution IDs. Reconcile idempotently; expose unmatched/partial states to operators. Keep sliced execution disabled until protective brackets and partial accounting work. |
| Resolved Sep 16 | Stop replacement previously persisted intent as fact and overwrote the thesis. | Exact replacement-chain verification now precedes a conditional stop update; request/result audits preserve initial risk and thesis. See the Alpaca review. |
| P1 | Watchdog and `/healthz` prove process liveness, not data, broker-stream or Telegram freshness. | Add readiness with last successful scan, quote age, broker sync, Telegram poll and reconciliation backlog; alert on staleness. Bind active diagnostic endpoints to a trusted interface or protect them. |
| P2 | Notification happens after the close transaction. A process crash or Telegram failure can lose delivery despite a correct closed trade. | Transactional outbox with idempotent delivery, retries, message ID and visible terminal failure state. Current audit records attempts/results but is not a retry worker. |
| P2 | `TradingCopilot` still constructs calendar, regime, strategy, graph and research services and returns transport-specific strings. | Move construction to a composition module; extract execution/reconciliation/report services with small protocols and typed results. Keep Telegram/CLI rendering at the transport boundary. |
| P2 | Missing macro enrichment is explicit but still allows volatility-only evaluation; daily observations lack a maximum-age admission policy. Worker threads keep the event loop responsive but share executor capacity; heavy research can still contend with trading work. | Propagate data age/quality, define stale-data policy, and move heavy research into a bounded job worker when workload grows. |
| P2 | Timeframe filtering follows strategy conflict resolution; timeframe is absent from persisted signals. Research annualization and live bar routing differ. | Filter before netting, persist timeframe/data timestamp, and validate per-timeframe research/live parity. |
| P2 | Promotion weights/convex optimizer are not used in live sizing. External YAML edits do not refresh the daemon registry. | Make allocation integration and reload explicit; report the loaded registry version, not only current file contents. |
| P3 | Model aliases remain for symbol/contract and duplicated report fields; multiple legacy helper exports persist. | Consolidate one model vocabulary when extracting services, update all callers, and remove alias-only tests rather than adding adapters. |
| P3 | Some older tests exercise provider-failure fallback via blocked network attempts; constructors still trigger repeated migrations. | Replace incidental network fallback with explicit fixtures; separate schema setup from repository construction. Retain real loopback/PG tests in integration groups and keep assertions on behavior, not implementation mirrors. |

## Test design and operational evidence

`tests/conftest.py` owns safety boundaries; domain-specific mocks belong in local
fixtures. `tests/broker/test_incident_regressions.py` parametrizes exit ownership,
chronology, side, symbol and quantity failures. The default suite cannot silently
fall back to a developer's broker or DB credentials. Integration tests validate
both fresh migrations and upgrade/downgrade paths on a dedicated database.

The concrete incident is recorded in [incident notes](incident-2026-09-15.md).
Use `copilot db audit` and the daemon's `/metrics` for future investigations.
Audit data has no automatic retention/archive job yet; size and back it up along
with PostgreSQL. Historical research notes and the reference PDF are design/source
material, not statements of current operational guarantees.

## Telegram and asyncio follow-up

The deployed incident traces showed `httpx.ReadError` while sending `/status`
and `/positions` replies. Those commands reached their handlers but response
delivery failed. The SDK retries polling automatically, but outbound delivery
needed its own transport policy. Previously there were no durable command
receipt/completion audits or poll freshness timestamps.

- Shared `RetryingTelegramRequest` retries transient network errors, HTTP 5xx and
  bounded rate limits for every Telegram API caller. Permanent 4xx responses retain
  SDK error handling. It does not repeat application/trade handlers. An ambiguous
  lost response can still produce a duplicate message; this is not an outbox.
- `ObservedPollingRequest` preserves the SDK retry loop and records errors,
  recovery, periodic successful polls and last-success metrics. One handler wrapper
  records update ID, handler name and start/completion/failure without message text
  or chat IDs. A shared error handler records failures and attempts an operator notice.
- Blocking scan fetches/strategy calculations, spot valuations, simulator quotes,
  dynamic correlation, Monte Carlo runs and async schema/diagnostic work now use
  worker threads. Scans share a lock; registry iteration snapshots its entries;
  Alembic upgrade/downgrade calls share a thread lock for its global context.
- Telegram and metrics initialize before the scheduler starts immediate scans.
  A generic event-loop monitor records lag metrics and persistent stall events.
  Metric values preserve timestamp precision instead of rounding epoch seconds.
- Removed the unused sync-or-async `fetch_daily_bars` compatibility branch and
  updated tests to the actual provider protocol.

Remaining limits: Telegram update handlers are serialized, so a long interactive
scan/backtest can queue later commands even while polling remains healthy. A bounded
job interface for slow commands is preferable to enabling blanket concurrent trade
handling. Synchronous constructor migrations and small configuration/catalog file
reads remain; separate schema setup from construction in the next persistence
refactor. Threads do not forcibly cancel synchronous SDK calls; enforce provider
timeouts and avoid sharing mutable provider configuration during a request.

## GEX and command-surface review

The real SPY option chain reproduced `cannot convert float NaN to integer`.
Both calls and puts now share numeric normalization before aggregation: invalid
strikes are excluded; missing counts become zero; modeled IV/time defaults and
missing fields are disclosed in quality notes. Spot prices must come from actual
provider data; hard-coded ETF estimates were removed. Expiration count participates
in cache identity, and unavailable/empty chains are reported explicitly.

GEX is labeled a Yahoo option-chain/model estimate. Its sign convention, approximate
time-to-expiry and strike-based gamma-flip calculation are research heuristics,
not measured dealer inventory or an Alpaca account valuation. Validate/refine this
model before using it as an execution gate.

Report titles now use the shared Agentic Trader name. Telegram help describes
operator approval and broker-confirmed accounting; fixed risk numbers were removed
from help, and the evaluation prompt renders actual configuration/timeframe limits.
CLI async errors use one logged nonzero-exit boundary; GEX JSON output contains
only JSON on stdout. Parametrized tests cover command help and read-command routing.

## Unified macro reporting and response defaults

`/macro` is the single market-context command. `/regime`, its callback and its
conversational tool were removed. The dashboard takes the same `RegimeSnapshot`
used by evaluation and combines VIX classification, macro indicators, breakout
permission, risk multiplier and `max(regime minimum R:R, risk.min_risk_reward_ratio)`.
`/explain_macro` explains the macro model; it is not a replacement for the combined
entry checks. The menu, help and inline buttons use `/macro`.

Macro enrichment reuses one VIX/yield/dollar snapshot. The two-year Treasury yield
comes from [FRED DGS2](https://fred.stlouisfed.org/series/DGS2); FRED observation
dates and the snapshot fetch time are displayed. These daily/closing observations
can have different dates. Missing/invalid macro data is reported as unavailable,
not replaced by fixed yields, credit spreads, inflation or VIX. If enrichment
fails, the dashboard explicitly labels the remaining volatility-only policy;
if VIX itself is missing, regime evaluation fails. Stale-data admission policy
and per-feed freshness alerts remain follow-up work.

VIX display colors use the classified enum, and stress scoring shares configured
VIX thresholds (`regime.vix_watch_threshold`, `vix_elevated_threshold`,
`vix_extreme_threshold`). Elevated/extreme minimum R:R uses
`regime.elevated_min_rr` / `extreme_min_rr`. Telegram backtests receive
`backtest.lookback` from the loaded app config; research symbol and message chunk
size use shared constants. Typed evaluation fields have no presentation fallback.
Conversational positions/status use the same report providers as slash commands,
removing invented default prices/P&L and duplicate notional calculations.
`macro_report` audit events retain fetched time, published dates, VIX and the
combined filter decision, including missing-enrichment details.

Accepted orders without a broker fill price now say “ORDER ACCEPTED / Awaiting
broker fill”; they do not label the proposed entry or zero as a fill. Research
provider errors reach the shared command error/audit boundary.

## Coordinated closes (September 16)

The new close service separates lifecycle/persistence from Alpaca transport and
Telegram/CLI presentation. Per-symbol database exclusivity and broker client IDs
prevent duplicate close submissions across processes. Preview and confirmation
share the same service; the adapter verifies cancellation before a market close.
Full-fill accounting remains separate and authoritative. `/flatten` preserves halt
state and does not fabricate trade history for externally opened positions.

Remaining boundaries: there is no atomic transaction across broker cancellation
and replacement, so failure after cancellation can leave a position unprotected;
responses and audits expose it. Uncertain requests block further closes until
exact recovery/operator review. Complete partial-fill allocation, durable recovery
of abandoned pre-submission claims, and coordination of concurrent external/new
entry orders remain work for the broader execution ledger/reservation design.
The live trailing implementation uses recorded risk distance rather than the
configured mode's promised ATR/high-water mark; correcting that is a separate task.

## Alpaca contract and integration follow-up

The [September 16 Alpaca review](alpaca-integration-review.md) records official
contracts, SDK transport fixes, HTTP/WebSocket/PostgreSQL coverage, and remaining
entry reservation/fill-ledger/readiness gaps. Broker-backed slicing is now refused
because acceptance alone cannot establish per-slice fills or protection.
