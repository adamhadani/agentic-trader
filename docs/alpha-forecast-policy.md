# Forecast timing and cost screens

**September 17, 2026 — A3 research increment.** The original forecast comparison
found small open-gap prediction leads. This contract tests whether those leads
survive delaying entry until the next observed open. No result can qualify an alpha.

## Shared target and payoff contracts

`research/alpha/targets.py` owns `ForecastTarget` and outcome construction:

| CLI label | Outcome at decision bar `t` |
| --- | --- |
| `close_to_close` | `close[t+h] / close[t] - 1` |
| `next_open_to_close` | `close[t+h] / open[t+1] - 1` |

`h` counts observed bars. Features never receive these future prices; training
labels must mature before validation, with the target horizon determining the purge.
The original source holdout boundary is unchanged. Features are explicit immutable,
canonical, deduplicated DSL expressions; normal compiler causality/budget checks apply.

`forecast_components_v2` hashes the target, selected features, model parameters and
optional payoff policy. Old v1 artifacts remain historical evidence. This changes
research identities only, not deployed alpha versions or qualification policy.

`next_bar_long_flat_proxy_v1` is deliberately bounded to **daily, one-bar** forecasts:

- At completed bar `t`, positive predicted return means a hypothetical fully funded
  long for bar `t+1`; zero/negative/missing predictions mean cash. No shorts, leverage,
  pyramiding, stop/target fills, overnight holdings or automatic deployment.
- Enter at the next observed open and exit at its observed close. Fold boundaries
  start flat; a proposal cannot borrow a return from another fold or the holdout.
- With per-side adverse cost `c = basis_points / 10000`, entered net return is
  `close[t+1]*(1-c) / (open[t+1]*(1+c)) - 1`. Entry sizing includes its cost, so the
  screen stays fully funded. These are cost assumptions, not measured live spreads.
- Cost scenarios use identical decisions. Higher costs cannot improve an individual
  entered payoff. A cost scenario is another charged trial, not a free tuning axis.
- Include every validation execution bar, including first-bar cash and missing-feature
  abstention. Missing/nonpositive/nonfinite endpoint prices invalidate the screen;
  do not drop losing/unavailable dates or silently fill prices.
- The fixed comparator enters every eligible day and also finishes in cash. It uses
  the same clocks, costs and fold boundaries. Report its results and each fold's
  mean return difference alongside forecast-policy results. This is not a claim of
  risk-matched alpha or independent statistical significance.

This is a **bar-price payoff screen**, not another broker/order execution simulator.
Alpaca's [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) describes
trade-condition-based aggregation. Its [order documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
describes primary-exchange opening/closing auction eligibility and submission cutoffs.
An aggregated first/last price is not guaranteed to equal a realizable auction fill.
Calendar timing, quotes, auction prints, participation, rejection/partial fill and
protective ownership require separate evidence before deployment. Raw corporate
changes and dividends are not converted into total-return observations here.

## Usage, charging and persistence

```bash
uv run copilot alpha benchmark RUN_ID --method single --budget 1 \
  --feature open_gap --label next_open_to_close --horizon 1 \
  --cost-bps 0 --cost-bps 1 --cost-bps 5 --cost-bps 10
```

`--feature` is repeatable; omission uses the existing economic library. `--cost-bps`
is optional, repeatable, bounded, unique and increasing. Without it the command
remains a forecast-only benchmark. Cost screens reject multi-bar/intraday targets.

A run reserves `model_budget * (1 + cost_scenario_count)` attempts before computation.
The example reserves **five**: one fitted forecast specification and four payoff
variants. The comparator is a frozen reference, never searched or qualified.
All forecast/payoff trials remain diagnostics; neither supplies strategy-Sharpe
variance or a qualification credential. Registry and execution permissions are unchanged.

The existing `AlphaBenchmarkService`, journal and private artifact adapters retain
plans, source integrity, excluded discovery intervals, forecast arrays, full payoff
clocks and fold summaries. Payoff arrays include gross/net returns, comparator returns,
position indicators and round-trip leg counts. No extra schema, queue or notifier.
The service continues offloading calculation/I/O and preserving failed reservations.

## Predeclared open-gap follow-up

Freeze this before evaluating any new payoff result:

- Use the **SPY and QQQ** dataset-only source records from the preceding
  [78-trial comparison](alpha-forecast-comparison-2026-09-17.md), with the same daily
  SIP/raw snapshots, discovery prefix, three expanding folds and untouched holdouts.
- Fit one ordinary least-squares feature, `open_gap`, separately to the original
  `close_to_close` and delayed `next_open_to_close` target. Horizon one; seed 20260917.
- Apply the fixed long/cash policy to each forecast with **0, 1, 5 and 10 basis
  points per side**. Four runs, **20 charged attempts** in total. No new data download,
  feature/sign/threshold search, gate change or promotion.
- **Advance-to-replay criterion:** the SPY delayed-target model must have complete
  forecast coverage, positive net return in every fold at one basis point per side,
  and positive mean excess return over the fixed daytime-long comparator in every
  fold. This is a discovery triage criterion, not a statistical qualification test.
- QQQ is a predeclared transfer diagnostic; it cannot rescue a failing SPY criterion.
  Report all cost levels and both training targets. Do not retune on these results.
- If the criterion fails, pause this standalone long/cash hypothesis and resume A2b
  durable session acquisition/decisions and A4 panel hypotheses. A failure of this
  policy does not establish that all possible uses of open-gap information fail.

The periods were already inspected in discovery. No result is a fresh final test.
Deterministic synthetic tests cover an overnight-only edge, a genuine daytime edge,
independent cash-flow arithmetic, costs, missing values, fold boundaries and future/
holdout perturbation. They do not establish search-wide false-discovery rates.
