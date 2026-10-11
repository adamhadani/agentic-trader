# Spread-reversion lane: pairs v2 (entry rule) — design

Date: 2026-10-11. Parent: [pairs v1 design](2026-10-10-spread-reversion-lane-design.md),
[v1 results](../../alpha-spread-results-2026-10-10.md). Operator direction: "go with your next
recommended items" (standing design trust; merge needs explicit approval).

## 1. Why

Pairs v1 failed discovery, but 84% of its 2,309 entries were opened at |z| ≥ 4, already beyond the
stop, and stopped on the next mark at ~zero gross while paying the full round trip. A construction-only
diagnostic on the discovery panel (archived under `~/agentic-trader-research/spread-v2-design-diag-20261011/`,
no returns read) shows what that is:

- spreads start each tile near zero (median |z| 0.62 on the first trading session; 5% at or above 2);
- σ_f is not biased low in the median (σ_trade/σ_f median 1.11) but has a heavy right tail (q90 2.6);
  the last 126 formation sessions are *calmer* than the whole formation (σ_hold/σ_f median 0.80), so
  a holdout σ would make |z| ≥ 4 **more** frequent (21% of trading sessions vs 14.7%);
- 14.7% of trading sessions sit at |z| ≥ 4, and v1's flat-only trader re-enters a broken spread every
  session after each stop. That churn is the whole defect.

Entry counts over discovery under candidate rules (v1 selection): v1 2,309; upper band only 466;
crossing only 274; crossing + band 268. The fix is in the entry rule. Formation and σ_f stay as in v1.

## 2. The change (one rule, three facets of "do not trade a broken spread")

`trading` in `pairs-v2.json`:

| Field | v1 | v2 |
| --- | --- | --- |
| `z_entry` / `z_exit` / `z_stop` / `fill` | 2.0 / 0.5 / 4.0 / next_open | unchanged |
| `entry` | *(absent → `threshold`)* | `crossing`: the previous session's z is finite and |z_{t−1}| < z_entry, and |z_t| ≥ z_entry |
| `z_entry_max` | *(absent → none)* | 4.0: no entry when |z_t| ≥ z_entry_max (must satisfy z_entry < z_entry_max ≤ z_stop) |
| `reenter_after_stop` | *(absent → true)* | false: a `stop` exit retires the pair for the rest of the window (re-entry after `reverted` stays allowed) |

Everything else in the protocol file is byte-identical to v1 except `version: 2`, the title, the
hypotheses/decision_rule text (which name the entry rule) and `"second_look": true` with a one-sentence
declaration (section 4). Cohort, schedule, formation, costs, bootstrap, pass rules, power and null specs are
unchanged, so the cohort hash pin is the same.

Semantics in `simulate_pair` (step 3, signals at the close, flat only, not the last session):

- `threshold` (v1): as today.
- `crossing`: requires `math.isfinite(z[t-1]) and abs(z[t-1]) < z_entry` in addition to `abs(z[t]) >= z_entry`;
  `t = 0` never enters (no previous session in the window). A missing close on the previous session
  blocks the entry (no signal is inferred across a gap).
- `z_entry_max`: when set, `abs(z[t]) < z_entry_max` is also required.
- `reenter_after_stop = false`: after a trade closes with reason `stop`, the pair emits no further entry
  signals in that window. Exit handling, marks, window-end close-out and costs are unchanged.

The v1 file must load unchanged (`extra="forbid"` with defaulted new fields) and v1 behaviour must be
bit-identical: a regression test runs the existing trading fixtures through both the explicit v1 rule and
the defaulted rule and compares trades and daily series.

## 3. Checks, study, decisions

Unchanged machinery: `alpha spread-power` and `alpha spread-null` must pass for `pairs-v2.json` (its own
protocol hash) at the clean merged revision before `alpha spread-study` will run; the study reads discovery,
and only a pass journals and reads the one-use confirmation window (`spread/confirmation`). The power world
is unchanged (planted OU spreads oscillate, so crossing entries occur naturally); if check A fails on v2 the
lane stops there and that is the result.

## 4. Second look, declared

v2 is the second protocol evaluated on the same discovery window. The change was chosen from
*construction* statistics of the v1 run (where entries happened, how often |z| ≥ 4 occurs), not from
returns; no return statistic conditional on the new rule was computed before this spec. A v2 discovery pass
is nonetheless weaker evidence than v1's would have been, and the confirmation window (never read) is the
only clean test. The protocol file records `"second_look": true` and this sentence; the results doc repeats it.
No third protocol runs on this discovery window without a new spec and operator approval.

## 5. Scope and non-goals

In scope: `TradingRule` fields, `simulate_pair` entry logic, `config/research/spread/pairs-v2.json`,
tests, docs (`docs/alpha-spread-lane.md` §3/§9, CLAUDE.md spread paragraph, roadmap item 2, this spec and plan).
Out of scope: formation, σ, cohort, schedule, pass rules, checks, journal, CLI, `/pairs`, baskets, any probe.

## 6. Testing

- `TradingRule`: defaults reproduce v1; `entry` literal validated; `z_entry_max` must satisfy
  `z_entry < z_entry_max <= z_stop`; `extra="forbid"` still rejects unknown keys.
- `simulate_pair`: crossing requires the previous finite z inside the band (gap before the signal blocks it;
  `t = 0` never enters); band blocks |z| ≥ `z_entry_max`; a stop retires the pair for the window while a
  reversion does not; v1 defaults are bit-identical on the existing fixtures.
- Frozen files: `pairs-v2.json` loads, pins the same cohort hash, has `version: 2`, `second_look: true`,
  and a different protocol hash from v1; the v1 file's hash is unchanged
  (`88dc4945751bfdbcb45b21dca9ad84f3266239ab0b839c9a01f44986bcaaf352`).
- Full suite and `pre-commit run --all-files` green before each commit.

## 7. Operations after merge

Controlled restart; then from the installed checkout, outside scan windows:
`alpha spread-power pairs-v2.json` → `alpha spread-null pairs-v2.json --cache …pooled-cache-v1/bars` →
`alpha spread-study pairs-v2.json --power … --null-check … --journal-scope production/alpaca:paper`.
Results doc bundles into the next workstream PR.

## 8. Design decisions taken under the standing instruction

1. Entry rule, not σ: the diagnostic falsified the σ hypothesis; a holdout σ would worsen the defect.
2. All three facets in one version rather than three versions: each alone still admits entries into
   broken spreads (band-only 466 entries, most of them in spreads oscillating between 2 and 4 after a break).
3. `z_entry_max = z_stop` (4.0) rather than a tighter band: the stop level already defines "broken"; a
   tighter band would be a tuned parameter.
4. Retirement only after `stop`, not after `reverted`: a reverted spread has behaved as modelled.
5. Defaulted new fields instead of a new model class: v1 stays loadable and testable at the same hash.
6. Declared second look rather than a new discovery window: no other long window exists before the
   confirmation window, and hiding the reuse would be worse than naming it.

Costs if wrong: (2) cannot attribute the improvement to one facet (acceptable: the question is whether
the lane works, not which facet matters); (3) may still enter spreads that then widen past 4 (the stop
handles it); (6) the discovery pass is softer evidence, stated everywhere the result is reported.
