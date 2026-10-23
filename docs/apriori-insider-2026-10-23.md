# A priori insider cluster leg v1 — October 23, 2026

**Result: `failed` (P1).** Clusters of opportunistic officer/director purchases on cohort v3 return
**+0.087R per trade after 15 bp per side** (1,062 events). The bootstrap 90% interval is
[−0.008, +0.189], so P1's lower bound misses zero by 0.008R.

They do beat the same-session matched controls: **+0.070R per trade, ci90 [+0.009, +0.134]**, so
P2 holds. P3 (trimmed +0.074R; mean since 2023 +0.163R) and P4 (1,062 events, 423 since 2023)
hold too. The frozen rule needs all four, so the leg stays off: no probe, and no threshold is
revisited.

This is the first a priori leg whose matched-control difference clears zero.

## Protocol and run

- **Entry and inputs.** Entry `config/research/apriori/insider-v1.json`, SHA-256 `12907738…5fc8`,
  frozen before any label was read. The [spec](superpowers/specs/2026-10-23-apriori-insider-leg-design.md)
  gives the sample sizing, which used event counts only. The entry reuses the pooled protocol
  `cohort3-insider-v1.json` (`969fc5df…`) and the cached cohort-v3 label cube (`75dbb80a…`):
  - 1,000 names in ranks 401–1,400 by liquidity;
  - a $5M 20-session median dollar-volume floor and a $10 minimum price;
  - a 10:35 New York decision, a 2×ATR14 stop, a 3R target and a 20-session time exit;
  - 15 bp per side.
- **Event.** At least two distinct officers or directors with opportunistic open-market purchases
  filed within 21 sessions, counting only filings on or before the trigger date. A purchase is
  routine, and ignored, when the same owner bought the same issuer in the same calendar month a
  year earlier. A trade above 10× the 20-session median dollar volume is dropped first. One trigger
  per name per 63 sessions. Entry is on the first session after the filing date.
- **Control.** The mean of 5 cube cells on the same session that meet all of these:
  - in the same dollar-volume tercile;
  - not themselves events;
  - with no purchase filed in the previous 126 sessions;
  - with at least one Form 4 transaction filed before the decision (so the issuer reports under
    Section 16);
  - nearest in 20-session return.
- **Run.** 2026-10-23 05:5x ET from branch revision `7b90bb1`. Everything came from cache except
  the trading calendar (2,681 sessions, SHA-256 `e829346d…`). Output is in
  `~/agentic-trader-research/apriori-insider-v1-20261023`.
- **Sources.** The insider archive is `67a262c0…` and the corporate actions `8a547b06…`. 111 trades
  were dropped as implausible, and 83 cohort names have no qualifying transaction (foreign private
  issuers).

## Event funnel

| Step | Count |
| --- | ---: |
| Triggers (after the 63-session cooldown; 1,715 further cluster days suppressed) | 1,485 |
| Outside the decision window (2016 before 08-01; 2026 after 07-31) | 104 |
| Before the name's latest identity break | 92 |
| Accepted triggers | 1,289 |
| Not eligible in the cube (price, dollar-volume floor or ATR as of D−1) | 222 |
| Share-class sibling / unlabelled | 2 / 3 |
| **Labelled events, every one paired with 5 controls** | **1,062** |

Events per session: a median of 1, a p90 of 2 and a maximum of 10.

## Rules

| Rule | Value | 90% CI | Holds |
| --- | ---: | --- | --- |
| P1: mean `r_cost` > 0, CI lower bound > 0 | +0.087R | [−0.008, +0.189] | ❌ |
| P2: leg − matched control | +0.070R (control +0.017R) | [+0.009, +0.134] | ✅ |
| P3: 1%-trimmed mean; mean from 2023-01-01 | +0.074R; +0.163R | — | ✅ |
| P4: ≥ 300 events, ≥ 100 from 2023 | 1,062; 423 | — | ✅ |

Bootstrap: a stationary session block, mean block 10, 2,000 draws, seed 20261023, over 741 decision
sessions. One-sided p = 0.068 (P1) and 0.026 (P2).

## Descriptives (never decisive)

- **By year** (mean `r_cost`):

  | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
  | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | +0.21 | +0.36 | −0.14 | +0.40 | −0.10 | +0.26 | −0.28 | +0.28 | +0.18 | +0.11 | +0.05 |

  The negative years are the drawdown years (2018 Q4, 2020, 2022). The leg is long-only and
  carries the market, which is why P1 misses while P2 holds. Excluding 2020: +0.119R (n 908).
- **By buyers:** 2 buyers +0.066R (n 838); 3 or more +0.165R (n 224). This is the gradient the
  cluster literature predicts.
- **By 20-session return before entry:**
  - bottom tercile −0.021R;
  - middle +0.125R;
  - top +0.157R.

  Clusters after a sharp fall do worst, even against controls matched on that fall.
- **By opportunistic dollars / dollar volume:**
  - low +0.048R;
  - middle +0.141R;
  - high +0.072R.

  Not monotone.
- **Exits:** 42 targets, 383 stops, 637 timeouts. Raw `r` is +0.130R for the leg against +0.063R
  for the controls.
- **Leg minus the mean of every labelled cell that session** (PEAD's style of control): +0.049R.
- **60-session close-to-close:** leg +0.059, matched controls +0.063, difference −0.004. The excess
  sits inside the bracket's 20 sessions, not in a months-long drift.

## What this says

1. **There is relative information here, but not an absolute edge.** On the frozen test, cluster
   buying adds about 0.07R per trade over comparable names on the same day, for a long-only card
   that lives or dies by its own R. The market drag in down years leaves the absolute mean
   insignificant at 90%. Under the frozen rules this is a failure, and the leg does not become a
   probe.
2. **It is the strongest a priori evidence on the desk.** PEAD v2's long leg passed with +0.149R
   and +0.055R over its control. Exdiv's paired difference was +0.025R with a CI through zero. The
   insider leg's paired difference is larger and its CI clears zero.
3. **Any second look needs a new window.** The decision rule allows no re-test on 2016–2026, and
   the diagnostics (3+ buyers, the return tercile) are hypotheses, not findings. Two honest routes
   exist, each needing its own spec and operator approval:
   - **(a) A prospective v2 on the same frozen rule from 2026-08-01.** It needs a nightly EDGAR
     ingestion. At about 9 events a month it would take roughly three years to reach P4's 300
     events.
   - **(b) A predeclared market-neutral variant judged on P2 alone.** It would be a pairs-style
     trade, which the desk's long-only card contract cannot express today.
4. **The pooled insider fixed sets stay retired.** This run read insider-conditioned outcomes over
   the pooled lane's unused confirmation slice (2024-01-02 → 2026-07-31). That window is still
   unused for every other information source.

## Caveats

- **Survivorship.** Cohort v3 is a current-membership snapshot (2026-10-15). The control is drawn
  from the same survivors, which differences out part of the bias in P2, but not in P1.
- **Live parity is unmeasured.** The study dates filings by EDGAR's filing date.
- **Earnings proximity.** Insiders buy in open windows, which often fall just after an earnings
  report, and post-earnings drift is not separated out.
- **Routine definition.** The rule uses one prior year, not Cohen–Malloy–Pomorski's three, which
  dilutes the signal.
- **Share classes.** Classes of one issuer that trigger on different days count separately (4
  issuers in the cohort).
- **Identity exclusion.** It uses the latest identity break, which is a later date than some of the
  events it excludes. It removes events and cannot select them.
