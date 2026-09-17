# Forward candidate evidence

`uv run copilot alpha forward --days 7 --limit 10000` reads the existing alpha
journal projections. Telegram `/alphas` uses the same application query and pure
report builder for a compact seven-day summary. It shows enabled/candidate counts,
aggregate recorded evaluations and missing/pending/coverage warnings; expressions,
version hashes and per-symbol timing stay in the full CLI report. Both are diagnostic, read-only surfaces:
no provider reads, orders, notifications, qualification or shadow credit are created.
The CLI's usual database initialization checks schema readiness.

“Shadow” means a registered candidate without permission to place orders. Registration
alone does not prove a worker is collecting observations for it. “Forward diagnostics”
checks newly arriving data and scores; these checks are not historical backtests,
realized profits or qualifying shadow credit. The current session cohort is diagnostic
only. Zero enabled alphas does not disable other configured strategies.

## Interpret the report

- Scope is **current registry session versions**, per eligible symbol. Historical
  native-bar shadows contribute to the candidate count in `/alphas` but are outside this session report.
- The inclusive time window uses candle close time. Each immutable decision is
  counted once by its current canonical outcome: scored, unavailable, missed,
  superseded, interrupted or claimed. Latest/pending aliases and late forensic
  results never enter the denominator. An expired claimed record is also shown as
  overdue pending; reporting does not mutate or recover it.
- `recorded_score_fraction` divides scored decisions by **recorded decisions**.
  It is null with no records or truncated history. This is not coverage of every
  theoretically eligible candle. No records means no recorded evidence, not a
  successful observation or proof that no window was due.
- Enrollment, cursor generation, configured-symbol/worker status and the current
  retained cursor gap are visible. `cursor_checked_at` advances on scheduling
  transitions, not every idle poll; use `/readyz` for current worker freshness.
  A retained gap is a warning, not a complete historical outage inventory. Older
  gaps and retired versions remain in the journal for forensic investigation.
- Only canonical **scored** decisions contribute score distributions and long/flat/
  short direction counts. Computed forecasts retained in failed/interrupted work
  do not count as successful decisions. Directions are not orders or fills, and
  the report contains no strategy or broker P&L.
- Receipt lag is `received_at - closed_at`; read duration is
  `received_at - requested_at`. Only stored datasets with ordered receipt timestamps
  contribute. Provider failures with only a completion timestamp are not measured
  receipts. Missing observations are counted rather than replaced with zero.
  Lag includes intentional delay and polling: it is an upper bound on when this
  worker observed the data, **not actual provider publication latency**.
- Unavailable reasons use bounded categories. Raw provider error text, paths,
  account details and credentials are not rendered.

## Bounds and consistency

Days are bounded to 1–31; row limits to 1–50,000. The default is 10,000. One extra
SQL row detects truncation, and counts become explicitly labeled lower bounds.
Distributions in a truncated report describe only the loaded subset.
The query reads latest canonical projections ordered by their last journal event,
using event time as a lower bound before exact candle/cohort filtering. Rows for
other cohorts and outside the candle window are counted separately; they may use
part of the limit. Increase the limit within bounds or narrow the period to inspect
more history. Truncation never silently becomes a healthy coverage percentage.

The registry is checked again after reading; a generation change fails the report
and asks for a fresh request. Outcomes are one SQL statement's snapshot. Cursor
metadata is a subsequent read and may reflect ongoing scheduler progress. This is
an operational view of current outcomes, not historical as-of reconstruction.
JSON decoding and statistics run off the asyncio loop. Readiness keeps its existing
lightweight query; it does not scan decision history.

## Validation and next use

Regression tests cover every decision status, receipt gaps/order, truncation,
unknown enrollment/gaps, registry races, event-loop responsiveness and bounded CLI inputs. SQLite/PostgreSQL
integration verifies no business writes, alias/late-result exclusion and identical
reports after journal replay. Real SDK HTTP acquisition is exercised through the
worker into this report. Telegram uses the existing generic chunked reply helper.
These checks establish software behavior, not elapsed live forward evidence.

Review the fixed six-control cohort after actual exchange sessions. Preserve missing
and failed decisions. Execution/qualification gates remain unchanged. The next
[roadmap item](alpha-roadmap.md#next-work-after-the-etf-session-campaign) is a shared,
versioned resting-order/holding-lifetime contract with broker cancellation recovery.
