# Holistic review findings index

All 57 canonical findings, ordered by priority. Fact-check verdicts: 56 partially confirmed
(corrections applied), 1 confirmed, 0 refuted.

| ID | Priority | Horizon | Area | Finding |
| --- | --- | --- | --- | --- |
| [C01](findings-p0-p1.md#c01) | P0 | now (days) | research_methodology | Stop launching single-source, single-formula pooled fixed-set lanes: by their own power curve they cannot detect literature-sized edges, and the pending insider campaigns should not run as designed |
| [C09](findings-p0-p1.md#c09) | P0 | now (days) | operations | Restart-timed swing scans spend the session card budget off-slot and starve the 14:35 suggestion scan; separate evidence scans from card slots |
| [C18](findings-p0-p1.md#c18) | P0 | now (days) | signals | The only research-positive card source (PEAD) is starved, and its probe expires 2026-10-28 with no continue or kill rule |
| [C03](findings-p0-p1.md#c03) | P1 | now (days) | research_methodology | The measured record behind card lines and card_policy is a censored, repeat-counted, raw-R, wrong-feed mean that swung from −0.38R to +0.54R in 13 days; rebuild it as a shrunk estimator before any policy acts on it |
| [C04](findings-p0-p1.md#c04) | P1 | now (days) | product | Cards are judged by raw R or a beta-one SPY control: the native stream is ~0.9-beta exposure with alpha near zero, and a raw-R card_policy would certify beta as quality |
| [C06](findings-p0-p1.md#c06) | P1 | next (weeks) | alpha_mining | Spend the single historical confirmation window on one pre-registered, literature-signed composite (plus at most a few declared model specs), not on single formulas |
| [C08](findings-p0-p1.md#c08) | P1 | now (days) | risk | Allocation 'based on existing positions' is nominal: there is no beta or sector exposure model and no single book view, and label-based groups let index clones be carded twice |
| [C10](findings-p0-p1.md#c10) | P1 | now (days) | data | Live native scans and every research base rate compute the trigger on different bars (IEX raw UTC-4h vs SIP all-adjusted), and the live 4h clock changes meaning on 2026-11-01 |
| [C11](findings-p0-p1.md#c11) | P1 | now (days) | research_methodology | Native R is ill-conditioned by tight 4h-ATR stops: cost is about 0.08R per setup, the SPY control is uncosted, the vol_20 lead is plausibly a cost artefact, and stop width has never been tested |
| [C12](findings-p0-p1.md#c12) | P1 | now (days) | sizing_portfolio | R is not the live sizing unit: the $7,500 notional cap binds on ~84% of setups, so drawdown and macro de-risking are largely inert; size by volatility, not by equal R |
| [C19](findings-p0-p1.md#c19) | P1 | now (days) | research_methodology | The pending insider campaigns do not test Cohen-Malloy-Pomorski as cited, and the short-volume lane measured a different variable from the one its cited papers studied |
| [C20](findings-p0-p1.md#c20) | P1 | now (days) | data | Point-in-time data the desk already downloads every day is thrown away: the asset master, analyst consensus and headline revisions |
| [C21](findings-p0-p1.md#c21) | P1 | now (days) | alpha_mining | The a priori event design is the only one that has produced a deployable result: generalise its route to cards and feed it data already on disk |
| [C23](findings-p0-p1.md#c23) | P1 | next (weeks) | research_methodology | Detect information with the whole cross-section and both tails, then test the long-only card implementation separately: the top-decile-minus-pool statistic wastes up to 40% of t |
| [C24](findings-p0-p1.md#c24) | P1 | next (weeks) | alpha_mining | Replace hand-picked 8-formula sets with a frozen, literature-signed feature library, and add the cross-sectional DSL operators that have been requested repeatedly |
| [C25](findings-p0-p1.md#c25) | P1 | next (weeks) | signals | Test the earnings-announcement premium: a long-only, days-long, large-cap effect that the 7-day earnings blackout structurally excludes |
| [C31](findings-p0-p1.md#c31) | P1 | now (days) | product | There is no north-star scorecard: the evidence journal is untyped and unversioned, and the funnel's denominators are not journaled |
| [C36](findings-p0-p1.md#c36) | P1 | now (days) | data | Live indicators and the book-vol covariance use raw prices across splits; under enforce, one split in a held name can block every card |
| [C37](findings-p0-p1.md#c37) | P1 | next (weeks) | research_methodology | One 20-session long bracket labels every hypothesis, and 'the bracket is closed' was generalised from a test of native exits |
| [C41](findings-p0-p1.md#c41) | P1 | now (days) | signals | Stop investing research in native technical setups beyond honesty and parity fixes |
| [C52](findings-p0-p1.md#c52) | P1 | now (days) | product | The operator loop is broken: cards are buried under ops traffic and the operator's judgment is never captured |
| [C54](findings-p0-p1.md#c54) | P1 | now (days) | architecture | Config loading is hand-wired, silently drops blocks and ships dead knobs that misdescribe behaviour |
| [C02](findings-p2.md#c02) | P2 | next (weeks) | alpha_mining | Mining has no cadence and the last confirmation window is ungoverned: add a daily prospective scorer, scheduled refits and a window-spending rule, and be honest about forward power |
| [C05](findings-p2.md#c05) | P2 | next (weeks) | research_methodology | The decisive pooled statistic is not neutral to risk, beta or cost in R units: store risk characteristics in the cube, use matched or residualised controls and a shorter volatility-scaled training target |
| [C07](findings-p2.md#c07) | P2 | next (weeks) | data | Cohort membership is look-ahead for the discovery window, and the survivorship repair was deferred on a misread window: build a point-in-time cohort v4 before the next confirmation spend or any model fit |
| [C13](findings-p2.md#c13) | P2 | next (weeks) | architecture | There is no typed Forecast or deployment contract, so no mining or model output can reach a card without bespoke code |
| [C15](findings-p2.md#c15) | P2 | next (weeks) | alpha_mining | Each new data source costs about ten touch points, cannot be combined with other sources and has no live path: replace per-lane plumbing with one FieldSource contract |
| [C16](findings-p2.md#c16) | P2 | next (weeks) | data | There is no reference data (industry, shares outstanding, filing-dated events); two free nightly SEC bulk files plus Ken French's definitions supply it |
| [C17](findings-p2.md#c17) | P2 | later (months) | data | There is no shared incremental point-in-time bar and field store: live refetches a year of history per scan, research re-downloads per lane, and raw evidence pages fill a disk that is 96% full |
| [C22](findings-p2.md#c22) | P2 | next (weeks) | data | PEAD runs on events with no timing, survivors only and today's consensus; free EDGAR data fixes all three |
| [C26](findings-p2.md#c26) | P2 | next (weeks) | product | The card does not present an allocation decision: tiers collapse at the cap and the book line always reads x1.00 |
| [C27](findings-p2.md#c27) | P2 | later (months) | research_methodology | No desk-level dollar replay exists: research verdicts are setup-weighted R while the desk sizes at near-equal notional |
| [C28](findings-p2.md#c28) | P2 | next (weeks) | execution | Native cards trade an execution policy that research measured as adverse: resting GTC entries, a value-destroying R-ratchet and no time exit |
| [C29](findings-p2.md#c29) | P2 | now (days) | research_methodology | The per-symbol DSL lane cannot promote anything: its holdout DSR double-counts the lifetime trial family, and its 16 shadow registrations are permanently invalid. Fix it in validation policy v2 or retire it explicitly |
| [C30](findings-p2.md#c30) | P2 | now (days) | signals | The LLM gate selects nothing, runs after nothing has been decided, has no deadline and approves on any error with raw exception text on the card |
| [C32](findings-p2.md#c32) | P2 | next (weeks) | research_methodology | Test news tone prospectively on the journal already being written; any historical LLM study of cached headlines is look-ahead contaminated unless entities are anonymised and the test window postdates the model's training cutoff |
| [C33](findings-p2.md#c33) | P2 | next (weeks) | data | Start capturing daily option-implied data now: the literature's best weekly-horizon family for liquid names cannot be bought back later |
| [C38](findings-p2.md#c38) | P2 | next (weeks) | sizing_portfolio | The book-vol rule is a no-op by construction and sizes against the wrong book |
| [C40](findings-p2.md#c40) | P2 | next (weeks) | research_methodology | Decompose the native trigger against a same-name random-time placebo and a same-session pool, not only against SPY at β=1 |
| [C42](findings-p2.md#c42) | P2 | next (weeks) | risk | Macro and regime gates are uncalibrated and fail open: curve inversion suppresses the setup that did best during the only inversion in the replay, and a calendar fetch failure renders 'CLEAR' |
| [C45](findings-p2.md#c45) | P2 | next (weeks) | data | Dynamic in-play names compete for the two cards but are unmeasured; keeping the dropped per-bar trade count would make their screen replayable |
| [C48](findings-p2.md#c48) | P2 | next (weeks) | data | Costs are a flat 5 or 15 bp everywhere, although per-name spread estimates and journaled arrival quotes are free |
| [C50](findings-p2.md#c50) | P2 | next (weeks) | data | The binding research resource is calendar time (5.4 discovery years on Alpaca SIP): buy long survivorship-free history, or borrow it through published factor returns |
| [C51](findings-p2.md#c51) | P2 | next (weeks) | data | Rank the remaining free sources by fit to the traded horizon, persist macro history for gate replay, and skip 13F, Google Trends and ETF flows |
| [C53](findings-p2.md#c53) | P2 | next (weeks) | execution | A single unmodelled ledger activity or partial fill blocks every card and entry |
| [C14](findings-p3.md#c14) | P3 | later (months) | signals | Card selection is an inline sort on a key measured as uninformative; build L3 as an injected selector with shadow challengers |
| [C34](findings-p3.md#c34) | P3 | later (months) | signals | Meta-labelling native setups already lost in its holdout: any L3 meta-label needs a different target, features and selection metric, should withhold rather than rank, and should be expected to help only modestly |
| [C35](findings-p3.md#c35) | P3 | next (weeks) | alpha_mining | Use LLMs as label-blind feature curators, not as formula search engines; do not build an Alpha-GPT, AlphaAgent or RD-Agent-style mining loop on this desk's data |
| [C39](findings-p3.md#c39) | P3 | next (weeks) | research_methodology | Replace fixed t≥3 gates and single-shot windows with power-aware thresholds, an alpha-spending plan and anytime-valid prospective tests |
| [C43](findings-p3.md#c43) | P3 | later (months) | risk | Gap, scenario and earnings risk on held positions is unbounded, while the stop-risk budget is read as a loss limit |
| [C44](findings-p3.md#c44) | P3 | later (months) | sizing_portfolio | The 'shadow optimiser' pins every weight and produces no allocation evidence; replace it with a free per-scan shadow allocator |
| [C46](findings-p3.md#c46) | P3 | later (months) | signals | Use the literature's negative short-horizon predictors as vetoes on native long cards, especially dynamic movers |
| [C47](findings-p3.md#c47) | P3 | later (months) | alpha_mining | Re-test reversal as the literature states it: one-month 'quiet' reversal, residual and VIX-conditioned, on horizon-matched labels |
| [C49](findings-p3.md#c49) | P3 | next (weeks) | alpha_mining | The pooled genetic search never used fitness, so check B's failure measured random sampling rather than search power |
| [C55](findings-p3.md#c55) | P3 | later (months) | architecture | Research and live import each other with no enforced boundary, and the shared kernels live under research/ |
| [C56](findings-p3.md#c56) | P3 | later (months) | architecture | Stop carrying dormant one-shot harnesses and contract-violating legacy components on main |
| [C57](findings-p3.md#c57) | P3 | later (months) | architecture | Borrow definitions and test oracles from open-source quant tooling; do not adopt frameworks, and avoid mlfinlab |
