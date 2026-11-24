# Map: sizing, book-aware sizing and portfolio optimization

Reviewed read-only at `db82817` (worktree `agentic-trader-intraday`). Paths are relative to the repo root.
"Now" = what the code does; "Recommend" = proposals. Arithmetic below uses the shipped `config/config.yaml`.

## 1. Purpose and scope

This layer decides how many shares a suggested card offers (three tiers), how the existing book limits that, and, in research only, how a set of calibrated forecasts would become a target portfolio. Live, it is stop-distance sizing under shared hard caps, plus one closed-form volatility-budget factor (`book_vol`) applied at card construction. The convex allocator, the forecast calibration/pooling and the persistent-book simulator are research/shadow artifacts with no path to orders.

## 2. Components and contracts

**Per-trade sizing (live).** `calculate_dynamic_sizing` (`agent/position_sizing.py:40-277`) sets `per_unit_risk = stop_distance x multiplier` (`:100`). It caps the max tier at the shared budget `per_trade_risk_budget` (`risk/capital.py:80-93`: `min(cash, equity) x max_risk_pct_cap x drawdown_factor x macro_factor`), at `max_trade_notional_cap` and at the remaining portfolio notional (`:95-96,109-117`). Under `static`, the base tier is `default_equity_risk_dollars` / per-unit risk (`:153-166`). Half, base and max tiers are then built (`:207-266`). The `volatility_targeted` branch (`:169-180`) uses `min(default_equity_risk_dollars, cash x target_risk_pct)` = min($250, $500), so for equities at the current config it is the same as `static`. Callers: `RiskEvaluator.calculate_levels_deterministic` (`agent/evaluator.py:165-288`) and the LLM-widened-stop re-size (`:302-360`).

**Shared risk rules.** These are pure functions over `Book`/`EntryIntent`/`RiskBudget`/`RiskLimits` (`risk/rules.py:1-10`). `book_gates` (`:470-488`) runs at scan time on EXECUTED signals plus this scan's cards. `admission_gates` (`:491-513`) runs at Execute on EXECUTED rows plus SUBMITTING reservations, with a broker-marked book on Alpaca (`docs/risk-policy.md` "Known divergences"). The portfolio-relevant rules:
- `aggregate_stop_risk` (`:270-277`): the sum of stop risks, which assumes perfect correlation.
- `portfolio_notional` / `asset_class_notional` (`:303-329`).
- `correlation_group` (`:332-348`): a count of same-direction names per configured sector group.
- The optional statistical-correlation check (`agent/evaluator.py:501-523`), off by default (`config.py:397`).

**Book-vol rule.** `book_vol_factor` (`risk/book_vol.py:72-111`) is pure and validated (symmetric, PSD, finite; `:39-69`). Write `vol(f)^2 = a + 2bf + cf^2`. The factor is the largest `f` in [0,1] with vol ≤ budget, computed with the numerically stable root (`:96-103`). It returns 1 when the full card fits, including a hedge (`:88-90`), and 0 when the book is already over budget (`:91-95`). Size never increases.

**Covariance.** `daily_returns` and `shrunk_covariance` (`research/setups/covariance.py:50-89`) use raw-adjustment frames only (`:20-26`) and closes strictly before `as_of` (`:28-40`). Returns are computed after an inner join, with no forward fill (`:43-47`). The shortest-history symbol is dropped until at least `min_observations` remain (`:70-76`). Estimation is sklearn `LedoitWolf` (`:87`).

**BookSizer service** (`execution/book_sizing.py`):
- `prepare` (`:158-186`) never raises; failures become `unavailable`.
- `_prepare`/`_build` (`:188-281`) build signed equity notional per symbol from `signals.notional_value` (`_book_weights`, `:126-141`; non-equity rows are skipped at `:130-131`). They gather scan frames and make one 20 s bounded `fetch_daily_many(..., adjustment="raw")` call for missing names (`:215-237`).
- `decide` (`:283-393`) exempts dry runs, drift, policy-locked and non-equity cards (`:317-328`). It prices the **default-tier** notional (`:339`) and, under `enforce`, applies `scale_sizing` to every tier (`agent/position_sizing.py:280-353`). A default tier below the minimum size is `BLOCKED`.
- `BookContext.with_card` (`:93-107`) adds each recorded card to the book. A card it cannot price makes the rest of the scan `unavailable`.
- `attach_shadow`/`_shadow_check` (`:395-474`) runs `ConvexAlphaPortfolioOptimizer` with `lower_bounds == upper_bounds == pinned` (`:434-445`). Nothing is free to optimize, so this only recomputes `w'Σw` through a second code path (spec D4, `docs/superpowers/specs/2026-10-09-book-aware-sizing-design.md`).

**Wiring and outputs.** The sizer is constructed in `agent/copilot.py:316-326`. It is prepared once per scan, after ranking (`:1205-1224`). It is decided per candidate after the LLM (`:1399-1426`), and each sent card joins the book (`:1562-1582`). Outputs:
- `decision_provenance["book_sizing"]` and the `📐` card line;
- the journal block (`execution/book_sizing.py:476-501`, `copilot.py:1892`);
- `RankedOutcome.BOOK_SIZING_BLOCKED` (`execution/durable.py:90`);
- the `cards outcomes` summary (`research/setups/outcomes.py:723`).

Config: `BookSizingConfig` (`config.py:268-295`) and `config/config.yaml:465-470`, with `enforce` since 2026-10-20, a budget of 0.8%/day, a 120-session lookback and a 60-observation floor.

**Research allocator stack.**
- `ConvexAlphaPortfolioOptimizer` (`research/alpha/optimizer.py:45-282`) is a CVXPY/Clarabel QP. The objective is `μ'w − ½λw'Σw − c‖w−w₀‖₁ − κ·se'|w|` (`:194-199`). Constraints: box bounds, which asset bounds may only tighten (`:151-153`); gross; dollar-neutral; factor; turnover; and group constraints. Strict `OPTIMAL` is required, followed by an independent primal check (`:212-232`); duals and margins are recorded (`:238-264`).
- `build_shadow_portfolio` (`research/alpha/portfolio.py:76-214`) refuses stale or unversioned snapshots, mixed forecast/risk contracts and **any** pending reservation (`:86-121`). Participation bounds trades, not holdings (`:151-155`). Position count uses a "preserve holders, then |μ|" heuristic (`:127-135`). Covariance is a sample estimate with a fixed 25% diagonal shrink (`:167-170`). Its only entry point is `alpha portfolio SNAPSHOT.json` (`cli/commands/alpha.py:678-731`).
- Forecast contracts: `ForecastContract`/`ForecastCalibration` (`research/alpha/forecasts.py:27-170`) fit a shrunk OLS with HAC standard errors and causal outcome-availability checks (`:86-143`). `combine_forecasts` (`:219-264`) pools them. They are produced only by `AlphaShadowService.observe` (`research/alpha/shadow.py:69-150`), and only for registry versions that have a qualification calibration.
- `simulate_book` (`research/alpha/persistent_book.py:65-225`) is a self-financing adjusted-price ledger with a daily identity check (`:172-182`). `dependence_blend` (`:228-293`) is a causal minimum-variance blend of score ranks. `compute_persistent_study` (`research/alpha/persistent_study.py:138-249`) runs the study.

## 3. Data flow (live suggestion scan)

1. COLLECT/evaluate. Screeners emit `ScreenerCandidate` with a heuristic `setup_quality` in [0,1] (`screeners/strategies.py:29-50`, "not validated alpha"). The evaluator builds the bracket (stop ≥ 1.5 ATR or the swing level; target at the required R:R) and sizes it with `calculate_dynamic_sizing`, using the scan's open notional, ledger drawdown, observed equity and the regime macro multiplier.
2. Candidates are ranked by `-setup_quality` (`copilot.py:1172-1177`). The book plays no part in the ranking.
3. `BookSizer.prepare` builds one Ledoit-Wolf covariance over the book plus **all** ranked equity candidates (`execution/book_sizing.py:201`).
4. Send loop. Budget refusals and the card policy run first (off in config, `config.yaml:454`). Then `evaluate_candidate` runs, with `book_gates` on EXECUTED rows plus earlier cards in this scan, and then the LLM. Then `decide` computes the factor on the default tier and scales every tier under `enforce`. The shadow solve is attached and the signal recorded. Finally the card's notional joins both `active_positions` and `BookContext`.
5. Tap: only per-trade rules run. Admission then re-runs `admission_gates` on a broker-marked book plus reservations, with funding and borrow evidence. **`book_vol` is not re-checked** (`docs/risk-policy.md:111-115`).
6. Research path, which never reaches orders: registry definitions feed `AlphaShadowService`, which produces calibrated forecasts for qualified versions (currently none active). The `alpha portfolio` CLI takes hand-assembled JSON and returns shadow weights.

## 4. Evidence: what has been tried

- **Book sizing, preview week 2026-10-09 → 10-19.** There were six journaled factors, all 1.00, "against a one-share book" (`config/config.yaml:466`; `docs/card-evidence.md` "Book-aware sizing"). `enforce` shipped 2026-10-20 (#125). The rule has never bound live.
- **Persistent ETF book** (`docs/alpha-persistent-book-2026-09-17.md`). 160 comparisons, zero passes. Netting cut slow-momentum turnover from 51.86× to 18.90× capital, but mean IC was about zero or negative everywhere. The dependence blend made +2.91% at primary costs and −0.36% under stress, with one 20-session block contributing 148.6% of the gain. Conclusion in that doc: allocation mechanics work, and there is nothing to allocate.
- **Forecast-to-fill review** (`docs/forecast-to-fill-review.md` F1–F8) and the hardening (`docs/forecast-contract-hardening.md`). These fixed participation-on-holdings (F1), horizon/units (F2), return-vol posing as forecast error (F3) and duplicate-family pooling (F4). They also removed uncalibrated Kelly and the divergent retuner. F5 (executable plans) remains open by design.
- **Funnel EV.**
  - Runner-up cards scored −0.17R (n 33). `setup_quality` was within noise of random (`docs/alpha-roadmap.md:513`, Oct 10 status).
  - On replayed history, every exit arm trails SPY by 0.10–0.15R. The native longs' raw positive R is market exposure (`docs/bracket-counterfactual-2026-10-18.md`, "What this says" §3).
  - The frozen setup ranker failed its holdout and did worse than `setup_quality` (`docs/setup-outcomes-2026-09-23.md`, summary).
- **What was planned versus delivered.** The Oct 8 plan was for book-aware sizing to bound "a card's marginal variance, **beta and sector contribution**" (`docs/alpha-roadmap.md:521-524`). Only variance shipped.

## 5. Strengths worth preserving

- One implementation per risk rule with typed inputs. Seeded tests check that the scan and admission agree (`risk/rules.py:1-10`; `docs/risk-policy.md` tests section). Any allocator should plug in as *more constraints over the same `Book`*, not a parallel engine.
- `book_vol` is pure and closed form, never raises size, and has explicit failure statuses. Missing data never blocks; unpriceable exposure makes the context `unavailable` instead of being ignored (`execution/book_sizing.py:93-107,211-214`).
- No lookahead or forward fill in the covariance (`covariance.py:28-47`).
- The research optimizer is disciplined:
  - strict optimality, an independent primal check, and recorded duals/margins (`optimizer.py:212-264`);
  - asset bounds that only tighten (`:151-153`);
  - a refusal to allocate through pending orders (`portfolio.py:105-106`);
  - trade-based participation (`:151-155`);
  - a forecast standard error that is truly estimation error (`forecasts.py:150-155`).
- The persistent-book ledger identity and the independent Decimal audit (`persistent_book.py:172-182`; results doc "Independent audit").
- The hard rule that shadow weights never reach orders, and the removal of uncalibrated Kelly.

## 6. Gaps, weaknesses, risks, inconsistencies

**G1 — The vol budget cannot bind under the current caps.**
- The configuration:
  - The equity class cap is the code default of $40,000 (`config.py:393`; `max_equity_exposure` is not set in `config/config.yaml:1-8`).
  - Per-trade notional is capped at $7,500 (`config.yaml:343`).
  - The budget is 0.8% × $100k = $800/day.
- Two worked cases:
  - At the full $40k equity cap with 2%/day names and ρ = 0.3, book vol is about $500.
  - Even perfectly correlated 2%-vol names reach exactly $800.
- So the factor is below 1 only for a nearly full book of high-volatility, highly correlated names. `enforce` is effectively a no-op, which matches the all-1.00 preview.

**G2 — Inconsistent caps.** `max_concurrent_positions: 8` with $7,500 per card implies $60k (`config.yaml:4-6`). The $40k equity class cap admits only about five full-size equity cards. Alpaca is equities-only, so the sixth is *rejected* by `asset_class_notional` (`rules.py:315-329`); sizing does not shrink it, because sizing only reads total notional (`position_sizing.py:95-96`).

**G3 — R is not a constant dollar unit.**
- Base risk is $250 with a $7,500 notional cap, so any card whose stop is closer than 3.33% of price is notional-capped. Its risk falls below $250; for example, an ETF with a 1.8% stop risks about $135 (`position_sizing.py:109-113,161`; `config.yaml:336-343`).
- Every research metric (card_stats `mean_r_cost`, the bracket counterfactual, the pooled-lane R labels) weights setups equally in R. Desk dollar P&L weights low-volatility names roughly half as much.
- The roadmap already lists live/research sizing divergence (`docs/alpha-roadmap.md:483`, L5 planned).
- `volatility_targeted` ≡ `static` for equities at this config (§2), so the mode label is misleading.

**G4 — Size carries no conviction.**
- No live expected-return input reaches sizing or selection:
  - `setup_quality` is a heuristic with no measured information (setup-outcomes doc).
  - `scan.shadow_ranker_artifact` is unset (`config.yaml:442-449`).
  - card_stats measured EV is displayed but never used for size (`copilot.py:1227-1240`).
  - Calibrated `AlphaForecast`s exist only for qualified versions (`shadow.py:96-120`), and none are active.
- Every card risks the same nominal $250 whatever its measured `(strategy, direction)` EV or sample size.

**G5 — The "shadow optimiser" holds no allocation information.** With every weight pinned (`execution/book_sizing.py:434-445`), it can only re-verify `w'Σw`. It produces no "allowed weight" or binding-constraint evidence, so no prospective evidence for allocator-based sizing is accumulating. The full allocator (`build_shadow_portfolio`) has no daemon producer and rejects any live reservation (`portfolio.py:105-106`).

**G6 — Beta and sector exposure are not measured.** The book context has no market or sector factor. Sector control is a same-direction count (`rules.py:332-348`) plus the per-session group card cap. The bracket study shows the card stream is long beta with negative excess, and nothing reports or bounds the book's beta-dollars.

**G7 — The raw-price covariance is vulnerable to splits.**
- `adjustment="raw"` (`book_sizing.py:222`; `covariance.py:20-26`) means a split within 120 sessions enters as a −50% (2:1) or −90% (10:1) return.
- Ledoit-Wolf shrinks toward `mean(diag)·I`, so one outlier inflates every name's shrunk variance.
- Under `enforce`, a book name with a recent split can push `vol_before` over budget. That gives factor 0 for every non-hedging card (`book_vol.py:91-95`), for up to 120 sessions.
- The daemon already captures corporate actions daily (`docs/corporate-actions.md`) but does not use them here. The limitation is documented, not mitigated (`docs/card-evidence.md` Limitations).

**G8 — The tier the operator can tap is not vol-checked.** The factor uses default-tier notional (`book_sizing.py:339`) and scales all tiers proportionally. A max tier above the default can breach the budget, and admission never re-checks `book_vol` (`docs/risk-policy.md:113-115`).

**G9 — The book is valued at recorded entry notional, not marks.** `_book_weights` reads `signals.notional_value` (`book_sizing.py:126-141`; `storage/db.py:859-870`), while admission marks to the broker (`docs/risk-policy.md:278`). A position that has doubled contributes half its true variance.

**G10 — Selection ignores the book, and sizing depends on order.**
- Ranking precedes sizing (`copilot.py:1172-1177`), and `max_cards_per_scan: 1` (`config.yaml:443`) means the first approved card wins regardless of how much it diversifies the book.
- The greedy rule gives earlier cards full size and squeezes later ones (`book_vol.py:88-90`), and open positions are never resized.
- A blocked card has already spent LLM budget (`copilot.py:1399-1401`).

**G11 — Coupled covariance sample (inferred from code, not observed).** One inner join spans the book plus every ranked candidate (`book_sizing.py:201`; `covariance.py:43-47`). A candidate with sparse IEX daily bars shortens the window for everyone. The drop loop removes the *shortest-history* name, not the sparsest (`covariance.py:74`), so it can drop a book name and make the whole context unavailable.

**G12 — Two risk models.** The live path uses Ledoit-Wolf (`covariance.py:87`); research uses a fixed 25% diagonal shrink (`portfolio.py:167-170`) and score-covariance shrink (`persistent_book.py:264-265`). Shadow and live comparisons are therefore not like for like.

**G13 — Horizon mismatch, and no tail measure.**
- The budget is one-day close-to-close volatility, while cards hold about 5.6 sessions (bracket doc table, `A0_live`).
- `aggregate_stop_risk` is a sum, which is conservative but ignores gaps. A stop is not a gap bound (`docs/alpha-stack-survey-2026-09-18.md` S3).
- No stress or CVaR number exists live, and the legacy CVaR report has sign errors (survey S4).

**G14 — The research book has only been tested on nine sector ETFs**, with adjusted-price units and no stop/bracket semantics (`docs/alpha-persistent-book.md`). It cannot evaluate the card stream that production actually generates.

**G15 (latent) — Equity-only book vector.** Non-equity rows are dropped silently (`book_sizing.py:130-131`). This is harmless in Alpaca (equities-only) mode, but it would understate volatility once futures (Tradovate) trade.

## 7. Improvement opportunities

Ordered by value to card quality per unit of effort. None of these create alpha. With measured EV ≤ 0, an honest allocator's main output is "size zero", which is information that agrees with `card_policy`.

1. **Make R a real dollar unit (G3, G2).** Choose one primitive and make the caps consistent with it.
   - Option (a): equal stop risk, with the notional cap raised to `R / min_stop_pct`.
   - Option (b): equal daily volatility contribution per card, `notional_i = k·capital/σ_i`, with the stop setting R.
   - Either way, add a gating reason "R capped by notional ($X)" when a cap binds, and set `max_equity_exposure` explicitly so that 8 × cap ≤ the class cap (or reduce the count).
   - Effect: card_stats EV and every R-space research result map linearly onto desk dollars. `cards outcomes` can then report dollar EV.
2. **Beta and sector in the book context (G6).** Add SPY and the nine sector ETFs (already in the universe) to the per-scan covariance. Compute candidate β_i = Σ_i,SPY/Σ_SPY and the book's net beta-dollars and sector dollars, and journal them with a card line ("book β$ +$18k → +$25k"). Then add a `max_book_beta_dollars` cap in preview mode.
   - Effect: the operator sees that a card is mostly a levered SPY bet, which the bracket study measured. It also enables a predeclared "β-hedged card" variant (a SPY short of β·notional) whose outcome is excess R directly.
3. **Split-safe risk model (G7, G12).** Fetch `adjustment="split"` (or `"all"`) for the risk model only, or mask the action-date return using the captured corporate actions. Move all estimators into `covariance.py`.
   - Effect: removes a false "block every card" failure that `enforce` can now trigger.
4. **Every offered tier fits (G8).** Compute the factor on the max-tier notional (or on each tier), so that "every tier on a card fits the vol budget" becomes an invariant the operator can rely on.
5. **Mark the book (G9).** Price `_book_weights` at the latest completed close already in `frames`, or from the shared broker positions snapshot, so that it matches admission's broker-marked book.
6. **Order-independent risk budgeting (G10, G1).**
   - Replace the total-budget-only rule with a per-card cap on marginal risk contribution, `x·(Σw)_c / vol ≤ budget/N_max`. This is equal-risk-contribution logic: Maillard, Roncalli & Teïletche 2010 (ERC); López de Prado 2016 (HRP) is a no-view alternative.
   - Use `−Δvol` as a tie-breaker inside the L3 card-selector seam (`docs/alpha-roadmap.md:494`), so that among approved candidates of similar quality the diversifying one is sent.
   - Effect: sizing becomes stable across scans, and the 2-cards/session budget goes to names that diversify.
7. **A real shadow allocator per scan (G4, G5).** Replace the pinned D4 solve with a free solve over approved candidates.
   - Bounds: [0, max-tier notional].
   - Expected return: μ_i = shrunk card_stats EV_R × stop%_i, with standard error from `n_mature` fed into the existing `alpha_standard_error` uncertainty term.
   - Risk: Σ from item 3, scaled to the median holding horizon (state the iid assumption).
   - Constraints: the existing caps as linear constraints plus a beta band.
   - Journal the suggested notional, the binding constraints and the duals.
   - Effect: this builds the prospective evidence that E2E stage B ("allocator sets suggested base size", memory roadmap) needs, without touching orders. `cards outcomes` can score allocator choice against `setup_quality` choice. It also gives `card_policy` a principled per-card EV threshold: trade only if μ_i exceeds round-trip cost plus the uncertainty penalty.
8. **Stress line (G13).** Compute historical-scenario daily P&L of the book plus the card over a longer split-adjusted window that includes 2020-03 and 2022 shocks, and journal worst-5-day and 95% CVaR (Rockafellar–Uryasev, already cited in the survey). This fills survey S3 without an optimizer.
9. **Desk replay (G14, L5).** The bracket study's 8,768 replayed setups (`docs/bracket-counterfactual-2026-10-18.md`) can be pushed through live sizing, caps, the 2/session budget and book_vol into a dollar equity curve versus SPY. The ledger identity in `persistent_book.py` can be reused. Effect: this answers the operator's real question, whether the card stream at desk caps beats the index in dollars, and shows how much the caps and selection change it.
10. **Conviction scaling, only once μ has evidence.** Use fractional Kelly with parameter-uncertainty shrinkage (Baker & McHale 2013, "Optimal betting under parameter uncertainty"; MacLean–Thorp–Ziemba), with f ∝ EV_R / E[R²] shrunk by SE². It should be capped by the existing per-trade budget and never raise above the max tier. Until EV > 0 is established, it correctly gives about zero.

**Outside work worth using as references (not new dependencies):**
- Grinold & Kahn: μ = IC·σ·z, and the fundamental law, as the score-to-μ bridge already noted in `docs/forecast-to-fill-review.md`.
- Moreira & Muir 2017 and Harvey et al. 2018 on volatility targeting: a regime-aware budget, beyond the coarse macro multiplier.
- Ledoit–Wolf analytical nonlinear shrinkage (2020): marginal for a book of ≤ 10 names.
- `cvxportfolio` (Boyd et al., already cited), `skfolio` and `Riskfolio-Lib`: independent validators for the risk-budgeting and CVaR formulations in tests. The forecast-to-fill review rightly declined a new optimizer framework.

## 8. Open questions

- What is the real distribution of stop% across sent cards, and how often does the $7,500 notional cap bind? This needs signals/journal reads, and running `copilot` was out of scope here.
- Is the live book really near-empty ("one-share book")? If the operator rarely taps, portfolio work matters far less than selection and alpha right now.
- Is the $40k equity class cap (the code default) intended alongside 8 positions × $7.5k?
- Is the desk mandate long-biased, or should native cards be judged and sized net of beta? This decides whether item 2 is a display or a constraint.
- At what granularity should μ be estimated? `(strategy, direction)` over 90 days is coarse and correlated through repeats (`docs/card-evidence.md` "Repeated setups").
- Should `book_vol` become an admission invariant, re-checked at broker marks? That would contradict the current "never shrink an offered tier" design choice.
