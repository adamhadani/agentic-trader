# Screened equity/ETF forecasts — September 17, 2026

**A concrete equity Ridge research lead emerged; zero alphas were promoted.**
All 96 predeclared comparisons completed. The cohort contains 64 current equity
candidates selected from the 300-name liquidity exercise, plus nine fixed sector
ETF controls. It is conditioned on today's surviving membership and liquidity,
not a historically tradable point-in-time universe.

Protocol and implementation were committed as
`02cb42204597720509b717f552ef5c53f2dcf51d` before market-price access. The
[frozen protocol](../config/research/screened-equity-forecast-iex-v1.json) and
[computation contract](alpha-panel-forecasts.md) remain authoritative. No costs,
features, regularization, cohort or thresholds were changed after seeing results.

## Forecast and economic results

Equity Ridge, using the same three predeclared features and alpha=100 throughout:

| Evaluation year | Mean daily Rank IC | HAC t-statistic | MSE skill versus training mean | Basket return, 1 bp/side | Basket return, 5 bp/side |
|---|---:|---:|---:|---:|---:|
| 2023 | 0.06310 | 1.112 | +3.18% | +44.82% | +43.45% |
| 2024 | -0.01466 | -0.435 | -3.15% | +9.72% | +8.67% |
| 2025 | 0.07897 | 1.611 | +0.48% | +39.28% | +37.96% |

The 692 daily IC observations average **0.04230**, with complete predicted-label
coverage. This descriptive combined average is not a pooled cross-fold significance
test. Each year contains 12 nonoverlapping 20-session baskets; all held outcomes
were observed. Compounding the three complete annual proxy paths gives **+121.31%**
at 1 bp and **+115.06%** at 5 bp. These are historical adjusted-price basket proxies,
not broker fills or portfolio-account returns. Borrow/funding are not included.

Boundary-marked annual drawdowns at 5 bp were 13.54%, 7.96% and 4.20%; these
marks do not measure intrabasket drawdown or execution liquidity. All three annual
HAC confidence intervals include zero. In 2023 the naive annualized ICIR is 4.17,
but the HAC t-statistic is only 1.11: overlapping 20-session labels make iid rules
of thumb inadequate here. No annual 95% significance claim is supported.

Other predeclared models were retained, not filtered from the report:

| Cohort/model | Mean daily Rank IC, all three folds | Compounded basket return at 1 bp | At 5 bp |
|---|---:|---:|---:|
| Equities: momentum60 | -0.01955 | -46.55% | -48.12% |
| Equities: reversal5 | -0.00279 | -1.03% | -3.87% |
| Equities: Ridge | 0.04230 | +121.31% | +115.06% |
| Sector ETFs: momentum60 | -0.06276 | -11.97% | -14.50% |
| Sector ETFs: reversal5 | 0.00558 | -6.04% | -8.73% |
| Sector ETFs: Ridge | 0.02885 | +6.55% | +3.51% |

ETF Ridge lost money under 5 bp in 2024 and 2025 and had negative MSE skill in
all three years. The equity result therefore does not establish cross-cohort
transfer. Training-mean controls had constant cross-sectional scores, undefined
Rank IC, zero weights and zero basket returns, as intended.

`trial.ic` is daily cross-sectional Spearman IC. The separately retained
`forecast_metrics.rank_ic` pools date-symbol rows and must not be compared with
cross-sectional IC production heuristics. MSE skill is a paired descriptive error
comparison, not a test treating correlated stock rows as independent evidence.

## Attribution: why the large return is not yet robust alpha

Independent descriptive attribution reconstructed all 36 equity Ridge baskets and
576 position-baskets. It found **22/36 profitable baskets**, but substantial
concentration: BTDR and NVTS contribute 46.67% of the summed net arithmetic gain;
the top five names (also COMP, HMY and STNE) contribute **74.23%**. These shares
use arithmetic contributions before compounding, not shares of the +121.31% curve.

| Year | Long contribution, net arithmetic pp | Short contribution, net arithmetic pp | Return with original best basket set to cash, 1 bp |
|---|---:|---:|---:|
| 2023 | +41.097 | -1.054 | +30.73% |
| 2024 | +20.858 | -10.605 | -1.43% |
| 2025 | +38.249 | -2.914 | +19.34% |

The 2024 final basket contributed +11.31%, more than the year's total arithmetic
gain. One NVTS holding in May 2025 returned +178.76% in the retained adjusted
prices; its original 6.25% weight contributed +11.17 percentage points to that
basket. The extreme NVTS and TDS observations match the captured provider pages
exactly. This verifies our data handling, not independent corporate-action truth.
The omission calculations above are **post-hoc attribution**, with original
weights/refits unchanged, not a new strategy or a promotion test.

All 39 equity fits assign a negative coefficient to momentum60; 38/39 assign a
positive coefficient to volatility20. Momentum or volatility dominates every
traded forecast spread. The lead therefore resembles medium-term reversal with
high-volatility exposure. Its profitable long side does not yet establish
risk-adjusted skill over matched passive/style benchmarks; the constant training
mean is a discrimination control, not an exposure benchmark. Daily IC ranks all
eligible names on 692 dates, whereas P&L selects tails on just 36 dates and retains
return magnitudes. Rare large winners can therefore dominate returns despite weak
or negative annual IC.

The retained source also reports zero-volume daily bars for some historical BTDR
endpoints. Three such entries collectively lost about 0.268 arithmetic percentage
points, so they do not explain the gains. They still distinguish a present source
bar from an executable price. Additionally, 37/576 position-baskets entered below
$5 in adjusted historical prices: the current $5 screen is not a historical price
screen. A new frozen endpoint-evidence contract must treat these cases explicitly,
without using future volume to rerank the original baskets. Original results remain
unchanged and do not claim executable-price coverage.

## Coverage and accounting

- 73/73 member acquisitions and one calendar request completed in 44.35 seconds;
  minimum request-start spacing was 0.600136 seconds.
- 86,031 normalized bars on a 1,255-session expected calendar; no source/cleaning
  row loss. Shorter histories remain missing, not intersected or filled.
- Equity decision-time breadth was 58–62 names in 2023, 62 in 2024, and 62–64
  in 2025. ETF breadth was nine throughout.
- All 78 scheduled refits succeeded. Equity training windows had 10,692–15,702
  paired observations, with strictly mature endpoints; ETF windows had 2,268.
- All 73 acquired symbol intervals, including warmup, were excluded before the
  first source request at 19:55:47.128263 UTC. The 96-comparison reservation
  preceded those exclusions. Lifetime attempts advanced **7,640 → 7,736**.
- Registry generation remained **16**, with **0 active / 16 shadow** candidates.
  No model was registered, no promotion occurred, and no broker order was submitted.

“Completed comparisons” means the frozen computation was performed and retained.
It does not imply every statistic is defined, a candidate passed, or promotion
power is calibrated. Undefined constant-control IC remains explicit evidence.

## Does this settle overfiltering?

No. The legacy pipeline still has poor planted-signal power and a one-step IC
versus multi-session trading-policy mismatch. This study avoids conflating those
gates with discovery: the equity Ridge lead remains visible despite negative 2024
IC and insufficient annual significance. The same evidence does not justify
loosening production gates retrospectively.

The next valuable exercise is a **predeclared exposure/concentration and endpoint
evidence comparison**, alongside prospective observation of the frozen lead with
exact training/model/basket artifacts retained before labels mature. Keep
the separate gate-power ablation and instrument/borrow/point-in-time-data work
on the [ordered roadmap](alpha-roadmap.md#next-work-after-the-screened-equity-forecast-study).
Do not translate a panel forecast directly into competing per-symbol bracket
screeners or claim that a current-survivor retrospective test qualifies it.

## Retained evidence

Private artifacts: `~/.local/state/agentic-trader/research/screened-equity-forecast-20260917/v1`.
Raw source pages, receipts, model evidence and instrument-level predictions stay
outside Git. Redacted identities:

- Run: `46cee95d3b83400bb26cf9ad44e4615b`.
- Plan: `ac57097e87fdea3f2c6956fddd3ec7c4a036e09a99f2abba88434aa4c3e2ed41`.
- Result SHA256: `ab6f71367132db6b1ac0b8eb3e6e409883460934bb9c4bd2fb2fa72fe2862166`.
- `journal-audit.json` independently verifies the reservation, all exclusions,
  projected artifact hash, lifetime attempts and unchanged registry generation.
- Independent numerical audit: `f4b017e5cc289817cc88ba1cf54dad39b2c4d8256313b511b2e3351aa09e6e94`.
- Independent audit script: `a9aa0ea3385e7a7c2e654240084400142b13265a2910757975f4b8c69be57b6a`.
- Descriptive attribution: `ca133bb5090c4d32020175905f99e7e2b193f75658de242f820e3a3203316838`.

The audit passed **19,264 checks**, reconstructing all 86,031 raw/normalized rows,
78 closed-form Ridge fits, 665,320 training pairs, 219,584 predictions and 288
model/cohort baskets. All weights matched exactly; maximum numerical discrepancy
was 1.42×10⁻¹⁴. It independently recomputed labels, eligibility, ranks, HAC and
cost/wealth arithmetic without application computation helpers. Initial audit-only
format/tie-handling errors, their scripts and corrected accepted report remain
immutable; there was one market acquisition, no changed study policy and no rerun
market experiment. The raw-source and zero-volume attribution supplements are
retained separately in the same private directory.

## Tests and deployment

Before the actual run, local validation passed 1,704 tests with 91 opt-in skips;
the subsequently completed focused baseline/forecast suite passed 61 tests.
The full real SDK/TCP/WebSocket/PostgreSQL suite passed 272 tests. All pre-commit
hooks, Ruff and mypy passed, and independent code review found no blocking issue.
Small fixed synthetic controls produced null IC -0.015–0.039 and planted IC
0.974–0.981. These are mechanical regression checks, not a statistical calibration
of production false-positive or detection rates. Deployed verification is recorded
separately after merge/restart.
