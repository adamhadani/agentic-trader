# Prospective equity mining campaign — 2026-09-19

This is the first bounded run through the verified prospective equity snapshot,
using the new `alpha mine --universe snapshot` path. It widens the discovery
funnel; it does not relax qualification or claim historical point-in-time
membership.

## Frozen protocol

| Field | Value |
| --- | --- |
| Snapshot | `prospective_equity_candidates_v2`, snapshot `fdf27df3cb2c76321119c140606d5362e115eb296e81abee63466a00fed72e42` |
| Cohort | First 32 deterministic selected members, cap identity `snapshot:<id>:cap:32` |
| Symbols | ATIIW, MAAS, VNT, AVT, OPAL, ARDT, SAZ, AIM, BELFB, PFGC, NVTS, RWAY, PDPA, FMST, EXYNW, VGZ, SGU, PARK, GYRO, STKE, SLG, BTDR, IMMX, KF, BDCIU, MRCOU, ACHV, EMP, GTY, SLSR, BRSL, PEBK |
| Data | Raw Alpaca IEX daily bars, five-year request, fixed-duration clock |
| Search | Genetic, 25 generated trials plus 7 catalog trials per symbol, 90-second symbol budget |
| Display gates | `min_sharpe=-100`, `min_dsr=0` for discovery visibility only; production qualification policy unchanged |
| Holdout | Untouched during mining |

## Results

- 30 symbols completed and 2 were charged failures (`EXYNW`, `MRCOU`) because
  their IEX histories were too short for the declared purged validation folds.
- 32 attempts were reserved per symbol before provider I/O: **1,024 charged
  trials**. The 30 completed symbols contain 928 evaluated trials and 32
  trial-level rejections; the two short-history symbols account for the other
  64 reserved but unevaluated trials. All are retained in the journal.
- 30 symbols produced discovery finalists. The best validation Sharpe among
  per-symbol leaders was 3.34, but the highest DSR was 0.890; **zero leaders
  met both the unchanged 1.0 Sharpe and 0.95 DSR policy gates**. No holdout was
  consumed, alpha was qualified or strategy activated.
- The strongest-looking formulas were concentrated in the existing
  price-volume families (`ts_corr(open, volume, 10)`, volume-conditioned
  momentum and their rolling transforms). This is breadth evidence, not
  independent alpha evidence; repeated expressions across names remain one
  family until cross-symbol transfer and multiple-testing controls are applied.

## Interpretation and next step

The broadened funnel is working mechanically: source identity is `alpaca:iex`,
all completed manifests carry the snapshot hash, and failures are visible rather
than silently omitted. The result also exposes two limits in the current
prospective screen: it is not liquidity-screened and current asset metadata does
not yet provide authoritative common-stock subtype. Several selected names have
short histories or untyped/special-share characteristics, so scaling directly to
all 300 names would spend budget on unavailable evidence.

Next, add a predeclared post-acquisition coverage/liquidity scoreboard (without
using it to rewrite the frozen cohort), then run a matched 32–64-name cohort of
liquid common equities. Compare transfer and failure rates against ETF32 before
increasing the cap. The DSL should be widened only with causal availability and
future-perturbation tests; this campaign does not justify weakening gates or
promoting a candidate.
