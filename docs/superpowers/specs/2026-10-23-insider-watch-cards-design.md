# Insider watch cards: discretionary suggestions from the insider cluster rule

Status: design, 2026-10-23. Operator request (2026-10-23), after the `insider` v1 result: "i feel like
should follow up on the insider trading signal though given it was so promising, e.g. maybe at least
have it surface card suggestions to me similar to our momentum strategies / mean reversal ones so i can
exercise discretion". This builds on PR #131, which holds the `insider` v1 entry, `insider_events.py`
and the results.

## 1. What this is, and is not

The frozen v1 leg **failed** P1: +0.087R per trade, ci90 [−0.008, +0.189]. It **passed** P2: +0.070R
over same-session matched controls, ci90 [+0.009, +0.134].

Under the catalog contract (CLAUDE.md contract 1, "Paper probes") only a passing leg becomes a
probe, so the leg is **not** enrolled, earns no registry, shadow or promotion credit, and is not
auto-entered.

What the operator asked for is narrower: show the cluster events as cards, and let the operator decide.
This adds a third card kind, the **watch card**, beside native cards and catalog drift cards:
- Its source is the frozen v1 event rule, applied live. Nothing is re-tuned; v1 is a rule the
  operator wants to see.
- It is labelled as unvalidated, with the study's numbers on the card.
- It is tap-only. It is never auto-entered, so item 1.4 / D9 excludes it.
- It is budgeted separately from native cards.
- It goes through every entry-risk rule, and its own per-trade risk cap is set below the native
  budget.
- It is journaled and labelled forward, so the live record becomes the prospective second look the
  results doc calls for (route (a)). That record is evidence only; it cannot pass anything.

## 2. Live data: a nightly SEC Form 4 capture

**Source.** For each filing date F, the work is:
- read `https://www.sec.gov/Archives/edgar/daily-index/form.YYYYMMDD.idx`;
- keep the `4` rows whose CIK is a cohort-v3 issuer (`symbol_ciks` over the cohort; about 1,000 CIKs);
- fetch each filing's primary document `.txt` (tens per day);
- parse the `ownershipDocument` XML.

All of it uses the operator's `EDGAR_USER_AGENT` (already exported to the daemon through `.envrc`)
and the research acquisition's pacing (`REQUESTS_PER_SECOND = 2`). It is never sent anywhere else and
never logged.

**Parsing (parity with the data sets).** A row qualifies exactly as `insider_filings.read_transactions`
qualifies one:
- `documentType == 4`;
- the first reporting owner whose relationship includes officer or director;
- a non-derivative transaction with code `P`, acquired;
- positive shares and price;
- `transactionDate ≤ F`;
- the 10b5-1 flag is parsed, and kept as v1 keeps it.

The filing date comes from the index, never from `acceptanceDateTime`. The output is
`InsiderTransaction` records, so the frozen event builder runs on them unchanged.

**Parity test (required before live use).** Parse the live documents for a past quarter already in
the cached data sets (2026q3) and compare qualifying rows.
- The match must be exact on (symbol, filing date, transaction date, code, dollars ±$0.01, owner CIK)
  for every cohort issuer in the sample.
- The test runs on fixtures checked into `tests/fixtures/edgar/`: about 20 real filings plus their
  data-set rows, fetched once by the controller.
- A full-quarter live comparison is a one-off research check recorded in the results doc.

**Worker.** `run_insider_capture_worker` follows the corporate-actions worker
(`data/corporate_actions_worker.py`).
- Schedule: once per New York date after 06:00 ET, skipping the scan windows (`in_scan_window`), with
  a retry every 30 minutes until it succeeds.
- Catch-up: each run captures every filing date since the last captured one. The first run backfills
  from 2026-10-01, the day after the cached quarterly sets end.
- Storage: one journal event `insider/form4/<F>` per filing date, holding the parsed qualifying
  transactions, the counts and the index digest. Raw documents go to private artifacts under
  `data/insider/<F>/` through `write_private`, and stay out of Git. No migration.
- Readiness: component `insider_capture`, ready while the latest captured filing date is within 4 days
  (the corporate-actions limit).
- Failure: a failed fetch records type names only and degrades readiness. It never touches scans or
  entries.

**History seed.** The 10:35 decision needs:
- 13 months of purchases for the routine rule;
- 21 sessions for the cluster window;
- 63 sessions for the cooldown.

It loads them once per New York date by merging the cached quarterly archives (the pinned 2015q1–2026q3
set, `~/agentic-trader-research/insider-filings-cache`, path in config) with the journaled live
captures. Where both cover a filing date, the archive wins. The plausibility filter (10× the 20-session
median raw dollar volume) and the identity breaks are applied as the study applies them.

## 3. The card source

**Seam first (review SG-R5).** PEAD is special-cased in about 12 places in `copilot.py`:
- budgets and caps;
- the `pead_event` key;
- `drift_card_facts`;
- `DriftPreparation`;
- `_journal_drift`.

Task 1 introduces a `CatalogCardSource` protocol and moves PEAD onto it with **no behaviour change**,
proven by a parity test: the same candidates, provenance, card payload, journal event and budget
decisions on the existing PEAD fixtures. The protocol members:
- `source_id`;
- `prepare(now, owned)`;
- `candidates(prepared, data)`;
- `card_facts(candidate)`;
- `provenance_key`;
- `journal(decision)`;
- `budget`, `max_open`;
- `kind`, either `probe` or `watch`.

The insider watch source is then a second implementation.

**Decision at the 10:35 suggestion scan only.** The steps:
1. Run `build_insider_events` on the seeded history with the window narrowed to today's session (D =
   today), the way `pead_live` narrows the PEAD study.
2. Apply eligibility as of D−1 on SIP daily bars, the study's own rule ($10 minimum price, $5M
   20-session median dollar volume, ATR14). Names the scan does not trade get synthetic contracts for
   that scan, as drift-only names do.
3. Rank by buyers (descending), then by opportunistic dollars over dollar volume (descending), then by
   symbol.

   This is display order only. The v1 diagnostics showed 3+ buyers at +0.165R against +0.066R for 2,
   and that is descriptive, not validated.
4. Send at most `insider_watch.max_cards_per_session` (2). Send none while
   `insider_watch.max_open_positions` (4) watch positions are open.
5. Journal every decision as one `insider_watch_decision` event (`EventKind.INSIDER_WATCH_DECISION`) on
   `scan/<date>`, with `status` `completed`, `unavailable` or `skipped` and a reason, every event
   (sent or not) and its eligibility reason. A missing capture for the previous filing date gives
   `unavailable`, with a type-name reason, and no card.

**Card construction.** It uses the study's bracket from the pooled protocol's cube spec:
- a 10:35 decision;
- a 2×ATR14 stop and a 3R target;
- a 20-session time exit, through `AprioriBracketPolicy` with `SessionLifetimePolicy` (the same lifetime
  machinery as PEAD, and session-bounded resting).

`alpha_version` is `None` and `probe` is false. `alpha_policy` holds the frozen policy, which makes the
card policy-locked (no re-pricing, `session_close` validity) and exempt from `card_policy`, book sizing
and the broad-index rule.

The LLM writes commentary only, never a gate, as for catalog cards. News context is attached as for
native cards.

Risk:
- every `risk/` entry rule applies;
- per-trade risk is capped at `insider_watch.max_risk_per_trade_usd` (100, PEAD's probe cap);
- the trade counts against `max_concurrent_positions` and aggregate stop risk like any position.

**Admission.** A new `_watch_signal_rejection` in `storage/workflow.py` refuses a tap unless:
- the signal's `alpha_policy` equals the source's frozen policy byte for byte;
- `decision_provenance["insider_watch"]` names a same-session event for the same contract.

This closes the gap where a non-catalog strategy id with no `alpha_version` would get no contract
check (`workflow.py:479-480`).

**Card text.** The header is "👀 INSIDER WATCH — unvalidated". The body names the cluster:
- the buyers, with roles where parsed;
- the total opportunistic dollars and the filing dates;
- the study line: "Study v1 (2016–2026): +0.087R/trade, not significant on its own; +0.070R vs matched
  names (90% CI excludes 0). Your call."

Then the standard levels, risk and buttons.

## 4. Attribution

`cards outcomes` gains an `insider_watch` block from the journaled decisions. Every sent and every
eligible-but-unsent event is labelled with the frozen bracket on hourly bars (`label_bracket`, as the
PEAD forward record does), with:
- the count;
- the mean `r_cost`;
- the beta-matched excess (the item 1.1 yardstick);
- taps and fills.

It is descriptive only.

## 5. Config (validated, through `load_config`)

```yaml
insider_watch:
  enabled: true             # code default false
  max_cards_per_session: 2
  max_open_positions: 4
  max_risk_per_trade_usd: 100
  archive_dir: ~/agentic-trader-research/insider-filings-cache
  backfill_from: 2026-10-01
```

## 6. Out of scope

- Auto-entry.
- Any change to the frozen v1 entry or rule.
- Promotion, registry or probe enrolment.
- A sell-side or market-neutral variant (route (b) needs its own spec).
- Refreshing the quarterly archive (a later `alpha insider-filings` run).

## 7. Testing

Synthetic fixtures only, plus the checked-in EDGAR fixtures.
- **Parser parity** against the data-set rows.
- **Worker:**
  - once per date;
  - catch-up across several dates;
  - backfill;
  - scan-window skip;
  - failure handling with type names only;
  - readiness ages.
- **Seed merge:** archive wins on overlap; plausibility and identity rules apply.
- **PEAD parity through the seam.**
- **Watch source:**
  - narrowed window;
  - eligibility reasons;
  - ranking;
  - budget and the open cap;
  - `unavailable` without a capture;
  - the journal event.
- **Admission:** policy equality, a same-session event, refusals.
- **Card payload and rendering:** every payload key is a `send_signal_alert` keyword.
- **Outcomes block.**
- Full suite, PostgreSQL integration, and pre-commit.

## 8. Rollout

1. Ship with `enabled: true`.
2. On deploy, the worker backfills from 2026-10-01.
3. The first watch cards can appear at the next 10:35 scan after a cluster.
4. At roughly 0.4 events per session, expect 1–3 cards a week.
5. The operator reviews the `insider_watch` block monthly.
