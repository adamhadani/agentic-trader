# Map: risk policy and entry admission

Revision reviewed: worktree at `db82817` (identical to production). All paths are relative to the repo root. "Now" means what the code does today; "Recommend" is my proposal.

## 1. Purpose and scope

This layer decides whether a suggested card may carry new risk, and how large it may be. It has three parts: the scan (sizing, then the evaluator's gates), the Telegram tap (freshness and re-checks), and durable admission (reservation, preflight, final submission against broker evidence). Since L2 (2026-10-06), every entry rule is a pure function in `agentic_trader/risk/`. Each layer passes its own book and budget inputs to those functions (docs/risk-policy.md §Purpose). The layer bounds *planned stop risk and snapshot notional*. It explicitly does not bound gap loss, portfolio volatility in a forecasting sense, or tail loss (`execution/capacity.py:3`, docs/entry-capacity.md §Aggregate exposure).

## 2. Components and contracts

**Typed core (`risk/`)**
- `RiskLimits` (`risk/limits.py:50-120`) is a frozen snapshot of every limit, built once by `from_config`, which validates finiteness. A class with no cap is uncapped (`:111`), and an empty `correlation_groups` disables the correlation rule (`:66`).
- `Book`/`BookPosition` (`risk/book.py:33-104`) are built from `signals` rows. Each row needs `notional_value` and `risk_dollars`; a missing or invalid value makes the book `invalid` and is never coerced to zero (`:47-48`, `:85-86`).
- `EntryIntent` (`risk/rules.py:106-164`) is the single validation boundary for a trade: positive finite numbers, LONG/SHORT only. Notional is priced at `max(entry, current_price)` (`:158-164`). `risk_dollars` is `max(entry−stop, 0)·qty·mult` (`:154-156`).
- `RiskBudget` and `per_trade_risk_budget` (`risk/capital.py:69-93`) compute `min(cash, equity) × max_risk_pct_cap × drawdown_factor × clamp(macro, 0.10, 1)`. `risk_capital` treats the configured cash as a ceiling (`:34-42`). `drawdown_risk_factor` is linear between the haircut threshold and the halt (`:45-58`).
- There are 18 `RiskRule` ids (`rules.py:62-82`). The composites fix the evaluation order: `book_gates` collects every shared book rejection for the scan (`:470-488`), and `admission_gates` returns the first rejection (`:491-513`).
- `book_vol_factor` (`risk/book_vol.py:72-111`) is the closed-form largest `f∈[0,1]` that keeps `vol(book + f·card)` within a dollar budget. It only ever scales a card down and never refuses (docs/risk-policy.md:111-115).

**Sizing and stops (`agent/`)**
- Stop placement is in `RiskEvaluator.calculate_levels_deterministic` (`agent/evaluator.py:166-276`). The stop is the swing low/high ± 2 ticks, but at least `risk.min_stop_atr_multiple × atr_14` (1.5×, `:208-214`). For trend-pullback, `atr_14` is the **4h** ATR (`screeners/strategies.py:163`). The target is built at exactly `required_reward_risk` times the actual stop distance, rounded away from the entry (`:216-219`).
- `calculate_dynamic_sizing` (`agent/position_sizing.py:40-277`) builds the tiers:
  - max tier = `min(max_shares, budget.dollars/unit_risk, min(max_trade_notional_cap, portfolio headroom)/unit_notional)` (`:95-117`);
  - base tier = `default_equity_risk_dollars × dd × macro / unit_risk`, clamped to the max (`:153-168`, static mode in production, `config/config.yaml:334`);
  - half tier = half the base.
- `scale_sizing` (`:280-353`) applies the book-vol factor.
- `RegimeDetector.get_regime` (`agent/regime.py:133-227`): VIX buckets set `breakout_allowed` and `min_rr` (`:172-183`); the macro report supplies `risk_multiplier` (`:185-189`).
- `compute_compound_macro_stress` (`agent/macro.py:207-280`) uses a point score over VIX, curve and HY OAS. An inverted curve alone gives HIGH (×0.5, breakouts off) (`:255-259`).
- `ForexFactoryCalendar` (`agent/calendar.py:173-221`) supplies tier-1 USD events from the "this week" feed (`constants.py:126`).

**Admission (`execution/`)**
- `authorization_expiry` (`execution/admission.py:16-27`) checks queue age and signal age.
- `reservation_rejection` (`:30-91`) checks direction and side, the multiplier and `EntryIntent`, then calls `admission_gates`. The budget is built **without** a macro factor (`:87`).
- `EntryExecutionService._preflight` (`execution/entries.py:63-110`) runs: signal status, expiry, halt, legacy claims, broker context, Alpaca price drift (`:89`), the injected macro check (lockout, then regime gate, `agent/copilot.py:3962-3967`, `:3989-4019`), and then capacity.
- `assess_entry_capacity` (`execution/capacity.py:220-312`):
  - evidence age, account and asset eligibility;
  - funding against the lowest observed buying power (`:264-289`);
  - exact parent/replacement exposure lineage (`_exposure`, `:79-217`);
  - re-valuation of held positions at `max(recorded, qty×max(mark, entry))` notional and `max(recorded, qty×(mark−stop))` risk (`:206-209`);
  - finally `reservation_rejection` with the broker-marked book.
- Tap-time freshness is `assess_card` (`execution/freshness.py:131-211`); re-priced quantity is `reprice_quantity` (`:222-254`).

**Seams**
- Inputs: `signals` rows (EXECUTED positions, `storage/db.py:859-870`); ledger `AccountRiskSnapshot` (lifetime high-water mark, cash-flow adjusted, `accounting/risk.py:198-209`); regime and macro from yfinance/FRED; the earnings calendar; the corporate-action capture; Alpaca account, asset, quote and order evidence.
- Outputs: card tiers, `rejection_rule` and reason on `LLMTradeEvaluation`, the `book_sizing` provenance block, and work-item refusals in the outbox.

## 3. Data flow

1. **Scan start** (`agent/copilot.py:779-785`): it fetches the regime, the recorded open notional and the EXECUTED rows. Ledger equity and drawdown come from `account_risk` (`:1369-1370`).
2. **Per candidate**, `evaluate_candidate` runs in order:
   1. sizing; a zero quantity is "Sizing blocked";
   2. `EntryIntent` on the default tier;
   3. `book_gates` on the scan book, which is EXECUTED rows plus cards already sent in this scan (`evaluator.py:491-499`);
   4. the optional statistical correlation check (`:503-523`, off in production because `enable_dynamic_correlation` defaults to False, `config.py:397`, and `config/config.yaml:1-8` does not set it);
   5. macro lockout (`:526-544`), earnings (`:546-575`) and corporate action (`:577-636`);
   6. regime breakout, then reward/risk (`:639`);
   7. session, then the LLM with the final-bracket re-check.
3. **Card construction**: `BookSizer.prepare` builds one Ledoit-Wolf covariance per scan from **raw** daily bars (`execution/book_sizing.py:222`, `research/setups/covariance.py:20-25`). `decide` scales tiers in `enforce` (`book_sizing.py:366`), which production has run since 2026-10-20 (`config/config.yaml:466`).
4. **Tap**: session/validity → price geometry → gate reason. The gate reason is lockout, then the regime gate on configured cash with zero drawdown, then earnings, then corporate action. After that comes EXECUTE / REPRICE / MISSED (docs/risk-policy.md:183-201). Book rules do **not** run at the tap.
5. **Admission**, at three moments: the reservation (`enqueue_entry`), the preflight (refreshed ledger, refreshed regime, broker capacity), and final submission under the trading → ledger locks with a pinned risk fingerprint (docs/entry-capacity.md:24-30). Any unexplained broker holding or order blocks all new risk (`capacity.py:213-216`).

## 4. Evidence of what has been tried

- **L2 unification** (2026-10-06): one function per rule, with seeded consistency and adversarial tests (docs/risk-policy.md §Adversarial and consistency guarantees). The rule semantics were operator-approved (decisions 1-7, docs/risk-policy.md:282-383).
- **Account-risk and capacity tranches** closed survey items S1 (drawdown not wired) and S3-funding (docs/architecture-review.md:368-382). Still open: "measured portfolio volatility/tail constraints" (same section; docs/alpha-stack-survey-2026-09-18.md §S3, §S4).
- **Book-aware sizing**:
  - preview 2026-10-09 → 19 produced "six journaled factors, all 1.00 against a one-share book" (docs/card-evidence.md:232);
  - enforce was flipped 2026-10-20 (`config/config.yaml:466`).
  - The roadmap had specified bounding "a card's marginal variance, **beta and sector contribution**" (docs/alpha-roadmap.md:521-523). Only total book volatility was built.
- **Bracket counterfactual v1** (docs/bracket-counterfactual-2026-10-18.md):
  - no exit arm beats the live 2R bracket;
  - the live trailing ratchet is worse by 0.013R, CI90 [−0.020, −0.006];
  - **every arm trails SPY by 0.10-0.15R**, and the raw positive means "are market exposure in rising tapes" (§What this says, items 2-3).
  - All arms shared the planned stop (§Protocol, "Arms"), so initial stop width was never varied.
- **Setup-outcome base rates**: stops hit 57-68% across strategies; the long trend-pullback earns +0.03R after cost (docs/setup-outcomes-2026-09-23.md §Base rates).
- **Native shorts disabled**: both strategies run `allow_short: false` (`config/config.yaml:305`, `:320`; docs/setup-baserates-short-2026-09-23.md). The native book is therefore structurally long-only.

## 5. Strengths worth preserving

- **One implementation per rule.** Rules are pure and the rule order is fixed in two composites (`rules.py:470-513`). The consistency tests make "the scan and admission disagree" a test failure, not an incident. This is the right foundation for every recommendation below: add rules here, never in callers.
- **Fail-closed exposure.** An unknown or invalid book position refuses everything (`rules.py:170-179`). At admission, untracked holdings, partial fills, unexpected protection or a mark through the stop all block new risk (`capacity.py:79-217`). Exposure is identified by exact order lineage, never by symbol (`capacity.py:69-76`).
- **Conservative re-valuation at admission.** Held positions are valued at broker marks and mark-to-stop giveback (`capacity.py:194-209`). Funding uses the lower of two account observations bracketing the read (`:264-266`).
- **A sound drawdown definition.** It is a cash-flow-adjusted high-water mark that restart or `/resume` cannot reset (docs/account-ledger.md:110-129).
- **Rules can only shrink size.** `macro_risk_factor` clamps to ≤ 1 (`capital.py:61-66`), `book_vol_factor` never raises size, and a probe cap only lowers the max tier (`position_sizing.py:105-108`).
- **Provenance.** Regime blocks, book-sizing decisions and rejection rule ids are journaled per card, so all the measurements proposed in §7 are possible without new plumbing.

## 6. Gaps, weaknesses, risks and inconsistencies

**G1. The de-risking levers mostly do not bite: the $7,500 per-trade notional cap is the binding constraint.**
- Production config: base risk $250, max risk 1% ($1,000), `max_trade_notional_cap` $7,500 (`config/config.yaml:337-343`).
- A base tier is notional-capped whenever the stop is tighter than 250/7,500 = 3.33% of price. With the 1.5× ATR floor, that is any name with ATR below about 2.2%.
- The max tier is notional-capped unless `dd×macro < 7.5×stop%`.
- I ran a read-only probe of `calculate_dynamic_sizing` with these caps (`~/agentic-trader-research/holistic-review-20261021/scripts/sizing_probe.py`). At a 1.5% stop:
  - drawdown factor 0.5 alone, or macro ×0.5 alone, leaves every tier unchanged at 75 shares / $7,500;
  - only the combined 0.25 shrinks the base tier;
  - the max tier stays at $7,500 in 15 of the 16 probed cases.
- So the drawdown haircut (`capital.py:45-58`), the macro-stress multiplier (`macro.py:250-269`) and their documented effect on "every sizing tier" (docs/account-ledger.md:131-139) are largely inert for ETFs and low-volatility names.
- The same arithmetic makes per-card dollar risk vary about 2.2× with stop width: $112 at a 1.5% stop versus $246 at 6%. Equal-R outcome statistics are therefore not equal-dollar.

**G2. No market-beta or net-exposure concept anywhere in the risk path.**
- Portfolio and class caps are gross notional (`rules.py:303-329`). The correlation rule counts same-direction members of configured groups (`:332-348`).
- The native book is long-only (G-evidence §4), and the measured outcomes are "market exposure" (bracket doc item 3). Even so, nothing measures or caps the book's SPY-equivalent dollars, and no card shows them.
- The roadmap's intended beta and sector bound for L3 (docs/alpha-roadmap.md:521-523) was not built.
- Book-vol's `0.8%/day × capital` budget (`config.py:279`) is barely reachable under the $40k equity class cap (`config.py:393`, not overridden in `config/config.yaml:1-8`). My arithmetic: $40k long at 2% daily volatility and pairwise ρ=0.5 is about $620/day, against an $800 budget. In effect, enforce adds no constraint for typical names (docs/card-evidence.md:232).

**G3. Dynamic-universe names escape the correlation rule.**
- Correlation groups are the defaults plus configured sector groups (`config.py:373-382`, `:1104-1107`; `UniverseConfig.sector_groups` skips "unknown", `config.py:163-169`).
- Dynamic names (up to 20 per scan from most-actives and movers, `config/config.yaml:259-269`) have no sector. `correlation_group` therefore never applies to them. Only the per-session card cap's `dynamic` pseudo-group limits them (`agent/copilot.py:614-617`), and that is a card budget, not a book rule (docs/risk-policy.md:429-432).
- The statistical correlation fallback is off and uses separate per-pair fetches (`evaluator.py:503-523`) instead of the covariance `BookSizer` already builds.

**G4. Gap and tail risk are unbounded by design, and the exposures are real.**
- Aggregate stop risk is 2% of capital (`config/config.yaml:3`; `rules.py:270-277`).
- My scenario: a −6% index day (S&P 500, 2025-04-04) on a full $40k equity class at β≈1.2 is about −$2.9k, roughly 2.9% of capital, before any single-name gaps.
- No scenario-loss, overnight-gap or CVaR rule exists. The survey's S4 (CVaR reporting defects, docs/alpha-stack-survey-2026-09-18.md:172-184) remains open.
- The earnings blackout runs only at entry: scan and tap only, admission has none (docs/risk-policy.md:106). It covers 7 calendar days (`config/config.yaml:279`).
- I found no earnings check for *held* positions anywhere in `execution/`, and no holding deadline for native strategy cards. Positions therefore ride into reports.

**G5. Calendar and regime failures are inconsistent and partly silent.**
- `ForexFactoryCalendar.fetch_events` swallows failures, caches an empty list for 30 minutes (`calendar.py:216-221`) and then tells the LLM "Lockout verified: CLEAR" (`calendar.py:156-160`). The earnings path, by contrast, labels the same situation "Unverified" (`agent/earnings.py:319-320`).
- When FRED is unavailable, the regime falls back to VIX/TNX/DXY with `risk_multiplier = 1.0`, even at VIX > 30 (`regime.py:155-189`). Sizing therefore fails open in exactly the stress case. Meanwhile the preflight fails *closed* if VIX itself is missing (`regime.py:168-169`; docs/risk-policy.md:331-333).

**G6. The regime and macro rules are uncalibrated heuristics that act as hard gates.**
- An inverted 10Y-2Y or 10Y-3M curve alone forces HIGH stress, which halves risk and suppresses squeeze breakouts (`macro.py:149-150`, `:255-259`). Inversion is a multi-quarter recession indicator, not a 5-session risk signal.
- It disables the strategy with the best measured base rate: squeeze 1h long, +0.08R after cost (docs/setup-outcomes-2026-09-23.md §Base rates).
- The regime's `min_rr` of 2.2 or 2.5 (`config.py:484-485`) changes bracket geometry. No protocol has tested it; the bracket study used the configured ratio only.

**G7. Capacity rules refuse instead of shrinking.**
- Sizing honors portfolio notional headroom (`position_sizing.py:95-96`), but not the equity-class cap, aggregate stop-risk headroom or book-vol. Those come later as refusals (`rules.py:270-277`, `:315-329`).
- A nearly full book therefore yields "rejected" runners-up instead of a smaller, book-consistent card. Collect-phase refusals are only logged (docs/risk-policy.md:455-458).
- Book rules are judged on the default tier only (docs/risk-policy.md:280).

**G8. Smaller inconsistencies.**
- **No joint per-trade budget at admission.** Admission checks `per_trade_risk` with observed equity and drawdown at macro 1.0 (`admission.py:87`). Separately, the regime gate checks configured cash, zero drawdown and the macro factor (`copilot.py:4018`). The product `equity×dd×macro` is never checked jointly after the scan, so a card carried overnight can exceed it.
- **Book-vol uses raw bars.** A split inside the 120-session window distorts variance (`book_sizing.py:222`; acknowledged at docs/card-evidence.md:262).
- **The probe risk cap is not an admission invariant.** `_alpha_entry_rejection` checks liveness and contract only (`storage/workflow.py:478-526`); this is a recorded follow-up at docs/alpha-roadmap.md:160-163.
- **`volatility_targeted` is mislabeled.** That sizing mode is plain fixed-fractional risk (`position_sizing.py:169-205`; survey §S3).
- **Original risk persists after a stop moves.** A position whose stop has ratcheted into profit still counts its original `risk_dollars` at the scan (`book.py:71`), and at least that at admission (`capacity.py:209`).

## 7. Improvement opportunities

Ordered by expected effect on the north star: 1-2 book-consistent, honestly sized cards per session.

1. **Make risk, not a fixed notional, the binding constraint** (fixes G1; small change in `agent/position_sizing.py:92-117`).
   - Apply `dd × macro` to the notional ceiling as well (`trade_notional_ceiling × effective_adjustment`).
   - Alternatively, size every native card to a constant ex-ante dollar volatility: `qty = target_$vol / (price × σ_daily)`, with σ taken from the covariance `BookSizer` already computes, and keep the notional cap well above typical sizes.
   - Expected effect: drawdown and macro de-risking actually reduce exposure; card risk becomes comparable in dollars; R statistics become dollar-meaningful.
   - Literature: drawdown-proportional exposure (Grossman & Zhou 1993, *Mathematical Finance*); volatility scaling (Harvey et al. 2018, "The Impact of Volatility Targeting", *JPM*).
   - Add a test pinning that a 0.5 drawdown factor shrinks every tier.
2. **A beta-dollar book view and an optional net-beta cap** (fixes G2).
   - Add SPY and the sector ETFs to the scan covariance and compute per-position β.
   - Put "Book: +$X SPY-equivalent; this card +$Y" on the card.
   - Add a rule `net_beta_dollars` in `risk/rules.py` (preview, then enforce, following the house pattern), or a suggested SPY hedge size.
   - Extend `book_vol_factor` to return marginal contribution to risk (`w_i(Σw)_i/σ_p`) and a cap on single-name risk share, completing the L3 spec (docs/alpha-roadmap.md:521-523).
   - Expected effect: cards become portfolio decisions, and outcome attribution separates beta from selection. That separation is what the bracket study's −0.10 to −0.15R excess says matters.
3. **Classify dynamic names into risk groups** (fixes G3). Options: SIC/sector from SEC company facts (the EDGAR lane already holds the user-agent plumbing), or a statistical cluster from the scan covariance (hierarchical clustering at ρ>0.7). Then retire the per-pair yfinance correlation check in favor of the same covariance. Expected effect: no stacking of correlated momentum movers.
4. **A scenario-loss rule** (fixes G4).
   - `Σ notional_i × (β_i × shock_mkt) + Σ gap_i`, where `gap_i` is a per-name 99th-percentile overnight gap from 250 sessions and the shock is a declared worst day. Budget it at, for example, 4% of capital, as a pure function in `risk/` with the house preview/enforce rollout.
   - Add a held-position earnings notice: when a held name reports within N days, emit an advisory exit or keep card (the A2 advisory-exit card design in docs/alpha-roadmap.md applies). This is the cheapest real tail-risk reduction available.
   - Literature: Rockafellar-Uryasev CVaR (already cited in the survey) only once joint scenarios exist.
5. **Honest calendar and regime degradation** (fixes G5).
   - Distinguish "fetch failed" from "no events". Render "Unverified" exactly like earnings, and add the published FOMC schedule as a static fallback; FOMC is the only lockout that hits the 14:35 scan.
   - In the VIX-only fallback, map VIX buckets to a multiplier through `compute_compound_macro_stress`'s VIX branch instead of 1.0.
6. **Put the regime gates under a predeclared replay** (fixes G6).
   - FRED history and the setup caches already exist. Test VIX-bucket and curve-inversion gating against the frozen setup population with the session-block bootstrap the desk already uses.
   - Candidate evidence-based conditioner: the momentum-crash state (trailing market return negative plus high realized volatility; Daniel & Moskowitz 2016, *JFE*; Barroso & Santa-Clara 2015, *JFE*).
   - Until the replay passes, run the breakout suppression in preview.
7. **Size to remaining headroom** (fixes G7). Compute `min(class headroom, stop-risk headroom, vol headroom)` in sizing, and refuse only below the minimum size. Surface collect-phase refusals in the digest. Expected effect: more sendable cards when the book is partly full.
8. **Untested stop width** (§4). Kaminski & Lo (2014, "When do stop-loss rules stop losses?", *J. Financial Markets*) show that stops add value only under return persistence. A stop near one daily ATR (a 4h-ATR floor, `evaluator.py:208`) with 57-68% stop-outs is a declared, untested choice. Run a risk-equalized stop-width arm protocol (1×/2×/3× daily ATR). Priority is below alpha work, because the index lag is an entry problem (bracket doc item 3).
9. **Hygiene.**
   - Use split-adjusted daily bars (Alpaca `adjustment="split"`) for the covariance.
   - Make the probe cap an admission invariant.
   - Add a joint `equity×dd×macro` per-trade check at preflight.
   - Rename `volatility_targeted`.
   - Decide on the ratchet: the 0.013R cost is measured, and turning it off needs its own protocol (bracket doc item 2).
10. **Outside tooling, only as needed.** skfolio or Riskfolio-Lib for risk-budgeting and CVaR primitives on top of CVXPY; cvxportfolio (Boyd et al.) for the eventual executable target book. Moreira & Muir (2017, *JF*) motivate book-level volatility management; Cederburg et al. (2020, *JFE*) caution that its out-of-sample gains are fragile, so treat it as risk control, not alpha.

## 8. Open questions

- How full has the live book actually been? The preview book was "one share" (docs/card-evidence.md:232). How often do `concurrent_positions`, `aggregate_stop_risk`, `asset_class_notional` or `correlation_group` fire? These are collect-phase log events only, and I could not query the journal.
- Since 2026-10-14, what share of scans journaled `breakout_allowed=false`, and was the cause the curve-inversion branch? The current curve state is unknown to me.
- Which tier does the operator tap, and what is realized loss versus planned R on stopped live trades (the gap-through frequency)? `cards outcomes` has fill slippage, but I did not read its live numbers.
- Is there any holding deadline or time exit for native strategy cards? The replay assumes a 20-session timeout (bracket doc §Protocol). I found none for native cards in `execution/lifetime_policy.py`, which governs alpha versions.
- Does the paper account's observed equity differ materially from the $100k mandate? That changes every budget through `risk_capital`.
- Should net-beta control be a hard cap, a card annotation, or a suggested hedge? That is an operator decision, given that the native strategies are long-only by measured necessity.
