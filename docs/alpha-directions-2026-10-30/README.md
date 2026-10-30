# Alpha directions review — 2026-10-30

A research review of where the desk should look for alpha next, after 64 fixed-set formula-cohort
tests, the spread lane, the event legs and the bracket counterfactual all closed without a source good
enough to trade. The operator asked for it on 2026-10-30:

> "i'm still under the impressions we dont have a data acquisition / signal generation / alpha mining
> pipeline thats generating alpha despite our best efforts. perhaps we can spawn another ultrathink
> effort to review our current codebase, recent PRs, current roadmap, look elsewhere online for similar
> projects and state of the art papers / blogs to try and figure out what directions we can pursue next"

The request named seven starting points: a more expressive DSL, a larger universe, vectorbt PRO, GPU
deep learning, the de Prado books and the rest of the operator's quantitative-finance library, paid
alternative data (and how to judge a source before paying), and anything else that can be tested quickly.
[report.md](report.md) §4 answers each one.

The code was read at main `183e33b`, identical to production that day. Line numbers drift as the
code changes; file and symbol names are the stable reference.

## Start here

1. [report.md](report.md) opens with a one-paragraph recommendation and the corrections box.
2. §1 is the executive summary, §2 the diagnosis and §3 the four ranked directions with the program
   rules R1–R6.
3. §4 answers the seven thoughts and §5 lists what not to do.
4. §6 has the operator decisions and the week-by-week plan; §7 records what the two critiques changed.

## The result in brief

- **Diagnosis.** At the desk's test power, single price/volume signals are exhausted: the minimum
  detectable effect is 0.065–0.08R per trade, against a plausible 0.02–0.05R, and the 64 pooled
  t-statistics are distributed like the null. Only the two information events tested against controls
  built for the event looked positive: PEAD (+0.055R over all reporters, t ≈ 2.66) and insider
  clusters (+0.070R over matched controls). Both were measured on survivorship-biased cohorts. Paper
  trading cannot validate anything within the program: a 0.1R card effect takes about 28 months of
  cards to see.
- **Direction 1, an honest card stream ($0, week 1).**
  - Decide `card_policy` at DP0.
  - Give `card_stats` an empirical-Bayes prior across keys.
  - Put a net-excess base rate on PEAD cards, and journal the raw EPS/consensus pair.
  - Replace the fabricated `gex.py` IV default.
  - Start a window × read ledger.
- **Direction 2, the Sharadar 2007–2016 era for the event lane (≈ $499/yr, to verify).**
  `insider-oos-v1` (P2 primary) and `pead-sue-v3` (a consensus-free SUE PEAD) run on point-in-time,
  survivorship-free cohorts from one frozen manifest. Each test is rehearsed on an already-read window
  first.
- **Direction 3, IC machinery and one mechanically chosen composite ($0 beyond Sharadar).** Adds
  cross-sectional operators, residual returns and rank IC, then confirms one mechanical JKP composite on
  the era. The composite becomes the yardstick every new data source must beat.
- **Direction 4, options.** Deferred to a feed check.
- **Cut:** residual reversal, TSMOM, intraday SPY/QQQ and `veto-v1`.
- **No** vectorbt PRO, no GPU work in this program, no social/congress/estimate feeds.
- **Expectation:** about 1 in 4 that a source passes both the statistical and the economic gate in
  time for the January earnings season. The most likely outcome is a sparse but honest stream and
  decisive verdicts on both near-misses.

## Operator decisions

| # | Decision | Recommendation | Status |
| --- | --- | --- | --- |
| O1 | Subscribe to the Sharadar Core US Equities Bundle (D8); day-1 diligence on price, retention after cancellation, research-use licence, `datekey` semantics, EVENTS item codes, delisting fields | Yes | Operator researching it (2026-10-30). Claude does not purchase |
| O2 | Spend cap through 12-31: Sharadar plus at most $49 for an ORATS check | ≈ $550 | Open |
| O3 | Book capacity: non-native sources (PEAD, insider watch, future probes) share at most 5 of the 8 desk slots | Approve the principle; numbers at DP2 | Open |
| DP0 | `card_policy` on Fri 11-06: `enforce` at 0.0 (≈ 0.5 cards/session) or preview with no auto-entry of `would_withhold` cards (≈ 2.5) | Enforce | Due 2026-11-06 |
| — | Start the $0 work (direction 1) while Sharadar is evaluated | — | Approved 2026-10-30: "take on highest priority work that doesn't require paid subscriptions yet" |

Later decision points DP1–DP4 (11-20, 12-04, 12-18, holiday week) are in [report.md](report.md) §6.1.

## How it was produced

One multi-agent workflow of nine agents, with no failed agents:

1. Five parallel strands:
   - an internal audit of the code, results docs and roadmap;
   - López de Prado (AFML, MLAM);
   - the operator's quantitative-finance library;
   - a web survey of current practice and papers;
   - a census of data sources and costs.
2. A synthesis of the five strands.
3. Two independent critiques: one on statistics and power, one on fit to this codebase and its contracts.
4. A final editor's revision, which [report.md](report.md) reproduces verbatim apart from the archive
   pointers.

Figures marked "my arithmetic" are planning estimates. The power check named beside each one must
confirm it before any read.

## Caveats

- The strand reports (01–08), the book text extractions and the workflow script are archived locally,
  not in Git, at `~/agentic-trader-research/alpha-directions-20261030/`.
- Sharadar's price, licence retention after cancellation and `datekey` semantics were not verified. No
  vendor statement on them was found on 2026-10-30.
- Probabilities are the editor's judgement after the critiques, not measured quantities.
