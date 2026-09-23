# Setup-outcome study — September 23, 2026

**Question.** Do the live native strategies' setups (trend pullback, squeeze breakout)
have a measurable edge when sized as the live bracket cards are? Does `setup_quality`
(today's card ranking) pick the better ones? Would a model on causal cross-sectional
features rank them better?

**Answer, from a predeclared protocol with a one-shot holdout:**

- The strategies' edge is thin in development and **negative in the March–August 2026
  holdout**.
- `setup_quality` carries **no measurable information**.
- The frozen ranker **failed its holdout acceptance, and was significantly worse than
  `setup_quality`**.

Live card ranking stays on `setup_quality`. The ranker artifact is not deployed. Live
shadow features are still recorded, as prospective evidence.

## Protocol and run

- Protocol `config/research/setup-outcomes-v1.json`, identity `3bf3060e…c3e07`.
  [Design](superpowers/specs/2026-09-23-setup-outcomes-and-shadow-ranker-design.md).
- **Replay.** The unchanged live `StrategyEngine` runs at the 10:35 and 14:35 New York
  scan instants over the 159-name scan universe. Frames match the live fetch:
  - a 365-day daily window, plus the in-progress daily bar;
  - 60 days of hourly bars, and 4h bars resampled from them;
  - the live duplicate rule, and deterministic levels.
- **Labels** follow the bracket in regular-session hourly bars:
  - entry at the next hourly open;
  - when stop and target are both hit in the same bar, the stop wins; gaps fill at the
    open;
  - a 20-session timeout;
  - R is in planned-risk units, with `R_cost` at 5 bp per side.
- **Windows.**
  - Development: 2021-06-01 to 2026-01-29.
  - Holdout: 2026-03-02 to 2026-08-14, with a 20-session embargo.
  - Data: Alpaca SIP, all-adjusted, through 2026-09-22.
- **Coverage.** All 159 symbols. Development has 14,017 setups over 1,163 sessions;
  the holdout has 1,627 setups over 116 sessions. No labels were immature.
- **Run history.** Run 1 lost SPY's hourly bars to a transient Alpaca API error. The
  runner then dropped SPY entirely, which blanked every market and residual feature.
  The HGB scorer could not bin the all-NaN columns, and the development phase **failed
  closed without reading the holdout**. The runner was fixed (commit `8a03334`):
  - bar fetches are retried;
  - names with daily bars only still feed the cross-section;
  - missing reference symbols stop the run before any labelling.

  Run 2 (reported here) completed. Both runs' artifacts are retained under
  `~/agentic-trader-research/`.

## Base rates (development, per setup)

| Strategy | TF | Dir | n | Target hit | Stop hit | Mean R | Mean R after 5 bp/side |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Trend pullback | 4h | long | 7,457 | 34% | 61% | +0.10 | **+0.03** |
| Trend pullback | 4h | short | 4,004 | 29% | 68% | −0.12 | **−0.18** |
| Squeeze breakout | 1h | long | 1,185 | 39% | 60% | +0.20 | **+0.08** |
| Squeeze breakout | 1h | short | 1,139 | 30% | 67% | −0.08 | **−0.16** |
| Squeeze breakout | 4h | long | 122 | 34% | 57% | +0.20 | +0.16 |
| Squeeze breakout | 4h | short | 110 | 25% | 56% | +0.13 | +0.08 |

Longs are marginally positive and shorts clearly negative over a mostly rising
2021–2026 market. Current-universe survivorship flatters longs, so read these as upper
bounds.

## Hypotheses (development; stationary session-block bootstrap; Holm over H1 + H2a–h)

| | Feature (direction-aware rank) | Spearman ρ vs R_cost | 90% CI | Holm p |
| --- | --- | ---: | --- | ---: |
| H1 | `setup_quality` | +0.016 | [−0.003, +0.035] | 0.54 |
| H2a | `mom_231_21` | −0.001 | [−0.022, +0.020] | 1.00 |
| H2b | `mom_60` | +0.019 | [+0.001, +0.037] | 0.32 |
| H2c | `rev_5` | −0.000 | [−0.020, +0.020] | 1.00 |
| **H2d** | **`vol_20`** | **+0.049** | [+0.029, +0.071] | **0.004** |
| H2e | `dist_high_240` | −0.012 | [−0.032, +0.009] | 1.00 |
| H2f | `dollar_volume_20` | +0.007 | [−0.012, +0.025] | 1.00 |
| H2g | `resid_mom_60` | +0.009 | [−0.008, +0.025] | 0.89 |
| **H2h** | **`sector_rel_mom_60`** | **+0.028** | [+0.012, +0.045] | **0.02** |

- **`setup_quality` is not informative.** Development ρ is 0.016 (not significant) and
  holdout ρ is −0.012.
- **Two cross-sectional features survive Holm:** higher 20-day realized volatility, and
  stronger momentum relative to the sector in the trade's direction. Both effects are
  small (ρ ≈ 0.03–0.05).

## Selection (H3): top-2 setups per session, mean R_cost

Scores are from development purged walk-forward folds (5 folds, 20-session embargo).

| Scorer | Fold 1 | Fold 2 | Fold 3 | Fold 4 | Fold 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| random | −0.09 | −0.14 | +0.07 | +0.04 | −0.00 |
| `setup_quality` | −0.09 | −0.05 | −0.05 | +0.08 | −0.01 |
| logistic | −0.07 | +0.08 | +0.21 | −0.13 | +0.00 |
| **ridge (winner)** | −0.06 | +0.03 | +0.19 | +0.06 | +0.03 |
| HGB | −0.01 | −0.04 | +0.03 | +0.02 | +0.05 |

**Holdout (one-shot; ridge frozen before it was read):**

| | Top-2 mean R_cost | Top-1 mean R_cost |
| --- | ---: | ---: |
| random | −0.118 | — |
| `setup_quality` | −0.126 | −0.213 |
| ridge | **−0.293** | −0.444 |

- The paired difference, ridge minus `setup_quality`, has a 90% CI of
  **[−0.325, −0.016]**. Ridge is significantly *worse*.
- 116 sessions had at least two setups (the minimum is 60). **Acceptance: failed.**

## Interpretation and decisions

1. **No ranker change.** Ridge's development gains came from one fold (fold 3) and
   reversed out of sample. The artifact stays in the private research directory and is
   not configured as `scan.shadow_ranker_artifact`.
2. **The native strategies are not a demonstrated edge.** Every scorer's holdout
   selection lost money after costs, including random selection. The cards remain
   useful for exercising the paper workflow end to end. They should not be read as
   validated alpha, and this matches `setup_quality`'s existing "not validated alpha"
   labelling.
3. **Candidate follow-ups (operator decisions; not justified by this study alone):**
   - Suppress short native setups. Development is clearly negative, but that bias may
     be survivorship- and regime-driven; test it prospectively or in a fresh window.
   - Condition on `vol_20` and `sector_rel_mom_60`, which is a hypothesis for the panel
     lane.
   - Add a delayed-entry sensitivity run to measure how quickly the edge decays with tap
     latency.
4. **The live shadow evidence continues** as designed. Every scheduled suggestion scan
   journals the same feature vector for every ranked candidate, and
   `copilot cards outcomes` labels them. That is independent, prospective data on
   whether any of this holds live.

## Caveats

- **Membership.** The universe uses current membership, which is survivorship-biased
  toward longs.
- **Prices.** Labels are adjusted-price payoff proxies, not fills.
- **Timing.** Labels use hourly granularity, with same-bar stop-first ordering and an
  entry at the next hourly open. Live taps happen at arbitrary latency.
- **Replay parity.** Replay's hourly/4h/in-progress bars are up to about 35 minutes
  staler than live. Live acts on the unfinished current bar.
- **Config.** The protocol pins the strategy config and sector map. Risk and universe
  config were read at run time and were unchanged since the protocol commit (revision
  `8a03334`).
- **Feed and adjustment.** Accepted feed and adjustment differences are listed in the
  design's "Accepted parity gaps".
