---
layout: default
title: Production Operations — Cash-Plus Trading Copilot
---

# Production operations

Current local baseline: **Alpaca paper**, PostgreSQL, one macOS launchd daemon and
one Telegram poller. `EXECUTION_MODE=alpaca` plus `ALPACA_PAPER=true` selects the
broker's paper account. `EXECUTION_MODE=paper` selects the local simulator.

See [development notes](development-notes.md), [CLI reference](cli-reference.md),
[incident remediation](incident-2026-09-15.md), and [architecture review](architecture-review.md).

## Ownership and scheduling

`com.agentictrader.copilot` runs `uv run copilot daemon` from this checkout after
its launchd shell sources `.envrc`. `com.agentictrader.watchdog` checks the PID every
60 seconds; `com.agentictrader.alphaminer` runs weekly. Do not start a second daemon,
`listen`, or Compose service while the installed poller owns the bot.

- Swing scans: every four hours from startup, immediate first run, all timeframes.
- Intraday scans: every 15 minutes from startup, session gated, `15m` filter.
- Position monitor: every minute, with additional broker stream wakeups.
- Macro briefing: weekdays 12:30; retuning: Saturday 02:00. These cron schedules
  inherit scheduler/system timezone. Intervals are not candle-close aligned.
- Alpha miner: Saturday 02:00 local launchd time, 25 iterations; no auto-promotion.

Scans stage suggestions; an operator approves entry orders. Configuration and the
strategy registry load at construction. External file changes require a restart.

## Configuration and state

Production `load_config()` merges YAML, `.envrc` values and environment overrides,
without modifying the process environment. Explicit environment values (even empty
ones) win. `COPILOT_ENV_FILE=''` disables dotenv. `COPILOT_CONFIG` selects a YAML.

Database precedence: `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML →
production default. `--db-path` accepts a URL or SQLite path; `--db-name` explicitly
selects a SQLite sandbox. There is no automatic SQLite failover. `data/signals.db`
is historical and must not be mistaken for the current PostgreSQL database.

Head migration is `003_audit_provenance`. `signals`, `system_state` and
`audit_events` hold trading and operational state. Construction currently checks
migrations, even for informational copilot commands. Back up PostgreSQL before
schema or historical repairs. Do not use `db clear` to fix contamination: quarantine
preserves original evidence and removes rows from operational queries.

## Broker accounting and reports

`copilot positions` and `/positions` use one Alpaca account snapshot for quantity,
average entry, current price and unrealized P&L. Source/retrieval time and tracking
mismatches are shown and audited. Broker-only positions are displayed once. A
failed snapshot reports an error; another feed or zero is not substituted.

`/perf` separates current broker open-position P&L from **all recorded confirmed
closed-trade P&L before fees**. It is not an account-day return. Entry/exit IDs,
actual average fill prices, full quantity and chronological order determine realized
profit. Unverified Alpaca closes and quarantined signals are excluded. Out-of-band
or partial transactions may remain unmatched pending review; see architecture findings.

Alpaca manual close submits an order and waits for confirmed fill accounting.
A requested `exit_price` cannot become brokerage profit. Panic persists the halt
first and requests closes; inspect broker positions for pending or failed exits.

## Safe verification

```bash
launchctl list | rg 'com\.agentictrader\.'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/healthz
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/metrics
uv run copilot db current
uv run copilot db audit --limit 20
uv run copilot positions
```

`/healthz` proves liveness. `/healthcheck`, `copilot doctor`, and `launchd.sh health`
perform active diagnostics, including migrations and an LLM request. A separate
`copilot metrics` instance does not contain the running daemon's metrics.

```bash
uv run copilot scan --dry-run --no-llm --symbols IWM
uv run copilot test-alert
```

A dry scan has an empty temporary database and simulated portfolio, no Telegram,
no broker connection/execution and no monitoring. Market-data calls still occur.
`test-alert` previews locally. Explicit `test-alert --send` requires a different
`TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` and sends a labeled,
non-actionable message without persistence. Never test order buttons using real
production signals merely to check connectivity.

## Controlled maintenance and restart

1. Finish isolated tests, type/lint checks and `uv run pre-commit run --all-files`.
   Commit the intended source so the daemon's startup revision is identifiable.
2. For a brief ordinary restart use `./scripts/launchd.sh restart`. For a maintenance
   pause, unload the watchdog first, then the daemon. A plain `stop` is not durable
   while the watchdog is loaded. Preserve the installed plist files.
3. Apply migrations/validated data repair. Keep original rows/snapshots; run the
   incident repair tool in preview mode before `--apply`.
4. Reload the daemon and then watchdog. The installed launch agents reside under
   `~/Library/LaunchAgents/`; use `launchctl bootout/bootstrap gui/<uid>` with their
   exact labels/plist paths. Editing `scripts/launchd.sh` does not regenerate plists.
5. Verify the new PID and `runtime_started` audit event match the committed revision;
   verify `/healthz`, recent reconciliation, Alpaca paper account access, Telegram
   bot identity/command registration, and the shared position report.

Do not restore old code against a newer schema without checking compatibility.
Quarantined records can be reviewed/restored with an audited migration; they were
not deleted. Keep broker-held protective orders in place during a daemon restart.

## Logs, audit and monitoring

- `data/copilot.err.log`: JSON application logs with UTC time and process run ID.
- `data/copilot.log`: printed cards; `data/watchdog.log`: timestamped PID checks.
- `data/alphaminer.log` and `.err.log`: scheduled research output.
- `audit_events`: signal creation, exact fill changes, stream and REST evidence,
  valuations, close submissions/completions, notification message IDs/results,
  quarantine/restoration, and source revision at daemon startup.
- Prometheus includes active positions, fills, entry-fill synchronization and
  existing scan/data metrics. Scrape the daemon at `:9108/metrics`.

Alembic preserves logging configuration; Telegram token URLs are redacted and
HTTP transport info logging is suppressed. Never commit credentials, raw logs,
DB files or private incident snapshots. Audit is evidence, not a notification
retry queue; delivery retries and freshness-based readiness are priority follow-ups.

## Alternative deployment

Docker Compose defines PostgreSQL and copilot services with persistent PostgreSQL
storage and healthchecks. Configure credentials and `DATABASE_URL` explicitly;
review port exposure and default example passwords before using it on a server.
Do not start Compose on this desk alongside launchd.

```bash
docker compose up -d --build
docker compose logs -f copilot
docker compose exec copilot copilot db current
```

For systemd or another supervisor, run the same `copilot daemon` command with one
process, an explicit working directory/environment, persistent DB storage and restart
policy. Review data/Telegram freshness in addition to process health.
