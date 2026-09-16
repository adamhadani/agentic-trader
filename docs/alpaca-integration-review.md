---
layout: default
title: Alpaca integration review
---

# Alpaca integration review — September 16, 2026

## Official contract and implementation

The runtime uses `alpaca-py` typed requests/models. Broker I/O runs in worker
threads; HTTP and WebSocket tests exercise the installed SDK itself.

- Bracket children activate after the complete entry fill. Cancellation can race
  execution, and partial take-profit fills change remaining protection. Closure
  therefore verifies exact group IDs, terminal cancellations, position quantity,
  side and cost basis before submitting once. A read-only paper probe also found
  held stops absent from OPEN listings, including nested listings; exact parent
  retrieval supplies those legs. See [Alpaca order semantics](https://docs.alpaca.markets/us/docs/orders-at-alpaca).
- A successful PATCH only acknowledges a replacement. The adapter follows exact
  replacement links, reads back the working stop and checks its price before
  local state changes. It never resolves ownership by symbol. Pending, rejected,
  partially filled or unverified replacements leave the local stop unchanged.
  See [replacement contract](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1).
- Bulk cancellation returns individual statuses. The panic report counts accepted
  requests, not assumed terminal cancellations. Individual failures remain in
  structured logs. See [bulk cancellation](https://docs.alpaca.markets/us/reference/deleteallorders-1).
- Stream fills wake authoritative REST reconciliation. Message price alone never
  closes a tracked trade; exact REST fills govern accounting. See
  [trade-update streaming](https://docs.alpaca.markets/us/docs/websocket-streaming).
- Entry requests preserve client IDs and reject unsupported types/combinations
  instead of silently becoming market orders. Equity prices use the documented
  decimal precision. Crypto bracket entries are refused; no crypto instruments
  are currently configured. Crypto capabilities differ from equities; see
  [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading).

The installed SDK retries HTTP 429/504 and does not set request timeouts.
`BoundedTransport` is the shared SDK extension used by trading and stock-data
clients: it supplies a socket timeout and disables mutation retries. GET requests retain SDK retries. The SDK
exposes this boundary privately, so the HTTP tests must pass on upgrades.
`execution.broker_request_timeout_seconds` is per HTTP attempt, not a total
transaction deadline. Stop verification uses `execution.stop_replace_timeout_seconds`.

Ambiguous entry submissions retain `SUBMITTING`, record the client ID before the
request, and persist a trading halt. They require exact broker lookup and operator
review before `/resume`; schema 005 workers now perform exact client-ID recovery,
and `/resume` refuses unresolved requests.
Close intents already support exact lookup recovery. Neither path blindly replays
an order. Broker-backed multi-slice execution is disabled until per-slice accounting
and protection exist; immediate execution remains available.

## Integration coverage

These tests are deterministic local integrations, not proof of exchange behavior.
No test has production credentials, database access, or external network permission.

| Critical path | Coverage |
| --- | --- |
| Entry request → accepted → partial → complete fill → valuation → close → performance | `tests/integration/test_alpaca_http.py`, actual SDK/HTTP plus SQLite and opt-in PostgreSQL |
| Native bracket/held-leg cancellation, close after stop replacement | Same HTTP suite; stateful cancellation and exact replacement chains |
| Stop acknowledgement versus confirmed replacement; thesis/risk preservation | HTTP + real storage; parametrized NEW/pending/rejected results |
| Lost entry/close acknowledgement, socket timeout, HTTP 504 | Actual SDK transport; assert one POST, durable uncertainty/halt or close recovery |
| Broker WebSocket authentication/subscription and binary fill delivery | Actual `TradingStream` against loopback WebSocket, REST reconciliation, duplicate-notification guard |
| Closed session, emergency queueing, bulk cancel partial failure | HTTP tests plus `tests/agent/test_panic_kill_switch.py` |
| Concurrent close intents, migrations, DB guards | `tests/integration/test_postgres.py`; independent DB clients and disposable `test_` database |
| Partial/external/unrelated fills and cancellation races | `tests/broker/test_incident_regressions.py`, `tests/execution/test_position_closing.py` |
| Telegram delivery retry/poll recovery and command authorization | Loopback HTTP transport tests plus command/service fixtures; handlers execute once |
| CLI preview/confirmation, Telegram flatten menu/scopes | Close lifecycle tests and read-only deployed `scripts/verify_runtime.py` |
| Event-loop responsiveness during broker calls | Delayed real HTTP response with concurrent asyncio task |

Default CI runs the isolated full suite, including loopback integration. A separate
PostgreSQL job provisions a disposable database and runs all integration tests with
`--run-postgres`; the broker-to-database lifecycle runs against PostgreSQL there.
No runtime DB guard is disabled.

## Holistic findings and next priorities

1. **Schema 005 verification:** durable reservation/FIFO admission, lookup recovery,
   event journal, outbox and readiness have real HTTP/WebSocket and PostgreSQL
   integration coverage. Approval, signal, quote and session deadlines are rechecked
   after slow preflight work. Verify each deployment separately with the shared runtime
   verifier. See [workflow design](durable-execution.md).
2. **Complete account accounting:** cumulative order observations now preserve
   partial/replaced/canceled/external order evidence and replayable views. Individual
   execution allocation, fees, corrections and corporate actions still need an
   account/lot ledger. `/perf` remains confirmed tracked full closes, before fees.
3. **Operational alerting:** wire readiness/dead letters to alerting and define event
   retention. Daily macro feed age and shared research executor contention remain.
4. **Module boundaries:** extract entry admission/reconciliation and move remaining
   construction into a composition root. Keep small typed service results separate
   from HTML. The shared close coordinator and broker transport are useful boundaries.
5. **Remaining async/config work:** synchronous YAML promotion reads/writes remain
   in chat tools; moving writes to workers requires cross-process locking first.
   Research shares the executor with trading I/O. The macro tutorial still has
   explanatory threshold literals, and live trailing still uses initial risk
   distance despite its historical ATR mode name. Consolidate these policies in
   their domain modules rather than performing a mechanical string replacement.

See the broader [architecture review](architecture-review.md) and
[operations](production.md) for deployment checks and recovery boundaries.

### Schema 005 contracts

Entry preflight uses the official latest-trade and order/clock/position SDK models.
Recovery uses the persisted `client_order_id`, validates symbol/side/quantity, and
never resubmits after a 404. Cumulative order snapshots retain `filled_qty`,
`filled_avg_price`, update time and replacement links; they are not presented as
individual execution IDs. See [SDK order models](https://alpaca.markets/sdks/python/api_reference/trading/models.html)
and [client-ID order lookup](https://docs.alpaca.markets/us/docs/working-with-orders).
The in-process HTTP adapter is an explicit restricted-environment test option;
the default harness and CI retain real TCP/WebSocket and disposable PostgreSQL.
