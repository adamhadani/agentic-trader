# A priori entry `runup` v1: the uncertainty-conditioned pre-earnings run-up, development read

Date: 2026-11-06. Operator: "Go ahead" (2026-11-06) on the recommended first steps of the pre-event program:
the dated earnings-calendar capture (its own PR) and a development read of the run-up rule (this spec).
Design calls are mine under the operator's standing design trust.

## 1. Why, and what this read can decide

The one pre-earnings bet the 2026-11-06 research found testable with free data is to buy high-uncertainty
announcers about ten sessions before a scheduled report and sell at the close of the session before it,
never holding through the release. Gao, Hu and Zhang report +1.26% market-adjusted over those ten sessions
for the top tercile by option-implied volatility (1996–2019), against +0.26% for all announcers, with no
measured run-up for the low group. Nothing after 2019 has been measured, the desk has no historical implied
volatility, and published anomalies typically lose about half their return after publication.

This read tests the rule on the desk's own data with a free proxy for uncertainty. It runs on 2016–2026,
which is development-only for any new hypothesis (`docs/research-window-ledger.md`, windows W1, W2, W6), and
the cohort is today's survivors with report dates known after the fact. Every one of those biases favours
the rule, so **this read can kill the rule but cannot pass it.** The outcomes are:

- `failed`: the rule is closed on this proxy. A different proxy or window is a declared second look.
- `survives_development`: the rule earns a prospective read on the desk's own dated captures from the Q4
  2026 season (late January to March 2027), specified separately. Nothing trades and no card is sent.
- `inconclusive_underpowered`: the planted-effect check (section 7) failed, so the read decides nothing.

## 2. Goals, non-goals, anti-goals

Goals: a frozen catalog entry with its SHA-256; the ledger row before the read; a study that is offline
except for the trading calendar and one benchmark bar read; the frozen pass rule with a pseudo-event
negative control and a planted-effect check (program rules R3, R4); a results doc.

Non-goals: no card, probe, registry entry, shadow or promotion credit; no change to the earnings blackout,
risk rules or any live path; no 8-K Item 2.02 timing audit (a separate measurement); no implied volatility.

Anti-goals: tuning anything after reading outcomes; reading the prospective window (from 2026-08-15);
treating `survives_development` as permission to trade.

## 3. Inputs (all pinned)

The entry pins `config/research/pooled/cohort3-events-v1.json` by SHA-256. That protocol fixes:

- cohort v3 (`config/research/pooled/cohort-v3.json`, 1,000 names, ranks 401–1,400) and its cube spec: the
  10:35 New York decision clock, ATR window 14, eligibility `min_price` $10 and a 20-session median dollar
  volume of at least $5M as of the prior close, decisive cost 15 bp per side, bars 2016-01-01 → 2026-09-01;
- the Nasdaq calendar page archive, 2016-02-23 → 2026-07-31, `pages_sha256` 9273e40a…;
- the cohort-v3 corporate-actions acquisition, `content_sha256` 8a547b06…

The entry also pins the cached cohort-v3 cube by content hash (`cube_sha256`), computed from the cache when the
entry is frozen. Inputs are assembled as the insider runner does (`build_cube_inputs` with `require_cached`,
`load_hourly`, and `load_event_sources` for the pages and actions), so cohort bars come from the pooled cache.
SPY daily bars are in the cache; SPY hourly bars (needed for `net_excess_r_v1`'s market leg) are fetched once
through the research bar source after the manifest is written and cached in a run-specific directory under
`~/agentic-trader-research`. The trading calendar read is the other provider access.

## 4. Events

For each deduplicated archive row of a cohort symbol with report date `E`:

1. `E` must be an observed trading session (`non_session_report_date`). The decision session is
   `D = E − lead_sessions` (10). `D` must fall in `window.decisions` (`outside_window`) and inside the
   calendar (`outside_calendar`).
2. Another report of the same symbol within the 40 sessions before `E` excludes the row
   (`recent_previous_report`): it is a duplicate or garbled listing, and it would put a release inside the hold.
3. An identity break of the symbol (`pooled/events.identity_breaks`) on or after the first session of the
   volatility window excludes the row (`identity_break`). A structural corporate action (anything but a cash
   dividend) within 5 calendar days of `[D, E]` excludes it (`structural_action_nearby`).
4. The cube's eligibility at `D` (price and liquidity as of `D − 1`) must hold (`not_eligible`).
5. **Uncertainty.** `rv` is the standard deviation of daily log returns of adjusted closes over the 60
   sessions ending `D − 1`, needing at least 55 finite returns (`no_rv`). Among every cube-eligible cohort
   name at `D` with a finite `rv`, rank ascending (ties by the cube's `tiebreak`); rank `r` of `n` falls in
   tercile `floor(3r / n)`. The event is a **leg** when its tercile is 2 (the top); otherwise it is an
   `announcer` row kept only for descriptive statistics.
6. ATR(14) on adjusted bars as of `D − 1` (`no_atr`); 20-session return to `D − 1` (`no_return`); the cube's
   dollar-volume tercile at `D` among eligible names (the insider study's `_buckets` rule).

Realized volatility is the primary proxy because the published sort is on the *level* of implied
volatility, which across stocks is dominated by the level of ordinary volatility; it is available for every
event and does not depend on the after-the-fact report-date archive. The stock's average absolute reaction
to its previous reports is the natural second proxy; it is computed for every leg and reported only as a
descriptive split (section 8).

## 5. Trade and labels

LONG only. Decision at 10:35 New York on `D`; fill, stop and exits are `research/setups/labels.label_bracket`
on the cached regular-session hourly bars (fill at the open of the first regular bar at or after the
decision; a bar touching the stop fills at the stop or the gap open). Stop: 2 × ATR(14) below the decision
price. **No target**: `label_bracket` is called with a `BracketArm` whose `target_r` is `None` and with no
trail (not `LIVE_ARM`, which keeps the levels' target). Time exit: the close of the `lead_sessions`-th
session, which is `E − 1` for a leg. Costs 5, 15 and 25 bp per side; 15 bp decides.

Exclusions while labelling, by reason and year: `no_hourly_bars`, `missing_session_bars` (any observed
session of the hold without a regular hourly bar), `no_decision_price`, `degenerate_levels`, `immature`, and
`exit_on_or_after_report` (a leg whose walk ends on or after `E`).

Every labelled row also gets `net_excess_r_v1` through the same helpers as the PEAD base rate
(`trailing_beta`, `name_cost_bps`, `market_r` on SPY hourly bars over the row's own hold, `net_excess_r`);
a missing input gives `NaN`, never a fill.

## 6. Matched controls

For each leg, 5 controls on the same session `D`, each a cohort name that is:

- cube-eligible at `D`, in the leg's dollar-volume tercile and also in the top `rv` tercile at `D`;
- not a leg on `D`, with no archive report date in `[D, D + lead_sessions]` (no release inside its own hold);
- covered by the archive: at least one report date in the 130 sessions before `D`, so "no report" is not
  missing data;

nearest in 20-session return to the leg, ties by the cube's `tiebreak`. Each control is labelled with the
leg's bracket on its own bars (exit at the close of the `lead_sessions`-th session). Fewer than 5 candidates
leaves the leg unpaired (counted by year). The control row is the mean of the 5.

## 7. Pass rule, negative control and planted effect

Stationary session-block bootstrap over decision sessions (the existing `statistics.mean_test` and
`paired_test`), block mean 10, 2,000 draws, seed 20261106; ci90 is the [5th, 95th] percentile pair.

- **P1**: the legs' mean `net_excess_r_v1` > 0 with ci90 lower bound > 0 (legs with a finite value).
- **P2**: the mean of `r_cost` (15 bp) of paired legs minus the mean of their controls > 0 with the paired
  ci90 lower bound > 0, pairs keyed by `D`.
- **P3**: the 1%-trimmed mean of `net_excess_r_v1` > 0, and its mean over decisions from 2023-01-01 > 0.
- **P4**: at least 1,000 legs with a finite `net_excess_r_v1`, at least 300 of them from 2023-01-01.

**Negative control (R4).** Every leg gets one pseudo-event: the same symbol at a session `D'` drawn with a
seeded generator (seed 20261107) from the sessions within 63 of `D` such that the symbol has no archive
report date in `[D' − lead_sessions, D' + 2 × lead_sessions]`. `D'` goes through the identical gates (steps
3–6 of section 4, the top-tercile rule included), labels, controls and statistics. The placebo's P2 must
**not** hold; if it does, the matching leaves a bias the legs could ride, and the read is `failed`.

**Planted effect (R3).** Before the legs' statistics are computed, the executor adds to every paired placebo
leg a planted return of half the published effect, 0.63% of the decision price, converted to R per row
(`0.0063 × decision_price / risk_unit`), to both `r_cost` and `net_excess_r_v1`, and runs P1 and P2 on that
frame. Both must hold (`planted_detected`); otherwise the decision is `inconclusive_underpowered`. The
planted result is saved to `planted.json` before `result.json`.

**Decision.** `inconclusive_underpowered` if the planted check fails; else `failed` if any of P1–P4 fails or
the placebo's P2 holds; else `survives_development`. No threshold, proxy, lead, stop, cost, window or
control parameter is revisited after the run; any change is a new version.

## 8. Descriptive diagnostics (decide nothing)

All announcers (every tercile) and the bottom tercile, each with `net_excess_r_v1` and `r_cost`; legs by
year, by release timing (pre-market, after-hours, unspecified), by dollar-volume tercile, excluding 2020;
the stop/time-exit split and mean holding sessions; costs 5 and 25 bp; the placebo's P1; the per-leg
standard deviation of `net_excess_r_v1` and the session design effect (variance of session means × legs per
session ÷ per-leg variance), which the prospective power plan needs; the last-day excess (adjusted close
`E − 2` → `E − 1` minus beta × SPY over the same day) for the day −1 liquidity-provision bias; and terciles
of legs by the past-reaction proxy (mean absolute 2-session excess reaction around each of up to the last 4
archive reports before `D`, at least 2 needed; reaction session `E` for pre-market releases, `E + 1`
otherwise).

## 9. Code and artifacts

- `research/apriori/catalog.py`: `RunupEntry` (`id: "runup"`), `load_runup_entry`, and its validation
  (decision window inside the cube spec's decisions; `bars_through` at least 30 days after the last decision).
- `config/research/apriori/runup-v1.json`: decisions 2016-08-01 → 2026-07-17 (the last `E` the archive covers
  is 2026-07-31), `bars_through` 2026-09-01, `recent_from` 2023-01-01, and the values above.
- `research/apriori/runup_events.py` (sections 4 and 6's candidate rules, placebo draws),
  `research/apriori/runup_study.py` (labels, controls, net excess, P1–P4, placebo, planted, diagnostics,
  executor), `research/apriori/runup_runner.py` (input assembly with every digest checked before use).
- `alpha apriori-study` dispatches on `id: runup` (as it does for `exdiv` and `insider`).
- The executor writes `protocol.json` and `manifest.json` before any provider call, then `events.csv.gz`,
  `labels.csv.gz`, `controls.csv.gz`, `placebo.csv.gz`, `planted.json`, `result.json`. Every failure is
  `status: failed` with the exception; `authorizes_promotion` is always false.
- Run output goes under `~/agentic-trader-research/apriori-runup-v1-<date>`, never into the repository.

## 10. Ledger and docs

Before the read, `docs/research-window-ledger.md` gains row L43 (pre-earnings run-up `runup` v1, cohort v3,
decisions 2016-08-01 → 2026-07-17, outcomes read, development only, cannot pass), an α-table line (the
run-up lane: development read, no α; a prospective read declares its α in its own row), and the windows'
read lists updated. The entry, ledger row and code are committed before the run. After the run:
`docs/apriori-runup-<date>.md` (the result), `docs/apriori-alphas.md` (catalog section), a `CLAUDE.md`
paragraph, the ledger row's verdict, and `docs/alpha-roadmap.md`'s pre-event section updated.

The run avoids 10:20–10:50 and 14:20–14:50 New York.
