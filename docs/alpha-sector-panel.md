# Causal sector-panel research

This implements the [IC contract review](alpha-information-coefficient.md) for a
new cross-sectional research path. It does not reinterpret existing miner metrics,
change acceptance gates or submit multi-alpha/portfolio orders.

## Layers and evidence

- `data/sessions.py`: injected bounded SDK readers share observed-calendar access;
  native daily acquisition uses inclusive SDK bounds through an exclusive next-day
  cutoff. SDK pagination is preserved. Historical publication time is not observed.
- `research/alpha/panel.py`: pure exchange-date alignment, retained missing-member
  coverage and trailing beta adjustment. No intersection, resampling, price filling,
  inferred historic membership or fallback feed. Invalid observed OHLCV fails loudly.
- `information.py`: typed/versioned cross-sectional Spearman observations. The full
  expected clock, breadth, ties, missing reasons and fold boundaries are retained.
  Each fold reports sample standard deviation, per-observation ICIR, explicitly
  scaled IID annualized ICIR/t-test and Bartlett HAC/Newey–West uncertainty. No
  epsilon or fabricated zero replaces undefined correlation/variance. Any missing IC
  date withholds that fold's inferential statistics; dates are never compressed.
- `panel_study.py`: immutable plans, existing causal DSL features, explicit forecast
  targets, tie-aware rank baskets, cost attribution and descriptive screening.
- `panel_workflow.py`: injected application service over the existing alpha journal
  and artifact writer. Reserve every comparison and exclude all inspected member,
  benchmark and warmup dates before provider I/O. Save arrays/receipts, failed inputs,
  manifests and result hashes; keep diagnostic results outside strategy variance and
  qualification. Blocking reads/calculation run off the asyncio loop.
- `copilot alpha panel-study`: CLI composition, protocol validation and retained
  screen report. It starts no daemon/poller, sends no bot messages and changes no
  registry. Source failure exits nonzero and retains the charged denominator.

No schema, event bus, live portfolio simulator or promotion mechanism is added.
Existing rolling single-symbol miner IC remains its prior version; its known
measurement debt is not silently repaired by adopting this separate panel metric.

## Frozen first experiment

Protocol: [sector-panel-v1.json](../config/research/sector-panel-v1.json).
Commit the tested code/protocol before acquiring its observations; no post-result
retuning, new symbols, duration sweep or automatic rerun belongs to this study.

- **Nine sector ETFs:** XLB/XLE/XLF/XLI/XLK/XLP/XLU/XLV/XLY; SPY is only the beta
  reference. These are the legacy sector cohort, not the complete current sector
  taxonomy. Their [issuer's historical overview](https://www.ssga.com/library-content/pdfs/etf/us/target-a-lower-cost-of-ownership-with-sector-etfs.pdf)
  records December 1998 inception. This curated surviving-fund sample is **not** a
  point-in-time equity universe or proof of historic borrow/tradability.
- Observed native daily raw SIP bars: 2021 warmup; separate 2022 and 2023 evaluation
  folds. These are development-history stress windows, not untouched confirmation.
  All ten symbols share the observed exchange-date clock and frozen input snapshots.
- Four fixed hypotheses: 60-session momentum skipping the last five sessions;
  five-session reversal; 20-session momentum scaled by 20-session realized volatility;
  and the first score minus trailing 60-session beta times the matching SPY score.
  Beta uses only returns known through the signal day. Adjusting a score does not
  imply that the resulting portfolio is beta-neutral or hedgeable as implemented.
- The label is next native-daily open to fifth subsequent native-daily close. Label
  endpoints never cross a fold boundary. Last-five-date unavailable outcomes are an
  explicit maturity boundary, not dropped failures. No model/hyperparameter fitting.
- IC ranks across all nine ETFs on each matured signal date. Reports declare 252
  observations/year, 20 HAC lags and 95% normal-approximation HAC intervals. Those
  are frozen conventions, not proof of independence or multiple-search adjustment.
- Rank baskets use top/bottom three with +0.5/−0.5 capital weights. Cutoff ties split
  equally; opposing tied weights net to cash. Gross is at most one, net is zero.
  Decisions occur every five session bars; their baskets do not overlap. Fully close
  at the fifth native bar's close and begin the next basket at a later bar's open.
  Full entry/exit costs apply even when names repeat. Report actual proxy turnover.
- Costs: **0/1/5 bp per fill side**, charged against entry and exit notionals.
  The equal-weight long sector comparator uses the same disjoint windows/costs; it
  is descriptive, because its market exposure differs from a long/short basket.
- **32 charged comparisons:** four hypotheses × two years × (one IC evaluation +
  three cost scenarios). Every charge survives missing coverage or failed acquisition.

Daily timestamps label **bars**, not fill events. Scores assume that the preceding
native day is complete by the next local midnight; the actual historical publication
lag is unknown. Artifacts separate `signal_bar`, `assumed_decision_at`, `entry_bar`
and `exit_bar`. Daily opens/closes are price proxies, not verified auction fills.
[Alpaca's aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq) apply;
we do not relabel native daily bars as complete regular-session minute evidence.

## Predeclared screen and interpretation

Further research requires complete IC/feature coverage, at least 200 IC dates in each
year, positive mean IC in both years and combined mean IC at least 0.03, at least 90
completed basket proxies overall, positive annual returns at both 1 and 5 bp per side,
and no basket contributing over half the primary signed net log gain. Undefined
metrics fail. The 0.03 level is a contextual research screen for this fixed small
panel, **not a production-grade certificate**. No ICIR/t-stat threshold is substituted
for qualification; inferential reports remain conditional descriptive diagnostics.

A pass authorizes only consideration of further research. It is not statistical
acceptance, shadow credit, deployment eligibility, a broker fill or paper-account
profit. Preserve attempt history and separate formal qualification, prospective
receipts and portfolio execution attribution/protection. If all fail, retain the
whole matrix and attribute the result before adding formulas or changing thresholds.

Wealth compounds nonoverlapping basket proxies, with flat annual boundaries. Reported
drawdown observes basket-end marks only. Intrahorizon drawdowns, dividends, corporate
actions, borrow/funding, impact, liquidity, rounding and partial fills are unmodeled.
No claim of risk-equivalence with the long comparator or live protection is warranted.
The historical arrays are normalized provider frames; raw-response/`dropna()`
provenance and the prior SPY minute gaps remain a separate recorded follow-up.

## Run and verification

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  nice -n 10 uv run copilot alpha panel-study config/research/sector-panel-v1.json \
  --output /private/path/new-directory
```

Use explicit authorized research configuration in a worktree. This writes intentional
research evidence to the selected journal; pytest always uses isolated fixtures and
disposable databases. Keep arrays, logs, receipts and reports private. Independently
recompute ranks, uncertainty, weights, fees and wealth before interpreting outcomes.
Tests cover ties, missing/constant observations, sample/annualization units, HAC against
an independent covariance formula, persistence-induced uncertainty, fold/future
perturbations, exact cost arithmetic, real paginated HTTP/SDK, both database backends,
CLI failures and event-loop responsiveness. Deployment verification is separate.

## Completed first run

The [September 17 result](alpha-sector-panel-2026-09-17.md) records 32/32 complete
comparisons, zero passes, independent arithmetic/journal audits and the next priorities.
The original protocol and rejection reasons remain frozen.
