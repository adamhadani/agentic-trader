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

Quote `"off"` in YAML. YAML 1.1 reads a bare `off` as `false`; the loader maps `false` back to `"off"`
and rejects `on`/`true`.

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
