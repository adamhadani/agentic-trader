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
| P1 | `manage_trailing_stops` updates local state before broker stop replacement; it can also overwrite the thesis text. | Keep requested and acknowledged broker stops separate, preserve initial risk/thesis, and commit confirmed stop state only after broker success. |
| P1 | Watchdog and `/healthz` prove process liveness, not data, broker-stream or Telegram freshness. | Add readiness with last successful scan, quote age, broker sync, Telegram poll and reconciliation backlog; alert on staleness. Bind active diagnostic endpoints to a trusted interface or protect them. |
| P2 | Notification happens after the close transaction. A process crash or Telegram failure can lose delivery despite a correct closed trade. | Transactional outbox with idempotent delivery, retries, message ID and visible terminal failure state. Current audit records attempts/results but is not a retry worker. |
| P2 | `TradingCopilot` still constructs calendar, regime, strategy, graph and research services and returns transport-specific strings. | Move construction to a composition module; extract execution/reconciliation/report services with small protocols and typed results. Keep Telegram/CLI rendering at the transport boundary. |
| P2 | Synchronous market-data/provider retries run in the async scan path. Missing data may produce permissive fallback macro state. | Offload blocking work, propagate data age/quality, and define explicit stale-data policy before expanding automation. |
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
