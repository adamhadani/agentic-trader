# Honest card stream: shrunk card priors, a PEAD base rate, the raw EPS pair, no fabricated IV, a window ledger

Status: design, 2026-10-30. Direction 1 of the [October 30 alpha-directions program](../../alpha-directions-2026-10-30/README.md)
([report §3, direction 1](../../alpha-directions-2026-10-30/report.md#direction-1-an-honest-card-stream-now)).
The operator wrote on 2026-10-30: "take on highest priority work that doesn't require paid
subscriptions yet". Standing design trust applies ("go with your recommended approaches i trust your
judgement"), so every call below marked *(my call)* is the controller's.

## 1. Why

The north star is 1–2 reasonable suggestions per session. Since 2026-10-08 the desk has defined
"reasonable" as: an honest base rate, bracket EV ≥ 0, and a size consistent with the book. Four gaps
remain in what the cards and journals state:

1. **Card priors overstate a thin key.**
   - SQUEEZE_BREAKOUT 4h LONG's replay prior is +0.132R, from 114 setups over 93 sessions
     (se_session 0.138).
   - Each key's prior is estimated on its own, so this key is the winner's-curse survivor. Under
     `card_policy: enforce` it would be the one native key still sending cards.
   - The report's fix is an empirical-Bayes prior: shrink each key's replay mean toward the cross-key
     mean before it seeds the posterior.
2. **PEAD cards state no base rate.**
   - A PEAD catalog card shows the surprise, the reaction and the hold. It does not show what such
     events have earned against the desk's yardstick.
   - The v2 study's informative statistic is P2 (+0.055R over all reporters, t ≈ 2.66). The leg's
     absolute mean was +0.41R in 2024, +0.05R in 2025 and +0.01R in 2026 to July, while all reporters
     averaged +0.094R.
   - Recent excess is probably ≤ 0. Nobody has computed it in `net_excess_r_v1`.
3. **The live PEAD journal lacks the raw EPS pair.**
   - `pead_decision` journals the derived `surprise_pct`, not the EPS and the consensus it came from.
   - The Nasdaq page serves EPS "as served today". Only a journal written on the day records what was
     actually seen.
4. **GEX fabricates inputs, against contract 9.**
   - `options/gex.py` replaces a missing or invalid implied volatility with 0.20, and a missing expiry
     with 14 days.
   - The profile then reports gamma exposure for contracts whose gamma was never observable.

There is also a research-hygiene gap. No single document records which historical windows each lane
has read, so a new study cannot tell which windows are still fresh (program rule R1).

## 2. Goals, non-goals, anti-goals

**Goals**
- **G1. `card-stats-prior-v2`.** An empirical-Bayes shrinkage of v1's per-key replay means toward
  their cross-key mean. It is a new version and a new digest, it records v1's digest, and
  `card_policy` pins it. A native card's prior line shows both the shrunk mean and the raw replay mean
  whenever they differ.
- **G2. `pead-base-rate-v1`.** A frozen, digest-pinned file with the PEAD v2 LONG leg's
  `net_excess_r_v1`:
  - overall, by year, and over a recent window;
  - with P2 (the leg minus same-session all reporters) by year in the same unit;
  - with one shrunk "expected excess" figure.

  It is computed offline from the v2 study's frozen artifacts and cached bars, with no provider access.
- **G3. A base-rate block on every PEAD card,** on the Telegram card where the PEAD block already
  renders (the terminal card has no PEAD block today), plus the same block in the signal's decision
  provenance.
- **G4. The raw `eps`, `eps_forecast` and `n_estimates`** on every PEAD event dict, so the live
  `pead_decision` journal (and the study's events frame) carries the pair the surprise was computed
  from.
- **G5. GEX computes gamma only from observed IV and expiry.** A contract missing either contributes
  no gamma exposure, a quality note says so, and no default value enters any calculation.
- **G6. `docs/research-window-ledger.md`:** every historical window, each read of it (lane, cohort,
  date, result doc) and its status (fresh, spent, development only).

**Non-goals**
- *Superseded 2026-10-30: the operator chose enforce and it ships with this PR.* No `card_policy` mode change. Enforce versus preview is operator decision DP0 on 2026-11-06. This PR
  must not need a code change for either choice. Enforce is already a config edit; the
  preview-without-auto-entry option is not built *(my call: the report recommends enforce)*.
- No change to which cards are sent, ranked, sized, auto-entered or admitted. The PEAD block is
  evidence only and never a gate. Catalog cards stay tap-only (contract 1).
- No live PEAD record in `card_stats`. The probe's own outcomes stay in `cards outcomes` (`pead_probe`).
- No PEAD throughput or cap change. The report moves that after DP1.
- No schema or migration. No new journal stream.
- No re-estimation of the v1 replay frame. The v1 file's per-key moments are the inputs to v2.

**Anti-goals** (this PR would fail even if every test passed):
- A shrunk prior that hides its raw replay number from the card.
- A PEAD figure that reads as validated alpha, or that omits that EPS and consensus are "as served
  today".
- Any provider, network or database access in the base-rate script.
- A GEX profile whose total or regime still depends on a value nobody observed.

## 3. Design

### 3.1 `card-stats-prior-v2` (G1)

**Model.** Write each key's replay mean as `m_k`, with session-clustered standard error `s_k`. Let
`A = pooled_tau²`, the v1 file's pooled between-quarter variance (0.1643² = 0.0270). The between-key
spread is taken to be no larger than the between-quarter spread *(my call; the report specifies the
pooled τ)*. Then:

- the precision weight is `w_k = 1 / (A + s_k²)`;
- the cross-key mean is `μ = Σ w_k m_k / Σ w_k`;
- the shrinkage factor is `B_k = A / (A + s_k²)`;
- the shrunk mean is `m_k' = μ + B_k (m_k − μ)`.

**Only the mean changes.**
- `sd`, `se_session`, `tau`, `n_rows` and `n_sessions` carry over unchanged.
- `posterior()`'s weight `k = sd² / (tau² + se_session²)` is therefore unchanged, and the live record
  moves the posterior exactly as fast as it did under v1 *(my call: conservative toward the live
  record)*.
- A key the replay never saw keeps the skeptical zero-mean entry *(my call)*. `μ` is −0.008R, within
  0.01R of zero, so the render and its contract stay as they are.

**Expected values from v1's committed moments** (to be confirmed by the derivation):

| Key | v1 mean | se | B | v2 mean |
| --- | --- | --- | --- | --- |
| SQUEEZE_BREAKOUT 1h LONG | −0.0210 | 0.049 | 0.92 | ≈ −0.020 |
| SQUEEZE_BREAKOUT 4h LONG | +0.1315 | 0.138 | 0.59 | ≈ +0.074 |
| TREND_PULLBACK 4h LONG | −0.0784 | 0.025 | 0.98 | ≈ −0.077 |

**Representation.**
- `PriorEntry` gains `replay_mean` (the raw `m_k`) and `shrinkage` (`B_k`, in [0, 1]).
- `CardStatsPrior` gains `cross_key_mean` (`μ`) and `between_key_tau` (`√A`).
- `source` gains `shrunk_from` (`card-stats-prior-v1.json`) and `shrunk_from_sha256` (v1's digest,
  `6bab4e8d…`).
- `PRIOR_VERSION` becomes `card-stats-prior-v2`. `load_prior` refuses v1 (a version mismatch) and
  checks that every entry's `mean` equals `μ + shrinkage·(replay_mean − μ)` within 1e-12, so a
  hand-edited file is invalid.

**Derivation.**
- `scripts/card_stats_prior.py` gains a second mode: `shrink V1_JSON V1_SHA256 OUT_JSON`. It reads v1
  as plain JSON after checking its digest, keeps every v1 field, applies the shrinkage, writes v2 and
  prints the digest.
- The existing `STUDY_DIR CACHE OUT_JSON` mode applies the same shrinkage after estimating, so a future
  re-estimate also produces a v2-shaped file.
- One pure function does the shrinking for both modes.

**Config and runtime.**
- `card_policy.prior_path` points to `config/research/card-stats-prior-v2.json` (both the code default
  and `config/config.yaml`), and `prior_sha256` pins the new digest.
- v1 stays in the repo as the recorded old version.
- The daily snapshot records `prior_sha256` as it does today, so the change of prior shows in the
  journal.

**Display.**
- `card_stats` per-key stats and `CardEvidence` gain an optional `prior_replay_mean` (default None),
  so stored snapshots and notifications still validate.
- The prior line reads `replay 2021–26: +0.07R (shrunk from +0.13R) over 114 setups` when
  `|replay_mean − mean| ≥ 0.005R`; otherwise it reads as today.
- The skeptical line is unchanged.

**Effect.** Today no key has 20 matured live cards except perhaps TREND_PULLBACK 4h, so `card_policy`
preview flags nothing new from the shift. TREND_PULLBACK's posterior moves by about 0.001R and SB 4h's
card headline falls from about +0.13R to about +0.07R.

### 3.2 `pead-base-rate-v1` (G2)

**Inputs** (offline, read-only; every input is digest-checked or recorded):
- `~/agentic-trader-research/apriori-pead-v2-20260926/`: `events.csv.gz`, `labels.csv.gz`,
  `manifest.json` (entry sha256 `fc8a5b3e…`) and `protocol.json`.
- The PEAD study cache `apriori-pead-v1-cache/bars`: hourly and daily bars per symbol, plus SPY daily.
- SPY hourly from `setup-cache-2017` and `setup-cache-v1`, concatenated and de-duplicated.
- The frozen entry `config/research/apriori/pead-v2.json`.

**Per event** (direction LONG, every event, leg or not, because P2 needs all reporters):
1. Re-label with the study's own `decision_price`, `levels_for` and `label_bracket` at the decisive
   5 bp cost (the entry's `decision_cost_bps`), keeping `entry_price`, `stop`, `entry_time` and `exit_time`.
2. **Rehearsal check (R2).** Assert that the re-labelled `r` and `hit` reproduce the study's
   `labels.csv.gz` for every (symbol, session, LONG) row, with `r` within 1e-9 and identical counts. If
   anything differs, the script refuses to write.
3. Add `beta` (`trailing_beta` on the symbol's daily bars against SPY daily, 120 sessions before the
   session) and the cost inputs (`cost_inputs`, `name_cost_bps`) from the daily bars.
4. Add `market_r` (`outcomes.market_r` on SPY hourly over the event's own entry → exit) and
   `net_excess_r` (`net_excess_r`).
5. Rows missing beta or market bars are counted as `n_no_beta` and `n_no_market` and excluded. They
   are never filled.

**Statistics.**
- **Overall and by year,** for the leg (`is_leg`) and for all reporters: `n`, mean `net_excess_r`, and
  the session-clustered se.
- **P2 by year:** the mean over leg events of `net_excess_r` minus the same-session all-reporter mean,
  a within-session difference, unlike v2's pooled P2 (a difference of means), with its session-clustered se.
- **Recent window:** sessions from 2025-01-01 to the study's last decision (2026-07-31), the most
  recent measured window (R5).
- **τ:** the method-of-moments between-quarter sd of the leg's `net_excess_r`, using `card_prior._tau`
  with the same floor and the same 20-row quarter minimum.
- **Expected excess (the card figure):** the recent-window leg mean shrunk toward 0, `B = τ² / (τ² +
  se_recent²)`, giving mean `B · m_recent` and se `√B · se_recent` *(my call: R5's "shrunk toward 0";
  the PEAD leg's own between-quarter τ stands in for the a priori family's spread, which has no other
  member measured in this unit)*.
- **Pass rule:** none. This is descriptive and decides nothing.

**Output.** `config/research/pead-base-rate-v1.json` contains:
- the version and unit;
- the source digests: entry, events, labels and the bar-cache manifest summary;
- the cost-model constants, `r_clip` and the beta window, checked against the code like the card prior;
- the counts, the by-year table, overall, recent, τ, the expected excess, and the code revision.

A results doc, `docs/pead-base-rate-2026-10-30.md`, gives the tables and states the caveats:
- EPS and consensus are as served today, not point-in-time;
- cohorts are survivorship-biased (current Alpaca listings);
- the 2016–2026 window was already read by v1 and v2;
- the figure is descriptive, not validated alpha.

### 3.3 PEAD card block (G3) and raw EPS pair (G4)

**Config.**
- New `apriori.pead_base_rate_path` (default `config/research/pead-base-rate-v1.json`) and
  `apriori.pead_base_rate_sha256` (default None; `config/config.yaml` pins it).
- A missing digest, a mismatch or an invalid file makes the block `unavailable`. It never blocks a
  card.

**Loading.** One loader, `load_pead_base_rate(path, sha256) -> PeadBaseRate`, which raises
`BaseRateUnavailable` with a fixed message, mirroring `load_prior`.

**On the card.** The PEAD catalog source holds the loaded block and attaches it as `base_rate` to the
drift card's facts (`notification["drift"]`). The Telegram card renders it under the PEAD block:

```
• Expected excess (PEAD v2, LONG): −0.03R ± 0.05 vs beta-matched SPY after cost
  replay 2016–26: +0.05R over 2,104 events · 2025–26: −0.04R over 412 (shrunk toward 0) · beat vs all reporters 2025–26: +0.01R
Not validated alpha. PEAD v2 events replayed on SIP bars in the native card unit; EPS and consensus as served today, not point-in-time.
```

The numbers above are illustrative. An unavailable file renders one line: `• PEAD base rate: unavailable`.

**In provenance.** A catalog-source hook, `card_provenance(candidate, preparation) -> dict` (default
`{}`), lets the PEAD source add `decision_provenance["pead_base_rate"]`: the same block plus the
file's digest, so `cards outcomes` and later reviews can see which figure each card carried.

**Raw EPS pair.**
- `build_events` adds `eps`, `eps_forecast` and `n_estimates` (each the `CalendarRow` value, or None)
  to every event it emits, and `EVENT_COLUMNS` lists them.
- The live path already journals each event dict in `pead_decision.events`, so the pair reaches the
  journal with no other change.
- The frozen v2 study's artifacts are not rewritten.

### 3.4 GEX without fabricated inputs (G5)

**Normalisation.**
- `normalize_option_chain` keeps missing or invalid `impliedVolatility` and `dte_years` as NaN, with
  no default.
- `strike`, `openInterest` and `volume` keep their current normalisation: a missing count is 0, and
  a non-positive strike drops the row. Those are counts and identity, not model inputs.

**Calculation.**
- A row with a NaN IV or expiry contributes 0 gamma exposure. Its OI and volume still count toward
  walls and totals, because they were observed.
- One quality note per side and field records the exclusion, for example
  `calls: 12 contracts without impliedVolatility excluded from gamma exposure`.
- If no contract on either side has both IV and expiry, `calculate` raises `ValueError` with a fixed
  message, as it does for a bad spot, so no profile or regime is produced *(my call; callers already
  handle `ValueError`, which the plan verifies)*.

**Constants and docs.**
- `black_scholes_gamma` takes `implied_volatility` without a default.
- `DEFAULT_IMPLIED_VOLATILITY` and `DEFAULT_TIME_TO_EXPIRY_DAYS` are deleted from `constants.py`. No
  other user exists.
- The docstring stops promising a 0.20 default.

### 3.5 Window × read ledger (G6)

`docs/research-window-ledger.md` holds:
- one row per window: the pooled cube's discovery/selection and confirmation slices, the native setup
  frames (2017–21, 2021–26, 2026-03 → 08), the a priori event windows, the 2007–2016 Sharadar era and
  the prospective window from 2026-08-01;
- the reads of each window, each with its lane, cohort, date and result-doc link, plus the window's
  status;
- the α declared per lane (R1).

Every row cites a result doc already in `docs/`. The ledger states only what those docs record. Future
studies add a row before they read.

### 3.6 Docs and contracts

- [card evidence](../../card-evidence.md) gets the v2 prior and the shrunk-from line.
- [card-stats prior](../../card-stats-prior-2026-10-28.md) gets a "v2 (2026-10-30)" section with the
  derivation and the table.
- [apriori-alphas](../../apriori-alphas.md) gets the PEAD card block.
- `CLAUDE.md` contract 8 notes the empirical-Bayes prior beside `net_excess_r_v1`. Contract 9 notes
  that GEX excludes contracts without observed IV or expiry.
- [The roadmap](../../alpha-roadmap.md) gets the direction-1 status. The review README gets the status.

## 4. Constraints

- **Repo rules:** Python 3.14, `uv`, TDD, `uv run pre-commit run --all-files`. The full affected tests
  run on SQLite. No new migration (head stays `008`).
- **No provider, network or database access** in either script. Tests keep the blocked-network
  guards.
- **Type names only** in readiness details, Telegram text and journal reasons. A loader failure
  message is a fixed literal, never a path or file content.
- **Old payloads still validate.** Every new field on a stored model is optional with a None default,
  so stored snapshots, notifications and provenance from before this PR still validate and render.
- **Runs and restarts** avoid 10:20–10:50 and 14:20–14:50 New York. The base-rate script is offline and
  CPU-only.
- **Deploy** through the controlled restart. Verify that the first post-deploy `card_stats` snapshot
  records the v2 digest, and that a PEAD card (if one is sent before the season ends, about 11-09)
  renders the block.

## 5. What is left to the builder

- Exact module placement of the PEAD base-rate model and loader. A small module beside `card_prior`
  under `research/` is expected.
- The test fixtures' shapes.
- The wording of quality notes, within the pattern above.
