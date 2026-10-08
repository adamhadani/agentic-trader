# Honest cards (desk-direction item 1, PR A) — design

**Date:** 2026-10-08. **Status:** operator approved the scope on 2026-10-08 ("go"), with card
validity defaulting to today's behaviour. **Context:** [desk direction](../../alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8),
the [funnel diagnostic](~/agentic-trader-research/card-funnel-diagnostic-20261008/report.md)
(private), and the code survey in `.superpowers/honest-cards-survey.md` (untracked). PR B
(book-aware sizing) is a separate spec.

## Goal

Make every native suggestion card truthful about its own evidence, and give the operator a
configured switch to stop sending setups whose measured expected value is negative. Nothing here
claims or creates edge; it measures, displays and, only when the operator turns it on, withholds.

## What the diagnostic established

56 journaled candidates since 2026-09-24 (all trend-pullback longs), 30 mature: 23% target / 77%
stop at exactly 2.0:1 (break-even 33%), mean −0.39R after cost; SPY +0.11R over the same windows;
pure time exits also negative, so the bracket is not the cause; `setup_quality` ranks like random;
9 of 10 sent cards expired untapped (valid until 16:00 ET, about 81 minutes for 14:35 cards); the
card text says "TRADE SIGNAL" and shows no hit rate, EV, sample size or the code's own "not
validated alpha" caveat.

## Non-goals

- No change to strategies, screeners, `setup_quality` or the ranking key. L1 keeps measuring them.
- No change to the LLM prompt (prompt changes need LangSmith trace verification; out of scope).
- No new schema or migration (schema head stays `008_alpha_pipeline`); new journal kinds only.
- No change to the risk rules, admission, FIFO or the shadow optimiser. Shadow weights never reach
  orders. Book-aware sizing is PR B.
- No automatic enabling: the send policy ships `mode: off`; the operator flips it to `preview`,
  reads the evidence, then `enforce`.

## Decisions

### D1. Card statistics snapshot (daily, journaled, per scope)

A new daemon worker, `card_stats`, computes once per New York trading day a snapshot of measured
outcomes per `(strategy, direction)` from the existing outcomes labeller and persists it as one
idempotent journal event.

- **Source.** `label_journaled` over `scan_candidates_ranked` events of the last
  `card_policy.stats_window_days` ET days (default 90), with the labeller's protocol defaults
  (`DEFAULT_MAX_HOLD_SESSIONS = 20`, `DEFAULT_COST_BPS_PER_SIDE = 5.0`, SPY market proxy) and
  the configured market-data feed. The CLI helpers `_scan_ranked_events` and `build_bar_source`
  move from `cli/commands/cards.py` into a shared module `agentic_trader/research/setups/sources.py`
  (the CLI imports from there; no CLI code is imported by the daemon).
- **Statistics per key.** For each `(strategy, direction)` and for the aggregate key
  `("*", "*")`: `n_mature`, `n_immature`, `n_fetch_failed`, `target_rate`, `stop_rate`,
  `timeout_rate`, `mean_r_cost` (the measured EV at the journaled bracket, cost included),
  `mean_timeout_r` (mean `r_cost` of timeouts; `None` when none), `median_holding_sessions`,
  `first_decided_at`, `last_decided_at`. Rates are over mature rows only; `fetch_failed` rows are
  excluded from every rate and counted separately. All numbers are finite or `None`
  (`finite_or_none`; the journal encoder rejects NaN).
- **Provenance in the snapshot.** `computed_at`, `window_start`, `window_end` (ET dates),
  `feed`, `cost_bps_per_side`, `max_hold_sessions`, `labeller_protocol` (`setup-outcomes-v1`),
  `code_revision`, `events_considered`, `rows_labelled`.
- **Persistence.** `WorkflowStore.append(stream="card_stats", kind=EventKind.CARD_STATS_SNAPSHOT
  ("card_stats_snapshot"), key=f"card_stats/{et_date}", payload=snapshot)` under the workflow lock in
  its own transaction. The key makes the write idempotent; scope (`{environment}/{execution_mode}`)
  separates simulator and Alpaca statistics. No migration (kind is a string column). Retention
  compacts only health observations, so snapshots are retained.
- **Scheduling.** The polling-worker pattern (`_poll_research_worker`): every
  `card_policy.stats_poll_seconds` (default 300) the worker checks whether today's key exists and
  whether `now_et >= card_policy.stats_time_et` (default `"08:30"`, a weekday-only check is not
  needed: a non-trading day simply produces a snapshot identical in content to the previous one).
  If due and missing, it runs `label_journaled` in `asyncio.to_thread`, then appends the event. The
  worker catches up after host sleep and restarts. It never runs between
  `scheduler.suggestion_scan_times_et − 5 min` and `+ 20 min` (the scan bursts share the provider).
- **Readiness.** New `HealthComponent.CARD_STATS = "card_stats"`, observed `True` on every
  successful poll (including idle waits) and `False` with detail on failure; `limits` entry with
  `card_policy.stats_max_age_seconds` (default 4 days, covering weekends and holidays). A
  labelling failure (provider error) writes no snapshot and observes `False`; the previous snapshot
  stays authoritative until it ages out (D3).
- **Read side.** `CardStatsRepository` (in `agentic_trader/execution/card_evidence.py`) with
  `latest(session) -> CardStatsSnapshot | None` (newest event on stream `card_stats`) and a pure
  `lookup(snapshot, strategy, direction, *, now, max_age) -> CardEvidence`. `TradingCopilot`
  receives it as an injected constructor kwarg (like `earnings_drift`); tests inject a fake with
  a fixed snapshot. `run_scan` reads the snapshot once per scan, never recomputes.

### D2. Card evidence block and wording

`CardEvidence` (frozen pydantic) is what a card displays: `status` (`"measured" | "insufficient"
| "stale" | "unavailable"`), `strategy`, `direction`, `n_mature`, `min_mature`, `target_rate`,
`stop_rate`, `timeout_rate`, `mean_r_cost`, `mean_timeout_r`, `window_start`, `window_end`,
`feed`, `cost_bps_per_side`, `snapshot_key`, `computed_at`.

- **Transport.** New defaulted kwarg `card_evidence: dict | None = None` on `format_alert_card`,
  `format_terminal_card` and `TelegramNotifier.send_signal_alert`; stored in the notification
  dict and in `decision_provenance["card_evidence"]`; copied by `_replacement_card`; passed by
  the dry-run print. Queued notifications without the key still deliver (default `None`).
- **Header.** `🚨 TRADE SIGNAL:` becomes `📋 SETUP:` in both renderers. Tests that order on the
  header substring are updated to the new header; probe/drift `endswith(plain)` invariants are
  preserved because the block sits inside the plain card.
- **Block position.** Inside the plain card, immediately after the target line and before the
  thesis, in both renderers:
  - measured: `• <b>Measured record</b> ({strategy}, {direction}): {n} mature cards since
    {window_start}: {target%} target / {stop%} stop / {timeout%} timeout, mean {mean_r_cost:+.2f}R
    after cost` and `• <b>Implied EV at {rr:.1f}:1:</b> {ev:+.2f}R` where
    `ev = target_rate·rr − stop_rate + timeout_rate·mean_timeout_r` (`mean_timeout_r` treated as 0
    when `None`; `rr` is the card's own `risk_reward_ratio`). The implied figure is labelled
    "implied" because the card's ratio can differ from the journaled bracket; the directly
    measured quantity is `mean_r_cost`.
  - insufficient: `• <b>Measured record:</b> insufficient evidence ({n}/{min} mature cards)`.
  - stale: `• <b>Measured record:</b> statistics stale (last computed {date})`.
  - unavailable (no snapshot, dry run): `• <b>Measured record:</b> no statistics in this scope`.
  - always, last line of the block, italic: `Not validated alpha. Record measured on journaled
    candidates' deterministic brackets at the next hourly open ({feed}, {cost} bp/side).`
- **Macro line honesty.** In the LLM path `macro_clearance` is the LLM's own JSON value today.
  A candidate that reaches the LLM has passed the deterministic lockout gate, so the evaluator
  sets `macro_clearance = True` as a hard invariant after the LLM response (the LLM's value is
  not displayed). No prompt change.
- **Terminal and Telegram render the same facts**; HTML is escaped; numbers are formatted from
  the same helper so the two never disagree.

### D3. Configured send policy (default off, preview first)

New top-level validated block `card_policy: CardPolicyConfig` (`model_config = {"extra":
"forbid"}`), added to `AppConfig` and to the explicit `AppConfig(...)` call in `load_config`
(a load round-trip test guards the wiring):

| Field | Default | Meaning |
| --- | --- | --- |
| `mode` | `"off"` | `off`: no lookup for policy (evidence block still rendered); `preview`: compute and journal `would_withhold`, still send; `enforce`: withhold |
| `min_measured_ev` | `None` | threshold on `mean_r_cost` (R after cost); required when `mode != "off"` |
| `min_mature_cards` | `20` | minimum `n_mature` for the key before the policy may withhold |
| `stats_window_days` | `90` | labeller window |
| `stats_time_et` | `"08:30"` | daily due time (HH:MM, validated like scan times) |
| `stats_poll_seconds` | `300` | worker poll |
| `stats_max_age_seconds` | `345600` | evidence older than this is `stale` (readiness and card) |
| `validity` | `"session_close"` | see D4 |

- **Where.** In `run_scan`'s send loop, before the LLM call and beside the budget refusals, for
  native candidates only (drift/catalog cards are outside the native budget and keep their own
  contracts). The evaluation is pure: `decide(policy, evidence) -> CardPolicyDecision(withhold:
  bool, would_withhold: bool, reason: str | None, measured_ev, n_mature)`.
- **Rule.** `would_withhold = evidence.status == "measured" and evidence.n_mature >=
  min_mature_cards and evidence.mean_r_cost < min_measured_ev`. `withhold = would_withhold and
  mode == "enforce"`. Insufficient, stale or unavailable evidence never withholds: the policy is a
  switch on measured evidence, not a fail-closed risk rule, and this is stated in the docs.
- **Outcome.** New `RankedOutcome.CARD_POLICY_WITHHELD = "card_policy_withheld"`. A withheld
  candidate spends no scan, session or LLM budget, is appended to `runners_up` with reason
  `"card policy: measured EV {ev:+.2f}R over {n} < {threshold:+.2f}R"`, and the loop falls
  through to the next rank (like a veto). Preview records `would_withhold` and sends normally.
- **Journal.** `scan_candidates_ranked` gains top-level `card_policy: {mode, min_measured_ev,
  min_mature_cards, snapshot_key}` and per-candidate `card_policy: {measured_ev, n_mature,
  would_withhold}` (`None` fields when evidence is not measured). `decision_provenance` of a sent
  card gains `card_policy` (the same per-candidate block) and `card_evidence` (D2).
- **Labeller.** `_row` adds `card_policy_withheld = outcome == RankedOutcome.CARD_POLICY_WITHHELD`
  and `would_withhold` (from the journal, `None` when absent); `summarize` adds
  `card_policy: {withheld: n, would_withhold: n, withheld_mean_r_cost, would_withhold_mean_r_cost}`.
  Withheld candidates stay in the journal and keep being labelled, so a strategy below threshold
  keeps accruing evidence and can recover. `summarize`'s existing blocks are unchanged.
- **Digest.** `publish_scan_digest` already lists runner-up reasons; the withheld reason appears
  there without change. Preview adds one structured log line per would-withhold.
- **Contract.** CLAUDE.md contract 8 is amended: the `cards outcomes` decompositions stay
  descriptive; the one gate derived from measured outcomes is `card_policy`, operator-configured,
  default off, preview first, withholding only on measured evidence with a sample floor, and
  never touching risk, admission or the broker.

### D4. Card validity (optional, default unchanged)

`card_policy.validity: "session_close" | "next_session_close"`, default `session_close` (today's
rule). `next_session_close` applies only to native, non-policy-locked equity cards (no
`alpha_version`/`alpha_policy`, no `paper_probe`, no `pead_event`); every other card keeps the
current rule, and the docs say so.

- **Helper.** `next_regular_close_after(calendar, now_utc) -> datetime | None` in
  `market/session.py`: walks `get_calendar_range` forward (up to 10 days), skips non-trading days,
  uses each day's `close_time` (early closes included), returns the first close strictly after
  `now`'s current-session close (that is, the next trading day's close). `None` when the calendar
  is unavailable; `_card_valid_until` then falls back to today's rule (conservative).
- **Tap while closed.** `assess_card` gains a branch before the EXPIRED check: if the session is
  closed and `now < valid_until`, the result is a new retryable `WAITING` assessment (card stays
  PENDING) with the reply `⏳ Market closed; card valid until {valid_until}. Next regular open
  {next_open UTC}.`. The existing EXPIRED rule applies when `valid_until` has passed or when
  validity is `session_close`. Tests pinning the old behaviour are updated for the explicit
  `validity` argument; the default path keeps the old results.
- **Day-2 taps** are never fresh (`fresh_seconds`), so they go through REPRICE or MISSED at the
  current price against the same stop/target and the required ratio. The re-pricing gate is
  unchanged. Admission's `signal_max_age_seconds` is satisfied by the replacement's new timestamp.
- **Live-card guard.** `run_scan` skips a native candidate whose `(contract, strategy)` has a live
  PENDING card (`live_signal_id`), with outcome `"live card pending"` (free text), so
  `next_session_close` cannot produce duplicate live cards after the 12-hour dedup window.
- **Rendering.** `Valid until:` shows `HH:MM NY` when the close is on the issue date and
  `Www DD HH:MM NY` otherwise. Substring tests on `16:00 NY` still pass.
- **Budget.** Cards carried into the next day are PENDING signals from a previous session; they
  do not count in `signals_since(session_start)`. This is documented as an accepted limit; the
  live-card guard bounds it to one card per `(contract, strategy)`.

### D5. Documentation

- New `docs/card-evidence.md` owns the contract: snapshot schema, evidence block wording, policy
  rule and outcome, validity option, limits (proxy bracket, censoring bias, coverage gap: only
  scheduled suggestion scans are journaled; swing/intraday/`/scan` cards show evidence and obey
  the policy but are not labelled).
- `docs/production.md`: card wording, `card_stats` worker and readiness, `card_policy`
  operation (off → preview → enforce), WAITING tap reply, validity option.
- `docs/risk-policy.md`: the tap-assessment table gains the WAITING row and the explicit
  statement that `card_policy` is not a risk rule.
- `docs/alpha-roadmap.md`: item 1 PR A status. CLAUDE.md: contract 8 amendment (one sentence),
  running-system bullet unchanged.

## Interfaces

- `agentic_trader/research/setups/sources.py`: `scan_ranked_events(db, days, *, now) -> list[dict]`,
  `build_bar_source(config) -> BarSource`.
- `agentic_trader/research/setups/card_stats.py`: `compute_card_stats(frame, *, window_start,
  window_end, feed, cost_bps, max_hold_sessions, code_revision, now) -> dict` (pure, from a
  labelled frame), `CardStatsSnapshot` (pydantic, `model_validate` from the payload).
- `agentic_trader/execution/card_evidence.py`: `CardEvidence`, `CardStatsRepository`,
  `lookup(...)`, `format_evidence_lines(evidence, rr, *, html: bool) -> list[str]`.
- `agentic_trader/execution/card_policy.py`: `CardPolicyDecision`, `decide(policy, evidence)`.
- `agentic_trader/config.py`: `CardPolicyConfig`, `AppConfig.card_policy`, `load_config` wiring.
- `agentic_trader/execution/durable.py`: `RankedOutcome.CARD_POLICY_WITHHELD`,
  `EventKind.CARD_STATS_SNAPSHOT`.
- `agentic_trader/diagnostics/readiness.py`: `HealthComponent.CARD_STATS`.
- `cli/commands/service.py`: `run_card_stats_worker(copilot, config, readiness)` registered like
  the daily-panel worker.
- `notifier/telegram_bot.py`: `card_evidence` kwarg on the three functions; header change;
  valid-until date rendering.
- `agent/copilot.py`: snapshot read once per scan; policy check before the LLM; provenance and
  journal keys; `_card_valid_until` honours `validity`; live-card guard; `_replacement_card` copies
  `card_evidence`.
- `execution/freshness.py`: `assess_card(..., validity)` WAITING branch.
- `market/session.py`: `next_regular_close_after`.

## Testing

TDD per task; integration-style regression each task; adversarial cases:

- Stats: pure `compute_card_stats` on a synthetic labelled frame (per-key rates, aggregate,
  fetch-failed exclusion, timeouts' partial R, NaN-free output, empty frame → empty keys);
  worker: due/not-due, idempotent key, off-loop labelling (`to_thread` spy), provider failure →
  no snapshot and readiness `False`, scan-window avoidance, readiness age.
- Evidence: `lookup` statuses (measured, insufficient, stale, unavailable); rendering in both
  renderers with identical numbers; old notification payload without the key delivers; the
  replacement card carries the block; dry run shows "no statistics in this scope"; probe and drift
  `endswith` invariants hold; header is `SETUP`.
- Policy: `decide` truth table; `run_scan` with an injected snapshot: `off` sends; `preview` sends
  and journals `would_withhold`; `enforce` withholds rank 1, falls through to rank 2, spends no LLM
  budget, outcome fixed, provenance and journal keys present; insufficient/stale never withholds;
  config validation (`enforce` without threshold rejected; unknown key rejected; `load_config`
  round trip from YAML).
- Labeller: `card_policy_withheld` column and `summarize.card_policy`; the journal → labeller
  round trip extended.
- Validity: helper on a calendar with a holiday and an early close; default `session_close`
  keeps every existing assessment result; `next_session_close`: WAITING while closed and before
  `valid_until`, EXPIRED after; day-2 tap re-prices; live-card guard; policy-locked cards keep
  today's rule; rendering with a date.
- Regression: `tests/agent`, `tests/notifier`, `tests/execution`, `tests/research/setups`,
  `tests/cli`, then the full suite and `pre-commit run --all-files`.

## Acceptance

- A production card renders the measured record, implied EV, sample size and the caveat, or an
  explicit "insufficient evidence" line; the terminal copy matches.
- With `card_policy.mode: off` (default) the only behaviour changes are the card text, the macro
  invariant and the new worker/readiness component; `preview` journals `would_withhold`;
  `enforce` withholds and journals the fixed outcome; nothing else changes.
- `cards outcomes` counts withheld candidates and keeps labelling them.
- `validity: session_close` (default) preserves every existing tap result.
- Full suite and pre-commit clean; docs and CLAUDE.md reflect the final state; the v4 results doc
  and roadmap direction (already on this branch) ship in the same PR.

## Amendments (2026-10-08)

- D3 "Digest": the digest is unchanged. It lists top runners-up by quality without reasons, so the
  withheld reason is visible in `runners_up`, in `/scan SYMBOL` replies and in the journal, not in
  the digest.
- D2 "Measured line": it reads `• <b>Measured record</b> ({strategy}, {direction}): {n} mature
  candidates since {first_decided_at:%Y-%m-%d}: …` — candidates, not cards (the count includes
  runners-up), dated from the key's first journaled decision (New York date), falling back to
  `window_start` when the key has none. The insufficient line likewise says `mature candidates`.
- D3 "Policy-locked cards": a native candidate with `alpha_version`, `alpha_policy` or `probe` gets
  no card-policy decision (never withheld or `would_withhold`; its `card_policy` journal and
  provenance block has every field null) and still renders its evidence block. `probe_block_reason`
  stays the single probe liveness rule.
- D3 "Partial snapshots": `CardEvidence` and the per-candidate `card_policy` block carry the key's
  `n_fetch_failed`; `decide` never withholds (`would_withhold` false) when it is above zero. A run
  with some fetch failures is recorded and blocks a retry until the next New York date.
