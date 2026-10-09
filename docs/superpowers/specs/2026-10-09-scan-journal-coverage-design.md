# Scan journal coverage (PR A.1) — design

**Date:** 2026-10-09. **Follows:** `2026-10-08-honest-cards-design.md` (PR #110).

## Problem

`scan_candidates_ranked` — the denominator for the cards' evidence block (`card_stats`),
`copilot cards outcomes` and the 2026-10-08 funnel diagnostic — is written only when
`run_scan` was asked for shadow evidence, which only the scheduled suggestion-scan job does.
The daemon's interval `swing_scan` job (every `scheduler.cron_hour_interval` hours and once
at every daemon start via `next_run_time=now`) and an unrestricted operator `/scan` run the
same full-universe, FULL-budget scan, send cards, and journal nothing. Deployed evidence on
2026-10-08: 18 of the 22 cards sent since 2026-09-24 are absent from the ranked journal
(post-L1 misses #33, #37, #39 are all off-slot swing-scan cards). The measured record a card
shows therefore describes mostly unsent runners-up from two scans a day, not the cards the
operator actually received.

## Decision

1. **Journal every full-universe scan that can send cards.** The ranked-candidate journal is
   written whenever `not dry_run and budget != ScanBudget.NONE and native_count and not
   symbols and timeframe is None`, independent of `shadow_evidence`. The shadow ranker block
   stays gated by `shadow_evidence` exactly as today (suggestion scan only); other scans journal
   `"shadow": None` per candidate. Symbol- or timeframe-scoped scans (intraday job, `/scan
   SYMBOL`, Re-evaluate) still journal nothing: their population is not a ranking of the
   universe and the labeller's reader keeps its `scope == "universe"` guard.
2. **Name the trigger.** New `ScanTrigger(StrEnum)` in `agentic_trader/execution/durable.py`:
   `SUGGESTION_SCAN = "suggestion_scan"`, `SWING_SCAN = "swing_scan"`,
   `OPERATOR_SCAN = "operator_scan"`. `run_scan` gains `trigger: ScanTrigger =
   ScanTrigger.OPERATOR_SCAN`; the journal payload's `"trigger"` is `trigger.value`
   (today's hard-coded `"suggestion_scan"` becomes this). `make_suggestion_scan` passes
   `SUGGESTION_SCAN`; the `swing_scan` job passes `SWING_SCAN`; `copilot scan`, Telegram
   `/scan` and every other caller keep the default. `trigger` never changes ranking, budget,
   sending, shadow computation or the policy.
3. **Startup run stays.** The swing job keeps `next_run_time=now`: the `scan` readiness
   component has no observation in a fresh run until a scan completes (`max_age` 18000 s), so
   removing the startup run would degrade readiness and raise an incident at every restart.
   Its cards are now journaled like any other.
4. **Outcomes report the trigger.** `research/setups/outcomes.py` adds a `trigger` column
   (the payload's `"trigger"`, `None` for pre-A.1 events) after `session`; `summarize` adds
   `"triggers": {trigger: {"scans": n, "sent": n}}` so the operator can see how many cards
   each path produced. `compute_card_stats` and the card policy are unchanged: they key by
   `(strategy, direction)` over every journaled candidate regardless of trigger.
5. **Docs.** `docs/card-evidence.md` coverage statements, `docs/production.md` (shadow gating
   paragraph, "what is recorded", swing-scan sentence), the `run_scan` docstring and the comment
   above the shadow block, CLAUDE.md contract 8 (one clause: every full-universe scan journals
   its ranking; shadow evidence remains suggestion-scan only), `docs/alpha-roadmap.md` item 1
   status.

## Non-goals

No change to card budgets, the LLM gate, the shadow ranker, `card_stats` keys, the policy, the
readiness contract, scan scheduling or the digest (`_is_suggestion_scan` keeps its shape test).
No migration (head 008). Historical journal gaps are not back-filled: pre-A.1 sent cards stay
unlabelled and the docs say so.

## Tests

- Swing-shaped call (`run_scan(True, False, budget=FULL, trigger=SWING_SCAN)`) journals one
  event with `trigger == "swing_scan"`, every `shadow` None, candidates/outcomes/signal ids as
  for a suggestion scan; `live_cross_section` is never called.
- Unrestricted operator call (default trigger) journals `trigger == "operator_scan"`.
- Suggestion call journals `trigger == "suggestion_scan"` with shadow blocks (existing tests).
- Restricted scopes (symbols, timeframe) and dry runs journal nothing (existing + new).
- `register`/scheduler test: the `swing_scan` job's kwargs carry `trigger=ScanTrigger.SWING_SCAN`
  and `budget=ScanBudget.FULL`; `make_suggestion_scan` passes `trigger=SUGGESTION_SCAN`.
- Outcomes: a frame built from events with and without `"trigger"` has the column; `summarize`
  reports per-trigger scans/sent counts; `card_stats` over mixed-trigger events counts them all.
