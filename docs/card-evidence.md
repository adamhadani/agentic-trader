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
