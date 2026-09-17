# Sector-panel experiment — September 17, 2026

**32/32 comparisons complete; zero candidates passed the frozen research screen.**
The independent audit agrees with the calculations. This is negative development
history evidence, not a new qualification, paper trade or proof that no sector alpha
can exist. No registry change, order or Telegram notification was requested by the
study; live position protection and registry generation remained unchanged.

## Frozen design and coverage

The [protocol and implementation](alpha-sector-panel.md) were committed and pushed at
`52c2e0dd17332eb5ff4753a61187e9d65ec78759` before the first provider request. No
parameter, cost, symbol, direction or date retry followed the results. A private DB
backup preceded the authorized research writes. The 32-comparison reservation and
all ten member/benchmark/warmup exclusions precede the first recorded provider read.

Nine legacy sector ETFs plus SPY beta reference have **753/753 native raw SIP daily
bars each** over 2021–2023; no missing exchange dates. 2021 supplies warmup, with
separate 2022/2023 development folds. Each hypothesis has **246/245 daily Rank IC
observations** and **50/49 disjoint five-session basket proxies**, respectively.
Four hypotheses × two folds × (IC plus three cost scenarios) accounts for every
charge. Lifetime research attempts increased from **7,280 to 7,312**.

## Full result matrix

Returns compound native-bar price proxies at half-long/half-short gross exposure of
at most one. Costs are **per side**, on actual proxy entry/exit notionals. These omit
borrow, dividends, corporate actions and intrahorizon execution/risk. HAC t uses the
predeclared 20-lag Bartlett estimator; it is not a multiple-search-adjusted test.

| Hypothesis | Fold | Mean Rank IC | HAC t | 0 bp | 1 bp | 5 bp |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| sector_momentum | 2022 | -0.0209 | -0.42 | -7.04% | -7.96% | -11.58% |
| sector_momentum | 2023 | -0.0046 | -0.09 | -2.26% | -3.21% | -6.94% |
| sector_reversal | 2022 | -0.0400 | -1.20 | +0.81% | -0.20% | -4.11% |
| sector_reversal | 2023 | -0.0058 | -0.10 | -7.89% | -8.79% | -12.31% |
| vol_scaled_momentum | 2022 | 0.0163 | 0.45 | +3.67% | +2.64% | -1.39% |
| vol_scaled_momentum | 2023 | 0.0109 | 0.21 | +1.96% | +0.96% | -2.93% |
| beta_adjusted_momentum | 2022 | -0.0017 | -0.04 | -4.34% | -5.30% | -9.01% |
| beta_adjusted_momentum | 2023 | -0.0200 | -0.41 | -2.92% | -3.87% | -7.57% |

All eight per-fold 95% HAC intervals include zero. Volatility-scaled momentum's
annualized **IID** ICIR is 0.63/0.44; dependence-aware HAC t is 0.45/0.21. Its combined
mean IC is **0.0136**, below the predeclared contextual 0.03 screen. An annualized
ICIR or naive t would not turn this into persuasive evidence either.

| Hypothesis | Combined 1 bp | Combined 5 bp | Failed criteria |
| --- | ---: | ---: | --- |
| sector_momentum | -10.92% | -17.71% | mean_ic, ic_fold_stability, primary_stability, stress_stability, concentration |
| sector_reversal | -8.97% | -15.92% | mean_ic, ic_fold_stability, primary_stability, stress_stability, concentration |
| vol_scaled_momentum | +3.63% | -4.27% | mean_ic, stress_stability, concentration |
| beta_adjusted_momentum | -8.96% | -15.90% | mean_ic, ic_fold_stability, primary_stability, stress_stability, concentration |

Coverage and sample-size screens passed for every hypothesis. Volatility-scaled
momentum is the only one with positive 1 bp returns in both years, but 5 bp makes
both years negative. Its largest positive basket contributes **84.78% of the total
signed net log gain**, exceeding the frozen 50% concentration limit. Even omitting
the 0.03 IC rule would not rescue its cost or concentration failures. Negative-total
candidates have undefined gain concentration and fail that screen explicitly.

## Why this experiment failed

- **Predictive weakness:** three hypotheses have negative IC in both years and lose
  overall even before costs. These fixed formulas did not sort the next five-session
  returns usefully in this cohort/window. Flipping their sign after seeing that would
  be a new charged hypothesis, not a correction.
- **Thin, concentrated edge:** volatility-scaled momentum has small positive mean IC
  and primary returns, but weak uncertainty evidence, concentrated gains and losses
  under frozen cost stress. Transaction costs matter even at this lower frequency.
- **No detected coverage/arithmetic explanation:** the observed calendar is complete;
  independently reconstructed causal features, ranks, labels, ties, signed inventory,
  fees, wealth and uncertainty agree. The earlier SPY *minute* coverage failure is
  a different source contract and remains unresolved by complete native daily bars.
- **Limits remain:** this small curated sector cohort and two development years do
  not establish universal absence of alpha. Daily prices omit publication/auction
  timing and total-return/borrow details; the audit cannot remove those assumptions.
  HAC handles the chosen serial lags, not adaptive research-selection bias.

The outcome supports keeping the four hypotheses rejected under this protocol.
It does not justify relaxing promotion or cost gates, increasing blind search volume,
or enrolling the least-bad formula as a qualified strategy.

## Independent and operational evidence

The separate audit imports no application scoring, panel, target, statistics or
payoff code. From immutable NPZ arrays it reconstructs each formula (including
trailing beta), rank correlations, IID units, the Bartlett mean covariance, cutoff
ties and a signed-quantity cash ledger. Maximum discrepancies: Rank IC **1.11e-16**;
IC statistics **5.00e-16**; HAC statistics **4.45e-16**; weights **zero**; basket return
**2.16e-16**; fees **4.34e-19**; compounded wealth/drawdown **3.47e-15**.

Calendar, dataset and receipt hashes match; every input has actual request/receipt
times. The DB diagnostic binds the result hash. Read-only journal verification
confirms exactly 32 added attempts, all ten exclusions before acquisition, unchanged
registry generation 10 and **zero active alphas**. Research writes did not change
tracked position protection. All 17 live readiness checks remained healthy during
the experiment. That observation is distinct from later deployed verification.

Private artifacts reside under `~/.local/state/agentic-trader/research/sector-panel-v1-20260917/`.
Prices, runtime records, credentials and account details are excluded from Git.
Evidence identities:

- `manifest.json` SHA-256: `30cd726c049abadfcb60acffe820e6319830b76d6359157ac52afe00ca0f5bc0`.
- `inputs.json` SHA-256: `b942909e947958357658918b9558934c2927bc75a9c6b80b1f253a84b5b8452d`.
- `calendar.json` SHA-256: `e01508f8e55929f62d4f8c0d7831113f45ce06bda4fc3e1933132c9ac1fe436e`.
- `result.json` SHA-256: `929e67958b495b93a131de550039c18ad6a7fc285675b890f5a056d11e4965c6`.
- `screen.json` SHA-256: `8c2e56b6c448c7197b1b518ab65d065bab9eeb6b346a93f2824d23f215532392`.
- `independent-audit.json` SHA-256: `87d5135f8de6533e8ab7b76657eb0468336ee267e2ee04ba34b821a653e28fe6`.
- `journal-audit.json` SHA-256: `e7dc88063d465a0f2328ad89c3e374b3e7e32b15fc74b73fe88d939af1fd4b76`.
- Independent audit script SHA-256: `56150f01e926b792bd296f594fb38cc68e2ded845aff9f223956527c931149e7`.

TDD covered missing/constant observations, ties, sample/annualized units, serial
correlation, future/fold perturbations, unavailable dates, precise fees and immutable
protocols. Actual paginated HTTP/SDK and disposable PostgreSQL tests cover successful,
missing-member and denied-provider acquisitions, exposure ordering, replay and artifact
binding. Local verification: **1,382 tests passed, 53 skipped** in the full suite; **142
transport/PostgreSQL integration tests passed** using a guarded disposable DB; the
latest affected metric/coverage/CLI/SDK/PG checks passed **67 tests**. All-file
pre-commit, Ruff and mypy passed. Deployment verification is recorded separately in
the PR evidence; a healthy test process is not proof of live broker/Telegram freshness.

## Next work

1. **Lossless acquisition provenance:** retain raw omitted/null rows and normalization
   decisions in the existing data boundary; diagnose the retained SPY minute gaps
   without rewriting the old failed study. Add malformed/missing SDK-response tests.
2. **A newly frozen, economically different study:** after provenance is trustworthy,
   predeclare a small slower-turnover relative/residual ETF study with explicit
   dividend/corporate-action and borrow assumptions. Treat any horizon/buffer/cohort
   change as a new charged comparison; keep the current failures intact. Require
   independent confirmation before paper-candidate enrollment. Do not add futures or
   options before their data, costs and execution/accounting contracts exist.
3. Continue real receipt/decision collection and independent quote/fill cost evidence.
   Preserve the six session controls as diagnostics; no historical backfill earns
   forward credit. Legacy miner IC migration/calibration and multi-owner protection
   remain explicit engineering gates on the [roadmap](alpha-roadmap.md).
