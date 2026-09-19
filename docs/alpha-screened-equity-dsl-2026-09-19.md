# Screened equity breadth and DSL coverage — September 19, 2026

This is a research-only campaign report. It records the first mining run over
the completed IEX liquidity cohort and a smaller rerun after fixing genetic
seed coverage. Neither run consumed a holdout, changed the registry, sent a
broker order, or authorized paper trading.

## Protocol

- Cohort: the completed `equity_liquidity_screen_v1` result from the dated
  300-name prospective snapshot; 64 selected names, source-qualified to
  `alpaca:iex` daily activity.
- Mining entry point: `alpha mine --universe screen`, which records the screen
  result hash and cap in each dataset manifest. The selection is current
  development evidence, not historical point-in-time membership.
- Lookback/feed: five years, raw Alpaca IEX daily bars, fixed-duration daily
  clock, three purged discovery folds and an untouched 20% holdout.
- Per symbol: seven catalog hypotheses plus 25 genetic trials; the normal
  discovery gates were left unchanged. The campaign display used
  `--min-sharpe=-100 --min-dsr 0` so every evaluated finalist was retained for
  diagnostics; this does not alter qualification policy.
- Artifacts: private isolated roots under `/tmp/agentic-alpha-cohort.GnBMVp/`;
  no raw bars, credentials, logs or databases are tracked.

## Breadth result

The first 64-name run completed all names and charged all **2,048** declared
trials. **2,040** candidates evaluated and eight were rejected by evaluator
errors; no symbol acquisition failed. The best discovery Sharpe was **3.279**
and the largest DSR was **0.9145**. 213 candidates had validation Sharpe at
least 1, but **zero** met both unchanged gates (Sharpe ≥ 1 and DSR ≥ 0.95).
No candidate reached holdout or promotion.

This broad run is useful funnel and failure-rate evidence. The leaders remain
concentrated in the existing price/volume family; a high validation Sharpe on
one current cohort is not independent evidence of a deployable strategy.

## DSL coverage correction and rerun

The first run exposed a search-coverage defect: `TypedGeneticSearch.ask()`
mutated its seed expressions before evaluating them. Newly declared families
could therefore be absent from a nominally successful genetic campaign. The
search now drains a deterministic seed queue before crossover/mutation, and a
test asserts that every declared seed is evaluated first.

A 16-name protocol-matched rerun charged **512/512** trials and evaluated all
of them. It exercised the new families (counts include mutations containing the
family expression):

| Family | Evaluated candidates containing the family |
| --- | ---: |
| Volatility-scaled trend | 32 |
| Causal range location | 16 |
| Signed volume pressure | 16 |
| Serial return dependence | 32 |
| Standardized trend slope | 16 |

The best Sharpe was **2.305** and the largest DSR **0.8834**; **zero** of 512
candidates passed both gates. This is the expected result for a controlled
coverage check, not evidence that the new expressions are useless. They now
have a fair chance to appear in future discovery budgets, while causal
prefix-invariance and field-availability tests prevent lookahead or fabricated
inputs.

## Interpretation and next step

The wider screened cohort increases the number of observed hypotheses without
weakening validation. It did not produce a promotable alpha. The next valuable
experiment is a fresh, predeclared matched panel study that compares these
families against constant, market-residual and volatility controls with
turnover/cost and factor exposure diagnostics. Only stable net results should
enter untouched holdout and prospective paper-probe gates. Do not increase
search cadence or lower DSR/Sharpe thresholds based on this result.
