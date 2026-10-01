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

Run: `copilot alpha apriori-study config/research/apriori/<entry>.json --output NEW_DIR [--cache DIR]`.

| Entry | Version | Hypothesis | Long | Short | Result |
| --- | --- | --- | --- | --- | --- |
| `pead` | 1 | Post-earnings drift when EPS surprise and price reaction agree | — | — | Failed closed on calendar coverage (Nasdaq gap 2016-06-06..07-08); no outcomes computed |
| `pead` | 2 | Same, decisions from 2016-08-01 | **eligible** (+0.149R; +0.055R vs control) | failed (−0.064R) | [result](apriori-pead-2026-09-26.md), [spec](superpowers/specs/2026-09-25-apriori-pead-study-design.md) |

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
  (`AlphaRepository.probe_report()`, `kind == "apriori"`) between scans.
- **Known limits.** The counterfactual label assumes a fill at the study's own
  decision price (the last regular-session hourly close at or before 10:35), not
  an actual broker fill — real slippage/timing is not measured. The live cohort
  is whatever the Nasdaq calendar reports each session (no point-in-time
  survivorship control, unlike the study's static universe). The event source
  depends entirely on Nasdaq's earnings calendar; an outage there fails the
  session closed rather than falling back to another source.
