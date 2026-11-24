# Filing veto and release exit: enforce prep

## Why

The filing veto and the release exit shipped in `preview` on 2026-11-07. Their preview week (11-09 → 11-14)
gave the following.

**Filing veto.**
- 13 of 13 capture passes ran, with 130 of 130 mapped names covered and 0 failures.
- It would-blocked 1 of 31 ranked candidates: CART, an Item 2.05 8-K, in a journal-only swing scan. No sent card
  was affected.
- The capture held 32 offering prospectuses (all 424B5): 5 identifiably debt, 0 equity, 0 convertible and 27
  unclear by document name. At any time 3–4 megacaps (META, ORCL, GOOGL, BKNG, QCOM) sit inside the 7-day
  offering window, most likely on routine debt takedowns.

**Release exit.**
- 4 of 4 daily passes ran: 11 native `none` and 3 `exempt`.
- 0 `would_close`, 0 `unknown`, 0 failures.
- During the Nasdaq 403 on 11-10 and 11-11 it decided from the capture alone.

**What is safe to enforce.** The NT-filing and adverse-8-K categories carry the evidence the veto rests on, and
can be enforced. The `offering` category would refuse cards on large issuers' bond issues; the drift evidence
behind it is for equity and convertible offerings, and the document name cannot tell those apart. So `offering`
stays journal-only until a prospectus can be read.

**What the operator would miss.** Under `enforce` the operator relies on the release exit. Its silent gaps (no
pass, `unknown` positions, an owed close found after the notice bound) appear only in the journal and the log,
and should reach the morning operations summary.

The docs' "Before enforce" list (`docs/production.md`, Filings capture and Release exit) maps to this spec as
follows:

| Item | Topic | Handled by |
| --- | --- | --- |
| 1 | Dynamic-universe coverage | Deferred (see Non-goals) |
| 2 | Offering by document | Part A |
| 3 | Catalog exemption | Part B |
| 4 | Silent gaps | Part C |

## Goals

- **A. Enforce by category.** `filing_veto.enforce_categories` chooses which categories refuse under
  `enforce`; the others are journaled and labelled only.
- **B. Exempt non-native cards.** These are the cards the release exit already exempts: catalog (`pead`,
  `insider_watch`), paper probe and alpha-versioned cards. They are exempt from the filing veto at both gates,
  and their evidence is still recorded.
- **C. Report gaps in the morning.** The morning operations summary reports the previous trading day's
  release-exit gaps.
- **D. Ship config and docs.**
  - `filing_veto.entry_block: enforce` with `enforce_categories: [late_filing, adverse_8k]`.
  - `release_exit.mode: enforce`.
  - The docs updated, plus a short preview-week result doc.

## Non-goals

- **Dynamic-universe coverage (item 1).** Uncovered names are never refused, so the gap is missing protection,
  not a false refusal. It stays documented as a v1 gap.
- **Reading prospectus bodies** to classify offerings.
- **Changing** the windows, the capture, admission, the release exit's decision rule or its close path.

## Part A: enforce by category

- **Config.** `FilingVetoConfig.enforce_categories: tuple[FilingCategory, ...]`.
  - The code default is all three categories, so an existing `enforce` config keeps today's meaning.
  - Validation: non-empty, distinct values, and each must be a `FilingCategory` value. A YAML list of strings
    loads.
  - `enforced_windows()` returns `windows()` restricted to those categories.
  - Shipped config: `enforce_categories: ["late_filing", "adverse_8k"]`.
- **Risk.** `risk/rules.py` `blocking_filing` skips a filing whose category has no entry in `windows`, which
  today raises `KeyError`. A missing category never blocks. `risk` still imports no data or config module.
- **Evidence.** `agent/filing_veto.py` `filing_veto_evidence` computes two things:
  - **The label:** the newest qualifying filing over all windows. It fills `would_block`, `category`, `form`,
    `items`, `filing_date`, `accession` and `reason`, all as today.
  - **The refusal:** `filing_veto_block` over `enforced_windows()`. It is the returned `Rejection`, and its
    reason names the enforced filing.

  The block gains `would_refuse: bool`. It is true iff that refusal exists and the card is not exempt (Part B).

  A 3-day-old offering therefore never masks a 20-day-old NT filing: the label shows the offering, and the
  refusal and its reason name the NT filing. `journal_filings_block` adds `would_refuse` (null unless the status
  is ok).
- **Gates.**
  - The evaluator (gate 2d) refuses iff mode is `enforce` and the returned rejection is not None, as today, with
    the rejection now restricted to the enforced categories.
  - The tap gate `_filing_veto_reason` does the same.
  - Preview still never reads filings at tap time.
- **Card line** (`notifier/telegram_bot.py` `_filing_veto_line`). Under `preview` it is unchanged. Under
  `enforce`, a sent card whose block has `would_block` true and `would_refuse` false (a preview-only category,
  or an exempt card) shows `🗂 Filing (not enforced): {detail} — card unchanged`, where `detail` is the label
  reason without its lead or period.
- **Outcomes.** `cards outcomes` `_filing_veto_group` adds a `would_refuse` count beside `would_block`, and
  `by_category` is unchanged. Under `enforce`, refused natives leave the scan journal, so `would_block` there
  counts only matches that did not refuse. Refusals count in `funnel.rejected_by_rule.filing_veto`. The docs say
  so.

## Part B: exempt non-native cards

- **Evaluator.** Gate 2d still computes the block for a candidate with `catalog_event is not None`, `probe`, or
  an `alpha_version`. It adds `exempt: true` and never refuses that candidate. The scope must match
  `TradingCopilot._release_exit_scope` on the resulting signal row; a test pins that.
- **Tap gate.** `_filing_veto_reason` returns None for a signal whose `_release_exit_scope` is not
  `native`. It is checked before any filings read, and it applies in every mode.
- **Why:** an enforce refusal would change a frozen probe's entry population and override the operator's
  discretionary watch cards. The release exit already exempts the same set.

## Part C: release-exit gaps in the morning summary

The external supervisor's morning summary (`OperationsStore.queue_quiet_summary`, run by
`copilot doctor --monitor`) gains an optional `release_exit` section for the trading day that ends the quiet
period. That day is `period_start`'s New York date, used only when `is_alert_day` is true and `release_exit.mode`
is not `off`. Each gap kind is listed:

1. **Missing pass:** no `release-exit/<date>` event. This covers a broker calendar down through the close, a host
   asleep or a daemon down through the window, and a pass that never completed.
2. **Unknown positions:** native positions with status `unknown` in that pass, by symbol and signal ID.
3. **Owed closes never notified:** `release_exit_close` events for that date with `notified: false`. These are a
   failed, refused or erroring close found after the notice bound. Each entry gives its symbol, signal ID and
   `outcome`.

**Model and delivery.**
- `QuietHoursSummary` gains `release_exit: ReleaseExitGaps | None = None`, so already-queued payloads still
  validate.
- The summary is queued when there are tallies, open incidents or any release-exit gap.
- A reader in `storage/` uses only stream, kind and key strings, so the supervisor imports no
  broker, trading or earnings module.
- `monitor_once` passes `config.release_exit.mode` through `OperationsMonitor`.
- `presentation/operations.py` renders one short block per gap kind, with type names and fixed text only.
- The summary's existing once-per-window decision and journal event are unchanged.

## Part D: config, docs and the result doc

**`config/config.yaml`:**
- `filing_veto.entry_block: "enforce"`, with a comment in the corporate_actions style naming the preview dates.
- `enforce_categories: ["late_filing", "adverse_8k"]`.
- `release_exit.mode: "enforce"`.

`tests/config/test_shipped_config.py` pins the new shipped values.

**Docs:**
- **`docs/production.md`:**
  - Filings capture: the "Before enforce" list becomes a record of what shipped. Items 2–4 are done; item 1
    stays as the open v1 gap.
  - Correct the watch-card sentence: catalog cards do carry a `filings` block and are exempt.
  - Release exit "Failure" and "Notice bound": the morning summary now reports the gaps.
- **`docs/risk-policy.md`:** the `filing_veto` row and the tap-order text cover categories and exemption.
- **`docs/card-evidence.md`:** the filing veto now counts `would_refuse` and catalog exemption, and the
  release exit names the morning summary.
- **`docs/operational-monitoring.md`:** the summary paragraph.
- **`CLAUDE.md`:** the contract lines on filing_veto, release_exit and quiet hours.
- **`docs/alpha-roadmap.md`:** the "Both ship in preview" line.
- **Comments:** `config.py` and the evaluator comment.

**Result doc:** `docs/filing-veto-release-exit-preview-2026-11-14.md` gives the preview week's evidence, using
the numbers above and the 11-13 data added before merge, and records the decision.

## Constraints

- No migration (head `008`) and no new outbox kind; the summary keeps `OPERATIONS_SUMMARY`.
- POST/PATCH/DELETE are never retried.
- The release exit's close path and request IDs are unchanged.
- Refusal texts are unchanged; the refusal reason names the enforced filing.
- No compatibility aliases.
- The summary and the card line carry type names and fixed text only.
- **Tests:** offline fakes; disposable PostgreSQL (`test_*`) only if a storage query changes shape.

## Rollback

All three are config-only:
- `filing_veto.entry_block: preview`;
- `release_exit.mode: preview`;
- or `enforce_categories` including `offering`.

Each needs a controlled restart.
