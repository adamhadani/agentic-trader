# Alpha execution return timeline

This is implemented A2a of the [alpha roadmap](alpha-roadmap.md), delivered in
[PR #37](https://github.com/adamhadani/agentic-trader/pull/37). It corrects the observation
defect found by the [A1b study](alpha-study-2026-09-16.md); session-correct finer-bar
replay remains A2b. No qualification threshold, signal rule or fill rule is relaxed.

## Contract

The simulator owns the **supplied execution clock**, independently of the feature
clock. Every bar in `[start, end)` has a portfolio equity return. An unavailable
feature suppresses new signals; it does not erase known portfolio observations.

- Cash, including feature warmup and pending unfilled orders, has zero return under
  the current no-interest cash model. Feature values remain unavailable, never
  imputed to create a trading signal.
- An existing order/position retains its lifecycle when subsequent features become
  unavailable. Fills, fees, marked exposure, protective exits and realized returns
  remain on the original bar sequence.
- Missing/nonfinite/nonpositive OHLC invalidates a simulation. Unknown prices are
  not manufactured from cash returns or forward-filled. Infinite supplied scores
  are invalid; NaN scores mean no new signal.
- A fold cannot inspect observations beyond `end`, including their validity. It
  may use preceding bars to warm up causal features and protection. Folds still
  start with no inherited position or order.
- Return statistics and bootstrap consumers require finite, unique, ordered
  observations. They reject missing returns rather than dropping them and making
  separated bars appear adjacent. Sample moments include cash observations;
  annualization retains the existing observed-calendar-cadence calculation.

This does not prove the supplied bars include every exchange session. Holidays,
early closes, missing market bars and finer execution clocks are A2b work. It does
not infer missing quotes, intrabar paths or broker fills.

## Observable coverage

Simulation results include `feature_coverage`: total bars, preceding finite-score
bars, unscored bars and score fraction. The first bar has no preceding score when
starting from index zero; warmup is counted explicitly. Coverage describes signal
availability, not the fraction of time invested or independent statistical samples.

Mining retains each validation fold's coverage and training coverage with trial
evidence. Model baselines retain fold coverage; holdout summaries retain their own
coverage. This separates an unavailable feature from an unknown equity return or
an insufficient number of completed trades. IC still uses observed feature/label
pairs; forecast coverage and execution returns have different meanings (A3).

## Evidence versioning and journal behavior

`ValidationPolicy.return_timeline` is `complete_observed_bars_v1`. New runs and
qualifications carry the complete policy. Old records remain immutable; they are
not relabeled as current evidence. This changes measurement semantics, not the
immutable strategy's signal/fill rules or existing position protection.

- New research writes require the repository's injected validation policy.
  Historical-policy runs cannot consume a fresh qualification holdout.
- Promotion requires current-policy evidence. Durable entry reservation and the
  submission commit both reject obsolete qualification policy under the alpha
  lock. A submission already committed stays lookup-only, as before.
- `family/all` preserves **all lifetime attempts**, including old runs and crashes.
  Per-bar Sharpe samples used to estimate trial variance are partitioned by
  timeframe and return timeline. Historical `family/<timeframe>` projections remain
  available for audit/replay; they are not used as current variance samples.
- Consumed evidence records the timeline and number of comparable variance
  observations. With multiple lifetime attempts and fewer than two comparable
  observations, variance is unavailable, not fabricated as zero. Statistical
  qualification consequently fails closed. Changing semantics never refunds trials
  or releases consumed symbol/holdout intervals.

These are existing journal aggregates and transactions; no new queue, storage
backend, migration, compatibility calculation or automatic registry mutation.

## TDD and integration boundary

Regressions cover all-cash warmup/intermittent features; long/short pending entry,
marking and exit after feature loss, including a same-bar fill/stop and both fees;
invalid prices/scores; strict return consumers;
future perturbations; actual miner coverage; policy rejection before holdout
consumption; lifetime counts with compatible variance; replay; and entry fencing.
The PostgreSQL suite exercises independent clients changing alpha authorization
before/after submission commit. Real SDK TCP HTTP/WebSocket coverage is retained.

Software tests establish those contracts, not profitable strategies. A successful
synthetic study likewise cannot qualify an alpha or earn shadow dates/decisions.

## Frozen follow-up study

Before evaluating new observations, `config/research/a2a-v1.json` was committed
with root seed **10472909262026** and the current contracts. Protocol identity:
`1038e35b873a6ca8c8d9e6e1f77df5ccf6a266a5ff42c16d64043e6bf3d670d6`.

All other scenario definitions, sample sizes, selection rules, blocks, economic
gates and acceptance criteria remain those of the [A1b design](alpha-study-protocol.md):
1,952 jobs and 13,312 reserved synthetic trials. Both development and validation
use fresh seed namespaces. Do not tune after either phase or substitute favorable
sensitivity results for a primary endpoint. Original A1b artifacts remain unchanged.

The frozen 7,049 historical trials and variance scalar remain a **synthetic
sensitivity assumption**, not a reconstruction of the newly partitioned production
variance estimator. Neither historical-family nor fixed-panel comparisons establish
universal lifetime error control. Report all unavailable comparisons and bounds.

```bash
uv run copilot alpha study-plan --seed 10472909262026 --output /private/path/new-plan.json
uv run copilot alpha study config/research/a2a-v1.json --output /private/path/new-run
```

Run in an isolated process with numerical threads limited and reduced priority.
No runtime DB/provider/broker/notifier is constructed. The historical A1b JSON is
intentionally refused by current code because its frozen scientific contracts
differ; reproduce it only in an isolated checkout of its recorded source revision.

The [completed study](alpha-timeline-study-2026-09-16.md) retains all 1,952 jobs with
zero unavailable comparisons. Null-search criteria passed; positive-control power
remains insufficient. No statistical gate changes are justified.

After this measurement, prioritize execution realism and forecast/strategy
alignment before expanding search. Promotable candidates still need untouched
deployment-feed evidence, shadow observations, current-policy qualification and
the existing paper-entry/risk/protection boundaries. The target is reproducible
deployability, not a quota of passing formulas.
