# Pooled literature entries: 52-week high and low-MAX reversal — September 30, 2026

**Result: BOTH ENTRIES FAILED, on P2 (the paired edge) and P3 (robustness).**

- **`high52-v1`** (nearest the 52-week high, long top 3 per session): **+0.162R per
  trade after 5 bps per side** (ci90 [+0.089, +0.236], 7,220 picks). It beat the control
  (the same bracket on every eligible name that session) by only **+0.012R**
  (ci90 [-0.011, +0.036], t 0.81, one-sided p 0.220, 2,407 sessions). The edge since
  2023-01-01 is -0.007R.
- **`reversal-lowmax-v1`** (one-month losers among low-MAX names, long top 3 per
  session): **+0.153R** (ci90 [+0.082, +0.226], 7,397 picks). Paired edge **+0.008R**
  (ci90 [-0.016, +0.032], t 0.52, p 0.292, 2,501 sessions). The edge since 2023-01-01 is
  -0.021R.
- **Plain reading:** the picks earned about +0.15R per trade, but every eligible name
  earned about the same on the same days (+0.150R and +0.146R for the two controls), so
  the signal-specific edge is indistinguishable from zero, about +0.01R. P1 passing
  alone would have been misleading.
- Nothing trades. There is no Part 2 probe for either entry. The study grants no
  registry, shadow or promotion credit (`authorizes_promotion: false`).

## Protocol

- **Frozen entries** (committed before any data was read), with the
  [pooled lane contract](alpha-pooled-mining.md#literature-entries):

  | Entry | Formula | SHA-256 |
  | --- | --- | --- |
  | `high52-v1` | `close / ts_max(high, 252)`, no filter | `9c3369278cb667db6016e93453cea7a55231234362718b3643d7bcce31626930` |
  | `reversal-lowmax-v1` | `-1.0 * roc(close, 21)`, filter `ts_max(returns, 21)` at or below the cross-sectional median (`max_quantile` 0.5) | `da39ef0b49e2af96ebbc328bbc9c59fc0802a449bc94e456b4f46b3fb9c18967` |

  References: George & Hwang (2004) for `high52`; Jegadeesh (1990) and Bali, Cakici &
  Whitelaw (2011) for `reversal-lowmax`.
- **Selection:** k = 3 names per session, long only. On each session, rank eligible
  names by score, skip names still held, and break ties by a hash.
- **Window:** decisions 2016-08-01 to 2026-07-31 (bars through 2026-09-01); "recent"
  means decisions from 2023-01-01.
- **Trade:** decision at 10:35 New York; stop 2 x ATR14, target 3R, time exit at the
  close of the 20th session; conservative same-bar tie (stop) and gap fills at the open.
  5 bps per side decides; 0 bps is reported alongside.
- **Statistics:** a stationary bootstrap over whole sessions, block mean 20, 2,000 draws.
  ci90 is the 5th-95th percentile interval; a criterion holds when the point estimate is
  positive and the lower bound is above zero. The paired edge is, per session with a
  pick, the picks' mean R minus the mean R of all labelled eligible cells that session,
  averaged over those sessions.
- **Pass rule (all must hold):** P1 leg mean > 0 with ci lower bound > 0; P2 paired edge
  > 0 with ci lower bound > 0; P3 1%-trimmed leg mean > 0 and paired edge since
  2023-01-01 > 0; P4 at least 500 sessions with a pick, at least 150 since 2023-01-01.
- **Run:** `copilot alpha pooled study ENTRY --power ~/agentic-trader-research/pooled-power-a-20260930-run2 ...`
  on the cube that [power check A](alpha-pooled-power-2026-09-30.md) built and passed.
  Timings (UTC / New York): `high52-v1` 17:20:03-17:20:16 (13:20:03-13:20:16 EDT);
  `reversal-lowmax-v1` 17:20:16-17:20:27 (13:20:16-13:20:27 EDT). Both exited 0, as a
  completed study whose entry failed its rule does. Code revision
  `research/prospective-equity-panel-iex-v1-20260918-55-g202274c`.

## Scale

- **Cube** (SHA-256 `f5ed8989…9aad`, shared with power check A; see its
  [coverage](alpha-pooled-power-2026-09-30.md#cube-coverage)): 2,514 sessions, 214,308
  eligible cells, about 85 eligible names per session.
- **Picks kept:** `high52` 7,220 over 2,407 sessions (no score before the 252-session
  lookback is available, so none in 2016); `reversal-lowmax` 7,397 over 2,501 sessions.
  One pick per entry was dropped as unlabelled; none was purged.
- **Picks per session:** 3.00 and 2.96 on average.
- **Distinct symbols picked:** 117 and 105.

## Criteria

| Entry | P1 leg mean R (ci90, n) | P2 paired edge (ci90; t; one-sided p; sessions) | P3 trimmed / recent edge | P4 sessions / recent | Result |
| --- | --- | --- | --- | --- | --- |
| `high52-v1` | **+0.162** (+0.089, +0.236), 7,220; holds | **+0.012** (-0.011, +0.036); t 0.81; p 0.220; 2,407; fails | +0.147 / **-0.007**; fails | 2,407 / 897; holds | **failed** |
| `reversal-lowmax-v1` | **+0.153** (+0.082, +0.226), 7,397; holds | **+0.008** (-0.016, +0.032); t 0.52; p 0.292; 2,501; fails | +0.144 / **-0.021**; fails | 2,501 / 894; holds | **failed** |

P1 (t 3.67 and 3.49) and P4 hold and the trimmed means are positive; P2 fails on the
lower bound and P3 fails on the recent edge. Each entry is a single test.

### At 0 bps (`gross`)

| Entry | Leg mean (ci90) | t | Paired edge (ci90) | t | p |
| --- | --- | ---: | --- | ---: | ---: |
| `high52-v1` | +0.188 (+0.115, +0.262) | 4.24 | +0.014 (-0.009, +0.038) | 0.95 | 0.183 |
| `reversal-lowmax-v1` | +0.180 (+0.108, +0.253) | 4.06 | +0.010 (-0.014, +0.035) | 0.66 | 0.292 |

Costs reduce the leg mean by about 0.026R and the paired edge by about 0.002R. They do
not explain the failure.

## Cross-checks (reported, never decisive)

| Entry | Two-way clustered (date and symbol) t | Calendar-time Newey-West t | Design effect | Intra-session correlation | Effective n |
| --- | ---: | ---: | ---: | ---: | ---: |
| `high52-v1` | 0.76 (mean +0.0118, se 0.0156) | 0.55 (se 0.0011, 2,407 sessions) | 1.097 | 0.049 | 6,579 |
| `reversal-lowmax-v1` | 0.44 (mean +0.0075, se 0.0170) | 0.56 (se 0.0010, 2,514 sessions) | 1.000 | 0.000 | 7,397 |

- No cross-check finds an edge: every t is below 1.
- The two-way clustered standard error (0.0156 and 0.0170) sits above the bootstrap's
  (0.0149 and 0.0146). That is expected: the clustered estimator treats the control as
  fixed, while the paired bootstrap resamples each session once for both the leg and its
  control, which removes their common noise.
- The calendar-time mean is per-session portfolio residual spread over each pick's
  holding sessions, on a different scale from the per-pick means; only its t is
  comparable.
- The contract calls this calendar series an approximation: the cube stores final R, not
  a daily path.

## Diagnostics (descriptive, not used for the decision)

| | `high52-v1` | `reversal-lowmax-v1` |
| --- | --- | --- |
| Exits (stop / target / time) | 3,363 / 619 / 3,238 (46.6% / 8.6% / 44.8%) | 3,386 / 621 / 3,390 (45.8% / 8.4% / 45.8%) |
| Control mean R (about) | +0.150 | +0.146 |
| Residual by liquidity tercile (low / mid / high) | +0.047 / -0.034 / +0.022 | +0.001 / -0.004 / +0.025 |
| Picks by source: scan-universe only / both / snapshot only | 5,585 / 1,592 / 43 | 5,615 / 1,748 / 34 |

The control mean is the picks' control average taken from `picks.csv.gz`. The
paired-edge difference (+0.012 and +0.008) is small next to it. Tercile residuals are all within
about 0.05R of zero, with no common pattern across the two entries.

Paired edge (mean residual) by year, with sessions:

| Year | `high52-v1` | Sessions | `reversal-lowmax-v1` | Sessions |
| --- | ---: | ---: | ---: | ---: |
| 2016 (Aug-Dec) | n/a | 0 | -0.063 | 107 |
| 2017 | +0.076 | 251 | +0.070 | 250 |
| 2018 | +0.031 | 251 | +0.056 | 251 |
| 2019 | -0.014 | 252 | +0.056 | 248 |
| 2020 | +0.016 | 253 | -0.073 | 252 |
| 2021 | +0.022 | 252 | +0.074 | 251 |
| 2022 | +0.010 | 251 | -0.006 | 248 |
| 2023 | -0.068 | 250 | +0.020 | 250 |
| 2024 | +0.060 | 252 | -0.015 | 252 |
| 2025 | -0.039 | 250 | -0.035 | 247 |
| 2026 (to July) | +0.034 | 145 | -0.076 | 145 |

Years flip sign in both entries with no pattern, which is what a zero edge looks like.

### Picks by sector

Sector comes from `config/config.yaml` `universe.groups`; symbols outside those groups
have none. Of 7,220 `high52` picks, 43 had no known sector; of 7,397 `reversal-lowmax`
picks, 34 had none (the same counts as the snapshot-only picks above). Picks, with the mean `R_cost` of the
picks in brackets:

| Sector | `high52-v1` | `reversal-lowmax-v1` |
| --- | ---: | ---: |
| technology | 1,362 (+0.26) | 1,129 (+0.26) |
| financial_services | 1,232 (+0.21) | 1,184 (+0.14) |
| healthcare | 1,023 (+0.13) | 1,178 (+0.14) |
| industrials | 837 (+0.19) | 817 (+0.21) |
| consumer_cyclical | 800 (+0.16) | 900 (+0.08) |
| consumer_defensive | 778 (+0.12) | 881 (+0.14) |
| communication_services | 531 (+0.05) | 658 (+0.10) |
| energy | 242 (-0.03) | 257 (+0.08) |
| basic_materials | 241 (+0.09) | 230 (+0.07) |
| utilities | 116 (-0.06) | 107 (+0.34) |
| real_estate | 15 (-0.29) | 22 (+0.32) |
| (no known sector) | 43 | 34 |

The lane does not cap sector concentration in research. The two entries pick from the
same large-cap sectors (technology, financials, healthcare); sector means are
descriptive and the small sectors are too thin to read.

## Reading the result

- **The picks made money; the signal did not.** The leg mean is +0.15R per trade, but
  the control (the same bracket on every eligible name that day) made about the same.
  The 2016-2026 market rose and the cohort is current membership (survivors), which
  flatters every long. P2 exists to measure what the signal adds, and it adds about
  +0.01R (t below 1).
- **Consistent with post-publication decay.** Both anomalies were published decades ago
  and are known to weaken in liquid large caps. This cube holds roughly 85 liquid names
  per session, dominated by the scan universe (see the
  [breadth finding](alpha-pooled-power-2026-09-30.md#breadth)), which is where that
  decay would be strongest. That is an interpretation, not something this study tests.
- **Power.** Power check A detected +0.10R about 92% of the time and +0.08R about 68%
  (an upper bound, see its caveats). The observed paired edges (+0.012R and +0.008R,
  standard errors about 0.015R) sit well below those, so an edge of 0.08R or more is
  unlikely here; small edges of a few hundredths of R are not excluded and cannot be
  detected by this lane.
- **Size caveat.** Both formulas load on persistent common factors, so the nominal 5%
  level was likely optimistic (see the
  [lane contract](alpha-pooled-mining.md#what-power-check-a-does-and-does-not-show)).
  That would make a pass, not these failures, less trustworthy.

## Caveats

- **Survivorship and cohort.** Today's membership flatters absolute long results; the
  paired edge cancels the shared part. The two sources and the price and liquidity gate
  are described in the [contract](alpha-pooled-mining.md#cohort).
- **Breadth.** About 85 eligible names per session; 98.2% of eligible cells are
  scan-universe names.
- **Entry convention.** The first hourly bar at or after 10:35 is the setup-study
  convention, not a fill model.
- **Historical bars only.** No live fills or slippage.
- **Overlap with the campaign.** The entries read 2016-08-01 to 2026-07-31, which
  includes the campaign's confirmation window (2024-01-02 to 2026-07-31).

## Decision

- **`high52-v1`: failed.** **`reversal-lowmax-v1`: failed.** Both fail P2 and P3.
- **No Part 2 paper probe for either entry.** Thresholds are not revisited. A changed
  formula, filter, `k` or bracket is a new version tested on new data.
- **Overlap rule now applies.** The
  [pre-committed rule](alpha-pooled-mining.md#literature-overlap-rule-part-1b-requirement)
  governs the campaign's `high52`, `reversal` and `max_lottery` families. A finalist
  whose discovery-window pick set overlaps an entry's at Jaccard >= 0.5 is reported as
  already tested by that entry, keeps its Holm slot and gains no probe eligibility
  beyond the entry's own verdict, which is failure. Part 1b must implement it.

**Artifacts** (private, not in Git):

- `~/agentic-trader-research/pooled-high52-v1-20260930/` and
  `~/agentic-trader-research/pooled-reversal-lowmax-v1-20260930/` each hold
  `protocol.json`, `manifest.json`, `result.json` and `picks.csv.gz`.
- `~/agentic-trader-research/pooled-power-a-20260930-run2/` holds the power check that
  gated both studies.
- `~/agentic-trader-research/pooled-cache-v1/` holds the bars and the shared cube.
