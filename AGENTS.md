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
