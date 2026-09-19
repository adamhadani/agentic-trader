# IEX-native mining smoke test — September 19, 2026

This report records an isolated source-correct mining campaign used to test the
current funnel after the earlier Yahoo-to-Alpaca deployment mismatch finding. It
is research evidence only. It did not read the production database, modify the
active registry, place broker orders or send Telegram messages.

## Frozen run

- Feed: `alpaca:iex`, raw prices, daily bars
- Symbols: `AAPL`, `AMD`, `IWM`, `MSFT`, `NVDA`, `QQQ`, `SPY`, `TLT`
- Lookback: five years; 1,254 observations per symbol
- Search: genetic, 20 generated iterations plus the seven catalog hypotheses
- Discovery gates: deliberately relaxed (`min_sharpe=-100`, `min_dsr=0`) to inspect
  the funnel; the normal qualification policy was unchanged
- Run IDs: `1edb54e7389c41cebdb171d21d047631`,
  `7bec9b99f9e847ab8ef2da8af50cce7a`,
  `56a82ded4dc04a5c902ab8453127160e`,
  `8f0c576b8330475abea968a803d64977`,
  `8f21572b0fab4abc803dba68be91b5a6`,
  `a48e31bdf23e4f14bece5a3128d6d25e`,
  `b94e2f03e34048d087b8ffd1fb45145a`,
  `f33c67b2b5984cacade63befc1934fed`.

The campaign used an explicitly disposable SQLite database and temporary artifact
directory. The raw bars and credentials are intentionally not committed here.

## Qualification results

One finalist per symbol was qualified against the existing one-use holdout. The
deployment contract passed for all eight: every manifest and definition used
`alpaca:iex` and `raw`. No candidate qualified.

| Symbol | Validation Sharpe | Validation Rank IC | Holdout Sharpe | Holdout DSR | Main failure pattern |
| --- | ---: | ---: | ---: | ---: | --- |
| AAPL | 0.591 | 0.036 | 0.461 | 0.060 | unstable folds, drawdown and bootstrap |
| AMD | 0.244 | 0.081 | 0.807 | 0.110 | unstable folds, 44.3% holdout drawdown and bootstrap |
| IWM | -0.488 | -0.024 | 0.280 | 0.044 | negative IC, too few trades, unstable folds |
| MSFT | -0.574 | 0.117 | -0.206 | 0.015 | negative holdout and cost-stressed return |
| NVDA | 1.115 | -0.014 | -1.088 | 0.001 | validation/holdout reversal and cost stress |
| QQQ | 0.181 | 0.093 | -0.144 | 0.017 | negative holdout and cost stress |
| SPY | 0.981 | -0.129 | 0.743 | 0.110 | negative IC, cost stress and bootstrap |
| TLT | 0.080 | -0.049 | -1.397 | 0.000 | negative holdout, IC and cost stress |

The exact persisted decisions retain every gate and rejection reason. These
numbers are not a reason to lower DSR, fold stability, cost, drawdown or
holdout requirements: most candidates fail several independent checks, and the
highest validation Sharpe is a clear holdout failure.

## Decision on paper testing

The deployment-feed check remains mandatory. A Yahoo candidate can be useful for
discovery, but it must be re-mined or revalidated on the exact Alpaca feed before
even a paper execution experiment. Source equivalence is a research/live
semantics requirement, not a production-only preference.

We should add a separate, explicitly named **paper probe** stage rather than
weakening `qualified`:

1. Keep the current qualification policy as the only route to the active registry
   and ordinary alpha order admission.
2. Add a distinct paper-probe registry/status with a predeclared expiry, tiny
   notional and stop-risk caps, and an explicit `alpaca_paper` guard. Paper probes
   must use raw Alpaca IEX/SIP data, a frozen universe and an immutable candidate
   version.
3. Tag every signal, order, fill, P&L and Telegram message as `paper_probe`.
   Probe outcomes are execution and data-quality evidence; they earn no shadow
   dates, holdout credit or production promotion credit.
4. Require an operator command to enroll a probe and reject probes that overlap an
   existing active owner. Expiry and demotion must be durable and replayable.

This gives us a safe way to test execution and broker alignment while preserving
the meaning of statistical qualification. It should be implemented only after
the registry/admission/outbox contracts are specified and covered by TDD; it is
not safe to make `active` conditional on the account being paper.

## Funnel priorities

1. **Fix the acquisition boundary and add a campaign scoreboard.** Reserve the
   research attempt before acquisition, persist stage/rejection/maturity counts,
   and make repeated same-history searches visible as such.
2. **Continue improving the existing causal grammar before adding a new search
   engine.** PR #77 now exercises range/gap fields, realized volatility and
   volume-history mutations with acquisition-aware generation. Next add bounded
   feature-family quotas, stronger novelty tracking and equal-budget random/genetic
   controls; keep `vwap` behind an explicit provider-column contract.
3. **Expand the point-in-time equity cohort.** The current funnel is bounded by a
   64-name panel after the larger metadata/liquidity screen. Move toward 150–300
   names only with versioned membership, missing-bar accounting, resource limits
   and survivorship-safe evidence. Do not count more current survivors as fresh
   independent trials.
4. **Keep factor/panel models as a separate family.** Evaluate portfolio value
   after costs and exposures, then build an executable target-to-order plan before
   admitting any panel forecast to paper risk.
5. **Defer advanced ML/genetic variants.** More model complexity or mining cadence
   will increase selection burden until source alignment, breadth and prospective
   outcome power are measured.

## Expanded grammar follow-up

After the first campaign, the miner was run again with the broadened causal
grammar from PR #77. The isolated campaign used the same four source-correct
symbols (`AAPL`, `AMD`, `IWM`, `SPY`), 57 trials per symbol (seven catalog plus
50 generated), and the same deliberately relaxed discovery display gates. It
produced 21–26 discovery finalists per symbol, compared with 9–18 in the earlier
27-trial run. The added expressions exercised nested volatility, gap, spread and
volume-history features; no provider or production state changed.

The best discovery finalist per symbol was then qualified once:

| Symbol | Validation Sharpe | Holdout Sharpe | Holdout DSR | Result |
| --- | ---: | ---: | ---: | --- |
| AAPL | 1.200 | 1.306 | 0.247 | Rejected: DSR, fold stability, trade count and bootstrap |
| IWM | 1.096 | 1.021 | 0.164 | Rejected: recursive initialization, DSR, IC, fold stability and trade count |
| AMD | 1.043 | -0.321 | 0.014 | Rejected: holdout loss, drawdown, costs and DSR |
| SPY | 1.601 | -1.429 | 0.000 | Rejected: holdout loss, costs, IC, trade count and DSR |

The source contract passed for all four. This demonstrates useful search breadth,
not a reason to lower DSR or other qualification gates. The positive AAPL/IWM
holdout Sharpe values are post-selection diagnostics with weak DSR and insufficient
trade support; they are not paper strategy approvals.
