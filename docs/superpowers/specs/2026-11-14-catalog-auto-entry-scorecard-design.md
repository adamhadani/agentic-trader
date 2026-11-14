# Catalog auto-entry on paper and a weekly lane scorecard

## Why

The operator's 2026-11-14 stock-take found two things.
- The operator does not use Telegram to tap cards: 0 taps since auto-entry shipped.
- Every PEAD-probe and insider-watch card has expired untapped: 20 cards since 10-20, and 1 realized probe
  trade in a month.

The desk was built around a human tap, and the operator's workflow has none. So the two catalog lanes produce
no realized record. The PEAD probe, whose purpose is to accumulate a forward record toward its kill and renewal
rules, is starved. The operator also cannot see return on the work: `cards outcomes` exists, but nobody reads it.

The operator approved both changes ("approved all, proceed"):
1. **Auto-enter catalog cards on the paper account:** the PEAD probe and insider watch, under their existing
   caps.
2. **A weekly Telegram scorecard per lane:** native, PEAD probe and insider watch. For each: activity, the
   realized record, the counterfactual record and the comparison with SPY.

This creates no edge, and the docs and the scorecard say so. It turns the frozen catalog rules into a measured
paper book, and it gives the operator a weekly view of whether anything is working.

## Goals

### A. Catalog auto-entry (paper only)

- **Config.** `execution.auto_entry_catalog: tuple[Literal["pead", "insider_watch"], ...]`.
  - The code default is `()`; the shipped config is `["pead", "insider_watch"]`.
  - It is valid only with `execution.auto_entry: paper`. `load_config` refuses a non-empty value otherwise,
    and it inherits the existing live-account refusal.
- **Marker.** When `auto_entry_active` is true, a catalog card from an enabled source on the scheduled
  suggestion scan gets the auto-entry marker. `auto_entry_marker` gains a per-source rule. For an enabled
  catalog source:
  - **no LLM approval is required:** catalog cards carry commentary only, and their evidence is the frozen
    rule. A commentary failure does not block.
  - **the lifetime comes from `alpha_policy.lifetime`,** through the existing one-resolver
    `execution/lifetime_policy.py`, instead of the native lifetime marker.
- **Unchanged.**
  - Registry alpha cards, operator/swing/intraday scans and futures stay excluded, and native eligibility is
    unchanged.
  - The sweep, attempt, abandonment (1,800 s), re-card and digest paths are reused. They select by marker
    only.
  - The tap gates and admission are unchanged. `probe_block_reason` still gates PEAD, including the sticky
    −4R kill, and the frozen-contract check still gates watch.
  - `entry_trigger: auto` labels the entries.
- **Card text.** The watch card's "tap-only, never auto-entered" line becomes "auto-entered on paper;
  unvalidated; earns no promotion credit" when its source is enabled, and stays as today when it is not. The
  existing auto-entry line renders on catalog cards.
- **Measurement.** `summarize_watch` splits `taps` into `taps` (`entry_trigger` tap) and `auto_entries`.
  `summarize_probe`'s forward record is unchanged: it never read the trigger.

### B. Non-native capacity (operator decision O3, 2026-10-30)

- **Config.** `portfolio.max_non_native_positions: int | None`.
  - The code default is `None` (off). The shipped config is `5`, so natives keep at least 3 of the 8 slots.
  - Validation: 1 ≤ value ≤ `max_concurrent_positions`.
- **Non-native** means a signal with a catalog or watch strategy (`pead_long`, `insider_watch_long`), a
  registry probe, or an `alpha_version`. The predicate sits in one place, beside `_release_exit_scope`, and a
  test keeps it equal to `_release_exit_scope != native`.
- **At admission.** Under the alpha lock, a non-native entry is refused with
  `non_native_capacity` when the scope's open non-native positions plus in-flight non-native submissions reach
  the cap. The refusal is a business refusal like the per-source caps: no halt.
- **At scan time.** Catalog sources treat the combined open count as a ceiling, so they never card when full.
  A crowded-out event stays in its source's decision journal, so its counterfactual label is kept.

### C. Weekly lane scorecard

- **Module.** `research/setups/scorecard.py`. The `cards outcomes` CLI blocks (native labelling, the PEAD
  probe block, the insider-watch block) move into reusable functions there. The CLI calls them, with no
  duplication and unchanged output.
- **Model.** `LaneScorecard`, with one entry per lane and the window bounds.
  - **Activity, last 7 New York days:** cards sent, entries split into auto and tap, fills, closes, and the sum
    of realized R.
  - **Realized record, trailing 90 days:** closed trades, mean realized R (`realized_pnl / risk_dollars`) and
    open positions.
  - **Counterfactual record, trailing 90 days:** matured labels and their mean R_cost, plus beta-matched SPY
    excess where the lane has it (native, watch). PEAD shows "n/a" for SPY.
  - **Account:** adjusted equity at the window's start and end, from ledger checkpoints.
- **Delivery.** A daemon job ticks every 15 minutes and publishes once per ISO week.
  - **When:** due Saturday 09:00 ET. If the daemon was down, it publishes on the next tick up to 4 days after
    the Saturday due, never twice in a week.
  - **How it runs:** in a worker thread, paced like `CardStatsWorker`, skipped while a scan runs.
  - **Output:** one `NotificationKind.MESSAGE` with key `weekly-scorecard/{iso_year}-W{iso_week}` and a journal
    event.
  - **Failures** log the type name and retry on the next tick, and never raise into the scheduler.
  - Quiet hours do not apply, because it is not an incident notice.
- **Text.**
  - Type names and fixed text only.
  - It states plainly that counterfactual labels assume study-price fills and that no lane has validated edge.
  - It never prints secrets, chat IDs or raw errors.
- **CLI.** `copilot cards scorecard [--days 7]` prints the same text without sending anything.

## Non-goals

- No change to the native eligibility rule, the LLM gate, `card_policy`, the release exit's scope (catalog
  positions stay `exempt`, which matches their studies), the frozen catalog contracts, or their risk caps
  ($100).
- No live-account auto-entry of any kind.
- No new outbox kind, migration (head 008) or compatibility alias.

## Constraints

- POST/PATCH/DELETE are never retried. The entry path is the existing `execute_signal_by_id`.
- The scorecard's bar reads are paced, in a thread, outside scan windows and on a weekend. SIP is read through
  `DelayedBars`, as `cards outcomes` does today.
- Tests use offline fakes, plus disposable PostgreSQL (`test_*`) for the admission count and the
  ledger-checkpoint read.

## Docs

- `CLAUDE.md`: contract 1 (sweep sentence, paper probes, insider watch cards) and contract 8 (scorecard).
- `docs/paper-auto-entry.md`: eligibility, out-of-scope, measurement.
- `docs/production.md`: auto-entry section, a scorecard runbook.
- `docs/apriori-alphas.md`: PEAD probe "never automatically" and watch cards.
- `docs/card-evidence.md`: watch taps vs auto entries, and the scorecard.
- `docs/pead-base-rate-2026-10-30.md:98`.
- O3 status in `docs/alpha-roadmap.md` and `docs/alpha-directions-2026-10-30/README.md`.
- Config comments.

## Rollback

All config only, each needing a controlled restart:
- `execution.auto_entry_catalog: []`;
- `portfolio.max_non_native_positions: null`;
- disabling the scorecard job (`scorecard.enabled: false`, code default `true`).
