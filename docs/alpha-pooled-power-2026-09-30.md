# Pooled mining, power check A — September 30, 2026

**Result: PASSED.** The stages detected a planted +0.15R edge in 98 of 100 replicates
(gate: at least 80%) and confirmed no null formula at any delta (false acceptance 0.00 at
delta = 0; gate: at most 0.05). The two literature entries were then run against the
cube this check built; see [the literature result](alpha-pooled-literature-2026-09-30.md).

The contract is [pooled alpha mining](alpha-pooled-mining.md#power-check-a). The check is
research only: no database, registry, broker or Telegram access, and no trial, shadow or
promotion credit (`authorizes_promotion: false`).

## What was run

- **Command:** `copilot alpha pooled power config/research/pooled/campaign-v1.json
  --output DIR --cache ~/agentic-trader-research/pooled-cache-v1 --workers 8`.
- **Frozen inputs** (SHA-256 of file bytes, from `result.json` and `manifest.json`):

  | Input | SHA-256 |
  | --- | --- |
  | `campaign-v1.json` | `897fd8e59d912975de9377389a75d8954a747ab49dfa6ae368a30879059ca5ba` |
  | `cohort-v1.json` | `966b67c53ad617d698089dd5b5786b05d4d5248f8844e3326d7addbe8d4ac5a9` |
  | cube spec identity | `f1c835a3d3364d2b81d69fe819dcb36f1cfb2520a1da27a57e0651a15885aa97` |
  | label cube (`cube-966b67c53ad617d6-f1c835a3d3364d2b.npz`) | `f5ed8989abe81e82a25629193b3ed8f71627ed95f1373629153afc05dfd49aad` |

- **Design** (protocol `power` block): 100 replicates; for each of six deltas
  (0, 0.05, 0.08, 0.10, 0.12, 0.15R) a replicate holds 200 null formulas plus one
  planted formula. All are per-name AR(1) score fields with phi 0.95. The planted
  formula's picks gain +delta R. Replicates are seeded by the protocol seed (20260932)
  and the replicate index, so the result does not depend on the worker count (8 here).
  The campaign's own stages (discovery, dedupe, selection, one-shot confirmation) run
  unchanged against an in-memory ledger. Only discovery-window cells are read.
- **Feed and window:** Alpaca SIP; decisions 2016-08-01 to 2026-07-31, bars read from
  2016-01-01 through 2026-09-01. Bracket: 2 x ATR14 stop, 3R target, 20-session hold,
  decision at 10:35 New York, 5 bps per side.
- **Code revision:** `research/prospective-equity-panel-iex-v1-20260918-55-g202274c`
  (from the manifest); Python 3.14.7.

## Two attempts

| Attempt | Started (UTC / New York) | Finished (UTC / New York) | Outcome |
| --- | --- | --- | --- |
| 1 (`pooled-power-a-20260930`) | 14:46:00 / 10:46:00 EDT | 16:31:53 / 12:31:53 EDT | `failed`, rc 1 |
| 2 (`pooled-power-a-20260930-run2`) | 16:32:03 / 12:32:03 EDT | 17:19:41 / 13:19:41 EDT | `passed`, rc 0 |

The first attempt read daily bars for 385 symbols (the 353 cohort names plus the static
reference names) and computed eligibility (130 symbols needed hourly bars). Its hourly
acquisition attempted all 130 symbols and then one fetch raised: CVX, `1h/all`,
`ReadTimeout` from `data.alpaca.markets` (read timeout 10.0 s). It stopped with
`ValueError: bar acquisition failed for 1 symbol: CVX (...); rerun to resume from the
cache` and wrote `status: failed`. No cube was labelled or saved from incomplete bars.
This is the designed behaviour described under
[acquisition](alpha-pooled-mining.md#acquisition-failures-and-provenance).

The second attempt used a new output directory and the same cache directory. Bars
already cached were reused (the cache holds immutable files, so a rerun fetches only what
is missing), and the cube was built and saved. It is the only run whose curve and cube count; the failed directory is
retained as evidence. Both literature studies and every number below come from the
second attempt's cube.

## Cube coverage

- **Sessions:** 2,514 (2016-08-01 to 2026-07-31). Skipped sessions: none.
- **Static reference:** 159 names used (`static_used`; the set is every configured
  contract with raw bars, ETFs included, as in the live gate). Between 141 and 159 names
  contributed on each session (`reference_names`).
- **Bar files read:** 900 (`bars_sha256` `37205fcc…238fca32c7`).
- **Bar failures:** one, `NFEGP` (a preferred share): no daily bars, adjusted or raw. It
  is ineligible throughout.
- **Cells:** 214,308 eligible, 214,278 labelled. Per year:

  | Year | Eligible | Labelled |
  | --- | ---: | ---: |
  | 2016 (Aug-Dec) | 8,480 | 8,480 |
  | 2017 | 20,378 | 20,378 |
  | 2018 | 20,732 | 20,731 |
  | 2019 | 20,705 | 20,696 |
  | 2020 | 20,860 | 20,860 |
  | 2021 | 22,237 | 22,227 |
  | 2022 | 21,422 | 21,413 |
  | 2023 | 22,058 | 22,058 |
  | 2024 | 22,380 | 22,379 |
  | 2025 | 22,133 | 22,133 |
  | 2026 (to July) | 12,923 | 12,923 |

- **Unlabelled eligible cells (30):** 10 degenerate levels (9 in 2021, 1 in 2024),
  8 immature (2019), 12 without a decision price (1 each in 2018, 2019 and 2021; 9 in
  2022). No year comes near the 5% coverage limit.
- **Why cells were not eligible** (counted per session and name, so a name can appear
  under one reason for many sessions): short history 219,335; illiquid volume 278,591;
  illiquid price 170,377; no daily bars 2,514; no raw close 2,307; no ATR 10.

## Breadth

- **214,308 eligible cells over 2,514 sessions is about 85 eligible names per session**
  (computed from the cube: min 78, median 85, max 92).
- **Only 130 of the 353 cohort names were ever eligible.** The gate (raw close at least
  $10, 20-session median dollar volume at least the 25th percentile of the static
  reference set) removes most of the random snapshot.
- **The two sources contribute very unequally:**

  | Source | Names | Ever eligible | Eligible cells |
  | --- | ---: | ---: | ---: |
  | Config groups (`mega_caps`, `research_cohort`, the scan universe) | 127 | 112 | 210,431 (98.2%) |
  | Names only in the equity snapshot | 226 | 18 | 3,877 (1.8%) |

  The literature studies show the same thing in their picks: of 7,220 (`high52`) and
  7,397 (`reversal-lowmax`) picks, only 43 and 34 (0.6% and 0.5%) come from
  snapshot-only names.
- **Implication:** the lane is effectively mining the scan-universe equities. The
  cohort's 300 random snapshot names add little breadth; a wider, liquidity-screened
  cohort would be needed to change that (an operator decision, not made here).

## Curve

Detection is the share of the 100 replicates in which the planted formula was confirmed.
False acceptance is the share in which at least one of the 200 null formulas was
confirmed. At delta = 0 the planted formula is itself a null, so its confirmation counts
under that delta's detection (0.00 here), not under false acceptance.

| Delta (R) | Detection | False acceptance |
| ---: | ---: | ---: |
| 0.00 | 0.00 | 0.00 |
| 0.05 | 0.10 | 0.00 |
| 0.08 | 0.68 | 0.00 |
| 0.10 | 0.92 | 0.00 |
| 0.12 | 0.96 | 0.00 |
| 0.15 | 0.98 | 0.00 |

**Gate:** detection 0.98 >= 0.80 at delta = 0.15, and false acceptance 0.00 <= 0.05 at
delta = 0. Status `passed`.

### Where the planted formula stopped

Replicates ending at each stage (`planted_stage_counts`; `not_carried` was 0 throughout):

| Delta (R) | Failed discovery | Failed selection | Failed confirmation | Confirmed |
| ---: | ---: | ---: | ---: | ---: |
| 0.00 | 100 | 0 | 0 | 0 |
| 0.05 | 71 | 10 | 9 | 10 |
| 0.08 | 10 | 8 | 14 | 68 |
| 0.10 | 0 | 6 | 2 | 92 |
| 0.12 | 0 | 3 | 1 | 96 |
| 0.15 | 0 | 2 | 0 | 98 |

Reading:

- At delta = 0 every replicate stopped at discovery, as it should for a null.
- From 0.10R the discovery gate no longer loses the planted formula; the 8 misses at
  0.10R were mostly at selection (6), then confirmation (2).
- Detection becomes reliable from about 0.10R (0.92) and is 0.68 at 0.08R.
- At 0.05R, close to the PEAD long leg's +0.055R paired edge, 71 of 100 replicates fail
  discovery and only 10 confirm. The lane will mostly miss PEAD-sized edges. As the lane
  contract already says, those belong to the a priori catalog.

## Caveats

From the contract's
[limits](alpha-pooled-mining.md#what-power-check-a-does-and-does-not-show):

- **Label-independent nulls.** The AR(1) score fields know nothing about the labels.
  The check certifies the stage machinery and the statistic for such nulls only.
- **False acceptance does not cover factor-loaded formulas.** A reviewer simulation
  found that picks sharing a persistent exposure to a zero-mean common factor reject at
  about 6.5-6.8% at a nominal 5%. Momentum-like and 52-week-high-like formulas have that
  exposure, so the 0.00 figure says nothing about their size. Part 1b should add a check
  with real DSL expressions on real bars.
- **Detection is an upper bound.** The planted edge is a constant +delta on every pick,
  the planted formula meets a Holm family of about one, and the 2016-2021 drift in the
  resampled labels feeds the leg-mean gates. A real edge of the same average size is
  noisier.
- **Breadth.** The figures above describe this cube (about 85 names per session), not a
  wider cohort.

## Artifacts

Private, not in Git:

- `~/agentic-trader-research/pooled-power-a-20260930-run2/` holds `protocol.json`,
  `manifest.json` and `result.json` (curve, stage counts, per-replicate detail, cube
  coverage and provenance).
- `~/agentic-trader-research/pooled-power-a-20260930/` holds the first attempt that
  failed closed, with its log.
- `~/agentic-trader-research/pooled-cache-v1/` holds the bars and the one shared cube.
