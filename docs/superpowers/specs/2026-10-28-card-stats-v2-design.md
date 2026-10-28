# card_stats v2: an honest expected-excess figure on every native card

Status: design, 2026-10-28. Holistic review item 1.2 (RM-R1, decision D3: "beta-adjusted excess R with
per-name cost; threshold set at 1.2 preview time"). Operator design trust ("go with your recommended
approaches").

## 1. Why

- Every native card carries a "Measured record" from the daily `card_stats` snapshot. Today that
  record is wrong in four ways (review §2.2 and §3.5, verified at 0c02a97):
  1. **Censored.** `compute_card_stats` drops immature rows (`card_stats.py` `_key_stats`). On a 2R
     bracket, stops resolve fast and targets slowly, so a young window over-weights whichever resolves
     first. The TREND_PULLBACK LONG figure swung from −0.38R (10-08) to +0.54R (10-21). It is +0.35R
     today on 80 rows.
  2. **Duplicated.** The same setup is journaled by the 10:35 and 14:35 scans, and by the swing job
     (`trigger` = `swing_scan`). Today's 90 labelled rows are 70 distinct suggestion-scan setups.
  3. **Raw R.** The unit is `mean_r_cost`, which carries the market's drift. The desk's mandate (D3)
     is excess over the beta-matched market, after cost.
  4. **Keyed by (strategy, direction)**, not timeframe.
- `card_policy` would withhold on that number. It ships `off`, so nothing has been withheld on it.
- Goal (roadmap row 1.2): "an honest base rate on every card; a safe withhold path". The desk's
  definition of a reasonable suggestion (2026-10-08) includes "bracket EV ≥ 0".

## 2. Goals, non-goals, anti-goals

**Goals**
- One unit, `net_excess_r` (version `net_excess_r_v1`), computed by one pure implementation. It is
  shared by the live labeller, `card_stats`, the replay prior builder and `cards outcomes`.
- The live record is time-matured, de-duplicated, suggestion-scan only and keyed
  (strategy, timeframe, direction).
- A frozen, digest-pinned replay prior per key, and a posterior that shrinks the live record toward it.
  Every native card shows the posterior, even before the live record reaches the floor.
- `card_policy` decides on the posterior and ships `preview` at threshold 0.0.

**Non-goals**
- No ranking change. The L3 selector (SG-R1) is a later item that will consume this unit.
- No change to risk, sizing, admission or the broker.
- No confidence sequences: preview never withholds. They are needed before `enforce` reads daily peeks
  (review RM-R1, last bullet). Recorded as a follow-up.
- No new schema or migration.

**Anti-goals**
- A number that looks precise but is driven by estimator noise (see §4.2).
- A withhold path that fires on partial, stale or missing evidence. This rule is unchanged.
- Any change to the frozen research decisions of record.

## 3. Current facts (2026-10-28, read-only)

- The journal has universe scans from 2026-09-24. 20 are `suggestion_scan` and 7 are `swing_scan`.
  There are 79 suggestion-scan candidates: TREND_PULLBACK 4h LONG 75, SQUEEZE_BREAKOUT 4h LONG 4. They
  de-duplicate to 70 rows over 18 sessions.
- About 17 of those rows are at least 21 sessions old today. The 20-row floor is reached within about a
  week.
- The live feed is IEX (`market_data.alpaca_feed`). The replay is SIP all-adjusted.
- Frozen replay frame: `~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz`.
  - Arm `A0_live` is "byte-identical to the live labeller".
  - Window: decisions 2021-06-01 → 2026-01-29.
  - Keys: TREND_PULLBACK 4h LONG (7,389 rows ex BIL/SHY), SQUEEZE_BREAKOUT 1h LONG (1,155),
    SQUEEZE_BREAKOUT 4h LONG (122).
  - The adjusted daily bars of every member, and SPY, are cached under
    `~/agentic-trader-research/setup-cache-v1/{SYM}_1d/`.
- Spike, beta-one stand-in for the unit: TREND_PULLBACK 4h LONG mean −0.112R, sd 1.50. Its quarterly
  true-mean sd τ ≈ 0.26R, which gives a prior weight of about 34 rows. The 2026-10-23 erratum puts the
  past-only-beta figure near −0.05R.

## 4. Decisions

### 4.1 The unit

For one labelled row:

```
cost_r        = 2 · c_i / 1e4 · fill / risk_unit                 (c_i = per-side cost in bp, §4.2)
r_net         = clip(r − cost_r, ±10)                            (r = the labeller's uncosted R)
net_excess_r  = clip(r_net − β_i · market_r, ±10)
```

`fill` (the entry bar's open) and `risk_unit` (|planned entry − planned stop|) are the labeller's own
(`labels.py` `_resolved`), so with `c_i` equal to the flat 5 bp, `r − cost_r` equals the existing
`r_cost` exactly. Within ±10R, `net_excess_r` then equals the existing unclipped `beta_excess_r`.

- `β_i` is the existing past-only 120-session `trailing_beta`, frozen at the decision.
- `market_r` is the existing SPY open-to-close control over the row's own holding window, in its R
  units.
- The ±10R clip is the bracket counterfactual's winsorisation, used for statistics.
- A row without `β_i` (`beta_reason` set), without `market_r`, or unresolved has no `net_excess_r`.
- Pure functions in `agentic_trader/research/setups/net_excess.py`. The labeller (`outcomes._row`)
  adds three columns beside the existing `beta_excess_r`: `name_cost_bps`, `cost_reason` and
  `net_excess_r`. They come from the same adjusted daily bars `with_beta` fetches.
- **The daily bars come from SIP historical in every caller.** This covers the CLI report, the daemon
  worker and the prior builder. A separate daily source is built with `feed="sip"`; the hourly
  labelling keeps the configured feed.
  - Every daily bar used ends before the decision session, so it is historical. Historical SIP is
    usable on the desk's keys (`docs/research-data-sources.md`).
  - IEX volume is a few percent of consolidated volume, so an IEX ADV would inflate the impact term
    several-fold against the SIP-built prior. It is also the more honest beta input.
- **The daemon now makes these requests.** Today it labels with `with_beta=False`. v2 adds one SIP
  daily request per candidate symbol, one for SPY, and the SPY hourly tape. That is about 60–80
  requests at most, once a day at 08:30, paced by `max_requests_per_minute`.

### 4.2 Per-name cost: square-root impact over the protocol floor

```
c_i = floor_bps + Y · σ_i · sqrt(N_i / ADV_i) · 1e4      bp per side, capped at cap_bps
N_i = reference_risk_usd · fill / risk_unit               the card's notional at the reference risk
```

- `σ_i` is the std of the daily log returns over the 20 sessions before the decision session.
- `ADV_i` is the median daily dollar volume (close × volume) over the same 20 sessions. Only past bars
  are used.
- Parameters, frozen in the prior file (§4.4):
  - `floor_bps = 5.0`: the frozen setup-outcomes protocol cost;
  - `Y = 1.0`: the square-root impact law (Tóth et al. 2011; Almgren et al. 2005);
  - `reference_risk_usd = 1000`: a Max-tier card, 1% of a $100k book;
  - `cap_bps = 200`.
- When σ or ADV is missing (fewer than 15 valid sessions), `c_i = floor_bps` and the row records
  `cost_reason` = `short_history`. Cost never drops below the floor.
- **Ruling, against the review's suggestion.** The review proposed Abdi–Ranaldo (2017) or
  Corwin–Schultz (2012) spread estimates from daily bars. A spike on the cached bars (2026-10-28, 120-
  and 21-session windows) gave:
  - SPY 16–26 bp;
  - MSFT 37–59 bp;
  - NVDA 49–58 bp;
  - AAPL 0–21 bp;
  - zero in 15–77% of windows.

  True spreads on these names are 0.3–3 bp. On this liquid universe, daily high-low estimators are
  noise. The cost would have been dominated by estimator error, which is the first anti-goal.
- **Ruling, against quoted spreads.** The live feed is IEX, whose top of book is not the NBBO. The
  replay has no quotes.
- **Consequence, stated honestly.** On SIP volume at today's notionals, the impact term is below 1 bp
  for most liquid names, so `c_i` is close to the 5 bp floor. It rises for thin names and tight stops.
  The prior doc records the distribution on the replay frame, and the snapshot's `mean_name_cost_bps`
  records it on live rows.

### 4.3 The live record

`card_stats` v2 builds its rows from `label_journaled` output:

- **Population.**
  - `scan_candidates_ranked` events with `scope == "universe"` and `trigger == ScanTrigger.SUGGESTION_SCAN`.
  - Candidates on `scan.broad_index_etfs` are excluded: they are never carded.
  - **Malformed candidates** are dropped before labelling and counted as `n_malformed`, which is
    displayed but does not make the key partial. These are candidates with no direction, no finite
    entry or stop, or `entry == stop`. Without this, the labeller would return them as permanently
    `immature`, and one bad journal row would keep its key unwithholdable for the whole window.
  - The labeller runs with `with_beta=True`, the SIP daily source (§4.1) and the SPY hourly tape.
  - A failed SPY hourly or SPY daily read raises `CardStatsUnavailable`; nothing is written.
  - **A failed per-symbol daily read** (`beta_reason` starting `fetch_failed`) counts that symbol's
    rows as `n_fetch_failed`: the key is partial. It is never `n_no_beta`, and its cost never falls
    back to the floor silently.
- **De-duplication.** One row per (contract, strategy, timeframe, direction, New York decision
  session). The earliest decision in the session is kept, so 10:35 wins over 14:35. This happens
  before labelling, so the duplicate costs no requests.
- **Maturity.** A row is eligible only when at least `max_hold_sessions + 1` (21) complete exchange
  sessions separate its decision session from the snapshot's New York date.
  - Sessions are counted with the deterministic `MarketHolidayCalendar` (weekdays that are not equity
    holidays).
  - This removes censoring exactly, with no survival model. Every eligible label has had its full
    20-session horizon, plus the following session's bar that resolves a timeout.
- **Counts per key:**
  - `n_mature`: eligible rows with a `net_excess_r`;
  - `n_young`: not yet eligible, excluded from every statistic;
  - `n_unresolved`: eligible but still `immature`, a data gap;
  - `n_fetch_failed`: hourly or daily;
  - `n_malformed`;
  - `n_no_beta`: eligible and resolved, but `beta_reason` is `no_daily_bars` or `short_history`, or
    `market_r` is None.
- **Partial key.** A key with any `n_fetch_failed` or `n_unresolved` is partial: shown, but never
  withheld. `n_no_beta` rows are excluded and displayed, but do not make the key partial, because a
  recently listed name must not block its key forever.
- **Window.** `card_policy.stats_window_days` goes from 90 to 180 (the cap stays 365). The 21-session
  maturity uses up a month of any window.

### 4.4 The replay prior

- `config/research/card-stats-prior-v1.json` is frozen. `card_policy.prior_path` names it and
  `card_policy.prior_sha256` pins it. Config validates only the hex pattern, as
  `risk.macro_calendar_sha256` does.
  - The **worker** loads and verifies the file on each labelling run. A missing, unreadable, invalid
    or digest-mismatched prior raises `CardStatsUnavailable` with a type name only. Nothing is written,
    `card_stats` readiness degrades and the incident monitor alerts. The previous snapshot stays
    authoritative until it ages out to `stale`, and stale evidence never withholds.
  - Cards and `decide` read only the snapshot, which carries the prior figures it used. Copilot
    construction and test fixtures never load the file.
  - The prior is needed in every mode, because every native card shows the posterior.
- It holds:
  - the unit version;
  - the cost parameters (§4.2);
  - the beta window;
  - the clip;
  - the source frame's SHA-256 and protocol id;
  - the decision window;
  - the excluded symbols: BIL, SHY and the 23 `scan.broad_index_etfs` at build time. With these out,
    the keys hold about 7,049 TREND_PULLBACK 4h LONG rows, 1,087 SQUEEZE_BREAKOUT 1h LONG and 114
    SQUEEZE_BREAKOUT 4h LONG, per the builder check;
  - one entry per key: `n_rows`, `n_sessions`, `mean`, `sd`, `se_session`, `tau`.
- Built once by `scripts/card_stats_prior.py` (research-only, offline) from the frozen frame (arm
  `A0_live`) and the cached daily bars, through the §4.1 and §4.2 functions:
  - `se_session` is the std of per-session means over √(sessions);
  - `tau` is the method-of-moments between-quarter sd of the true mean: var(quarterly means) − mean(var/n),
    floored at 0, over quarters with ≥ 20 rows;
  - a key with fewer than 8 such quarters takes the pooled all-key `tau`.
- **A key with no prior entry** (a new strategy, a SHORT key) uses a skeptical prior: mean 0, the
  pooled `tau` and the pooled `sd`. The card says "no replay prior".
- The live record is never part of the prior. Re-estimating the prior is a new file version and a new
  digest.

### 4.5 The posterior

Normal–normal, per key:

```
v0        = tau² + se_session²                      prior variance of the live mean
k         = sd² / v0                                 prior weight in rows
w         = n / (n + k)                              n = n_mature, x̄ = live mean net_excess_r
posterior = w · x̄ + (1 − w) · mean
post_se   = sqrt(1 / (n / sd² + 1 / v0))
```

- `sd` is the prior's row sd, not the live sample's. With n < 30 the live sd is too noisy.
- With n = 0 the posterior is the prior.
- Rows in one session are treated as independent. The beta adjustment removes most of the common
  market move; this is documented as an accepted limit.

### 4.6 Snapshot schema

`CardStatsSnapshot` gains:
- `unit` (`net_excess_r_v1`);
- `prior_sha256`;
- `cohort_end` (the latest eligible decision session).

`CardKeyStats` gains:
- `timeframe`;
- `n_young`, `n_unresolved`, `n_no_beta`;
- `live_mean`, `live_se`;
- `prior_mean`, `prior_se` (√v0), `prior_weight` (k), `prior_source` (`replay` | `skeptical`);
- `posterior_mean`, `posterior_se`;
- `mean_name_cost_bps`.

Other rules:
- The existing fields stay and are computed on the matured, de-duplicated cohort: `n_mature`, the
  rates, `mean_r_cost`, holding and decided-at. `n_immature` becomes `n_young + n_unresolved`.
- **Rollback:** the old reader validates the payload and ignores the new keys. Its `stats(strategy,
  direction)` takes the first timeframe; this degraded read is accepted.
  - `CardPolicyConfig` is `extra: forbid`, so old code refuses a config with `prior_path` or
    `prior_sha256`. A rollback reverts code and `config/config.yaml` together, which a git revert of
    the PR does.
- **Keys:** the union of live keys and prior keys, so every prior key has a row, even with n = 0. The
  aggregate `*` row is kept, with no prior or posterior.
- `stats(strategy, timeframe, direction)`.

### 4.7 The card

`CardEvidence` gains `timeframe` and the §4.6 key fields. `lookup` takes `timeframe`.

**Statuses:**
- `measured`: n ≥ floor;
- `insufficient`: below the floor. The prior and posterior are still shown;
- `stale` and `unavailable` are unchanged.

**Lines**, which the HTML and terminal cards share:

```
• Expected excess (TREND_PULLBACK 4h, LONG): −0.06R ± 0.10 vs beta-matched SPY after cost
  replay 2021–26: −0.05R over 7,049 setups · live: −0.12R over 23 mature since 2026-09-24 (43% target / 52% stop / 4% timeout)
```

- Below the floor, the live part reads `live: 17/20 mature, not yet decisive`.
- A skeptical prior reads `no replay prior (shrunk toward 0)`.
- The old "Implied EV at R:R" line is dropped. It was raw R from censored rates.
- **Caveat line:** "Not validated alpha. Replay prior (SIP 2021–26) and live record (IEX) on
  journaled candidates' deterministic brackets; mature = 21+ sessions old; per-name cost ≥ 5 bp/side."
- Catalog, alpha and probe cards are unchanged and exempt.

### 4.8 card_policy

- `decide` reads `evidence.posterior_mean`.
- `would_withhold` holds when all of these are true:
  - mode ≠ off;
  - status `measured` (n ≥ `min_mature_cards`);
  - the key is complete (no fetch failure, nothing unresolved);
  - `posterior_mean < min_measured_ev`.
- The config field names stay: `min_measured_ev` is now documented in R of `net_excess_r_v1`. The
  journal block and the scan-level `card_policy` block gain `"unit": "net_excess_r_v1"`, which marks
  old and new blocks apart.
- The reason reads `card policy: expected excess −0.06R over 23 < +0.00R`.
- **Shipped config:** `mode: preview`, `min_measured_ev: 0.0`, `prior_path`, `prior_sha256`.
- **Consequence, stated for the operator.** With the TREND_PULLBACK 4h LONG prior near −0.05R, preview
  will flag most native cards as `would_withhold` once the live floor is met, unless the live record is
  positive enough to lift the posterior. Under preview, cards are still sent. That journaled contrast
  tests whether the replay prior describes live, gated cards. `enforce` remains a later operator
  decision.
- **No validator refuses `enforce`.** It stays the operator's switch, as today. The docs say that a
  daily-peeking enforce needs the always-valid confidence sequence in §5 first.

### 4.9 cards outcomes

- The CLI report's per-row frame gains `name_cost_bps` and `net_excess_r`.
- `exposure` gains `mean_net_excess_r` and `n_net_excess`.
- Descriptive only, as today.

## 5. Out of scope and follow-ups

- Always-valid confidence sequences before any `enforce` (Howard et al. 2021).
- The L3 `CardSelector` on this unit (SG-R1).
- The shadow allocator's μ from the posterior (SZ-R4).
- Per-session clustering in `post_se`.
- Quoted-spread costs once an NBBO feed exists.

## 6. Tests (shape; the plan pins values)

- **Unit:**
  - the cost formula, floor, cap and short history;
  - `net_excess_r` with clip, missing beta and missing market control;
  - with `c_i` = 5 bp and values within ±10R, `r − cost_r == r_cost` and `net_excess_r == beta_excess_r`.
- **Population:** swing rows are excluded; broad-index ETFs are excluded; 10:35 and 14:35 duplicates
  keep 10:35; malformed rows land in `n_malformed` and do not make the key partial; a per-symbol daily
  fetch failure lands in `n_fetch_failed` and makes the key partial.
- **Prior loading:** a missing file, or one with a mismatched digest, raises `CardStatsUnavailable` and
  writes nothing.
- **Maturity:** a row exactly at 21 sessions is in, at 20 it is out, and a holiday is not counted. A
  young row that resolved by stop is still excluded. That last case is the censoring regression.
- **Prior:**
  - the file's digest is checked;
  - the posterior equals the prior at n = 0;
  - the weights follow the formula;
  - a skeptical prior is used for an unknown key.
- **Snapshot:** every prior key has a row; the old reader parses a v2 payload.
- **Card:** all three line variants; the HTML is escaped.
- **Policy:** it withholds only on measured and complete evidence below the threshold; never on
  partial, insufficient or stale evidence; the unit is in the journal block.
- **Prior builder:** a synthetic frame and bars reproduce the hand-computed mean, se and tau.

## 7. Docs

- `docs/card-evidence.md`: the snapshot, unit, prior, card and policy sections.
- CLAUDE.md, contract 8: the `card_policy` sentence and the `cards outcomes` exposure clause.
- `docs/alpha-roadmap.md`: item 1.2 done.
- `docs/holistic-review-2026-10-21/README.md`: D3 status.
- The prior's provenance goes in `docs/card-stats-prior-2026-10-28.md` (built in this PR).
