# Setup recurrence test — protocol (frozen before any data is read)

A predeclared, research-only test of the one pattern the 2026-10-14 `cards outcomes`
`recurrence` block surfaced on the live journal: a native setup that recurs within a few
sessions, especially after a first stop-out, resolved far better than a fresh one (45 days,
46 mature rows: first occurrences −0.46R, repeats +0.96R, repeats after a stop +1.52R; all
in-sample, correlated, hypothesis-grade). This test freezes that hypothesis and runs it on
history the live record never touched, the way the [short-suppression test](2026-09-23-short-suppression-test.md)
did for native shorts. Designed 2026-10-14 under the operator's standing trust; decisions are
in §7. Nothing here changes the live desk: a pass only makes a separately specified
construction rule *eligible*.

## 1. Why

- The live block is the hypothesis source, so it cannot be its own evidence. The setup-outcome
  study's replay machinery (`research/setups/runner.py:build_window_frames`) reproduces the
  live scans (10:35 and 14:35 New York, the unchanged strategy engine, deterministic levels, the
  bracket labeller) over years of history, so the same `(symbol, strategy, timeframe,
  direction)` recurrence can be computed there with the same no-lookahead rule.
- The desk-direction definition of a reasonable card requires non-negative measured expected
  value at the measured base rate. The native funnel fails that on first occurrences and may
  pass it on repeats; if the split survives a frozen test, "prefer repeats" is a card-quality
  improvement that needs no new alpha.
- It is cheap: both windows' bars are already cached from the September studies
  (`~/agentic-trader-research/setup-cache-v1`, `setup-cache-2017`), so the run is replay and
  labelling only.

## 2. Scope

**In this PR:**

1. The labelled replay frame gains `entry_time`, `exit_time` and `holding_sessions` (the
   bracket outcome already computes them); the setup-outcome study ignores them.
2. A pure recurrence function over a labelled frame, a frozen protocol model, the R1–R4
   hypotheses with the existing stationary session-block bootstrap, the decision rule, and
   the `alpha setup-recurrence` command that replays the two declared windows from their own
   caches and writes `protocol.json`, `manifest.json`, `result.json` and the labelled frames.
3. The frozen protocol file `config/research/setup-recurrence-v1.json` (SHA-256 identity
   recorded in the manifest), this spec, the plan, and after the run the results doc,
   roadmap status, card-evidence pointer and CLAUDE.md line.

**Out of scope (named so they are not implied):** any change to `run_scan`, ranking,
`card_policy`, `card_stats`, `risk/` or the cards; the construction rule itself (a pass
makes it eligible; its spec, preview mode and approval are a separate PR); the holdout
window of the setup-outcome study (2026-03-02 → 2026-08-14, consumed once by the ranker) and
the live period (2026-09-24 →, the hypothesis source) — neither is read; any model fitting.

## 3. Components

### 3.1 Replay frame columns — `research/setups/runner.py`

`_FRAME_METADATA_COLUMNS` gains `"entry_time"`, `"exit_time"`, `"holding_sessions"`, and
`_build_frame` writes them from the zero-cost outcome (`outcome_zero.entry_time`,
`.exit_time`, `.holding_sessions`; the primary-cost outcome shares the same bars and exits).
`study.model_features` selects feature names from the frozen tuples, so the study's
features, identity and results are unchanged; a test pins that `model_features` of a frame
with the new columns equals that of one without.

### 3.2 Recurrence over a labelled frame — `research/setups/recurrence.py` (new)

```python
BAR = pd.Timedelta("1h")                      # the labeller's bar; a prior counts once its exit bar has closed
STATES = ("first", "repeat", "repeat_open")
PRIOR_OUTCOMES = ("target", "stop", "timeout", "none")

def with_setup_recurrence(frame: pd.DataFrame, *, lookback_sessions: int) -> pd.DataFrame
```

Per row, over the other rows with the same `(symbol, strategy, timeframe, direction)`
whose `session` is strictly earlier and within `lookback_sessions` trading sessions (counted
on the frame's own sorted distinct `session` values, so a holiday gap is not a session):

- `state`: `first` when there is no such prior; `repeat` when the most recent prior's
  bracket had resolved by this decision (`hit` in target/stop/timeout and
  `exit_time + BAR <= decision_at`); `repeat_open` when the most recent prior exists but had
  not resolved (still open, or `immature`). Same-session rows are never priors (both scans
  of a session see the same completed bars).
- `prior_outcome`: the most recent prior's `hit` when `state == "repeat"`, else `"none"`.
- `prior_sessions_k`: the number of distinct earlier sessions within the lookback that hold
  the setup.
- `prior_r_cost`: the most recent resolved prior's `r_cost` (descriptive), else NaN.

Rows with a missing strategy, timeframe or direction are `first`. The frame must have a
unique index (`ValueError` otherwise). Pure; no I/O.

### 3.3 Protocol — `SetupRecurrenceProtocol`

Mirrors `SetupBaseRateProtocol` (frozen, `extra="forbid"`, SHA-256 identity of the sorted
JSON document) with:

| Field | Value in v1 |
| --- | --- |
| `version` | `"setup_recurrence_v1"` |
| `window` | `["2021-06-01", "2026-01-29"]` (primary; the setup-outcome study's development window, never used for this hypothesis) |
| `data_cutoff` | `"2026-09-22"` (the cached range's end; ≥ 30 days after the window) |
| `robustness_window` | `["2017-01-03", "2021-04-30"]` |
| `robustness_data_cutoff` | `"2021-06-15"` |
| `lookback_sessions` | `20` (one label horizon: a prior inside it can have resolved) |
| `scan_times_et`, `max_hold_sessions`, `cost_bps_per_side`, `bootstrap`, `feed`, `adjustment`, `strategy_config`, `sector_etf` | as the short-suppression protocol, with the **current** `strategies` block (long-only: `allow_short: false`) and `SECTOR_ETF` |
| `min_repeats`, `min_repeat_sessions`, `min_after_stop` | `300`, `60`, `100` |
| `hypotheses`, `decision_rule` | free text, verbatim from §4 |

Validation: windows ordered and non-empty; each cutoff ≥ 30 days after its window end;
`robustness_window` ends before `window` starts by more than `max_hold_sessions` sessions
(30 calendar days is the check); `lookback_sessions ≥ 1`.

### 3.4 Hypotheses and decision — `execute_recurrence(protocol, directory, *, frames, environment)`

`frames` is `{"primary": Callable[[], DataFrame], "robustness": Callable[[], DataFrame]}`; each
is called exactly once inside the try block (the base-rate test's pattern: any failure, including
inside a callable, writes `{"status": "failed", "error": "<Type>: <message>"}` rather than raising).
Immature rows are dropped and counted. Statistics use the primary cost (5 bp per side),
setup-weighted means, and the stationary session-block bootstrap
(`study._stationary_index_draws`; block mean 10, 2,000 draws, seed 20261014); every two-group
comparison resamples whole sessions once per draw and computes both means on the same draw.

| Rule | Statistic (primary window) | Holds when |
| --- | --- | --- |
| **R1** repeat edge over first | mean R_cost(`repeat`) − mean R_cost(`first`) | ci90 lower bound > 0 |
| **R2** repeat after a stop | mean R_cost(`repeat` with `prior_outcome == "stop"`) | ci90 lower bound > 0 |
| **R3** robustness | R1's point estimate on the robustness window | > 0 |
| **R4** sample | `n_repeat ≥ min_repeats`, sessions holding a repeat ≥ `min_repeat_sessions`, `n_after_stop ≥ min_after_stop` (primary window) | all true |

`decision`: `"eligible_repeat_preference"` when R1–R4 all hold, else `"no_change"`. Both are
recorded with `authorizes_promotion: false`. Descriptives (never decisive): per-state and
per-`prior_outcome` means and hit rates on both windows; the same split per `(strategy,
timeframe)`; by calendar year; R1 recomputed at lookbacks 5 and 10 sessions; the
`repeat_open` count; mean holding sessions per state; the 0 bp means.

### 3.5 CLI — `alpha setup-recurrence PROTOCOL --output DIR [--cache DIR] [--robustness-cache DIR] [--max-workers N]`

Validates the protocol against the loaded `strategies` block and `SECTOR_ETF` exactly as
`setup-baserates` does (refuses a stale protocol), refuses an existing output directory, then
calls `build_window_frames` twice — `{"primary": window}` with `--cache` (default
`OUTPUT/raw`) and `data_cutoff`, then `{"robustness": robustness_window}` with
`--robustness-cache` (default `OUTPUT/raw-robustness`) and `robustness_data_cutoff` — so each
window reuses its existing cache. The two coverage maps go into the manifest's environment.
`execute_recurrence` runs in a thread and also writes `frames/primary.csv.gz` and
`frames/robustness.csv.gz` (the labelled frames with the recurrence columns) at mode 0600.

### 3.6 Documentation

`docs/setup-recurrence-2026-10-14.md` (result, written after the run from `result.json`:
funnel, the R1–R4 table, descriptives, what it says, caveats, artifact paths);
`docs/card-evidence.md` "Regime and recurrence context" gets a pointer to the test;
`docs/alpha-roadmap.md` desk item 1 status; CLAUDE.md one sentence; this spec and the plan.

## 4. Hypotheses and decision rule (verbatim for the protocol file)

**Hypotheses.** Over the primary window 2021-06-01 → 2026-01-29 (decisions; bars through
2026-09-22), setups replayed as the live desk scans them (10:35 and 14:35 New York, current
long-only strategy configuration, 159-name current universe, SIP all-adjusted), labelled by the
live bracket (20-session timeout, R_cost at 5 bp per side): a setup is a *repeat* when the same
(symbol, strategy, timeframe, direction) was set up in an earlier session within the last 20
trading sessions and that prior's bracket had resolved (its hourly exit bar closed) by this
decision; it is *first* when no such prior exists within 20 sessions; otherwise it is
*repeat_open* and excluded from R1/R2. Setup-weighted means; stationary session-block bootstrap
(block mean 10, 2,000 draws, seed 20261014), whole sessions resampled once per draw for both
groups; one-sided. R1: mean R_cost(repeat) − mean R_cost(first) > 0, 90% CI lower bound > 0.
R2: mean R_cost(repeat whose prior hit the stop) > 0, 90% CI lower bound > 0. R3: R1's point
estimate on the robustness window 2017-01-03 → 2021-04-30 (bars through 2021-06-15) > 0. R4:
at least 300 repeats over at least 60 sessions and at least 100 repeats-after-stop in the primary
window. Per-strategy, per-year, per-lookback (5/10) and 0 bp figures are descriptive only.

**Decision rule.** If R1, R2, R3 and R4 all hold: `eligible_repeat_preference` — a separately
specified card-construction rule that prefers repeat setups (preview before enforce, operator
approval) becomes eligible; nothing changes until that PR. Otherwise: `no_change`; the live
`recurrence` block keeps measuring and no construction rule is proposed. Both windows are used
once for this test.

## 5. Failure behaviour

- A replay or labelling failure in either window writes `status: failed` with the exception
  type and message in `result.json`; nothing partial is reported as a decision.
- A window with no repeats makes R1/R2 `NaN` with `holds: false` and R4 false; the decision is
  `no_change`.
- A stale protocol (strategy block or sector map differing from the loaded config) is refused
  before any provider access, as the base-rate command does.
- The cache range check (`_claim_cache_range`) refuses a cache that does not cover a window's
  bar range; the operator passes the right cache directory or lets a fresh one fill.

## 6. Testing

Fakes only (network blocked): `with_setup_recurrence` on crafted frames (first/repeat/
repeat_open; the exit-bar-close boundary; same-session rows never priors; lookback counted
in sessions across a gap; per-setup not per-symbol; unique-index guard; empty frame);
protocol identity and validation (window order, cutoffs, robustness-before-primary gap,
lookback ≥ 1); R1/R2 on hand-built frames with a deterministic draw count (paired
resampling: both means from the same drawn sessions); R3/R4 and the decision table; the
failure path writes `status: failed`; `execute_recurrence` writes protocol, manifest,
result and the two frames at 0600; the CLI refuses an existing output and a stale protocol
(the base-rate test's CLI pattern); `model_features` unchanged by the new frame columns;
`_build_frame` writes the three new columns.

## 7. Design decisions

1. **Primary window = the study's development window, not its holdout.** The holdout was
   consumed once by the ranker; the development window was used to fit scorers but never to
   examine recurrence, and the hypothesis came from live data after both. The robustness
   window (2017–2021) was used once for the short test; its role here is a sign check.
2. **Resolved priors define `repeat`.** The live block counts any earlier session as a repeat;
   the study needs the prior's outcome, so a prior still open at the decision is `repeat_open`
   and excluded from the comparison rather than mixed into either group.
3. **Lookback 20 sessions.** One label horizon: inside it a prior can have resolved, and the
   live 45-day block spans about 30 sessions. 5 and 10 are descriptive.
4. **Exit-bar close, not open.** Same rule as the live block after the 2026-10-14 review.
5. **Seed 20261014, not the September seed.** A new test gets its own draw stream.
6. **Two caches, two calls.** `_claim_cache_range` binds a cache to one bar range; one call
   over both windows would force a fresh 2016–2026 fetch for nothing.
7. **No construction rule in this PR.** A pass is eligibility, not a change: the rule's shape
   (rank repeats first, or withhold firsts) and its preview period deserve their own spec.
