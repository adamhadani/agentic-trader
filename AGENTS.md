# Working in agentic-trader

Read [CLAUDE.md](CLAUDE.md), [development notes](docs/development-notes.md), and
[operations](docs/production.md) before changing runtime behavior.

- This checkout runs the launchd Alpaca **paper** daemon and Telegram poller.
  Inspect registration first. Never start a second daemon/poller alongside it.
- Preserve existing work; inspect `git status` before editing. User-authorized
  fixes may include a controlled restart after tests and operational verification.
- Use Python 3.14 and `uv`. Run `uv run pre-commit run --all-files` before commits.
- Tests use temporary SQLite, explicit fixture config, stripped credentials, and
  blocked network I/O. PostgreSQL tests require `--run-postgres` plus a disposable
  `TEST_POSTGRES_URL` whose database name starts `test_`. Never relax DB guards.
- Inject configuration and storage before constructing services. Bare `AppConfig`
  cannot implicitly select a runtime DB. Dry scans use an empty simulated portfolio.
- Broker identity, actual fills, and conditional DB updates govern trade closure.
  Never match an exit by symbol alone or infer fill price from an order request.
- Keep credentials, chat identifiers, raw runtime logs, DBs, and incident snapshots
  out of Git. Use redacted audit evidence and the shared report builder for debugging.
- Reuse domain enums/constants; put operator-adjustable policy in validated config.
  Preserve external wire formats and frozen migration literals.
- Update docs for changed behavior. Record tests and deployed verification separately;
  a healthy process is not proof of broker, data-feed, or Telegram freshness.

- Keep blocking I/O/CPU work off the asyncio loop. Use shared transport retry and
  observation mechanisms; never retry a trade handler to recover a Telegram reply.
  Validate event-loop responsiveness and shared-state races when adding threads.

- `/macro` owns the unified market-context report; `/regime` has been removed.
  Use the evaluator's combined snapshot and configured thresholds. Never fabricate
  quote, P&L or macro observations to make a response look complete.

- Route Alpaca closes through `PositionCloseService`: persist an exclusive intent,
  confirm symbol-order cancellation, revalidate the broker snapshot, then submit
  once with its client ID. Recover uncertain outcomes by lookup, never replay.
  `/flatten` previews by default and does not change the trading halt. Dry-run
  commands must not cancel/submit orders or send synthetic production messages.

- Preserve real SDK HTTP/WebSocket integration coverage. Mutation responses can be
  ambiguous: never retry POST/PATCH/DELETE automatically. Resolve stop replacements
  by exact IDs and confirm working price before DB updates; preserve thesis/risk.
  Review [Alpaca contracts and coverage](docs/alpaca-integration-review.md).

- Entry authorization uses `EntryExecutionService` and the durable FIFO. Reserve
  risk atomically; only a valid preflight token may commit `submitting`. Never
  expire/replay a broker submission. Recovery looks up the original client ID.
- Critical notification intents share the signal/exit/stop transaction. Outbox
  retries delivery only; Telegram delivery is at least once. Preserve dead letters.
- Order projections replay `domain_events`; do not fabricate fills or silently
  reset signal IDs while journal/work exists. Read [durable workflows](docs/durable-execution.md).
- `/readyz` and `doctor --readiness` are passive current-run freshness checks.
  Keep TCP/WebSocket and PostgreSQL integration verification separate from the
  optional in-process SDK transport used in restricted environments.

- Account performance uses the [activity ledger](docs/account-ledger.md): exact
  activity IDs, Decimal cash/inventory reconciliation and signed broker cost basis.
  Preserve account binding, fenced refreshes, revisions/retractions and replay.
  Unsupported/stale/unreconciled evidence must withhold account realized P&L;
  tracked full-close metrics are a separate subset. Never infer partial ownership.
