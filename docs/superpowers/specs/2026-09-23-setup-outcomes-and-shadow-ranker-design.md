# Setup outcomes and shadow card ranker — design

**Workstream:** WS2 of the [alpha expansion survey](../../alpha-expansion-survey-2026-09-23.md)
(approved September 23; the operator delegated sequencing to the assistant).
**North star:** one or two reasonable Telegram suggestions per session on the Alpaca paper desk.

## Problem

Every card today comes from the two native strategies (trend pullback, squeeze
breakout). They are ranked by `setup_quality`, a transparent heuristic that has never
been measured against outcomes. The survey's first recommendation, a cross-sectional
panel lane, needs a target that matters to cards. At one or two cards a day, live
evidence would take months.

Replaying the live strategies over history gives thousands of setups whose bracket
outcomes can be measured now. The same features can then be recorded prospectively
on every live candidate.

## Goals

1. **WS2a — offline setup-outcome study.** Replay the unchanged live strategies over
   the scan universe at the live scan clock. Measure each setup's bracket outcome in R.
   Test, under a predeclared protocol with a one-shot time-interval holdout, whether
   `setup_quality` and a small set of causal cross-sectional features predict R. If a
   predeclared criterion passes, freeze a shadow ranker artifact.
2. **WS2b — live shadow evidence.** At RANK time, compute the same feature vector
   (and the frozen ranker's score, if one exists) for every approved candidate. Journal
   all ranked candidates, not only sent cards. A read-only report labels them with the
   same bracket labeler.

## Non-goals

- No change to live ranking, card budget, gates, sizing, orders or the LLM prompt.
  Switching the RANK key from `setup_quality` to a ranker is a later operator decision
  that needs WS2a holdout evidence plus live shadow evidence.
- No alpha qualification, promotion or shadow credit. No DSL/miner changes. The
  panel-mining lane follows this workstream and uses its outcome target.
- No new database migration, queue or delivery path.

## Architecture

```
research/setups/            (new package, pure + injected I/O)
  replay.py      live-clock frame builder + strategy replay → SetupRecord
  labels.py      bracket labeler (shared by study and live outcomes report)
  features.py    causal cross-sectional feature vector (shared by study and live scan)
  study.py       protocol, splits, metrics, model selection, holdout, ranker artifact
cli: `copilot alpha setup-study PROTOCOL --output NEW_PRIVATE_DIR`
     `copilot cards outcomes [--days N]`   (read-only)
live: copilot RANK phase → features.py (+ optional frozen ranker) → decision_provenance
      and one journaled `scan_candidates_ranked` domain event per scan
```

`features.py` and `labels.py` are single implementations shared by research and live
code. This gives research/live parity by construction, as the alpha pipeline already
requires for scores.

### Replay at the live clock (`replay.py`)

- Evaluation instants are the configured `scheduler.suggestion_scan_times_et`
  (10:35 and 14:35 New York) on every regular session in the study window. The
  observed exchange calendar comes from the existing market-session components.
- At instant `t`, build the `ContractMarketData` that the live fetch would have built:
  - daily bars whose session closed before `t`;
  - 1h bars whose bar end is at or before `t`;
  - 4h bars from the **same** `resample_to_4h` over those 1h bars;
  - indicators from the same `compute_*_indicators` functions.

  Performance shortcuts (precomputing indicators, slicing) are allowed only when a
  parity test shows the shortcut frame equals a from-scratch frame at sampled
  instants, including a partial 4h bucket.
- Run the live `StrategyEngine.scan_contract` with the study's frozen strategy config
  (the repository `config/config.yaml` strategy section at the protocol's commit,
  copied into the protocol). Apply the live duplicate rule: same contract, strategy,
  timeframe within `risk.deduplication_hours`, capped at 2h/4h for 15m/1h. Keep the
  conflict resolver's output as live does.
- Levels come from `RiskEvaluator.calculate_levels_deterministic`, a pure call with an
  explicit config. Sizing is irrelevant; only entry, stop and target are used.
- Equities and ETFs only (the Alpaca desk). 15m timeframes are excluded if configured,
  and the protocol records the excluded strategy/timeframe pairs.

### Bracket labeler (`labels.py`)

The input is one setup (direction, entry, stop, target, decision instant) and the 1h
bars strictly after the decision instant.

- **Entry:** the open of the first 1h bar starting at or after the decision instant.
  The labeler does not model the live limit/market distinction. If the entry open
  already gaps beyond the stop or target, the outcome is that side at the open price.
- **Walk forward bar by bar.** A long hits the stop when low ≤ stop and the target when
  high ≥ target (mirrored for shorts). If both are hit in the same bar, **stop first**
  (conservative). A gap through a level fills at the bar open, so R can be below −1.
- **Timeout** after `max_hold_sessions` (protocol value, default 20) regular sessions:
  exit at that session's last regular 1h close.
- **Output:** `R = signed(exit − entry) / |entry − stop|`, plus `hit` ∈
  {target, stop, timeout}, the holding sessions, and `R_cost` at the protocol's
  per-side cost in bp.
- Labels are adjusted-price payoff proxies, the same caveat as the daily-panel docs.
  They are not fills.

### Features (`features.py`)

A pure function of daily bars for the whole cross-section available at `t`. Only
sessions closed before `t` are used, so an intraday scan never sees today's daily bar.
It returns a versioned vector (`features_version = "setup_features_v1"`).

- **Cross-sectional percentile ranks** across the names present at `t`:
  - `mom_252_21` (return from t−252 to t−21 sessions)
  - `mom_60`
  - `rev_5` (negated five-session return)
  - `vol_20` (realized)
  - `dist_52w_high`
  - `dollar_volume_20`
  - `resid_mom_60`: the 60-session cumulative residual after an OLS regression on the
    name's sector ETF over the prior 126 sessions. The sector→ETF map is frozen in the
    protocol; ETFs regress on SPY.
  - `sector_rel_mom_60`: group-neutralized `mom_60` via the existing
    `panel.group_neutralize`.
- **Market context:** SPY above its 200-session mean (0/1); SPY 20-session realized
  volatility percentile over the prior 252 sessions.
- **Setup:**
  - `setup_quality`
  - strategy (one-hot)
  - timeframe (one-hot)
  - stop distance in ATR units
  - target/stop ratio
- **Direction-aware sign.** For shorts, directional features (momentum, reversal,
  residual, sector-relative, distance to high) use `1 − rank`, so "high = favourable
  for this direction" holds for both sides.
- Missing history leaves a feature NaN, never forward-filled. Models use explicit
  missing-indicator handling: HGB natively; linear models get a median imputer fitted
  on training rows only.

### Study protocol (`study.py`, `config/research/setup-outcomes-v1.json`)

The protocol is frozen JSON and its hash is its identity. It contains:

- universe (the current 159-name scan universe); the survivorship caveat is recorded;
- feed `alpaca:sip` historical, adjustment `all`;
- strategy config;
- scan times;
- `max_hold_sessions = 20`;
- costs of 0 and 5 bp per side;
- **development** window 2021-06-01 to 2026-02-27;
- **holdout** window 2026-03-02 to 2026-08-14 (labels mature by the 2026-09-18 data
  cutoff). The protocol records the exact embargo: holdout decisions are at least
  `max_hold_sessions` sessions after the last development decision.

Artifact lifecycle follows `study_artifacts.execute_study`: `protocol.json`, then
`manifest.json` (hypothesis count, `authorizes_promotion: false`), then per-phase
results. Holdout labels are read **only after** the frozen model selection and
ranker artifact are saved and hashed. A development failure leaves the holdout
unexamined. Raw bars go under the private output directory, never in Git.

Predeclared hypotheses (the family size is recorded; Holm-adjusted p-values over the
family):

- **H0 (descriptive):** base rates by strategy/timeframe/direction — hit mix, mean R,
  R_cost.
- **H1:** Spearman(`setup_quality`, R) > 0.
- **H2a–H2h:** Spearman(each cross-sectional feature, R) > 0 with direction-aware
  sign.

  **Inference for H1–H2:** p-values and CIs come from a stationary **date-block
  bootstrap** (resample sessions, block length 10, 2,000 draws, fixed seed). Setups on
  the same date are not independent.
- **H3 (selection, the card-relevant test):** per session, choose the top-1 and top-2
  setups by a scorer and compare mean R_cost. Scorers:
  1. random (baseline);
  2. `setup_quality` (live);
  3. logistic regression (target hit before stop), standardized;
  4. ridge on R;
  5. sklearn `HistGradientBoostingRegressor` on R (small, fixed hyperparameters in the
     protocol; no tuning loop).

  Selection among 3–5 uses **purged walk-forward CV** on development: five
  expanding-window folds, embargo `max_hold_sessions`, fold metric = top-2 mean R_cost.
  The winner is the highest median fold metric. Then refit once on all development
  rows, save the artifact, and evaluate on the holdout.

**Acceptance (predeclared; the only path to a "ranker v1" recommendation):** on the
holdout, all of the following must hold:

- the frozen winner's top-2 mean R_cost exceeds `setup_quality`'s top-2 mean R_cost;
- the paired date-block bootstrap 90% CI lower bound on the difference is above 0;
- there are at least 60 holdout sessions with at least two setups;
- the winner's top-2 mean R_cost is above 0.

Otherwise the result is recorded and live ranking stays on `setup_quality`. H1 is
reported regardless; a negative or null H1 is itself operator-relevant.

The artifact is `ranker.json`, with model type, coefficients or the pickled HGB plus
its sha256, `features_version`, the training window and the protocol hash. It is
loaded only by explicit config path.

### Live shadow (WS2b)

- **RANK phase in `copilot.py`:** after `ranked` is built, compute `features.py` over
  the daily frames already fetched this scan (the whole scanned universe is the cross
  section). Attach `shadow_ranker = {features_version, features, score, ranker_sha}`
  to each approved candidate. `score` and `ranker_sha` are None unless
  `scan.shadow_ranker_artifact` names a file whose sha256 and `features_version` match.
  A mismatch logs a warning and records features only.
- **Sent cards:** `decision_provenance["shadow_ranker"]` holds that block.
- **Every scan with at least one approved candidate** appends **one** domain event
  `scan_candidates_ranked` through the existing `domain_events` journal API. The event
  contains the scan ID and time, budget and ranking key (`setup_quality`), and per
  candidate: contract, strategy, timeframe, direction, entry/stop/target, setup_quality,
  rank, the outcome (`sent` | runner-up reason | `rejected: …`) and the shadow block.
  - The event is written in its own transaction after the RANK/SEND loop. A journal
    failure is logged and counted in the scan summary. It never blocks cards or the
    budget.
  - Dry and NONE-budget scans do not journal.
- **`copilot cards outcomes [--days N]`:** read-only.
  1. Read the journaled events.
  2. Fetch 1h bars after each decision through the existing market-data path.
  3. Label with `labels.py`, marking immature labels as such.
  4. Print per-scorer top-1/top-2 selection stats (setup_quality vs shadow score vs
     random) and base rates.

  No orders, no Telegram.
- **Budget and latency:** feature computation is vectorized over at most about 159
  names and must add under 1s to a scan. It must not refetch data.

## Error handling

- **Study:** a provider/entitlement failure is charged and recorded per symbol. A
  symbol with incomplete history is excluded **before** any label is read, with the
  reason recorded. The study is not rerun with a different universe after results are
  seen.
- **Live:** any exception in feature/score computation or journaling is caught at that
  boundary, logged with `event=shadow_ranker_failed`, and leaves ranking and sending
  untouched.

## Testing

- **Labeler:**
  - target first / stop first / same-bar (stop wins) / gap through stop (R < −1);
  - gap at entry;
  - timeout exit;
  - short mirror;
  - cost application.
- **Replay parity:**
  - the frame at sampled instants equals a from-scratch build, including a partial 4h
    bucket;
  - no bar ending after `t` is visible (future-perturbation test: altering bars after
    `t` leaves the setup and features unchanged);
  - the dedup rule matches the live rule.
- **Features:**
  - prefix invariance (appending future sessions never changes the values at `t`);
  - direction-aware sign;
  - NaN on short history;
  - residual regression uses only prior sessions.
- **Study:**
  - holdout is not read before the artifact is saved (spy/fake loader asserts call
    order);
  - development failure leaves the holdout unexamined;
  - bootstrap determinism with a fixed seed;
  - purged folds respect the embargo.
- **Live:**
  - the RANK phase attaches the shadow block, and ranking order is unchanged by it;
  - a journal failure does not block a card;
  - an artifact hash/version mismatch records features only;
  - dry runs don't journal.
- **Outcomes report:** fake bars → labeled table; immature labels are flagged.

## Delivery

Two PRs in this workstream:

1. **PR-1:** `research/setups` (replay, labels, features, study) with the CLI. Then run
   the study on real data and write the result doc
   `docs/setup-outcomes-2026-09-XX.md` into the same PR, per the bundling rule.
2. **PR-2:** live shadow + journal + `cards outcomes`, then deploy and verify.

**Risks:**

- Current-universe survivorship. Mitigation: the claims are relative ranking within
  today's names, not absolute strategy profitability.
- Label optimism from hourly granularity. Mitigation: the stop-first same-bar rule.
- Possible Alpaca SIP historical rate limits. Mitigation: the existing pacer.
