# Card evidence and the card policy

Desk-direction item 1, PR A ([roadmap](alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8),
[design](superpowers/specs/2026-10-08-honest-cards-design.md)). Every native suggestion card states its
own measured record, and one operator-configured switch, `card_policy`, can stop sending setups whose
measured expected value is negative. Nothing here claims or creates edge: it measures, displays and,
only when the operator turns it on, withholds. `card_policy` is not a risk rule
([risk policy](risk-policy.md#what-is-not-a-risk-rule)); it never touches the book, admission, the
FIFO or the broker.

## Configuration

Top-level `card_policy:` (`CardPolicyConfig`; unknown keys fail the load):

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `"off"` | `off`: the policy is not evaluated (the evidence block is still rendered); `preview`: evaluate and journal `would_withhold`, still send; `enforce`: withhold |
| `min_measured_ev` | `null` | Threshold on the measured mean R after cost; required when `mode` is not `off` |
| `min_mature_cards` | `20` | Mature labels a `(strategy, direction)` needs before the policy may withhold; also the card's "insufficient evidence" floor |
| `stats_window_days` | `90` | Labeller window, in New York calendar days |
| `stats_time_et` | `"08:30"` | Daily due time of the statistics worker (HH:MM New York) |
| `stats_poll_seconds` | `300` | Worker poll interval |
| `stats_max_age_seconds` | `345600` | Evidence older than this is `stale` on cards and in `card_stats` readiness (four days covers weekends and holidays) |
| `validity` | `"session_close"` | Card validity; see [Card validity](#card-validity) |

Validation requires `stats_max_age_seconds` to exceed `stats_poll_seconds`.

Quote `"off"` in YAML. YAML 1.1 reads a bare `off` as `false`; the loader maps `false` back to `"off"`
and rejects `on`/`true`.

## Card statistics snapshot

The daemon task `run_card_stats_worker` (readiness component `card_stats`) polls every
`stats_poll_seconds`. When the New York time is at or after `stats_time_et`, outside every
suggestion-scan window (each `scheduler.suggestion_scan_times_et` slot from 5 minutes before to 20
minutes after), today's key `card_stats/{et_date}` is missing, and no scan holds the scan lock
(`TradingCopilot.scan_running`: suggestion, swing, intraday or operator scans, including a late
misfire-grace run), it reads every `scan_candidates_ranked` event of the last `stats_window_days` ET
dates (`research/setups/sources.scan_ranked_events`), labels them in a worker thread with the
outcome labeller's protocol defaults (`label_journaled`: 20 sessions, 5 bp per side, SPY proxy) on
the configured feed, and appends one `card_stats_snapshot` event to stream `card_stats` with key
`card_stats/{et_date}` (idempotent; retention keeps it). The scan checks apply when a run starts; a
scan that begins during a labelling run shares the provider with it. The worker runs every calendar
day without a trading-day check; consecutive non-trading days differ only where the window's oldest
date drops out.

Payload (`CardStatsSnapshot`, `agentic_trader/research/setups/card_stats.py`):

- provenance: `computed_at`, `window_start`, `window_end` (ET dates), `feed`, `cost_bps_per_side`,
  `max_hold_sessions`, `labeller_protocol` (`setup-outcomes-v1`), `code_revision`,
  `events_considered`, `rows_labelled`;
- `keys`: one row per `(strategy, direction)` plus the aggregate `("*", "*")`, each with `n_mature`,
  `n_immature`, `n_fetch_failed`, `target_rate`, `stop_rate`, `timeout_rate`, `mean_r_cost`,
  `mean_timeout_r`, `median_holding_sessions`, `first_decided_at`, `last_decided_at`. Rates are over
  mature rows; fetch failures are counted and excluded from every rate; rows without a strategy or
  direction count only in the aggregate. Every number is finite or null.

Readiness: every successful poll (detail `not_due`, `scan_window`, `present`, `scan_busy` or
`recorded`) and the start of a labelling run (`labelling N scan events`) observe `card_stats` ready;
a failure observes it failed. The detail of `CardStatsUnavailable` is `CardStatsUnavailable: every
bar fetch failed for N candidates: …` (first 200 characters); any other error's detail is its type
name only, because readiness details reach Telegram incident notices, and its message goes to the
log. If every candidate's bar fetch fails, nothing is written (`CardStatsUnavailable`) and
the previous snapshot stays authoritative until it is older than `stats_max_age_seconds`, when cards
say "statistics stale". A failure retries on the next poll. Only shutdown ends the loop: if the
readiness write itself fails (the database is down) the worker logs it and polls again.

Shutdown cancels the worker's coroutine. The snapshot is written only by that coroutine after
labelling returns, so a cancelled run records nothing and the next start recomputes. The labelling
thread itself cannot be interrupted: it finishes its provider reads, and process exit waits for it
(asyncio's default-executor join). The labeller paces its own reads at
`market_data.max_requests_per_minute`, apart from the scan's read pool, which is why it avoids the
scan windows and running scans.

## Evidence block

`TradingCopilot.run_scan` reads the newest `card_stats` snapshot in this scope once per scan
(`CardStatsRepository.latest`) and looks up each native candidate's `(strategy, direction)`
(`lookup`). The result, `CardEvidence`, is stored in the signal's `decision_provenance["card_evidence"]`
and in its outbox notification, so the Telegram card, the terminal card, the dry-run print and a
re-priced replacement render the same facts from one helper (`format_evidence_lines`). PEAD drift
cards keep their own block and carry none. The block sits inside the card, right after the target
line:

| Status | When | Line |
| --- | --- | --- |
| `measured` | snapshot fresh, at least `min_mature_cards` mature labels | `• Measured record (STRATEGY, DIRECTION): N mature cards since WINDOW_START: T% target / S% stop / O% timeout, mean ±X.XXR after cost`, then `• Implied EV at R.R:1: ±Y.YYR` |
| `insufficient` | fewer mature labels, or no row for the key | `• Measured record: insufficient evidence (N/MIN mature cards)` |
| `stale` | snapshot older than `stats_max_age_seconds` | `• Measured record: statistics stale (last computed DATE)` |
| `unavailable` | no snapshot in this scope (every dry scan), a failed read or an unreadable payload | `• Measured record: no statistics in this scope` |

The last line is always, in italics: "Not validated alpha. Record measured on journaled candidates'
deterministic brackets at the next hourly open (FEED, C bp/side)." The parenthesis is omitted when no
snapshot exists.

`mean` is the directly measured quantity: the mean cost-adjusted R of the key's mature labels at the
journaled bracket. The implied EV is `target_rate·rr − stop_rate + timeout_rate·mean_timeout_r` at the
card's own ratio `rr` (no timeouts' R counts as 0); it is labelled implied because the card's ratio can
differ from the journaled bracket.

The card title reads `📋 SETUP:` (it read `🚨 TRADE SIGNAL:`). `Macro Check` states the deterministic
lockout gate: a candidate that reached the LLM already passed it, so the evaluator sets
`macro_clearance` to true after the LLM answers; the LLM's own value is not displayed and the prompt is
unchanged. A notification queued before this change has no `card_evidence` and renders without a
block; a malformed one renders the `unavailable` line and still delivers.

## Send policy

`decide(policy, evidence)` (`agentic_trader/execution/card_policy.py`) is pure:

    would_withhold = mode != "off" and evidence.status == "measured"
                     and n_mature >= min_mature_cards and mean_r_cost < min_measured_ev
    withhold       = would_withhold and mode == "enforce"

It runs in `run_scan`'s send loop for native candidates only, after the card-budget refusals and before
the LLM. A withheld candidate gets the fixed outcome `card_policy_withheld`
(`RankedOutcome.CARD_POLICY_WITHHELD`), spends no scan, session or LLM budget, becomes a runner-up with
reason `card policy: measured EV -0.39R over 30 < +0.00R`, and the next rank is considered, like an LLM
veto. Insufficient, stale or unavailable evidence never withholds: the policy is a switch on measured
evidence, not a fail-closed rule. PEAD drift and catalog cards are outside it. Every scan applies it
(suggestion, swing, intraday, `/scan`, Re-evaluate); only scheduled suggestion scans are labelled.

Evidence recorded:

- `scan_candidates_ranked` gains top-level `card_policy: {mode, min_measured_ev, min_mature_cards,
  snapshot_key}` and, per candidate, `card_policy: {measured_ev, n_mature, would_withhold}`
  (`measured_ev`/`n_mature` null unless the evidence is measured; `would_withhold` null while the mode is
  `off`). `would_withhold` is the policy's verdict on every native candidate, whether or not it reached
  the send step.
- A sent card's `decision_provenance` carries the same `card_policy` block and its `card_evidence`.
- `preview` logs one `card_policy_would_withhold` line for each candidate that reaches the check.
- `copilot cards outcomes` adds the `card_policy_withheld` and `would_withhold` columns and the summary
  block `card_policy: {withheld, would_withhold, withheld_mean_r_cost, would_withhold_mean_r_cost}`
  (counts over every row, means over mature rows). Withheld candidates stay in the journal and keep
  being labelled, so a strategy below the threshold keeps accruing evidence and can recover. The
  existing summary blocks are unchanged; a withheld candidate is still a runner-up there. The
  end-of-session digest counts it among the runners-up; the reason shows in the scan summary and in a
  `/scan SYMBOL` reply.

Operate it in order: `off` until a snapshot exists; `preview` with a threshold, reading `would_withhold`
for several sessions; then `enforce`. Each change is a config edit and the controlled restart.

## Card validity

`card_policy.validity` is `session_close` by default: a card is valid until the close of the session it
was issued in (today's rule). `next_session_close` extends only native, non-policy-locked equity cards
(no `alpha_version`/`alpha_policy`, no paper probe, no PEAD event), and only while
`execution.card_freshness.enabled`: such a card records the next trading day's regular close as
`valid_until` (`market/session.py:next_regular_close_after`, which skips weekends and holidays and keeps
early closes; an unavailable calendar keeps today's close). Every other card keeps today's rule:
admission refuses a policy-locked card's original signal after `execution.signal_max_age_seconds`, and
a drift card needs its same-session event.

- A tap while the session refuses entries and before `valid_until` is `WAITING`: retryable, the card
  stays `PENDING`, the tap is journaled `waiting` (`applied: false`), and the reply is "⏳ Market closed;
  card valid until Www DD HH:MM NY. Next regular open YYYY-MM-DD HH:MM UTC.". After `valid_until` the tap
  is `EXPIRED` as before.
- While `execution.card_freshness.fresh_seconds` is shorter than the overnight gap (about 17.5 h), a
  next-session tap is never fresh, so it re-prices or misses at the current price
  against the same stop and target and the required ratio; the re-pricing gate is unchanged, and the
  replacement's new timestamp satisfies admission's signal age.
- `run_scan` skips a native candidate whose `(contract, strategy)` already has a live `PENDING` or
  `SUBMITTING` card (outcome and runner-up reason `live card pending`), so the 12-hour duplicate window
  cannot produce a second live card the next morning. The guard runs only under `next_session_close`,
  for a candidate whose card it extends, after the card-budget refusals and before the send policy.
- `Valid until:` shows `HH:MM NY` when the close is on the card's issue date (the signal row's
  timestamp) and `Www DD HH:MM NY` otherwise.
- Accepted limit: a carried card is a `PENDING` signal from an earlier session, so it does not count in
  the new session's card budget (`signals_since`); the live-card guard bounds it to one card per
  `(contract, strategy)`. Carried cards are likewise outside the correlation-group caps and the
  one-dynamic-card cap, which derive from the same `signals_since` rows. Measured statistics never include next-day entries: labels enter at the first
  hourly bar after the scan.

- Switching `validity` back to `session_close` while carried cards exist: the sweep keeps them `PENDING`
  until their day-2 close, and the live-card guard turns off, so a day-2 scan may issue a second live
  card for the same setup. An overnight tap now expires instead of `WAITING`. Nothing can be entered
  without tap re-assessment, so this is safe.

## Limits

- **Proxy bracket.** Labels use the journaled deterministic COLLECT bracket (not the LLM-edited bracket
  that was sent), enter at the open of the first regular hourly bar at or after the scan's `decided_at`
  (not the limit price, not the tap), cost a fixed 5 bp per side, and use the configured feed (IEX in
  production, with its hourly gaps). The record is the setup's, not this card's own win rate.
- **Censoring bias.** A label stays immature until the stop or target hits or 20 regular sessions pass,
  so early samples over-represent fast resolutions. The sample floor does not remove this.
- **Coverage.** Only scheduled suggestion scans journal `scan_candidates_ranked`. Swing, intraday,
  `/scan` and Re-evaluate cards show evidence and obey the policy but are never labelled.
- **Scope.** Snapshots are per `{environment}/{execution_mode}`: simulator and Alpaca paper statistics
  never mix, and a dry scan (an empty temporary database) always says "no statistics in this scope".
