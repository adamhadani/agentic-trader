# A priori alpha catalog

An a priori alpha is a literature-documented anomaly tested as **one frozen hypothesis
per leg**, pooled over many names, before it may produce any card. It sidesteps the
mining funnel's multiple-testing problem (thousands of candidate formulas, few trades per
symbol) by spending one confirmatory test per leg.

Rules:

- Each entry is a JSON file under `config/research/apriori/`, committed before any data
  is read. Its SHA-256 goes into every run manifest. A changed file is a new version
  with a new, non-overlapping window — never an edit.
- Studies are research only: no registry, trial, shadow or promotion credit; no database,
  broker or Telegram access.
- A leg that passes becomes eligible for a live, capped paper probe, which needs its own
  spec (Part 2). A failed leg stays off.

Run: `copilot alpha apriori-study config/research/apriori/<entry>.json --output NEW_DIR [--cache DIR]` — the command reads the entry's `id` (`pead`, `exdiv`) to choose the study.

| Entry | Version | Hypothesis | Long | Short | Result |
| --- | --- | --- | --- | --- | --- |
| `pead` | 1 | Post-earnings drift when EPS surprise and price reaction agree | — | — | Failed closed on calendar coverage (Nasdaq gap 2016-06-06..07-08); no outcomes computed |
| `pead` | 2 | Same, decisions from 2016-08-01 | **eligible** (+0.149R; +0.055R vs control) | failed (−0.064R) | [result](apriori-pead-2026-09-26.md), [spec](superpowers/specs/2026-09-25-apriori-pead-study-design.md) |
| `exdiv` | 1 | Regular quarterly payers run up in the five sessions before the ex-date (Hartzmark & Solomon 2013); long from E−5 at 10:35 to the last cum-dividend close; same-name mid-quarter placebo | failed (+0.069R, ci90 [+0.035, +0.102]; but the same-name placebo earns +0.046R and the paired difference +0.025R has ci90 [−0.026, +0.072], so P2 fails) | — | failed on P2, 2026-10-12 ([result](apriori-exdiv-2026-10-12.md), [spec](superpowers/specs/2026-10-12-exdiv-leg-design.md)) |

## Entry 2: pre-ex-dividend run-up

`exdiv` version 1 (`config/research/apriori/exdiv-v1.json`, [spec](superpowers/specs/2026-10-12-exdiv-leg-design.md))
is desk item 3, part 2. Research only, one frozen hypothesis, long leg only; the entry's SHA-256 goes
into the manifest and a change is a new version with a new window.

- **Event source.** Part 1's cached [corporate-actions acquisition](corporate-actions.md) on the frozen
  spread cohort (`config/research/spread/cohort-v1.json`, resolved against the repository root). The
  entry pins the cohort by content (`cohort_sha256`; a cohort file with another digest fails the run
  before any provider call) and the process-date window (`actions_window`). The manifest records the
  cohort's path, digest and symbols; `result.json` records the acquisition's request and content
  digests. A failed or empty fetch fails the run.
- **Event rule.** A non-special cash dividend whose previous non-special ex-date lies 77–105
  calendar days earlier (quarterly regularity), with no split, merger, spin-off, name change or
  other non-dividend action for the symbol within 10 calendar days of the decision-to-ex span, no
  other ex-date of the symbol from the session before the decision through E−1, and passing PEAD's
  liquidity gate on raw bars as of the session before the decision. The decision is at 10:35 New
  York on E−5 (five sessions before the ex-date E). Dividends outside the calendar or the decision
  window are counted as such before any eligibility reason; a second dividend on the same ex-date
  is counted once (`duplicate_ex_date`).
- **The hold is the lead.** The position is long from the fill after the decision and exits at the
  last cum-dividend close (the close of session E−1), or at the stop (2 ATR) or target (3R) first.
  No dividend is received and no ex-date drop is crossed. A leg whose hourly bars miss any session
  from the decision through E−1 is excluded and counted (`missing_session_bars`), as is any exit on
  or after the ex-date.
- **Control.** The same symbol, same bracket and hold, decided 32 sessions later (the middle of a
  quarter), with the same gates; dropped when an ex-date of the symbol falls within one session of
  its span. P2 pairs each leg with its control by `pair_id`; a leg without a control stays in
  P1, P3 and P4 and is counted as unpaired.
- **Secondary hold.** A 10-session hold crosses the ex-date; it is descriptive only.
- **Costs and pass rule.** 0, 5 and 10 bp per side; 5 bp decides. The same P1–P4 criteria and
  stationary session-block bootstrap as PEAD, via the shared `research/apriori/statistics.py`
  (300 events, 100 from 2023-01-01). A pass makes the leg `eligible_for_probe`; a probe needs its
  own spec.
- **Caveats.** The cohort is current-member (survivorship-biased, not point-in-time). The study
  knows the ex-date five sessions ahead from history, while live knowledge depends on the
  declaration: the 2026-10-12 capture held 17 future ex-dates up to 60 days ahead, and a probe must
  measure its own lead-time coverage before claiming parity. Slippage is not measured.

**Result (2026-10-12):** the leg is positive (+0.069R per event at 5 bp, 2,647 events, P1, P3 and P4 hold)
but not distinguishable from the same name's mid-quarter drift (control +0.046R; paired difference +0.025R,
ci90 [−0.026, +0.072]), so P2 fails and the leg is **failed**; no probe. The yield gradient (low +0.038R →
high +0.117R) is the one descriptive the placebo cannot explain and would motivate a yield-conditioned v2
declared as a second look ([result](apriori-exdiv-2026-10-12.md)).

## Pooled literature entries

Two further single-hypothesis entries run on the [pooled mining lane](alpha-pooled-mining.md)
(`config/research/pooled/`, one formula across a frozen 353-symbol cohort, session-paired
edge, PEAD's P1-P4 bar). They were tested with `copilot alpha pooled study` after
[power check A](alpha-pooled-power-2026-09-30.md) passed (detection 0.98 at +0.15R,
false acceptance 0.00), not with `alpha apriori-study`.

| Entry | Version | Hypothesis | Status |
| --- | --- | --- | --- |
| `high52` | 1 | Names nearest their 52-week high keep outperforming (George & Hwang, 2004) | failed (2026-09-30): +0.162R per trade, paired edge +0.012R (ci90 [-0.011, +0.036]); fails P2 and P3; [result](alpha-pooled-literature-2026-09-30.md) |
| `reversal-lowmax` | 1 | One-month losers outperform among low-MAX names (Jegadeesh, 1990; Bali, Cakici & Whitelaw, 2011) | failed (2026-09-30): +0.153R per trade, paired edge +0.008R (ci90 [-0.016, +0.032]); fails P2 and P3; [result](alpha-pooled-literature-2026-09-30.md) |

Pooled campaign v4 tests a related variant of the reversal hypothesis as decile baskets
(the one-week reversal alone and after high volume) and leaves nearness-to-high out after a
label-blind probe showed the 126-session high overlapping `high52` at pick Jaccard 0.76; a
v4 finalist whose picks still overlap either entry is not probe-eligible; see
[fixed-set mode](alpha-pooled-mining.md#fixed-set-mode-campaign-v4).

## Part 2: live paper probe

A passing leg runs only as a catalog paper probe — never automatically, never active
or shadow, and never combined with the DSL registry's promotion path. The probe
is `pead_long`, the study's LONG leg; the failed SHORT leg cannot be enrolled
(`load_catalog_probe` rejects any leg outside `LIVE_LEGS = ("LONG",)`).

- **Policy kind.** The bracket is the study's own trade block —
  `stop_atr_multiple`×ATR14 stop, `target_r`×R target, no structural anchor, no
  trailing (`AprioriBracketPolicy`, `agentic_trader/research/alpha/strategy.py`) —
  with a `session_count_v1` time exit (see
  [trade lifetimes](alpha-trade-lifetimes.md#session_count_v1)): the fill session
  counts as session 1, and the position closes at 15:45 New York (or 15 minutes
  before an early close) on session 20 unless the stop or target fills first.
- **Live event source.** Every scheduled scan whose time equals the entry's
  `decision_time_et` (10:35 by default) calls `EarningsDriftService`, which
  recomputes the narrow LONG leg for that one session with the study's own
  `pead_events.build_events` — the identical liquidity gate, reaction z and ATR
  the study used, never a reimplementation. A missing Nasdaq calendar page, an
  empty page, a missing SPY D-1/D+1 close or a bar-fetch failure fails the whole
  session closed (`status: "unavailable"`), never partially. The whole
  preparation (calendar, bars and event construction) is bounded at
  `DRIFT_PREPARE_TIMEOUT_SECONDS` (60 s, `agent/copilot.py`); exceeding it is
  also `"unavailable"` (`reason: "timeout"`) — the reads run under the scan lock
  before the native fetch, so a slow provider must never hold up native cards.
- **Lateness cap.** The frozen study entered at 10:35 prices, so a 10:35 scan
  that reaches its PEAD decision point more than
  `apriori.max_drift_lateness_seconds` (default 900, must be positive) after the
  slot was due prepares no drift candidates and reads no calendar or bars. The
  lateness is measured at that decision point — after any wait for the scan lock
  (e.g. behind a swing scan) and the bounded dynamic-universe read — not when the
  job fires, so both a late wake of a sleeping host and a long lock wait count.
  It journals one `pead_decision` with `status: "skipped"` and
  `reason: "late decision: N min after 10:35 NY"` (logged as
  `pead_decision_late`), without a notice; native cards from the same scan are
  unaffected. The bound applies only to the scheduled job, which passes the
  slot's due instant (`run_scan(scheduled_at=...)`). A 10:35 slot that the
  scheduler drops entirely (past `scheduler.suggestion_scan_misfire_grace_seconds`)
  journals nothing but queues one "suggestion scan was missed" notice (see
  [suggestion scans](production.md#suggestion-scans)).
- **Budget.** At most one drift card per session, outside the native suggestion
  budget (`apriori.max_drift_cards_per_session`, default 1); the per-scan/
  per-session native budgets are unaffected. A drift card is never counted as a
  dynamic-universe name (the earnings calendar chose it, not the screener), so
  it neither counts toward nor is capped by the dynamic group. Configured sector
  correlation groups still apply and still cap it exactly like a native card
  (`card_groups(contract, drift=True)` returns `correlation_groups_of(contract)`).
  Ties on multiple qualifying events break by the largest reaction z, then
  symbol.
- **Open-position cap.** Each PEAD position holds for up to 20 sessions, so the
  probe could otherwise fill `portfolio.max_concurrent_positions` and block native
  cards at tap. `apriori.max_open_drift_positions` (default 4) caps it: when the
  scan's executed `pead_long` positions reach the cap, the 10:35 scan still
  prepares and journals the session's events (outcome `"drift position cap
  reached"`, `status` stays `"ok"`, payload `open_drift_positions`) but sends no
  drift card and reads no drift-only bars.
- **LLM commentary only.** The evaluator's LLM call still runs, but its verdict is
  stored as `llm_verdict` in the signal's evaluation and never gates the card; the
  thesis is prefixed "LLM commentary (not a gate): …". The bracket, quantity and
  time exit come entirely from the frozen policy.
- **Fail-closed behaviour.** Admission fails closed for the catalog strategy IDs
  (`pead_long`, `pead_short`) when a signal carries no `alpha_version` — the same
  rule as the DSL lane's `alpha_` prefix. The holding-time exit requires the
  strict broker calendar (`AlpacaCalendarProvider`, no fallback); a calendar
  failure is a lifecycle `REVIEW` that halts new risk, never a guessed date. If
  the drift service itself fails to construct while a catalog probe is enrolled,
  the 10:35 scan journals `status: "unavailable"` with a debounced Telegram
  notice; native cards are unaffected either way. A 10:35 scan that returns early
  because trading is halted or a macro lockout is active still journals one
  `pead_decision` with `status: "skipped"` and the reason (`"trading halted: …"`,
  `"macro lockout: <title>"`), without a notice. A drift event whose intraday
  price is missing, stale (the last bar is not from the decision session) or
  failed to fetch is journaled as `"no intraday price"`, `"stale intraday price"`
  or `"fetch failed: <Type>"`. The daemon logs `pead_decision_time_unscheduled`
  at startup when the entry's decision time is not a configured suggestion scan
  time.
- **Enrolment.** A passing leg is enrolled (or renewed) explicitly by the
  operator — never automatically:

  ```
  copilot alpha apriori-probe config/research/apriori/pead-v2.json \
      --study ~/agentic-trader-research/apriori-pead-v2-20260926 \
      --leg long --generation N
  ```

  This validates the study directory against the exact entry file (SHA-256,
  entry ID/version, `status: "completed"`, a passing decision for `--leg`) before
  building `pead_long`'s identity: `apriori:pead:v2:long:<first 16 hex of the
  entry file SHA-256>`. It is risk-capped
  (`alpha_pipeline.probe_risk_dollars` per trade), expiring
  (`alpha_pipeline.probe_term_days`, renewable with `--renew` only while
  unkilled), and earns no shadow, holdout or promotion credit. `probe_block_reason`
  (`storage/probe_state.py`) is the single liveness rule: paper scope, current
  enrolment/policy, an unexpired term and a live forward record (kill at −4R
  cumulative from first enrolment). Demote with `copilot alpha demote
  VERSION_ID --generation N`. Run `copilot alpha apriori-probe --help` for the
  exact CLI options.

  **Renewal (operator decision, 2026-10-21).** The PEAD probe is renewed for 180
  days at unchanged risk, judged on the counterfactual label over every eligible
  event (`probe_outcomes`, below), not on taps. Renewal is a separate operator
  action, never a deploy step: the command above with `--renew --days 180` and
  the registry generation observed at the time (`--days` accepts up to
  `MAX_PROBE_TERM_DAYS = 180`, `agentic_trader/research/alpha/probe.py`). It extends
  a current, unkilled probe from now. Done 2026-10-21 14:46 ET:
  `apriori:pead:v2:long:fc8a5b3ec14845a2`, registry generation 17 → 18, expires
  2027-04-19, the $100 per-trade cap unchanged.
- **Measurement.** `copilot cards outcomes` reports the probe alongside the
  native scan-outcome report: every journaled `pead_decision` event in the
  window (`agentic_trader/research/apriori/probe_outcomes.py`), including events
  skipped because the name was already owned (outcome `"skipped: <reason>"`,
  counted as events but never as carded), how many became a card
  (`outcome == "sent"`), how many were executed (a broker fill; no tap or fill
  rate is computed) and closed, the closed
  trades' realized mean R, and — from the study's own `label_events` — the
  counterfactual mean `r_cost` over every mature LONG-leg event the scan saw,
  carded or not. `report_date`/`session` round-trip to `date` and `decision_at`
  to an aware `datetime`; each `pead_decision` also records the preparation's
  wall time (`elapsed_seconds`); a bar-fetch failure for one symbol is recorded in
  `bar_failures` and never aborts the rest of the report. Realized and
  counterfactual results are not comparable until the probe has at least 10
  closed trades. `/alphas` shows the probe's forward record
  (`AlphaRepository.probe_report()`, `kind == "apriori"`) between scans. The
  renewal decision above reads the counterfactual mean over every eligible
  event, because carded and tapped events are a selected subset.
- **Known limits.** The counterfactual label assumes a fill at the study's own
  decision price (the last regular-session hourly close at or before 10:35), not
  an actual broker fill — real slippage/timing is not measured. The live cohort
  is whatever the Nasdaq calendar reports each session (no point-in-time
  survivorship control, unlike the study's static universe). The event source
  depends entirely on Nasdaq's earnings calendar; an outage there fails the
  session closed rather than falling back to another source.
