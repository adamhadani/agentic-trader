# Lifetime attribution — September 21, 2026

## Result and decision

All **800/800 paired searches completed** with no failed jobs and no unavailable
endpoints. Bounding the **resting entry** to one day (P1) restores trade conversion
for the planted forecast and separates it from the null by a wide margin. Adding a
one-day **holding** expiry (P2) is strongly harmful. No production gate, alpha
version or trading configuration was changed.

This run executes the mechanistic half of the [attribution plan](alpha-lifetime-attribution-plan.md).
The implemented harness replays each frozen selection under P0/P1/P2 and records
trades, expiries and net returns; it does **not** compute the plan's confirmatory
endpoint (P2 current-family full-policy acceptance with a frozen family snapshot).
That endpoint is therefore **not evaluated**, not passed or failed. The plan named
P2 as the sole confirmatory policy; the mechanism measured here does not support
P2, and the P1 finding is exploratory until a separately frozen confirmatory run
evaluates full-policy acceptance.

## Validation results

Replays cover each 2,500-bar synthetic series in full, not a holdout slice. Known
route rows use one search method because the data seed, and therefore the known
measurement, is shared across methods. Returns are net simulated percentages on a
synthetic generator with positive drift; they rank policies and are not forecasts.

| Known planted route | n | P0 trades / return | P1 trades / return | P2 trades / return |
| --- | ---: | ---: | ---: | ---: |
| Dense, planted effect | 64 | 1.3 / +1.12% | 75.2 / +119.42% | 157.7 / +57.56% |
| Dense, null | 128 | 68.7 / −7.66% | 84.1 / −9.45% | 264.0 / −26.25% |
| Sparse, planted effect | 64 | 1.3 / +0.66% | 62.2 / +34.63% | 62.2 / +34.50% |
| Sparse, null | 128 | 57.8 / −5.85% | 64.7 / −6.71% | 105.4 / −11.55% |

| Frozen GTC-selected winner | n | P0 trades / return | P1 trades / return | P2 trades / return |
| --- | ---: | ---: | ---: | ---: |
| Dense, planted effect | 128 | 10.6 / +17.99% | 65.0 / +163.33% | 474.3 / −39.25% |
| Dense, null | 256 | 45.1 / −2.95% | 54.1 / −3.73% | 482.0 / −39.37% |
| Sparse, planted effect | 128 | 26.0 / +260.53% | 49.6 / +571.62% | 312.6 / −33.41% |
| Sparse, null | 256 | 32.5 / −1.47% | 37.9 / −2.01% | 335.8 / −28.49% |

Paired differences on planted-effect data (same dataset, same frozen selection):

| Route / profile | P1 − P0 | P2 − P1 |
| --- | --- | --- |
| Known, dense | +118.30 pp; improves in 100% of pairs | −61.86 pp; improves in 0% |
| Known, sparse | +33.97 pp; 100% | −0.13 pp; 3% |
| Winner, dense | +145.34 pp; 98% | −202.58 pp; 0% |
| Winner, sparse | +311.10 pp; 77% | −605.04 pp; 0% |

Standardized separation between planted and null returns, `(mean_edge − mean_null)
/ pooled sd`, for the known route rises from 2.28 (P0) to 17.85 (P1) in the dense
profile and from 2.13 to 8.13 in the sparse profile. For frozen winners it rises
from 1.01 to 10.17 (dense) and 1.50 to 12.55 (sparse), and collapses to about zero
under P2. Paired routes and methods share data; these are not independent samples.

## Mechanism: GTC limit entries select against a correct forecast

`entry_limit` places the entry at the tick-rounded signal-bar close and the order
rests until filled. With a correct long forecast the next bars trade away from that
price, so the order does not fill and then suppresses every later pulse. With no
forecast the price wanders back and fills. Under P0 the planted route completes
**1.3 trades while the null completes 58–69**: the policy fills preferentially when
the forecast is wrong. One-day expiry lets unfilled entries lapse (61–73 expiries
per series) so later pulses can trade; the null stays negative after costs.

The same construction is used live: alpha signals submit a limit bracket at
`entry_limit(current_price)` with DAY or GTC time in force.

Holding expiry is a different question. The frozen winners were selected for
multi-day bracket economics; forcing a one-day exit multiplies turnover roughly
ninefold and converts them to cost-dominated losses. It does not show that a
horizon-matched strategy would fail, only that imposing the deadline after
selection is destructive.

Search selection remains a separate loss: the frozen winner was the planted
expression in 0 of 256 planted-effect searches, consistent with the
[power diagnosis](alpha-power-diagnosis-2026-09-18.md).

## Provenance

- Source: `15b99ab80f9f42c9e92b6d0c26fbc054708502ca`.
- Root seed: `20226090511185288264757259359810597586` (fresh; not shared with the
  power diagnosis).
- Protocol identity: `c39c1d3959245100b52d982afee91652d16f3e526ddc82f6e61c98c2d907bae8`;
  protocol file SHA-256 `008a3b99d9eb60f7350da5c3fd5a12fcd7f1c6df47ef64ff38b8491197e27ce4`.
- Completion SHA-256: `f43031ea63f859bac722326960f0d6d2a0510fe6ab9ed0e8fdbce893de4acc48`.
- Manifest SHA-256: `5275836add405e11f33d5e2408bbebbabd60d721de2456b76b623432292fd2ea`.
- 12,800 synthetic expression evaluations were reserved in the study manifest; none
  increments the production research ledger.

Private artifacts are under `/tmp/agentic-lifetime-run-20260921`. No runtime
database, provider, broker or notifier was constructed.

## Limits

Synthetic generator with next open equal to prior close and UTC business-day
timestamps; no real exchange timing, partial fills or queue position. Full-series
replay includes the discovery region, so returns are not out-of-sample estimates.
Statistical gates (DSR, folds, bootstrap, trade minimum) were not evaluated. One
lifetime value per deadline was tested; this is not a lifetime grid.

## Next steps

1. Freeze a confirmatory protocol with **P1 as the predeclared policy** and
   full-policy acceptance as the endpoint, using fresh seeds and the unchanged
   bounds (null upper ≤5%; dense lower ≥80%; sparse lower ≥50%). Run it through
   the existing power study (`alpha power-plan --entry-policy session`), which
   already measures that endpoint.
2. Evaluate a day-bounded entry for live alpha orders through the existing timed
   execution contract ([trade lifetimes](alpha-trade-lifetimes.md)); it creates new
   immutable version identities and needs the native-daily clock decision first.
3. Treat holding horizon as a property to select for during discovery, not a
   deadline imposed afterwards.
