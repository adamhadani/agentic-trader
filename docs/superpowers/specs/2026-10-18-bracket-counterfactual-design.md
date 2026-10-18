# Bracket counterfactual v1 — design

**Date:** 2026-10-18. **Status:** approved under the operator's standing design trust
(2026-10-09, "go with your recommended approaches"); the operator said "start it" on
2026-10-18. Design decisions taken here are recorded as rulings in §12 and presented in the PR.

## 1. Question

Every negative result since October 7 shares one constant: the exit bracket. The pooled lane
tested 48 formula–cohort pairs under a frozen 2-ATR stop / 3R target / 20-session long bracket
and found no finalist; the native funnel's cards use a structural-or-1.5-ATR stop, a 2R target
and no time exit, and resolve at −0.17R to −0.47R per card. The October 8 funnel diagnostic ran a
4×4 stop/target grid plus three time exits over **30 overlapping live candidates**: nothing was
credibly positive, but the table was in-sample, underpowered, and **had no trailing-ratchet
arm** — the one exit mechanism the live desk actually operates on every position
(`trailing_stop`, trigger 1.5R, trail 1.5R behind price). The roadmap lists live
trailing-policy parity as unfinished for the same reason: nothing has ever measured it.

This workstream asks one predeclared question on a population large enough to answer it:

> Holding the native entries fixed, does any of four predeclared exit policies (3R target, the
> live ratchet with a 2R or 3R target, or the ratchet alone) earn more than the live 2R fixed
> bracket, net of cost and net of the index, and is that policy's own expected value
> non-negative?

A pass makes **one construction rule eligible** — a change to the native card's bracket, shipped
in its own PR with operator approval, `preview` before `enforce`. Nothing here is an alpha,
nothing promotes, nothing touches risk, admission, the registry or the broker.

## 2. Scope

In scope:

- A frozen protocol `config/research/bracket-counterfactual-v1.json` and its pydantic model.
- A labeller extension so one shared function labels the live bracket and the four
  counterfactual arms on the same hourly bars.
- A research executor and CLI, `alpha bracket-counterfactual`, following the setup-recurrence
  template (manifest before data, private frames, `result.json`, fail closed).
- A descriptive `counterfactual` block in `cards outcomes` so the live journal keeps measuring the
  same arms on real cards, never as a gate.
- Docs: this spec, the protocol's hypotheses text, `card-evidence.md`, the roadmap status, the
  `CLAUDE.md` paragraph, and the result doc after the run.

Out of scope (explicitly):

- Any change to live card construction, `risk/`, `card_policy`, admission, trailing-stop
  management or the broker. A pass only makes a later PR eligible.
- New stop geometries (the October 8 grid covered them; no cell was positive), short setups
  (the live configuration is long-only), new entries, new cohorts, minute bars, or the pooled
  cube's label (its unconditional leg mean is already positive; its problem is cross-sectional
  ranking, not the bracket).
- Re-tuning on validation results. The arms, rules and windows are fixed in the protocol before
  any bar is read.

## 3. Population and replay

Reuse the setup-recurrence replay unchanged (`research/setups/replay.py`,
`runner.build_window_frames`): the unchanged live `StrategyEngine` at the 10:35 and 14:35 New
York scan instants over the 159-name current universe, current long-only strategy configuration
(`allow_short: false` on both strategies), deterministic levels
(`RiskEvaluator.calculate_levels_deterministic`), live duplicate rule, Alpaca SIP all-adjusted
daily and hourly bars from the two existing research caches. The protocol pins `strategy_config`
and `sector_etf` and the CLI refuses when either differs from the loaded config.

Windows (decisions; the labeller reads bars to the data cutoff):

| Window | Decisions | Bars through | Role |
| --- | --- | --- | --- |
| primary | 2021-06-01 → 2026-01-29 | 2026-09-22 | rules B1, B2, B4 |
| robustness | 2017-01-03 → 2021-04-30 | 2021-06-15 | rule B3 (sign) |
| confirmation | 2026-03-02 → 2026-08-14 | 2026-09-22 | rule B5, read once, only if an arm is selected |

The confirmation window is the setup-outcome study's one-shot holdout. It was read once on
2026-09-23 for the shadow ranker's acceptance test (a different hypothesis) and was not read by
the recurrence test. This protocol declares its second use openly (`confirmation_prior_use` in
the protocol), records the intent to read it in the manifest before any confirmation bar is read,
and reads it at most once, for at most one arm. The 20-session embargo between the primary window
and the confirmation window is preserved by construction (primary decisions end 2026-01-29; the
last primary bracket resolves by 2026-02-27).

Survivorship: the current-universe replay flatters longs. That bias is common to every arm and
cancels in the paired contrasts; it does not cancel in rule B2, which is read as an upper bound,
exactly as the setup-outcome base rates were.

## 4. Arms

All arms share the entry (open of the first regular-session hourly bar at or after the decision),
the planned stop (the card's structural-or-1.5-ATR stop), the planned risk unit
R = |entry − stop|, the 20-session timeout, and cost at 5 bp per side.

| Arm | Target | Ratchet | Role |
| --- | --- | --- | --- |
| `A0_live` | planned (2R) | off | baseline; byte-identical to the live labeller |
| `A1_3r` | 3R | off | the pooled lane's target on the native entries |
| `A2_2r_trail` | planned (2R) | on | the live desk as it actually runs |
| `A3_3r_trail` | 3R | on | |
| `A4_trail_only` | none | on | let the ratchet alone close winners |

**Ratchet on hourly bars (the declared approximation of `Copilot.manage_trailing_stops`).** The
live rule polls the last trade every minute and, once the open gain reaches `trail_trigger_r`
(1.5R), proposes stop = price − `trail_atr_multiple` × R (1.5R; despite the key name the live
distance is R-based), moving only upward and only when the move exceeds two ticks. On hourly bars
the study evaluates the rule at each completed regular-session bar's **close**: if
close ≥ entry + 1.5R, the proposed stop is close − 1.5R; the stop becomes
max(current stop, proposed) and takes effect from the **next** bar. Bar highs are not used (an
intra-bar poll would sometimes catch a higher print, so the close-based rule is the conservative
side of the live behaviour, matching the research `simulate_strategy` convention). The two-tick
step is ignored (sub-cent effect at these prices). Exits through a ratcheted stop count as
`BracketHit.STOP` with the ratcheted level as the stop price; a bar touching both the current stop
and the target still counts as a stop; gaps fill at the open.

"No target" (`A4`) means the walk ends only on the stop (planned or ratcheted) or the timeout.

Because every arm shares the entry and the planned stop, rows are **paired by setup**: for a
setup that never reaches +1.5R, arms A0, A2 and A4 produce identical outcomes, and the paired
difference is exactly zero. That is where the design's power comes from.

## 5. Labeller extension

`research/setups/labels.py`:

```python
@dataclass(frozen=True)
class TrailSpec:
    trigger_r: float  # 1.5 in v1
    distance_r: float  # 1.5 in v1


@dataclass(frozen=True)
class BracketArm:
    name: str
    target_r: float | None  # None → no target; float → entry ± target_r × risk_unit
    trail: TrailSpec | None  # None → fixed stop


LIVE_ARM = BracketArm(name="A0_live", target_r=None, trail=None)  # sentinel: planned target
```

`label_bracket(levels, decision_at, hourly, *, max_hold_sessions, cost_bps_per_side=0.0,
arm: BracketArm | None = None)`. With `arm=None` the function is unchanged and its results are
byte-identical to today's (pinned by a regression test over the existing fixtures), so
`card_stats`, `cards outcomes` and the recurrence frames are unaffected. With an arm:

- `arm.target_r` is `None` **and** `arm is LIVE_ARM` → planned target (A0);
  `arm.target_r` is `None` otherwise → no target (A4); a float → the target is
  `entry ± target_r × risk_unit` in the direction of the trade, where `entry` is the planned
  entry and `risk_unit` the planned risk (so the arm's target in R is exact by construction).
- `arm.trail` applies the §4 rule.

`BracketOutcome` gains `final_stop: float | None` and `ratchet_moves: int` (0 when the trail is
off). Nothing else in its shape changes.

## 6. Statistics and decision rules

Per window, per arm, one row per labelled setup: `decision_at`, `symbol`, `strategy`,
`timeframe`, `direction`, `entry`, `stop`, `target`, `atr`, `setup_quality`, `arm`, `hit`,
`entry_price`, `exit_time`, `exit_price`, `holding_sessions`, `ratchet_moves`, `final_stop`,
`r`, `r_cost` (5 bp), `market_r` (SPY from the entry bar's open to the exit bar's close over
**that arm's own** holding window, in the setup's R, uncosted — the existing `market_r`
convention), `excess_r = r_cost − market_r`.

**Winsorisation.** All statistics use `r` and `r_cost` clipped to ±`r_cap` (10R). The frames keep
the raw values. The recurrence test's robustness sign was carried by one +1,546R cell (a
near-zero planned stop); a declared cap removes that artefact class without touching the
labeller.

**Weighting and resampling.** Setup-weighted means. Stationary session-block bootstrap (block
mean 10 sessions, 2,000 draws, seed 20261018), whole sessions resampled once per draw and
applied to every arm, so each draw's contrast is paired. One-sided p-values are the fraction of
draws with the contrast ≤ 0; 90% CIs are the 5th and 95th percentiles.

**Rules (primary window unless stated).** For each counterfactual arm i ∈ {A1, A2, A3, A4}:

- **B1 — advantage over the live bracket, net of the index.** Paired mean
  (`excess_r_i − excess_r_A0`) > 0 with Holm-adjusted one-sided p < 0.05 over the four arms.
- **B2 — the arm stands on its own.** The arm's mean `r_cost` has a 90% CI lower bound ≥ 0.
- **B3 — robustness sign.** On the robustness window, the paired mean
  (`excess_r_i − excess_r_A0`) point estimate > 0.
- **B4 — sample floors.** At least 3,000 labelled setups over at least 600 sessions in the
  primary window, at least 95% of setups mature, and at least 300 setups whose A0 and arm-i
  outcomes differ (the contrast must rest on real divergence, not a handful of trades).

An arm **passes discovery** when B1–B4 all hold. If no arm passes: decision `no_change`; the
confirmation window is not read. If one or more pass: the **selected arm** is the passing arm
with the largest B1 point estimate (ties: the arm with fewer mechanisms — A1 before A2 before A3
before A4). Then:

- **B5 — confirmation.** Read the confirmation window once, for the selected arm and A0 only.
  The selected arm is **eligible** when its paired excess advantage over A0 is > 0 (point
  estimate) and its own mean `r_cost` is ≥ 0 there. Decision `eligible_bracket_change` with the
  arm's name; otherwise `no_change`.

Decisions are the only normative output. Everything else in `result.json` is descriptive and
never decisive: per-arm hit rates, timeout shares, mean holding sessions, mean `market_r`,
per-strategy × timeframe strata, per-year means, the unpaired arm means, the 0 bp figures, and
the same five arms on the live journaled cards since 2026-09-24 (from `cards outcomes`, §8).

**Pre-registered reading.** `eligible_bracket_change(A2)` or `(A3)` means the live ratchet is
worth what the desk pays for it and the card's stated target should carry it; `(A1)` means the
native target should be 3R; `(A4)` means the card should state a trailing exit and no fixed
target. In every case the follow-up PR changes card construction only, states the measured arm
record on the card, and ships `preview` first. `no_change` closes the bracket as an explanation
for the native funnel's base rate: the entries, not the exits, are the problem, and the next work
is on the entry side.

## 7. Protocol file and model

`config/research/bracket-counterfactual-v1.json`, loaded by `BracketCounterfactualProtocol`
(`research/setups/bracket_counterfactual.py`; pydantic v2, frozen, `extra="forbid"`; `identity`
= SHA-256 of the sorted JSON, as in `SetupRecurrenceProtocol`). Fields:

| Field | Value |
| --- | --- |
| `version` | `"bracket_counterfactual_v1"` (underscore form, like `setup_recurrence_v1`) |
| `window`, `data_cutoff` | `["2021-06-01", "2026-01-29"]`, `"2026-09-22"` |
| `robustness_window`, `robustness_data_cutoff` | `["2017-01-03", "2021-04-30"]`, `"2021-06-15"` |
| `confirmation_window`, `confirmation_data_cutoff` | `["2026-03-02", "2026-08-14"]`, `"2026-09-22"` |
| `confirmation_prior_use` | verbatim: the setup-outcome ranker acceptance read of 2026-09-23 |
| `embargo_sessions` | 20 |
| `scan_times_et` | `["10:35", "14:35"]` |
| `max_hold_sessions` | 20 |
| `cost_bps_per_side` | `[0.0, 5.0]` (5.0 decisive) |
| `arms` | the five §4 arms as `{name, target_r, trail: {trigger_r, distance_r} | null}`, with `A0_live` first and `target_r: "planned"`; the model maps `"planned"` to `LIVE_ARM`, `null` to no target, a number to that many R |
| `r_cap` | 10.0 |
| `bootstrap` | `{block_mean: 10, draws: 2000, seed: 20261018}` |
| `holm_alpha` | 0.05 |
| `min_setups`, `min_sessions`, `min_mature_fraction`, `min_divergent` | 3000, 600, 0.95, 300 |
| `feed`, `adjustment` | `"alpaca:sip"`, `"all"` |
| `strategy_config`, `sector_etf` | copied from `config/config.yaml`; must match the loaded config |
| `hypotheses`, `decision_rule` | verbatim prose of §4–§6 |

Validators: `data_cutoff` ≥ window end + 30 days (and likewise for the other two windows); the
robustness window ends ≥ 30 days before the primary window starts; the confirmation window
starts ≥ `embargo_sessions` trading days after the primary window ends (checked as ≥ 28 calendar
days); exactly one arm named `A0_live` with `target_r: "planned"` and no trail; arm names
unique; `r_cap` > 0; `holm_alpha` in (0, 0.5).

A regression test loads the frozen file, checks the pinned values above, and checks
`strategy_config`/`sector_etf` against the loaded `config/config.yaml`, as the recurrence test
does.

## 8. Executor, CLI and the live measurement

**Executor** `execute_bracket_counterfactual(protocol, output_dir, *, primary_frame,
robustness_frame, confirmation_frame, started_at)`. Frames are callables returning the replayed
setup frames (decision rows with levels and the hourly bars needed to label them), exactly as
`execute_recurrence` receives them; each is called at most once, inside `try`. Order:

1. Write `protocol.json` (with `identity`) and `manifest.json` (`protocol_id`, `environment`,
   `started_at`, `arms`, `windows`, `authorizes_promotion: false`,
   `confirmation_read: false`).
2. Label the primary frame under all five arms; compute B1, B2, B4; write
   `frames/primary.csv.gz` (mode 0600).
3. Label the robustness frame under all five arms; compute B3; write `frames/robustness.csv.gz`.
4. Decide. If an arm is selected, write the declaration `confirmation.json`
   (`confirmation_read: true`, `confirmation_arm`, `confirmation_declared_at`, `protocol_id`)
   **before** calling `confirmation_frame` (artifacts are write-once, so the declaration is its
   own file and the manifest names it), then label the confirmation frame under A0 and the
   selected arm, compute B5, write `frames/confirmation.csv.gz`.
5. Write `result.json`: `decision`, `selected_arm`, `b1`..`b5` (estimate, ci90, p, holm_p, holds,
   per arm), `descriptives`, `windows`, `started_at`, `finished_at`. Any exception writes
   `result.json` with `status: "failed"` and the exception type name, and re-raises.

**CLI** `copilot alpha bracket-counterfactual PROTOCOL --output DIR --cache DIR
--robustness-cache DIR [--max-workers N]` (`cli/commands/alpha.py`, beside
`setup-recurrence`). It refuses, before any provider access: an existing output directory, a
protocol whose `strategy_config` or `sector_etf` differ from the loaded config, and a protocol
whose feed is not `alpaca:sip`. It claims both cache ranges up front (`claim_cache_range`), builds
the SIP `AlpacaDataProvider` and `AlpacaCalendarProvider`, uses `research_environment()`, calls
`build_window_frames` per window (the confirmation frame uses the primary cache: same cutoff),
and exits non-zero on failure. No database, registry, broker or Telegram access; no journal
charge (the recurrence precedent; the protocol text and manifest record the window use).

**Live measurement** (`research/setups/outcomes.py`, `cards outcomes`): add `target` and
`atr_14` to `_COLUMNS` (the labeller already reads `target`); add a `counterfactual` summary block
that labels every journaled candidate under the same five arms with the shared labeller
(`BracketArm` objects built from the frozen protocol file, so the CLI and the study can never
drift), on the bars `cards outcomes` already fetches (IEX hourly, raw adjustment), reporting per
arm: `n_mature`, `target`/`stop`/`timeout` rates, `mean_r_cost`, `mean_excess_r`, and the paired
`advantage_vs_live` mean. The CLI renders it under the existing attribution blocks with the
standing caveat ("descriptive; not a gate"). Nothing in `risk/`, `card_policy`, `card_stats` or
admission reads it.

## 9. Outputs

`~/agentic-trader-research/bracket-counterfactual-v1-<date>/`: `protocol.json`, `manifest.json`,
`frames/{primary,robustness[,confirmation]}.csv.gz` (0600), `result.json`, the run log. The
result doc `docs/bracket-counterfactual-<run date>.md` follows the recurrence doc's shape
(result first, protocol and run, rules table, descriptives, interpretation, artifacts) and is
bundled into this PR if the run completes before merge, otherwise into the next workstream PR.

## 10. Testing

TDD, `tests/research/setups/test_labels.py` and new `test_bracket_counterfactual.py`,
`tests/cli/` for refusals, `test_outcomes.py` for the live block:

- Labeller: `arm=None` reproduces every existing fixture outcome exactly (regression pin);
  `A1_3r` hits a 3R target the live arm times out on; ratchet trigger at exactly +1.5R close moves
  the stop to entry and the stop is effective from the next bar (a same-bar low below the new
  stop does not exit); the stop never moves down; a ratcheted-stop exit is `STOP` with `r` ≥ 0
  minus cost; `A4_trail_only` never returns `TARGET`; both-touched bar counts as stop with the
  ratcheted level; gap through the ratcheted stop fills at the open; `ratchet_moves` and
  `final_stop` are populated; short-direction symmetry (the labeller is direction-aware even
  though v1's population is long-only).
- Statistics: paired bootstrap applies one session draw to all arms (identical arms give an
  exactly zero contrast with zero-width CI); Holm ordering on a synthetic p-vector; winsorisation
  clips `r` at ±`r_cap` for statistics but not in frames; `min_divergent` counts rows whose A0 and
  arm-i `(hit, exit_time)` differ.
- Decision table: no arm passes → `no_change` and `confirmation_frame` never called; two arms
  pass → the larger B1 estimate is selected, tie → lower index; B5 fail → `no_change` with
  `selected_arm` recorded; manifest shows `confirmation_read: true` before the confirmation frame
  callable runs (assert ordering with a recording callable); exception → `status: failed`.
- Protocol: identity stability, every validator, frozen-file-matches-config test.
- CLI: refuses existing output, stale `strategy_config`, non-SIP feed; exits non-zero on failure;
  never touches the database (the research environment fixture).
- `cards outcomes`: the `counterfactual` block appears with five arms, `target` and `atr_14`
  columns are present, the block is absent from `card_stats` and from any `risk/` input (grep
  test as for `news`).

## 11. Non-functional

Runtime: the recurrence run (same replay, one arm) took about an hour on this host; five arms
share one replay and one bar read, so labelling adds minutes, not hours. The CLI must not run
across the 10:35/14:35 New York scan windows (operational rule, not code). No new migration;
schema head stays `008_alpha_pipeline`. Readiness, Telegram and the daemon are untouched.

## 12. Rulings (design decisions under standing trust)

1. **Population is the replayed history, not the live journal.** The journal has ~40 mature
   long cards since 2026-09-24; the primary window has ~8,800. The live cards are reported
   descriptively through `cards outcomes` and keep measuring prospectively.
   *Cost if wrong:* replayed setups differ from sent cards by the LLM gate and the budget; the
   diagnostic found the labeller's entry drift at −0.01R, and gates do not change exits.
2. **Four arms, not a grid.** The October 8 grid answered geometry; the unmeasured mechanism is
   the ratchet. Four contrasts keep Holm cheap and the question answerable.
   *Cost if wrong:* a better exit outside these four stays unmeasured; a v2 can declare it.
3. **Close-based hourly ratchet.** Conservative relative to the live one-minute poll, consistent
   with the research simulator. *Cost if wrong:* understates the ratchet's benefit slightly; a
   pass is therefore a safe pass, a fail is slightly pessimistic.
4. **Index-controlled contrast (B1 on excess R).** Arms with longer holds in a rising tape would
   otherwise win on beta. *Cost if wrong:* none for a long-only desk that also requires B2.
5. **Winsorise at 10R for statistics only.** Removes the degenerate-stop artefact class the
   recurrence test hit, without changing the labeller or the frames.
6. **Reuse the consumed holdout as a declared confirmation window.** Different hypothesis,
   declared prior use, read once for one arm, manifest records intent before the read. The
   alternative — a prospective-only confirmation — would take months on ~2 cards a day.
7. **No journal charge.** Matches the recurrence precedent; the pooled ledger's charges are for
   formula search, which this is not.
8. **Bundle the cohort-v3 results doc.** This branch starts at commit `100a42b` so the pooled
   cohort-v3 results ride on this PR (no docs-only PRs).

## 13. References

- [Desk direction, October 8](../../alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8)
- [Setup-outcome study](../../setup-outcomes-2026-09-23.md); [setup recurrence test](../../setup-recurrence-2026-10-14.md) and its [spec](2026-10-14-setup-recurrence-design.md)
- [Card evidence](../../card-evidence.md); [pooled cohort v3 results](../../alpha-pooled-cohort3-2026-10-16.md)
- `~/agentic-trader-research/card-funnel-diagnostic-20261008/report.md` (outside the repo)
