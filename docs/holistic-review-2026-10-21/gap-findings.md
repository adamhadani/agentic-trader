# Gap findings (23)

Findings added by the gap-fill round after the completeness critic. They are integrated in the
report (marked **[added in revision]**) but were not separately fact-checked or value-rated.

## LLM formula proposers (AlphaAgent / RD-Agent(Q) / Alpha-GPT) need a fixed-set route and a proposer-cutoff guard the protocol cannot express today

_area: alpha_mining · kind: integration · impact: medium · effort: M · confidence: high_

**Claim**

No LLM is involved in alpha mining today. The only proposer is TypedGeneticSearch, built directly inside family_search (agentic_trader/research/pooled/genetic.py:147). Each proposal passes vet_expression (agentic_trader/research/pooled/scoring.py:82-96: compile, duplicate within the campaign, forbidden operator, dimensionless) and is then charged before it is scored (genetic.py:172-186 -> research/pooled/ledger.py:82-87 -> storage/alpha.py:1096-1124). A resume assumes the proposer is deterministic: 're-proposes the same formulas, whose charges are no-ops' (storage/alpha.py:1047-1052). The roadmap already plans to run RD-Agent(Q) 'behind our existing DSL, trial ledger and validator' (docs/alpha-roadmap.md:865-867). The stack review lists 'public-benchmark contamination' as the only LLM risk (docs/alpha-stack-review.md:384, section E3). Neither document records the binding constraint. Every pooled protocol uses the same calendar: discovery 2016-08-01->2021-12-31, selection 2022-01-03->2023-12-29, confirmation 2024-01-02->2026-07-31 (the `windows` block of every config/research/pooled/*campaign*/*-v1.json). Any usable LLM was pretrained on text from inside those windows. The protocol model has no field for proposer identity (CampaignProtocol, research/pooled/campaign.py:246-289), and the immutable reservation fields (storage/alpha.py:40) carry none either. So nothing can refuse a campaign whose proposer had already seen the period it is judged on. The confirmation window can be used only once across the whole lane (storage/alpha.py:1141-1155), and it is still unused (docs/alpha-pooled-short-v1-2026-10-20.md, Campaigns / cohort v3, lines 106-107). An iterative AlphaAgent- or RD-Agent-style feedback loop is adaptive search, which this lane classes as `genetic`. That mode is parked because check B failed twice (docs/alpha-pooled-checks-v3-2026-10-05.md, 'Check B — search power (failed)' and 'Decision').

**Evidence**

- agentic_trader/research/pooled/genetic.py:147 (TypedGeneticSearch hard-wired), :172-186 (vet -> charge -> score), :239 (seen-set is per campaign)
- agentic_trader/research/pooled/scoring.py:82-96 (vet_expression)
- agentic_trader/research/pooled/ledger.py:35-101 (JournalLedger charge / consume_confirmation)
- agentic_trader/storage/alpha.py:40 (POOLED_IMMUTABLE has no proposer identity), :1047-1052 (resume relies on deterministic re-proposal), :1096-1124 (charge_pooled_formula), :1141-1155 (lane-wide single-use confirmation)
- agentic_trader/research/pooled/campaign.py:87-99 (StageWindows), :102-104 (Family.rationale), :246-321 (CampaignProtocol fields and _consistent validator)
- config/research/pooled/campaign-v4.json, cohort3-*-v1.json, short-v1.json, insider-v1.json: identical windows 2016-08-01/2021-12-31, 2022-01-03/2023-12-29, 2024-01-02/2026-07-31
- agentic_trader/config.py:871, :989 (llm_model default 'openai/gpt-4o', an alias, env-overridable)
- docs/alpha-roadmap.md:865-867 (RD-Agent(Q) planned behind ledger, no cutoff constraint)
- docs/alpha-stack-review.md:384 (E3 table, LLM-assisted hypotheses row)
- docs/alpha-pooled-checks-v3-2026-10-05.md 'Check B — search power (failed)', 'Decision'
- docs/alpha-pooled-mining-research-2026-09-28.md:51 (AlphaAgent S&P IC 0.0056 ~ 0.02R), :94 (0.080R detection floor at t=3; 0.092R in 650 confirmation sessions)

**Recommendation**

Bring in an LLM proposer only as a one-shot author of a fixed set, never as an in-loop proposer. (1) Add a research-only command, run outside the daemon and not exposed as a copilot chat tool. It prompts a model pinned by dated snapshot (not the 'openai/gpt-4o' alias) at temperature 0 with tools and retrieval disabled. The prompt carries the DSL grammar, the cached field catalog (daily, events, short_fields_v1, insider_fields_v1) and the list of expressions the lane has already charged. It carries no data and no fitness. The command writes the response into a fixed_set protocol file whose new `proposer` block {model_snapshot, documented_knowledge_cutoff, prompt_sha256, response_sha256} is therefore pinned by protocol_sha256. After that the protocol follows today's path: Family validators, vet_expression, checks A and C, operator approval, charge before read. (2) Extend CampaignProtocol._consistent (campaign.py:291-321). When `proposer` is present, refuse the protocol unless windows.confirmation[0] >= knowledge_cutoff + a margin (for example 180 days, to cover post-cutoff tuning data), and refuse search_mode='genetic'. Label the discovery and selection reads as contaminated filters in the result document. With a gpt-4o snapshot (cutoff about October 2023), the 2024-01-02 confirmation window qualifies. Any 2025-26 frontier model fails this guard, so its proposals need a prospective scorer: freeze the formulas at time T and score only sessions after max(T, cutoff). No such scorer exists for pooled formulas today; DailyPanelService (research/alpha/daily_observations.py:56) is the only prospective worker. (3) Apply AlphaAgent's regularisers as vetting before any charge, not as fitness terms. Originality: the largest common AST subtree against every expression already charged lane-wide (pooled/formula/* journal keys written at storage/alpha.py:1104-1121). Today vet_expression only dedupes within one campaign (genetic.py:239), and an LLM prior anchored on the literature will re-propose the 64 pairs already tested. Alignment: an LLM-judged check that each Family.rationale (campaign.py:104) matches its seeds, recorded but never a scoring input.

**North-star impact**

Brings in cheap, auditable breadth of hypotheses over fields the lane already caches. It does so without spending the single-use confirmation window on evidence the proposer may have memorised, so no contaminated 'finalist' can reach card construction or a probe. Expectations should stay low: the lane's own translation of AlphaAgent's S&P 500 IC (0.0056) gives about 0.02R, against an 0.08-0.10R detection floor. The likely value lies in which new fields and families to acquire, not in more price/volume formulas.

**References**

- Tang et al. (2025), AlphaAgent: LLM-Driven Alpha Mining with Regularized Exploration to Counteract Alpha Decay, arXiv 2502.16789 — Source of the originality regulariser (largest isomorphic common AST subtree against an alpha zoo) and the LLM-scored hypothesis-description-formula alignment. Its S&P 500 test runs 2021-01 to 2025-01 on a GPT-3.5-turbo backbone, and the paper does not discuss pretraining cutoffs. (https://arxiv.org/html/2502.16789v2)
- Li et al. (2025), R&D-Agent-Quant, NeurIPS 2025 Datasets & Benchmarks, arXiv 2505.15155; code microsoft/RD-Agent — The main CSI 300 test (2017-2020) with GPT-4o/o3-mini ignores cutoffs. Its NASDAQ-100 extension (test 2024 to 2025-06) was chosen so that the backends' cutoffs fall 'completely or nearly prior to the test period'. That is the design to copy. (https://arxiv.org/html/2505.15155v2)
- Wang et al. (2023/2025), Alpha-GPT: Human-AI Interactive Alpha Mining, arXiv 2308.00016 (EMNLP 2025 demos) — Human-in-the-loop formula proposal, which matches an operator-approved fixed set. (https://arxiv.org/abs/2308.00016)
- Lopez-Lira, Tang & Zhu (2025), The Memorization Problem: Can We Trust LLMs' Economic Forecasts?, arXiv 2504.14765 — Finds that models recall exact economic figures before their cutoff, that masking and instructions do not stop this, and that recall disappears after the cutoff. This justifies a hard cutoff guard rather than prompt hygiene. (https://arxiv.org/html/2504.14765v2)
- Sarkar & Vafa (2024), Lookahead Bias in Pretrained Language Models — Direct tests of temporal look-ahead in LLM outputs. Prompting fixes are limited, and the recommended remedy is models trained only on text produced before the analysis period. (https://conference.nber.org/conf_papers/f205530/f205530.pdf)

---

## Text-signal research (news tone, filings) has no scorer-vintage contract; replaying today's LLM over the cached 2015+ headlines would measure memorised outcomes

_area: data · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

The news research acquisition caches Benzinga headlines back to 2015-01-02 (docs/news-context.md:154, 'Research acquisition'). The document names 'post-news drift conditioned on tone' as the next frozen entry (docs/news-context.md:179). The only tone scorer is the live, direction-free LLM call, which uses `policy.model or config.llm_model` (agentic_trader/agent/news_context.py:290); the code default is the moving alias 'openai/gpt-4o', overridable by environment (config.py:871, :989). No research protocol in the apriori or pooled lanes declares a scorer model or cutoff, or refuses windows that precede it. Replaying the live scorer over the 2016-2023 headlines therefore falls inside its training period. Filings text, the basis of Kim-Muhn-Nikolaev, is not acquired at all: the EDGAR acquisition reads only the structured Form 3/4/5 data sets (agentic_trader/research/insider_filings.py:1-12, :59-61). One part is already right: every live news block pins `model` and `prompt_sha256` (news_context.py:49, :166-169). Since 2026-10-13 the desk has been accumulating a sample that falls after the scorer's cutoff.

**Evidence**

- docs/news-context.md:154 (history reaches 2015-01-02), :157-180 ('Research acquisition'; 'No study exists yet. A frozen news entry (for example post-news drift conditioned on tone)...')
- agentic_trader/research/news.py:1-8 (acquisition only; no scoring)
- agentic_trader/agent/news_context.py:49 (NEWS_PROMPT_SHA256), :166-169 (block records model and prompt hash), :290-291 (model = policy.model or config.llm_model)
- agentic_trader/config.py:871, :989 (default 'openai/gpt-4o' alias via LLM_MODEL)
- agentic_trader/research/insider_filings.py:1-12, :59-61 (Form 3/4/5 data sets only; no 10-K/10-Q/8-K text)

**Recommendation**

(a) Add a text-scorer contract that every frozen text-field spec (apriori entry or pooled field family) must satisfy. It declares {scorer_model_snapshot, documented_knowledge_cutoff, prompt_sha256}. A validator refuses any discovery, selection or confirmation window that starts before cutoff + margin, unless the scorer is chronologically consistent: a ChronoBERT/ChronoGPT vintage no later than each decision year, or a dictionary pinned by vintage such as Loughran-McDonald. Anonymising entities is reported as a diagnostic, not as a substitute (Glasserman-Lin; Lopez-Lira-Tang-Zhu). (b) Build the first news study prospectively. Freeze the tone -> drift rule now and read it only on the journaled live blocks, which come after gpt-4o's cutoff. In parallel, run a historical replay with a point-in-time ChronoBERT scorer as the uncontaminated backtest, and keep the two reads separate. (c) If filings text is added through the existing EDGAR client (the User-Agent is already handled), copy Kim-Muhn-Nikolaev's design: anonymised, standardised inputs, with the decisive test on outcomes released after the model's cutoff (their FY2023 test after GPT-4-Turbo's April 2023 cutoff). Never read a 2016-2021 discovery window with a modern LLM.

**North-star impact**

News is the only LLM-derived signal on live cards today. A backtest that reads memorised outcomes would make tone look predictive and could push it into ranking, card_policy or a probe. With a vintage contract, a text signal can earn a place on cards honestly, and the desk's own post-cutoff news journal becomes the asset that counts.

**References**

- Glasserman & Lin (2023), Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis, arXiv 2309.17322 — GPT-3.5 headline sentiment: the in-sample gap between anonymised and original headlines loses significance out of sample, which the authors attribute mostly to look-ahead. This is the exact setup of a replayed news-tone study. (https://arxiv.org/abs/2309.17322)
- He, Lv, Manela & Wu (2025), Chronologically Consistent Large Language Models (ChronoBERT/ChronoGPT), arXiv 2502.21206 — Vintage-dated models trained only on text available at each date, evaluated on news -> next-day returns. Usable as a point-in-time scorer for historical replays. (https://arxiv.org/abs/2502.21206)
- Kim, Muhn & Nikolaev (2024), Financial Statement Analysis with Large Language Models, arXiv 2407.17866 — Template for a filings signal: anonymised statements, sample 1968-2021, plus a clean out-of-sample test on FY2023 earnings released after GPT-4-Turbo's April 2023 training cutoff. (https://arxiv.org/abs/2407.17866)
- Lopez-Lira, Tang & Zhu (2025), The Memorization Problem, arXiv 2504.14765 — Masking and boundary instructions do not stop recall before the cutoff, so only time-pinned evidence is valid. (https://arxiv.org/html/2504.14765v2)

---

## The card gate's llm_verdict lacks model and prompt identity, so llm_gate attribution pools every model and prompt that ever ran

_area: research_methodology · kind: defect · impact: low · effort: S · confidence: high_

**Claim**

The journaled native-card verdict records only approved, rejection_reason, stop_loss, take_profit and applied (agentic_trader/agent/evaluator.py:859-865). The news block records `model` and `prompt_sha256` (agent/news_context.py:166-169); the verdict records neither. The gate model is environment-switchable (config.py:989) and is logged only as LangSmith metadata (evaluator.py:153-156). `cards outcomes` averages vetoed and approved R into a single llm_gate summary with no model or prompt key (agentic_trader/research/setups/outcomes.py:936-956). A model swap or prompt edit therefore mixes regimes silently in the only measurement of the LLM gate. Separately, an offline comparison of a newer model against journaled candidates (say, re-gating 2026 cards) would be contaminated whenever that model's cutoff falls after the candidates' dates, and nothing records which dates are safe.

**Evidence**

- agentic_trader/agent/evaluator.py:859-865 (llm_verdict fields), :153-156 (model only in LangSmith log extra)
- agentic_trader/agent/news_context.py:49, :166-169 (news block pins model and prompt hash)
- agentic_trader/research/setups/outcomes.py:936-956 (llm_gate summary pooled across all rows)
- agentic_trader/config.py:989 (LLM_MODEL env override)
- docs/production.md:611 (llm_gate compares vetoed and approved mean R)

**Recommendation**

Add `model` (the resolved dated snapshot the provider returned, not the alias) and `prompt_sha256` (gate system prompt plus response schema) to llm_verdict at evaluator.py:859. The verdict is journaled JSON in decision_provenance, so no migration is needed. In cards outcomes, group llm_gate by (model, prompt_sha256) and report the 'all' row alongside the groups. Any offline harness that replays the gate on journaled candidates should take a declared knowledge cutoff for the model and refuse candidates dated before cutoff + margin. This gives the operator's existing rule of verifying prompt changes with traces and the replay harness a leakage-safe evaluation set.

**North-star impact**

Makes the LLM gate's measured contribution attributable to a specific model and prompt. A future upgrade of the gate model, or its retirement, then rests on clean evidence rather than a pooled average, and replay-based A/B tests of gate models cannot reward memorisation.

**References**

- Lopez-Lira, Tang & Zhu (2025), The Memorization Problem, arXiv 2504.14765 — Finds no recall after the cutoff: a gate evaluated on candidates dated after the model's cutoff is a valid test, one evaluated before it is not. (https://arxiv.org/html/2504.14765v2)
- Look-Ahead-Bench (2026), A Standardized Benchmark of Look-ahead Bias in Point-in-Time LLMs for Finance, arXiv 2601.13770 — Benchmark for checking whether a candidate gate or scorer model leaks look-ahead before it is used in any replay. (https://arxiv.org/abs/2601.13770)

---

## Accounting data is absent from code, maps and roadmap; the as-filed SEC route is cheap and reuses the insider acquisition

_area: data · kind: gap · impact: medium · effort: M · confidence: high_

**Claim**

The repo has no accounting data at all. A grep for xbrl, companyfacts and fundamental over agentic_trader/ and config/ returns zero hits. The setup features used by both the replay and the live shadow ranker are price-only: CROSS_SECTIONAL lists momentum, reversal, vol, distance from high, dollar volume, residual and sector-relative momentum (agentic_trader/research/setups/features.py:44-53, computed per date in cross_section at :221-248, live via copilot.py:1820-1831). The pooled cube stores no characteristic arrays (agentic_trader/research/pooled/cube.py:53). The desk's own assessment ranks EDGAR XBRL #4, 'third, as a conditioning test' (docs/alpha-data-augmentation-2026-10-18.md:45, §2 row 4) and lists 'Fundamentals conditioning' as step 4 (:91-92). Steps 1-3 have since been built and run (short volume, insider), but step 4 has no spec, no plan and no roadmap slot. Earlier docs block it on prerequisites: 'filing/availability times, revisions, historical membership' (docs/alpha-factor-research-plan.md:41-47) and 'publication-time, revision and corporate-action evidence first' (docs/alpha-stack-survey-2026-09-18.md:206-207). The SEC's own data meets the first two of those. A trap also exists: the XBRL frames API 'aggregates one fact for each reporting entity that is last filed', so a frames-based history is restated, which is lookahead. The insider lane already has nearly all the plumbing for the same shape of acquisition: quarterly SEC data-set zips, the paced User-Agent client, the digest-pinned store and the ticker-to-CIK map (agentic_trader/research/insider_filings.py:59-61, 134-280, 354, 574-596).

**Evidence**

- grep -rniE 'xbrl|companyfacts|fundamental' agentic_trader config -> no matches
- agentic_trader/research/setups/features.py:44-53 (CROSS_SECTIONAL price-only), :221-248 (cross_section)
- agentic_trader/agent/copilot.py:1820-1831 (live shadow ranker uses the same live_cross_section)
- agentic_trader/research/pooled/cube.py:53 (_ARRAYS: no characteristic arrays)
- docs/alpha-data-augmentation-2026-10-18.md:45 (§2 row 4), :91-92 (§4 item 4)
- docs/alpha-factor-research-plan.md:41-47 ('Useful hypotheses with present data': accounting items need filing/availability times, revisions)
- docs/alpha-stack-survey-2026-09-18.md:206-207 ('How to widen the funnel usefully')
- agentic_trader/research/insider_filings.py:59-61 (SEC_HOST, TICKERS_URL), :134-280 (resolve_archive_urls, validate_archive, InsiderFilingStore), :354 (acquire_insider_filings), :574-596 (symbol_ciks)
- SEC Financial Statement Data Sets doc (sec.gov/files/financial-statement-data-sets.pdf): data 'as filed', submissions from 4/15/2009, SUB has filed, accepted (datetime), sic, prevrpt ('submission information was subsequently amended'); 'updated quarterly'

**Recommendation**

Build a research-only `alpha xbrl-statements` acquisition of the SEC Financial Statement Data Sets (quarterly zips, 2009q2 onward). Clone the InsiderFilingStore/Quarter/resolve_archive_urls pattern and reuse symbol_ciks. Pin each zip by SHA-256. Keep sub.txt (adsh, cik, sic, form, period, fy, fp, filed, accepted, prevrpt) and the num.txt rows with no coreg and no segments. Visibility rule: a value is usable from the first regular session that opens after `accepted`. Since every lane already reads at D-1, require accepted < D-1 close. Take 'as first filed' as the min(accepted) row per (cik, tag, ddate, qtrs). An amendment becomes visible only at its own `accepted` time. Never use the frames API or the `frame`-tagged rows. Predeclare a small, robust field set with a frozen tag-priority list (the ASC 606 revenue-tag switch in 2018 is the main mess): asset growth (Assets vs four quarters earlier; Cooper-Gulen-Schill), ROE (NetIncomeLoss over lagged StockholdersEquity; HXZ q-factor), gross profitability ((Revenues - CostOfRevenue) or GrossProfit over Assets; Novy-Marx), and net issuance (12-month log change in split-adjusted weighted diluted shares, with split factors from the existing corporate-actions cache; Pontiff-Woodgate). Leave Sloan accruals out of the primary set, because cash-based operating profitability subsumes them (Ball et al. 2016) and the anomaly has decayed (Green-Hand-Soliman 2011). SUB's `sic` column also gives the point-in-time SIC codes that maps/data.md I6 wants for group_demean. For a live path, FSDS posts quarterly, which is too stale. Live would need nightly companyfacts.zip (republished ~3 a.m. ET) with the same first-filed dedupe, plus a parity test that it matches the FSDS as-filed value on overlapping filings, as maps/data.md I8 asks for the other research parsers.

**North-star impact**

This opens the one free, point-in-time source that describes the firm rather than its tape. It gives the native card funnel a quality/investment dimension it cannot see today. The same artefact supplies the SIC groups the cross-sectional operators need, so later lanes and card attribution get it at no extra cost. Building on as-filed data from the start avoids a lookahead-contaminated first result, which would be expensive to retract.

**References**

- SEC, EDGAR Application Programming Interfaces (data.sec.gov): frames API 'aggregates one fact for each reporting entity that is last filed'; companyfacts.zip/submissions.zip recompiled nightly — Shows the frames API serves restated values (lookahead) and gives the nightly bulk route for a live path (https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- SEC DERA, Financial Statement Data Sets documentation: 'as filed', from 4/15/2009, SUB fields filed/accepted/sic/prevrpt, updated quarterly — A point-in-time, revision-aware bulk source shaped like the Form 345 data sets the insider lane already ingests (https://www.sec.gov/files/financial-statement-data-sets.pdf)
- Novy-Marx, R. (2013), The other side of value: The gross profitability premium, JFE 108(1):1-28 — Gross profitability predicts returns, including among the largest and most liquid stocks (https://www.nber.org/system/files/working_papers/w15940/w15940.pdf)
- Cooper, Gulen, Schill (2008), Asset growth and the cross-section of stock returns, JF 63(4):1609-1651 — Asset-growth field definition; the authors report it retains power in large caps
- Pontiff, Woodgate (2008), Share issuance and cross-sectional returns, JF 63(2):921-945 — Net-issuance field; post-1970 predictive power stronger than size, B/M or momentum (https://ideas.repec.org/a/bla/jfinan/v63y2008i2p921-945.html)
- Ball, Gerakos, Linnainmaa, Nikolaev (2016), Accruals, cash flows, and operating profitability in the cross section of stock returns, JFE 121(1):28-45 — Cash-based operating profitability subsumes accruals, so prefer it over Sloan accruals (https://faculty.tuck.dartmouth.edu/images/uploads/faculty/joseph-gerakos/Ball,_Gerakos,_Linnainmaa,_et_al._2016.pdf)
- Green, Hand, Soliman (2011), Going, going, gone? The apparent demise of the accruals anomaly, Management Science 57(5):797-816 — Accrual hedge returns are no longer reliably positive, so accruals should not be a primary field (https://ideas.repec.org/a/inm/ormnsc/v57y2011i5p797-816.html)
- Hou, Xue, Zhang (2015), Digesting anomalies: An investment approach, RFS 28(3):650-705 — q-factor definitions (I/A as asset growth, ROE) for the predeclared fields

---

## A horizon-matched cube is not a config change: the 45-day label slice silently drops every timeout beyond about 30 sessions

_area: alpha_mining · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

The cube hands the labeller only the hourly bars up to decision + 45 calendar days (`_LABEL_SPAN`, agentic_trader/research/pooled/cube.py:51-52, used in `_Hourly.around` at :231-234). The comment says this is sized for 20 sessions. BracketSpec accepts any max_hold_sessions >= 1 and checks nothing against that span (:57-71). When the walk runs out of bars before session max_hold+1, label_bracket returns IMMATURE (agentic_trader/research/setups/labels.py:322). build_cube then skips the cell as 'immature' (cube.py:296-298). check_coverage counts only no_hourly_bars and no_decision_price as gaps (cube.py:350), so the cube passes coverage. I measured 29-34 NYSE sessions (median 32) in the D..D+45 window over 2016-2026. So any cube with max_hold_sessions above about 28 silently keeps only the cells that hit stop or target early. It labels no timeouts, a selection on the outcome. The review's pooled map calls a horizon-matched cube 'a configuration change, not a redesign' (scratchpad maps/pooled.md §6 item 4). The fundamentals topic recommends a horizon-matched cube for slow, monthly signals. Both would hit this. The native-setup replay does not have the problem: it passes the full hourly frame (agentic_trader/research/setups/runner.py:337-343). Its protocols, though, hard-code a 30-day maturation that assumes the 20-session hold (agentic_trader/research/setups/recurrence.py:50, 157-163), and immature rows are dropped (study.py:468-471). A longer-hold protocol there would bias the last window weeks the same way.

**Evidence**

- agentic_trader/research/pooled/cube.py:51-52 (_LABEL_SPAN = 45 days, '20 sessions' comment)
- agentic_trader/research/pooled/cube.py:57-71 (BracketSpec.max_hold_sessions only ge=1)
- agentic_trader/research/pooled/cube.py:231-234 (_Hourly.around trims to when + _LABEL_SPAN)
- agentic_trader/research/setups/labels.py:252-262 (TIMEOUT needs session max_hold+1 in the frame), :322 (_immature when bars run out)
- agentic_trader/research/pooled/cube.py:296-298 (immature cells dropped), :350 (coverage gaps exclude 'immature')
- agentic_trader/research/setups/runner.py:337-343 (native replay passes the unsliced hourly frame)
- agentic_trader/research/setups/recurrence.py:50, 157-163 (_MIN_MATURATION_DAYS = 30 fixed); agentic_trader/research/setups/study.py:468-471 (_drop_immature)
- Computed: sessions in [D, D+45d] over 2016-01..2026-08 with an NYSE holiday list = min 29, median 32, max 34 (scratchpad holistic-review, one-off python)
- scratchpad holistic-review/maps/pooled.md §6 item 4 ('A horizon-matched cube is a configuration change')

**Recommendation**

Before any horizon-matched cube is specified: (1) derive the slice from the spec. Either slice by session count from the decision (max_hold_sessions + 1 sessions, via the observed calendar), or set the span to ceil(max_hold_sessions × 7/5) + 10 days. (2) Add a BracketSpec validator that rejects a hold the slice cannot cover. (3) In check_coverage, count as a gap any immature cell whose decision is more than max_hold_sessions sessions before bars_through. Keep the legitimate end-of-window immatures exempt. (4) Add a regression test: a synthetic flat series with max_hold_sessions = 40 must label TIMEOUT, not IMMATURE. Do the same for the setup protocols: derive _MIN_MATURATION_DAYS from max_hold_sessions. The current 20-session cubes keep their identity, because the spec hash does not include the span. Pair this with maps/data.md I7 (store forward-return and benchmark-residual arrays in the cube). That lets a monthly characteristic be tested on a 21/63-session forward-return label without a bracket at all.

**North-star impact**

Without the fix, the first horizon-matched lane (fundamentals, or any slow signal the maps propose) would report R on a sample conditioned on early resolution. That is a biased base rate, which could produce a false finalist or a false null. It would land on the one lane-wide confirmation window that 64 tests have so far left unused.

**References**

_none_

---

## Predeclare fundamentals as a junk-tail veto on native setups with name-clustered inference, not as another pooled mining lane

_area: research_methodology · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

The desk wants a recurrence-style conditioning test (docs/alpha-data-augmentation-2026-10-18.md:91-92). The recurrence machinery does not fit a slow firm characteristic as it stands. (a) Power. Recurrence R1 had a CI90 of [-0.138, +0.005] at 3,447 vs 2,895 setups over 1,109 sessions (docs/setup-recurrence-2026-10-14.md 'Hypotheses'), so SE is about 0.044R. A tercile split of the ~8.8k replayed setups therefore has a minimum detectable effect of about (1.645+0.84) × 0.045 ≈ 0.11R. Measured premia in the cube's own era are far below that. From Ken French's 2x3 five-factor file (downloaded today, data through 2026-08), 2016-01→2026-08: RMW +0.21%/month (t 0.92), CMA -0.01%/month (t -0.05); 2011→2026: RMW t 1.06, CMA t -0.15; 1963→2026: RMW t 3.03, CMA t 3.23. A long-side tercile edge of half the RMW spread is about 0.1%/month. At R ≈ 4.2% of price (docs/apriori-exdiv-2026-10-12.md:54) that is about 0.025R per 20-session label, a quarter of the MDE. The q-factor main effects cannot reach the pooled lane's t ≥ 3 gate either: the factor-level long-short premia have t < 1.1 over 2011-2026. (b) Inference unit. The recurrence and setup tests resample whole sessions only, in stationary blocks of mean 10 (agentic_trader/research/setups/recurrence.py:215-249; agentic_trader/research/setups/study.py:143-167). A fundamental tercile in a 159-name universe is close to a fixed set of names for years. Name-level persistence, such as a handful of 2023-24 mega-cap winners, is then treated as independent evidence, and the CI is too narrow. (c) Survivorship. The replay universe is today's 159 members (docs/setup-recurrence-2026-10-14.md 'Caveats'). Junk and high-growth names that later failed are missing, so junk-tail returns are biased up. That makes a veto test conservative, but it also means a null does not close the question. (d) Priors. Fama-French (2008) find asset growth insignificant among big stocks, while net issuance holds in every size group. Avramov et al. (2007) find momentum profits concentrated in low-grade firms, mostly through losers. Interaction or tail effects are the plausible large effects. A symmetric high-minus-low main effect is not.

**Evidence**

- docs/setup-recurrence-2026-10-14.md 'Hypotheses' (R1 -0.067R, ci90 [-0.138, +0.005], n 3,447 vs 2,895, 1,109 sessions) and 'Caveats' (current-member universe)
- docs/setup-recurrence-2026-10-14.md:20 (one-shot holdout 2026-03-02 → 2026-08-14 not read)
- agentic_trader/research/setups/recurrence.py:215-249 (_paired_difference resamples sessions only)
- agentic_trader/research/setups/study.py:143-167 (_stationary_index_draws over sessions, block mean)
- docs/apriori-exdiv-2026-10-12.md:54 (2×ATR14 risk unit ≈ 4.2% of price)
- Ken French F-F_Research_Data_5_Factors_2x3 (monthly, through 2026-08), computed in scratchpad holistic-review/ff5_power.py: RMW 0.208%/mo t 0.92 and CMA -0.010%/mo t -0.05 over 2016-01..2026-08; RMW t 3.03, CMA t 3.23 over 1963-07..2026-08
- docs/alpha-data-augmentation-2026-10-18.md:91-92 (§4 item 4: recurrence-style conditioning test)
- agentic_trader/research/setups/runner.py:320-343 and features.py:221-248 (per-date CrossSection hook where an as-filed characteristic tag would attach, shared with the live shadow ranker at copilot.py:1831)

**Recommendation**

Freeze a `setup-fundamentals-v1` protocol before reading any data. (1) Tag each replayed native setup with as-filed terciles, computed within the universe on its D-1 date, of ROE, gross profitability, asset growth and net issuance. The tag goes through a new features version on the existing CrossSection hook, so replay and the live shadow ranker compute it the same way. (2) State one directional primary hypothesis: 'junk-tail' setups (bottom profitability tercile or top asset-growth or issuance tercile) have lower mean R_cost than the rest at 5 bp. A pass would make only a card-construction rule (withhold or deprioritise) eligible, through card_policy's preview→enforce path. (3) Use a joint bootstrap that resamples names (clusters) and session blocks. Add a check-C-style placebo that permutes whole characteristic paths across names, to calibrate the false-positive rate under name persistence. (4) Print the MDE (about 0.1R) in the protocol, so a null is read as 'no large interaction', not 'fundamentals don't matter'. (5) Use the unread one-shot holdout (2026-03-02 → 2026-08-14) as the confirmation read. (6) Do not open a pooled fundamentals campaign lane on the 20-session 3R label. The measured premia make a t ≥ 3 finalist arithmetically implausible, and it would spend the lane-wide confirmation window. Use the same fields as descriptive card attribution in `cards outcomes` instead, beside daily FF5 exposures (maps/data.md I6). Re-run the conditioning test on a survivorship-repaired universe once Form 25/15 dates exist (maps/data.md I5). Those dates come from the same EDGAR CIK-indexed source.

**North-star impact**

This turns the desk's #4 data source into a cheap, honest test that can change which 1-2 cards reach Telegram: a quality veto on the junk tail, where large effects are plausible. It also stops the team from spending a campaign and the confirmation window on a q-factor main effect that the 2016-2026 factor record says the tests cannot detect.

**References**

- Kenneth R. French Data Library, Fama/French 5 Factors (2x3), monthly CSV — Measured RMW/CMA premia and t-statistics by period, which set the power arithmetic (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_CSV.zip)
- Hou, Xue, Zhang (2020), Replicating anomalies, RFS 33(5):2019-2133 — With NYSE breakpoints and VW returns, 65% of 452 anomalies fail |t| ≥ 1.96 and replicated magnitudes are much smaller, so expect small main effects (https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)
- Fama, French (2008), Dissecting anomalies, JF 63(4):1653-1678 — Asset growth is not significant among big stocks; net issuance is pervasive across size groups. Informs which field to expect in cohort v2 vs v3 (https://www.johnhcochrane.com/s/fama_french_dissecting_anomalies_JF.pdf)
- Avramov, Chordia, Jostova, Philipov (2007), Momentum and credit rating, JF 62(5):2503-2520 — A prior that trend-type signals interact with firm quality, which motivates a tail/interaction hypothesis over a main effect (https://www.johnhcochrane.com/s/avramov_etal_momentum.pdf)
- McLean, Pontiff (2016), Does academic research destroy stock return predictability?, JF 71(1):5-32 — Post-publication decay further shrinks the expected effect below the conditioning test's MDE

---

## Cross-asset time-series momentum on the ETF32 has never been tested, and no current research lane can express it

_area: alpha_mining · kind: research_direction · impact: high · effort: M · confidence: medium_

**Claim**

CODE NOW: The live ETF32 holds 5 broad-equity, 11 sector, 6 international, 8 rates/credit and 2 metals ETFs (config/config.yaml:98-129). No doc, config or module mentions time-series momentum (TSMOM), Moskowitz or trend-following; a grep over docs/, config/ and agentic_trader/ returns nothing. All four research lanes are built so that they cannot test a long/flat, volatility-scaled, monthly trend book across asset classes:
(1) Per-symbol DSL. Every raw score is z-scored over its own trailing window (strategy.py:144-147). The window defaults to 30 bars (strategy.py:26) and can be set anywhere in 2..252 (models.py:78). Entries fire only when z >= a positive threshold (models.py:74, strategy.py:155-161). The z-score uses std.where(std>0), so a persistent sign(roc(close,252)) signal has zero rolling std and scores NaN. roc(close,252) itself becomes an 'acceleration versus its last N bars' signal, not a trend level. The brief said the window is fixed at 30; it is configurable, but the conclusion holds.
(2) Pooled lane. It takes the top-k=3 names per session cross-sectionally under a 2-ATR stop, 3R target and 20-session bracket (config/research/pooled/campaign-v2.json:24-30). It runs on a 452-name single-stock cohort that contains no ETFs (cohort-v2.json).
(3) Persistent book. simulate_book rejects any target that is not zero-net (persistent_book.py:104-107), and cash earns zero (persistent_book.py:75). It tested only cross-sectional half-long/half-short momentum and reversal on nine sector ETFs in 2022 and 2023 (docs/alpha-persistent-book.md 'Frozen comparison').
(4) A priori catalog. It is event-shaped: PeadEntry and ExdivEntry use ATR brackets and hold-session counts (research/apriori/catalog.py).
The native trend_pullback does use a trend filter: daily close > EMA50 > EMA200 (screeners/strategies.py:99; config.yaml:306-307). It harvests that filter through 4h-ATR pullback brackets with a mean hold of 5.6 sessions (docs/bracket-counterfactual-2026-10-18.md 'Descriptives'). The literature places the trend premium at 1-12 month holds. CORRECTION TO THE BRIEF: a long/flat trend book on the ETF32 is NOT beta-free by construction. 22 of the 32 members are equity, and Goyal-Jegadeesh show that most of time-series momentum's edge over cross-sectional momentum is its time-varying net-long exposure.

**Evidence**

- agentic_trader/research/alpha/strategy.py:26 (NORMALIZATION_WINDOW = 30)
- agentic_trader/research/alpha/strategy.py:144-147 (rolling z with std.where(std > 0), so a constant-sign signal becomes NaN)
- agentic_trader/research/alpha/strategy.py:155-161 (entries only at |z| >= threshold)
- agentic_trader/research/alpha/models.py:74,78 (threshold > 0; window 2..252)
- config/research/pooled/campaign-v2.json:24-30 (k=3 cross-sectional, 2ATR/3R/20-session bracket); cohort-v2.json has 452 symbols, no ETFs
- agentic_trader/research/alpha/persistent_book.py:104-107 (zero-net targets required), :75 (cash earns zero interest)
- docs/alpha-persistent-book.md 'Frozen comparison' (nine sector ETFs, top/bottom-three half-long/half-short, 2022/2023 folds)
- docs/alpha-persistent-book-2026-09-17.md 'Complete combined matrix' (24/24 cross-sectional combos fail)
- agentic_trader/screeners/strategies.py:99-100; config/config.yaml:306-307 (EMA50/200 filter only)
- docs/bracket-counterfactual-2026-10-18.md 'Descriptives' (A0_live mean hold 5.6 sessions, 20-session timeout)
- config/config.yaml:98-129 (ETF32 composition: 22 equity, 8 rates/credit incl. BIL, 2 metals)
- config/research/pooled/campaign-v2.json:8 (SIP bars_from 2016-01-01: the deployment-feed history depth)

**Recommendation**

Freeze a catalog entry config/research/apriori/tsmom-v1.json before reading any bars. Do not use the cached setup-cache-v1/2017 SIP ETF bars for any look before the entry is frozen. Spec:
- Universe: ETF32 minus BIL. BIL is the T-bill reference. SHY should be declared as either cash-like or risky before freezing.
- Signal: sign of the trailing 252-session adjusted total return in excess of BIL over the same window (Moskowitz-Ooi-Pedersen), observed at month-end close and traded at the next open.
- Weights: inverse 60-session volatility, with equal risk per sleeve (US equity / sectors / international / rates-credit / metals), so 22 equity names do not dominate the risk.
- Long/flat, monthly reset, costs 1 and 5 bp per side.
- Data: Alpaca SIP adjustment=all, decisions 2017-01 to 2026-08 (about 116 months). Late listings (XLC 2018, XLRE 2015) become eligible only after 252 own bars.
Predeclare matched controls on the same sessions:
- C1, a vol-scaled always-long book of the same assets. Kim-Tse-Wald attribute most of TSMOM's alpha to vol scaling, so C1 isolates timing value.
- C2, a 'historical-mean' sign control (Huang et al.).
- C3, SPY and 60/40 SPY/IEF, descriptive only.
Rules: P1, paired monthly return TSMOM minus C1, stationary block bootstrap, one-sided. P2, full-sample beta to SPY <= 0.3. P3, mean return in SPY's worst-quintile months > 0. Also report correlation with the native funnel's monthly excess R from the bracket-counterfactual frames. Declare power up front: at an assumed annual Sharpe of 0.4 for the paired difference, t is about 0.4*sqrt(9.7), roughly 1.25, which gives roughly one-in-three power. So run a check-A-style planted-effect power test first. Optionally add a separately labelled yfinance 2005-2016 robustness read, per the research-data-sources policy, never as deployment evidence.
Implementation: add a net-long target mode and a cash-yield input (BIL return) to persistent_book.simulate_book rather than a new engine. Use pysystemtrade (GPL-3.0) only as an external, independent audit reference, as the persistent-book audit did, and do not vendor it. A pass only makes the leg eligible for a separately specified probe (see the sizing finding).

**North-star impact**

This is the only literature-backed card source left untested on instruments the desk already scans and trades. Its payoff (flat or out in sustained drawdowns, as in 2022, and convex in long trends) differs from the native 4h pullback stream that trails SPY. If it passes, it adds roughly 3-6 monthly sleeve cards and a per-asset-class risk-on/off state that allocation can use. If it fails against C1, it closes the most-cited remaining public anomaly on the ETF32 with an honest control, not a cross-sectional proxy.

**References**

- Moskowitz, Ooi, Pedersen (2012), Time series momentum, Journal of Financial Economics 104(2):228-250 — Defines the 12-month excess-return sign signal with vol-scaled, 1-12 month persistence across bonds, equity indices and commodities (https://research.cbs.dk/en/publications/time-series-momentum/)
- Hurst, Ooi, Pedersen (2017), A Century of Evidence on Trend-Following Investing, Journal of Portfolio Management 44(1):15-29 — Long-sample evidence across 67 markets; diversification value during equity bear markets (https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing)
- Baltas, Kosowski (2013), Momentum strategies in futures markets and trend-following funds (working paper) — Monthly, weekly and daily TSMOM frequencies; trend strategies explain CTA returns; capacity evidence (https://climateimpact.edhec.edu/publications/momentum-strategies-futures-markets-and)
- Kim, Tse, Wald (2016), Time series momentum and volatility scaling, Journal of Financial Markets 30:103-124 — Most of TSMOM's alpha comes from vol scaling; unscaled TSMOM is close to buy-and-hold. Required control C1 (https://works.bepress.com/yiuman-tse/10)
- Huang, Li, Wang, Zhou (2020), Time series momentum: Is it there?, Journal of Financial Economics 135(3):774-794 — Asset-level predictability is weak; TSMOM performs like a historical-mean strategy. Required control C2 and a power warning (https://ideas.repec.org/a/eee/jfinec/v135y2020i3p774-794.html)
- Goyal, Jegadeesh (2018), Cross-sectional and time-series tests of return predictability: What is the difference?, Review of Financial Studies 31(5):1784-1824 — TSMOM's edge over XSMOM is mostly time-varying net-long exposure, so it is not beta-free on an equity-heavy ETF set (https://ideas.repec.org/a/oup/rfinst/v31y2018i5p1784-1824..html)
- Levine, Pedersen (2016), Which Trend Is Your Friend?, Financial Analysts Journal 72(3):51-66 — TSMOM and moving-average crossovers (like the native EMA50/200 filter) are equivalent linear filters, so one predeclared signal suffices with no search (https://research.cbs.dk/en/publications/which-trend-is-your-friend/)
- Faber (2007), A Quantitative Approach to Tactical Asset Allocation, Journal of Wealth Management — The closest long/flat, asset-class-ETF analogue (10-month SMA, in/out); a natural descriptive arm
- pysystemtrade (GPL-3.0), pst-group — Mature open-source trend implementation (EWMAC, vol targeting, buffering) to use as an independent audit reference; GPL, so do not vendor it (https://github.com/pst-group/pysystemtrade)

---

## The SPY 'beta-one' control multiplies SPY's return by entry/stop, so bond-ETF setups drive the 'trails SPY' and ratchet-penalty findings

_area: research_methodology · kind: defect · impact: high · effort: S · confidence: high_

**Claim**

CODE NOW: market_r is the SPY open-to-close return over the card's window multiplied by entry/|entry-stop|. Its docstring calls it 'a beta-one exposure control' (research/setups/outcomes.py:187-219), and excess_r = r_cost - market_r (outcomes.py:383). The bracket counterfactual recomputes excess the same way (bracket_counterfactual.py:177), and rule B1 is the paired excess difference (bracket_counterfactual.py:217-218). On a low-volatility, low-beta asset the stop is a tiny fraction of price, so SPY's drift is levered by hundreds to thousands.
MEASURED by reading the retained frames (~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/*.csv.gz, read-only, ±10R winsorisation as in the doc), for A0_live in the primary window:
- The 416 of 8,768 setups on rates/credit ETFs have median entry/risk of 233 (SHY 655, BIL 2,033).
- Their mean market_r is +2.15R against r_cost +0.17R, so excess is -0.65R; 44 rows hit the ±10R clip.
In the robustness window:
- 343 rates/credit rows (4.2%) carry mean market_r +7.66R and excess -2.30R.
- The doc's headline excess of -0.103R becomes -0.006R without them, or -0.008R without rates and metals. Single stocks show +0.016R.
On B1, from my approximate replication (500-draw stationary bootstrap, block mean 10, which reproduces the doc's A2 CI):
- The 'reliably worse' live ratchet goes from -0.0126 [90% CI -0.021, -0.005] to -0.0051 [-0.011, -0.0001] once rates/credit is excluded.
- Trail-only goes from -0.036 to +0.008 [-0.021, +0.040].
- On rates rows alone, trail-only is -0.92R per setup: longer holds subtract more levered SPY drift.
No live gate reads excess_r; it appears only in research/setups/*. The frozen no_change decision still stands, because no arm clearly passes without the rates rows either (A1 +0.014). But two descriptive conclusions in that doc and in CLAUDE.md ('every arm trails SPY by 0.10-0.15R in both windows'; 'ratchet worse by 0.013R') are largely artifacts of this yardstick in the robustness window. Any cross-asset trend card (TLT, IEF, GLD) would be mis-scored the same way.

**Evidence**

- agentic_trader/research/setups/outcomes.py:187-219 (market_r = sign*(SPY close/open-1)*entry/risk_unit; 'beta-one exposure control')
- agentic_trader/research/setups/outcomes.py:383 (excess_r = r_cost - market_value)
- agentic_trader/research/setups/bracket_counterfactual.py:177 (excess recomputed and clipped), :217-218 (B1 paired excess difference)
- ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz and robustness.csv.gz (read-only group-by of A0_live and arm pairs; my reproduction matches the doc's -0.110/-0.103R headlines and A2 CI)
- docs/bracket-counterfactual-2026-10-18.md 'Rules' (B1 table: A2 -0.013 [-0.020,-0.006], A4 -0.038) and 'Descriptives' (robustness excess -0.103R)
- CLAUDE.md contract 8 summary of bracket-counterfactual v1 (ratchet worse by 0.013R; every arm trails SPY by 0.10-0.15R)

**Recommendation**

1. Add a beta-matched control beside the raw column. market_r_beta = beta-hat * SPY open-to-close return * entry/risk, where beta-hat is a past-only trailing 120-session daily OLS beta of the symbol on SPY, frozen at decision time.
2. Add an asset-own buy-and-hold control in R units to measure timing value.
3. Make `cards outcomes` and every future setup study report excess by asset class.
4. Publish a descriptive erratum to docs/bracket-counterfactual-2026-10-18.md and the CLAUDE.md summary. It should give the ex-rates/metals and beta-matched numbers, and leave the frozen no_change decision as the decision of record; any new decision needs a v2 protocol.
5. Remove BIL (and arguably SHY) from the native scan universe. A 1.5x 4h-ATR stop on a T-bill fund implies 650-2,000x notional per unit of risk, so the card is meaningless at the $7,500 notional cap. Keep BIL as the cash reference for the trend leg.
6. Measure any cross-asset trend leg at book level against its own controls, never with per-card SPY excess.

**North-star impact**

Excess over the market is the desk's main yardstick for whether cards are 'reasonable'. As built, it penalises exactly the diversifying, low-beta, longer-hold cards (bonds, metals, trend-like exits) that the north star's 'portfolio allocation based on existing positions' needs. It has already put a distorted conclusion into the record. Fixing it changes which card sources and exit policies look viable.

**References**

- Fung, Hsieh (2001), The risk in hedge fund strategies: theory and evidence from trend followers, Review of Financial Studies 14(2):313-341 — Trend payoffs are option-like and poorly described by linear equity benchmarks, so a beta-one SPY control is the wrong yardstick for trend or cross-asset cards (https://ideas.repec.org/a/oup/rfinst/v14y2001i2p313-41.html)
- Goyal, Jegadeesh (2018), Review of Financial Studies 31(5):1784-1824 — Net exposure, not prediction, separates TS from XS momentum, so controls must match exposure (beta-matched), not assume beta = 1 (https://ideas.repec.org/a/oup/rfinst/v31y2018i5p1784-1824..html)

---

## A trend leg cannot run under today's book limits and PEAD-only catalog path unless the study is built around the live constraints

_area: sizing_portfolio · kind: gap · impact: medium · effort: M · confidence: medium_

**Claim**

CODE NOW: the live paper desk has:
- $100k notional book capital (config.yaml:2) and $60k max notional (config.yaml:4)
- at most 8 concurrent positions (config.yaml:6) and at most 2 per sector-derived correlation group (config.yaml:7; groups built from universe sectors in config.py:165-169)
- a $7,500 per-trade notional cap (config.yaml:343), with $250 default equity risk / 0.5% target risk (config.yaml:335-337)
Every entry must have a stop of at least 1.5 ATR and reward/risk of at least 2.0 (config.yaml:274-275). A catalog bracket requires a positive stop_atr and reward_risk (strategy.py:96-104), with a session-counted hold of at most 60 sessions (execution/lifetime_policy.py:75). The catalog probe path is hard-wired to PEAD: CATALOG_ENTRY_IDS = ('pead',) (research/apriori/probe.py:20), and copilot.py has a PEAD-specific drift-card flow (agent/copilot.py:623, :1271). It allows 1 drift card per session and 4 open drift positions (config.py:178,181).
Consequences:
- A 30-asset trend book of 10-25 longs cannot be expressed.
- The notional cap turns inverse-vol sizing into equal-notional sizing for any asset whose stop is below about 3.3% of price ($250 / $7,500). Rates/credit stops are a fraction of that (median entry/risk 233 in the counterfactual frames), so that sleeve would carry well under half the intended risk.
- Holding expiry closes and reopens; nothing renews it, so a persistent signal pays a full round trip every month. Netting cut slow-momentum turnover from 51.86x to 18.90x capital in the persistent book (docs/alpha-persistent-book-2026-09-17.md 'What improved and what failed').

**Evidence**

- config/config.yaml:2,4,6,7 (cash 100k, notional 60k, 8 positions, 2 per correlation group)
- config/config.yaml:274-275 (min RR 2.0, min stop 1.5 ATR)
- config/config.yaml:335-343 (0.5% risk, $250 default risk, $7,500 notional cap)
- agentic_trader/config.py:165-169 (correlation groups from universe sectors), :178,:181 (1 drift card/session, 4 open drift positions)
- agentic_trader/research/alpha/strategy.py:82-106 (AprioriBracketPolicy: stop_atr, reward_risk > 0, SessionLifetimePolicy)
- agentic_trader/execution/lifetime_policy.py:75 (MAX_HOLDING_SESSIONS = 60)
- agentic_trader/research/apriori/probe.py:20 (CATALOG_ENTRY_IDS = ('pead',))
- agentic_trader/research/apriori/catalog.py:162 (pead_execution_policy: the study's trade block is the live bracket, the precedent to copy)
- agentic_trader/agent/copilot.py:623,1271 (PEAD-specific drift-card selection and budget)
- docs/alpha-persistent-book-2026-09-17.md 'What improved and what failed' (netting cuts turnover 51.86 to 18.90x)

**Recommendation**

Freeze the tsmom-v1 study in its live-implementable form, following the PEAD precedent that the study's trade block equals the live bracket:
- One instrument per sleeve, at most 5: e.g. VTI, EFA, IEF, LQD or TIP, GLD. That fits 8 positions and 2 per group.
- Monthly reset via SessionLifetimePolicy(holding_sessions=21).
- Protective stop at k x daily ATR(20), labelled in the study. Stop-distance sizing then delivers inverse-vol scaling.
- A far target of at least 2R (e.g. 6R) so it rarely binds and the risk rules still pass.
- The unstopped MOP book as a descriptive arm only.
Model the $7,500 cap exactly in the study, or declare a leg-specific notional/risk budget in AprioriConfig, like max_open_drift_positions, so the bond sleeve keeps parity and the leg cannot crowd native cards.
Before any probe, generalise probe.py and the copilot drift path into a catalog-leg interface (entry id, scheduler slot such as first session of the month, per-leg budget) instead of adding a second PEAD-style branch.
Treat 'renew instead of close/reopen' (extend the holding deadline when the signal persists, journaled like entry_cancel) as a later execution item, justified only if the study's reset-versus-netted cost gap is material.

**North-star impact**

This decides whether a passing trend result can become Telegram cards at all, and whether those cards keep the risk balance that makes them diversifying. Without it, a pass would either fail to fit the book or be quietly reshaped by caps into a different, untested strategy.

**References**

- Moskowitz, Ooi, Pedersen (2012), Journal of Financial Economics 104(2):228-250 — Vol-scaled position sizing is integral to the documented effect; a notional cap that breaks it changes the strategy (https://research.cbs.dk/en/publications/time-series-momentum/)
- Kim, Tse, Wald (2016), Journal of Financial Markets 30:103-124 — Shows how much of TSMOM's result depends on vol scaling, which the $7,500 cap would remove for the bond sleeve (https://works.bepress.com/yiuman-tse/10)

---

## No options-implied state is captured anywhere, so free point-in-time IV/skew history is lost every session; start a post-close, digest-pinned chain-summary capture now

_area: data · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

Today the options code is display-only and persists nothing. `OptionsDataFetcher` pulls Yahoo chains one symbol at a time, for the first `max_expirations` (3) expirations only, and keeps a 60-second in-memory cache (agentic_trader/options/fetcher.py:35-41, 62-65, 67, 79, 126; config.py:605-610; config/config.yaml:375-380). Its only callers are Telegram /gex and `copilot gex` (agent/copilot.py:435-438, 4500-4506; cli/commands/options.py:32-49). It computes put/call OI and volume ratios (options/gex.py:219-221; options/models.py:58-59), renders them and discards them. The installed alpaca-py 0.44.0 ships `OptionHistoricalDataClient.get_option_chain` and `get_option_bars` (.venv/.../alpaca/data/historical/option.py:74, 204; OptionChainRequest at alpaca/data/requests.py:443), but no module under agentic_trader imports it. The research side has no options source, field set or DSL field (research/alpha/dsl.py:28-48 lists only OHLCV, event, short and insider fields). The data assessment ranks options as 'paid (CBOE DataShop; Polygon options tier) ... defer until a free lane pays' (docs/alpha-data-augmentation-2026-10-18.md §2 row 9), and the review's data map I4 warehouse list leaves options out (holistic-review maps/data.md §7 I4). Unlike FINRA or EDGAR, options quote history cannot be backfilled free later: Alpaca's history starts February 2024 and is trade-based, and Yahoo serves current chains only. Each session not captured is therefore permanently lost point-in-time IV and skew history. The existing normalizer is also unfit as a research source. It replaces missing or zero IV with 0.20 and missing DTE with 14 days (options/gex.py:30-47; constants.py:259, 263). Applied to thinly quoted OTM puts, the input to Xing–Zhang–Zhao skew, it would fabricate skew values.

**Evidence**

- agentic_trader/options/fetcher.py:35-41,62-65,79,122-127 (on-demand Yahoo fetch, first 3 expirations, 60 s in-memory cache, nothing persisted)
- agentic_trader/options/gex.py:24-48 (missing/zero IV -> DEFAULT_IMPLIED_VOLATILITY, missing DTE -> 14 d); agentic_trader/constants.py:259,263
- agentic_trader/options/gex.py:219-221 and options/models.py:58-59 (PCR computed for display only)
- agentic_trader/agent/copilot.py:435-438,4500-4506; agentic_trader/cli/commands/options.py:32-49 (only callers: /gex, copilot gex)
- agentic_trader/config.py:605-610; config/config.yaml:375-380 (default_symbols SPY/QQQ/IWM, max_expirations 3)
- grep -rn OptionHistoricalDataClient agentic_trader -> no matches; .venv/lib/python3.14/site-packages/alpaca/data/historical/option.py:74,204 (alpaca-py 0.44.0 provides get_option_bars/get_option_chain)
- docs/alpha-data-augmentation-2026-10-18.md §2 row 9 ('paid ... defer until a free lane pays') and §1 table DSL row ('options' not available)
- holistic-review maps/data.md §7 I4 (warehouse list: SIP bars, asset master, FINRA, EDGAR index, corporate actions; no options)
- Template: agentic_trader/data/corporate_actions_worker.py:30-68 (once per NY date, outside scan windows, offloaded fetch); agentic_trader/cli/commands/service.py:385-430 (paced client, readiness = worker progress); agentic_trader/config.py:300-324 (max_symbols, chunk_size, max_requests_per_minute)
- Digest pattern: agentic_trader/research/short_volume.py:132 (ShortVolumeStore), :194 (files_sha256 over sessions); private root agentic_trader/cli/utils.py:93-94 (artifact_directory)
- docs/alpha-daily-panel.md §Frozen experiment ('missed innovations remain missing') and §Architecture and operations (private artifacts + DB hashes)
- config/research/pooled/cohort-v2.json: 452 symbols

**Recommendation**

Add a research-only `OptionsCaptureWorker` modelled on `CorporateActionsWorker` (corporate_actions_worker.py:30-68), not on the heavier daily-panel claim machinery. It should run once per NY session in a post-close window, 16:20–20:00 ET. A missed session stays missing and is never repaired, which is the daily-panel rule. The symbol set is cohort v2 (452 names) ∪ universe ∪ held equities. For each underlying, make one paginated GET /v1beta1/options/snapshots/{underlying}, filtered to expirations 7–70 days out and strikes 0.75–1.25 × spot. The response gives the latest quote, latest trade, vendor IV/greeks and dailyBar volume. Record the feed identity as `indicative`, since this is the Basic plan. Also call GET /v2/options/contracts for `open_interest` with its `open_interest_date`. Pace at no more than 100 requests per minute (the Basic limit is 200). My estimate is about 500–1,000 requests, or 5–10 minutes, after the close and outside every scan window. Store the raw gzipped pages per session under `artifact_directory()` with page SHA-256s, plus a per-session digest in the `ShortVolumeStore`/`files_sha256` style, so a later protocol can pin the slice. Journal one capture event with counts, coverage and digests. The readiness component means worker progress only. Compute the per-symbol summary ourselves from quote midpoints, with NaN on missing, crossed or stale quotes, never 0.20:
- (a) XZZ skew: IV of the OTM put with K/S in 0.80–0.95 closest to 0.95, minus IV of the ATM call with K/S in 0.95–1.05 closest to 1.00, at 10–60 DTE.
- (b) Cremers–Weinbaum IV spread: OI-weighted mean of call IV minus put IV over matched strike and expiry pairs.
- (c) Interpolated 30-day ATM call and put IV, so An–Ang–Bali–Cakici ΔIV becomes computable after 21 captured sessions.
- (d) Put/call volume and option/stock volume from dailyBar.
- (e) Total OI.

Alpaca says indicative quotes are 'modified', so run a one-week agreement check: rank correlation of skew between Alpaca indicative and yfinance chains on about 30 names. Freeze the feed into the series identity, and if the desk ever buys OPRA, start a new series rather than mixing feeds. Leave the GEX display path unchanged and do not reuse `normalize_option_chain` for research.

**North-star impact**

Starts the one clock the desk cannot buy back cheaply: a point-in-time options panel on the large, liquid names where about 64 OHLCV, event, short-volume and insider fixed-set pairs have found no finalist. Without it, options stay 'paid, deferred' indefinitely. With it, options-conditioned cards and pooled fields become testable at zero data cost once enough sessions accrue (finding 2).

**References**

- Alpaca Docs, 'Historical Option Data': historical option data only since February 2024; indicative feed is a free derivative of OPRA, quotes not actual OPRA quotes, trades delayed 15 min — Shows free quote-level IV history cannot be rebuilt before 2024-02, and the indicative feed needs an agreement check (https://docs.alpaca.markets/docs/historical-option-data)
- Alpaca API reference, 'Option chain' (GET /v1beta1/options/snapshots/{underlying_symbol}): latestTrade, latestQuote, impliedVolatility, greeks, dailyBar, minuteBar, prevDailyBar; limit 1-1000 with next_page_token; no open interest — Endpoint and fields for the nightly capture; OI must come from the contracts endpoint (https://docs.alpaca.markets/us/reference/optionchain)
- Alpaca Docs, 'About Market Data API': options on Basic = Indicative Pricing Feed, historical limitation latest 15 minutes, 200 historical calls/min — Request budget and plan feasibility at zero cost (https://docs.alpaca.markets/docs/about-market-data-api)
- Alpaca API reference, GET /v2/options/contracts: open_interest, open_interest_date, close_price, status active|inactive; Alpaca forum: OI typically lags (OCC publication) — OI source with its own as-of date for OI weighting and later GEX history (https://docs.alpaca.markets/reference/get-options-contracts)
- Xing, Zhang & Zhao (2010), 'What Does the Individual Option Volatility Smirk Tell Us About Future Equity Returns?', JFQA 45(3):641-662 — Defines the OTM-put minus ATM-call skew field; steepest-smirk stocks underperform by 10.9%/yr, persisting at least six months (https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html)

---

## Pre-register the options legs before the first capture, backfill volume fields from Alpaca's free Feb-2024 option history, and size the wait honestly

_area: research_methodology · kind: research_direction · impact: medium · effort: M · confidence: medium_

**Claim**

The data assessment treats all options history as paid (docs/alpha-data-augmentation-2026-10-18.md §2 row 9). Alpaca, however, documents free historical option data since February 2024, and its Basic plan includes options historical calls. Those are trade-based contract bars: they carry per-contract volume but no synchronous quotes. They therefore support put/call volume and Johnson–So option/stock volume fields over about 684 sessions to 2026-10-21, but not quote-based IV skew. Both the 684 count and the power figures below are my calculation. The pooled lane's gates need 400 discovery, 150 selection and 300 confirmation sessions (config/research/pooled/short-v1.json: discovery_gate.min_sessions 400, selection_gate.min_sessions 150, confirmation_gate.min_sessions 300). An IV-based pooled protocol could therefore not run unchanged for about 3.4 years of capture. Power in the prospective window is tight. The lane's best recent edge was cohort v3 `shorts_retreat` at +0.020R, t +3.40 on 836 sessions (docs/alpha-pooled-short-v1-2026-10-20.md §Cohort v3). At the same noise, √n scaling gives:
- t ≈ 2.0 for +0.020R at 300 sessions, and t ≈ 3.1 at 684 sessions.
- t ≥ 3 at 300 sessions needs about +0.030R.

The literature horizons also matter against the desk's 20-session long bracket (short-v1.json bracket.max_hold_sessions 20):
- Pan–Poteshman (about 1%/week) comes from non-public open-buy volume, not public totals.
- Cremers–Weinbaum (50 bp/week) is weekly and its predictability declines over the sample.
- Johnson–So (0.34%/week) is weekly.
- Xing–Zhang–Zhao skew persists six months and An–Ang–Bali–Cakici ΔIV (about 1%/month) up to six months. These two fit the bracket.

The desk trades long brackets, so only the favourable tail is usable: low skew, rising call IV, low O/S. McLean–Pontiff's 58% post-publication decay applies.

**Evidence**

- docs/alpha-data-augmentation-2026-10-18.md §2 row 9 and §4 step 6 ('Paid data (#9–#11) only on evidence')
- config/research/pooled/short-v1.json (discovery_gate.min_sessions 400, min_t 3.0; selection_gate.min_sessions 150; confirmation_gate.min_sessions 300; bracket.max_hold_sessions 20)
- docs/alpha-pooled-short-v1-2026-10-20.md §Campaigns / Cohort v3 (shorts_retreat +0.020R, t +3.40 on 836 sessions)
- docs/apriori-alphas.md intro rules (one frozen hypothesis per leg, SHA-256 in manifest, changed file = new version) and Entry 2 pass rule (P1–P4, session-block bootstrap)
- agentic_trader/research/alpha/dsl.py:28-48 (FIELD_UNITS: dimensionless event/short/insider fields present only when a frame carries them)
- agentic_trader/research/pooled/campaign.py:486-506 (ShortInputs / ShortVolumeCampaignProtocol: pinned inputs + fixed_set-only variant)
- agentic_trader/research/pooled/short_fields.py:1-16 (NaN-before-visible, D-1 read, never carried forward)
- Power arithmetic (mine): per-session sd = 0.020/3.40*sqrt(836) ≈ 0.170 → se(300) ≈ 0.0098, se(684) ≈ 0.0065

**Recommendation**

(1) One-afternoon spike on the Basic plan. Does GET /v2/options/contracts?status=inactive&underlying_symbols=X&expiration_date_gte=2024-02-01 enumerate expired contracts, and does `get_option_bars` return their daily bars? If both hold, build a research-only `alpha option-volume` acquisition: digest-pinned and cached like `alpha short-volume`, aggregating daily put and call contract volume per underlying. Expose it as `options_fields_v1` volume fields (`opt_pc_volume`, `opt_os` = option volume × 100 / share volume, `opt_os_z`), NaN before visible and read at D-1. Test them as a fixed set of 4 or fewer formulas under checks A and C. The window is shorter than the 400/150/300 gates, so this needs its own protocol spec and operator approval. The spike result alone should also be recorded in the data assessment as a correction to row 9. (2) Before the capture's first session, commit a priori catalog entries (config/research/apriori/*.json, SHA-pinned) for the two horizon-compatible, long-side hypotheses:
- `skew_low`: long the lowest-decile XZZ skew names in cohort v2.
- `civ_rise`: long the top decile of the 21-session change in 30-day ATM call IV, per ABBC.

Use the live 20-session bracket, a SPY- or session-paired control and PEAD's P1–P4. Their only data is the prospective capture, so there is no discovery search to overfit. Declare one read at 300 sessions, about 2027-12, and run a synthetic power check A first so the entry states its detection rate at +0.03R. Keep Cremers–Weinbaum and put/call volume as descriptive context (finding 3), not primary legs, because their documented horizon is about one week. Report capture coverage weekly, but never read outcomes for the frozen legs before the declared date.

**North-star impact**

Turns 'options are paid, defer' into a dated, pre-registered test with an honest power statement. The volume backfill can return a first options verdict within weeks at zero cost. The IV legs spend the prospective window on two confirmatory hypotheses instead of a search the window cannot support.

**References**

- Pan & Poteshman (2006), 'The Information in Option Volume for Future Stock Prices', RFS 19(3):871-908: low open-buy put/call stocks beat high by >40 bp next day and >1% next week; source is non-public information — Public total put/call volume from snapshots is not the PP signal, so expect much weaker effects (https://www.nber.org/papers/w10925)
- Cremers & Weinbaum (2010), 'Deviations from Put-Call Parity and Stock Return Predictability', JFQA 45(2):335-367: expensive-call stocks beat expensive-put stocks by 50 bp/week; predictability declines over the sample — Weekly horizon and in-sample decay argue against it as a primary 20-session leg (https://ideas.repec.org/a/cup/jfinqa/v45y2010i02p335-367_00.html)
- Johnson & So (2012), 'The option to stock volume ratio and future returns', JFE 106(2):262-286: lowest-O/S decile beats highest by 0.34%/week — A public-volume options signal that the free Feb-2024 trade history can backfill (https://ideas.repec.org/a/eee/jfinec/v106y2012i2p262-286.html)
- An, Ang, Bali & Cakici (2014), 'The Joint Cross Section of Stocks and Options', JF 69(5):2279-2337: large prior-month call (put) IV increases predict high (low) returns, about 1%/month spread, persisting up to six months — Monthly, persistent horizon compatible with the 20-session bracket; needs captured ATM IV history (https://www.nber.org/papers/w19590)
- Xing, Zhang & Zhao (2010), JFQA 45(3):641-662: steepest-smirk stocks underperform by 10.9%/yr; predictability lasts at least six months — Primary skew leg; horizon fits the bracket (https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html)
- McLean & Pontiff (2016), 'Does Academic Research Destroy Stock Return Predictability?', JF 71(1): 97 predictors, returns 26% lower out of sample and 58% lower post-publication — Discount the published magnitudes when stating detectable edges (https://www.gwern.net/doc/economics/2016-mclean.pdf)
- Alpaca Docs, 'Historical Option Data' (since February 2024) and 'About Market Data API' (Basic plan: options historical, 200 calls/min) — Grounds the free backfill that row 9 of the assessment misses (https://docs.alpaca.markets/docs/historical-option-data)

---

## Journal an options-context block on every ranked candidate, as the news block is, so the desk measures implied-move realism and skew on its own label from day one

_area: signals · kind: improvement · impact: medium · effort: S · confidence: medium_

**Claim**

Every ranked native candidate already carries a per-candidate `news` block in the `scan_candidates_ranked` journal (agent/copilot.py:1871-1893) and in the card's decision_provenance (agent/copilot.py:1478). `cards outcomes` attributes that block descriptively (research/setups/outcomes.py:285, 744-760; docs/news-context.md §Attribution). Native brackets come only from swing levels and ATR14, with target = ratio × stop (agent/evaluator.py:208-223). No forward-looking volatility input exists in agent/, risk/ or screeners/ (grep 'implied' returns nothing). The desk therefore cannot tell whether a card's 2R target sits inside or far outside the move the options market prices over the hold. It also cannot tell whether steep-skew or high-put-volume candidates underperform on its own bracket label. That question is answerable over all ranked candidates from two scans a day, not just the 1–2 cards per day.

**Evidence**

- agentic_trader/agent/copilot.py:1871-1893 (per-candidate news block in scan_candidates_ranked)
- agentic_trader/agent/copilot.py:1478 (decision_provenance['news'])
- agentic_trader/research/setups/outcomes.py:285 (news_alignment), 744-760 (news_context summary by alignment)
- docs/news-context.md §Attribution ('descriptive ... card_policy and card_stats do not read it')
- agentic_trader/agent/evaluator.py:208-223 (stop = max(structural swing, min_stop_atr_multiple × ATR14); target = ratio × stop)
- agentic_trader/risk/rules.py:78 (earnings_blackout rule exists; no implied event-move input)
- CLAUDE.md contract 8 (news/regime/recurrence are descriptive, never gates)

**Recommendation**

Once the nightly capture (finding 1) exists, attach an `options` block, read at D-1, to each ranked native equity candidate and card. It should carry:
- status: ok, stale, uncovered or unavailable. Uncovered or stale never refuses, which is the corporate-actions rule.
- XZZ skew, Cremers–Weinbaum IV spread and 30-day ATM IV, each with its decile within that night's captured cohort.
- Put/call volume and O/S.
- `target_sigma` = target distance / (entry × ATM IV × sqrt(max_hold_sessions/252)), and `stop_sigma` on the same basis.

Add a card line 'Options (context, not a gate)'. `cards outcomes` should gain `options_*` columns and an `options_context` summary with n, mean_r_cost and hit_rate by skew decile and by target_sigma bucket (for example <0.5, 0.5–1, >1). No new LLM call. `risk/`, `card_policy`, `card_stats` and admission must never read it. Any later gate needs its own predeclared spec, as card_policy did.

**North-star impact**

Gives the operator an immediate realism check on every card: is the 2R target plausible within the hold, given priced volatility? It also builds a prospective attribution of the options literature on the desk's own bracket label, which can later justify a predeclared card_policy rule or an a priori probe.

**References**

- Xing, Zhang & Zhao (2010), JFQA 45(3):641-662: steepest-smirk firms suffer the worst earnings shocks in the following quarter — Skew is informative context for long cards, which is why its decile belongs on the card (https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html)
- An, Ang, Bali & Cakici (2014), JF 69(5):2279-2337 — Call/put IV changes as candidate-level context on a monthly horizon matching the bracket hold (https://www.nber.org/papers/w19590)

---

## Entry is tap-only, so the live book stays empty and the portfolio, risk and execution layers never act. Add a paper-only mode that enters every scheduled card at its default tier through the unchanged EntryExecutionService

_area: sizing_portfolio · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

CODE TODAY. Only two places call `execute_signal_by_id` (agentic_trader/agent/copilot.py:2912): the Telegram execute handler (copilot.py:466) and the CLI (cli/commands/trade.py:94). docs/production.md:43 states the rule: "Scans stage suggestions; an operator approves entry orders." Between scans the book is only the EXECUTED signal rows (storage/db.py:859-870). The scan reads that book at copilot.py:785 and passes it to admission (`_admit_candidate`, copilot.py:1113-1119) and to the book sizer (`book_sizer.prepare`, copilot.py:1217-1223; weights at execution/book_sizing.py:126-139,197). Only a tap creates EXECUTED rows, through `authorize` -> `enqueue_entry` (execution/entries.py:50-57; storage/workflow.py:334-446). Live evidence: 9 cards tapped out of 42 and 3 accepted entries in about 24 sessions (maps/live.md §3 item 5). Every book_sizing preview factor was 1.00 against a one-share book (docs/card-evidence.md:232). So `book_vol`, `concurrent_positions`, `aggregate_stop_risk`, `same_symbol`, `portfolio_notional` and `correlation_group` (risk/rules.py:270-346) almost never bind. MEASURED (read-only, my replay; scripts listed in evidence). I pushed all 41 cards sent since 2026-09-18 (#14-#54) through their own default tier, labelled each exit with the repo's `label_bracket` on 1h bars, and applied the configured caps: 8 positions, 2% planned stop risk, $60k notional, plus same-symbol (config/config.yaml:3-6). The book would have held a median of 4 and a maximum of 8 open positions. 10 of the 41 cards would have been refused by book rules: same_symbol 5, concurrent_positions 4, portfolio_notional 1. Planned stop risk would have peaked at $1,626, about 1.6% of equity against the 2% cap. About 31 entries in about 24 sessions works out to roughly 1.3 fills per session, against today's 3 fills in total, about 10x. Two more facts shape the design. First, native entries have no expiry. `OrderRequest` defaults to GTC (broker/base.py:98), the tap path sets no time-in-force (copilot.py:2967-2978), and `TradeLifetimeService` only times positions with an explicit policy (execution/lifetimes.py:137-138). The roadmap already measured that GTC limit entries "fill mainly when the forecast is wrong" (docs/alpha-roadmap.md:150-152). Second, the operator has no way to cancel a resting native entry: `/close` answers "No open broker position" (execution/closing.py:155-156).

**Evidence**

- agentic_trader/agent/copilot.py:466 (Telegram execute_handler) and agentic_trader/cli/commands/trade.py:94: the only two callers of execute_signal_by_id
- docs/production.md:43 'Scans stage suggestions; an operator approves entry orders.'
- agentic_trader/storage/db.py:859-870 get_active_positions = EXECUTED rows only; read at agent/copilot.py:785; fed to _admit_candidate copilot.py:1113-1119 and book_sizer.prepare copilot.py:1217-1223
- agentic_trader/execution/book_sizing.py:126-139,197 book weights come only from those rows
- agentic_trader/execution/entries.py:50-57 authorize; agentic_trader/storage/workflow.py:334-446 enqueue_entry (halt, close, alpha, account-risk and reservation checks, then SUBMITTING at :429)
- agentic_trader/agent/copilot.py:2959 quantity=None resolves to the card's default tier; :2979-2988 freshness then authorize
- agentic_trader/risk/rules.py:270-346 aggregate_stop_risk, concurrent_positions, same_symbol, portfolio_notional, correlation_group; config/config.yaml:3-7 caps
- docs/card-evidence.md:232 preview 2026-10-09 to 2026-10-19: 'six journaled factors, all 1.00 against a one-share book'
- maps/live.md §3 item 5 (Q10): 9 taps on 42 cards, 3 accepted entries; gap 7 (probe has 1 fill in 4 cards); gap 9 (62 trailing-stop notices, 29 for XOM)
- agentic_trader/broker/base.py:98 time_in_force defaults to GTC; agent/copilot.py:2967-2978 sets no TIF; agentic_trader/execution/lifetimes.py:137-138 only explicitly timed positions; docs/alpha-roadmap.md:150-152 GTC adverse-selection measurement
- agentic_trader/execution/closing.py:155-156 no cancel path for a resting native entry
- agentic_trader/research/alpha/probe.py:22,53-55 is_paper_scope ('/alpaca:paper'); agentic_trader/config.py:1053-1055 alpaca_paper derivation; agentic_trader/storage/db.py:127 scope suffix
- agentic_trader/execution/durable.py:75 ScanTrigger.SUGGESTION_SCAN (scheduled slots), distinct from SWING_SCAN at :76, which fires on daemon start
- Replay script: ~/agentic-trader-research/holistic-review-20261021/autoentry/book_sim.py (input cards.csv in the same directory, read-only psql export of signals)

**Recommendation**

This is an operator decision. It amends CLAUDE.md contract 1 and docs/production.md:43.

**Config key.** Add `execution.auto_entry: off | paper`, default `off`. Validate it at `load_config`: `paper` is refused unless `execution_mode == alpaca` and `alpaca_paper` is true. Re-check `is_paper_scope(scope)` inside `enqueue_entry` so a live scope can never auto-enter. Real money keeps the tap unchanged.

**Trigger.** Only cards with `RankedOutcome.SENT` from `ScanTrigger.SUGGESTION_SCAN` qualify. Swing, operator and restart scans are excluded (live.md gap 3). After the scan loop, call the existing `execute_signal_by_id(sig_id, quantity=None)` for each one. That uses the default tier and keeps freshness, `authorize`, the reservation and FIFO, the preflight token, the fingerprint and the lease exactly as they are. Only the caller is new. Record `entry_trigger: auto|tap` on `entry_queued` so the two populations stay separable.

**Prerequisites in the same change:**
- Bound native entries to the issuing session, with DAY TIF or the existing 57,600 s resting lifetime. Otherwise every unfilled limit holds a slot and its stop risk, and any later fills are the adversely selected ones.
- Add an operator cancel for a resting entry that reuses the existing `entry_cancel` work item. Dismiss then becomes a journaled opt-out veto.
- Move ratchet and expiry notices into the digest (live.md improvement 8). The notice volume grows with the number of open positions, and contract 3 keeps every notice.

**Alternatives considered and rejected:**
- A simulator shadow book (`EXECUTION_MODE=paper`, scope `/paper`) never reaches the alpaca:paper scan's `active_positions` and produces no broker fills.
- The desk replay (maps/portfolio.md improvement 9) is historical only.

**Experiment.** Run 20 sessions. Pass if all of these hold:
- At least one journaled `book_sizing` factor below 1.
- Per-rule refusals visible in admission audits.
- At least 20 entries with `fill_slip_r` and exit slippage.
- Realized R and labelled R reconciled card by card.

**North-star impact**

It makes "allocation decisions based on existing positions" happen live. In the replay, book rules would have changed the outcome of about 1 card in 4, against none today. The book_sizing factor would start to differ from 1, and per-rule refusals become measurable. Execution evidence (fill rate, entry and exit slippage, ratchet behaviour) would accumulate about 10x faster. The PEAD probe would get every eligible card instead of 1 in 4. The paper account's P&L would become the strategy's honest live record against SPY. Today it reflects whichever cards the operator happened to tap.

**References**

- Alpaca Docs, 'Paper Trading' (docs.alpaca.markets/us/docs/paper-trading): paper fills are matched against the NBBO. The model ignores market impact, information leakage, latency slippage and queue position for non-marketable limits, and gives random partial fills 10% of the time. — Auto-entry fill evidence on paper is an optimistic bound. It is valid for marketability, fill rate and lifecycle and protection paths, but not for queue-position slippage. Label it that way in cards outcomes before any real-money discussion. (https://docs.alpaca.markets/us/docs/paper-trading)
- Harvey, Rattray, Sinclair & Van Hemert (2017), 'Man vs. Machine: Comparing Discretionary and Systematic Hedge Fund Performance', Journal of Portfolio Management 43(4):55-69 — After factor and volatility adjustment, systematic and discretionary equity funds perform similarly. Systematic execution of a rule set does not lose return by itself, and it is what makes the rule set measurable. (https://papers.ssrn.com/abstract=2880641)

---

## The operator's tap is a fifth selection gate that nobody measures. A first replay of the 42 paper cards finds no evidence that tapped cards outperform untapped ones

_area: research_methodology · kind: gap · impact: medium · effort: S · confidence: medium_

**Claim**

CODE TODAY. `cards outcomes` breaks results down by the selection scorer (setup_quality, shadow, random), the LLM gate (approved vs vetoed mean R), market exposure, card policy, book sizing, news, regime, recurrence and counterfactual arms (research/setups/outcomes.py:857-1000). It has no operator stage. `collect_execution` keeps only the age and R consumed of an applied EXECUTE tap (research/setups/execution_evidence.py:126-136, :245-246), and `summarize_execution` reports entered and filled counts (:264-280). The PEAD probe report says "no tap or fill rate is computed" (docs/apriori-alphas.md:192). The raw inputs are already durable: `card_tap_assessed` events, signal status EXPIRED, and the labeller. MEASURED (read-only, Yahoo 1h bars through the repo's own `label_bracket` and `market_r`, 20-session hold, 5 bp per side):
- 29 sent cards since 2026-09-18 have resolved. 9 hit target. Mean −0.21R raw and −0.23R in excess of SPY.
- Collapsed to one row per symbol within 7 days, that leaves 24 distinct setups. The 4 tapped ones (XOM +2.0R, CRM −1.36R, EWJ −1.43R, VRNS −1.27R) average −0.51R, or −0.51R excess. The 20 untapped ones average +0.14R, or +0.17R excess. The difference is −0.65R with a two-sided permutation p of 0.31: no evidence that taps select better.
- 4 of the 9 tapped cards (#17, #19, #20, #21) are one CRM setup from 9-23/24.
- Tap latency on the 7 assessed taps: 4 s, 14 s, 21 s (CRM), 355 s (BLK), 1,487 s (VRNS), 15,982 s (MRK, timed out) and 17,473 s (EWJ, missed at R:R 1.33).
- No card has been tapped since BLK on 10-16. That covers the five cards sent 10-19 to 10-20, and #53/#54 are still pending.

So the human gate is taking in few cards, is concentrated on one name and is slow. Nothing shows it is informative. The sample is tiny, so the measured edge of the tap is unknown, not negative.

**Evidence**

- agentic_trader/research/setups/outcomes.py:857-1000 summarize(): selection/llm_gate/market_exposure/card_policy/book_sizing/news/regime/recurrence/counterfactual blocks; no operator block
- agentic_trader/research/setups/execution_evidence.py:126-136 execute_tap (applied EXECUTE only); :245-246 tap columns; :264-280 summarize_execution
- docs/apriori-alphas.md:192 'how many were executed (a broker fill; no tap or fill rate is computed)'
- agentic_trader/agent/copilot.py:3262 _journal_card_tap and :3481-3487 (every assessed tap is journaled, including unavailable)
- Read-only psql: card_tap_assessed payloads (7 rows: outcomes missed, missed, execute, missed, execute, unavailable, execute; ages 20.6 s, 4.3 s, 14.2 s, 17,472 s, 1,487 s, 15,982 s, 355 s)
- maps/live.md §3 item 5 and gap 9 (9 taps on 42 cards; 0 of 6 cards from the 14:35 slot tapped)
- Labelling script and output: ~/agentic-trader-research/holistic-review-20261021/autoentry/label_taps.py, labels.csv, cards.csv (Yahoo 1h buckets start at :30, Alpaca's at :00, so entry can be up to 30 min later than the production labeller)

**Recommendation**

**Add an `operator` block to `summarize()`.** Mirror the `llm_gate` block. It should report:
- tap rate by slot, trigger and strategy;
- the distribution of tap latency;
- the mix of tap outcomes (execute, missed, reprice, unavailable);
- tapped vs untapped `r_cost` and `excess_r`, de-duplicated to one row per (contract, strategy, direction, setup window), with a block-bootstrap CI.

Add tap and fill rates to the probe summary as well.

**Predeclare a decision rule before reading more data.** Use forecast-value-added logic: the human touchpoint must beat the automated baseline. Once at least 30 distinct setups have resolved:
- If tapped-minus-untapped excess R has a CI above 0, keep human selection on paper and log the features the operator keys on, as a candidate ranker.
- Otherwise treat the tap on paper as pure evidence loss. That supports the auto-entry finding.

**Under auto-entry, measure the opt-out instead.** Attribute each Dismiss or cancel veto the same way the LLM gate is attributed (vetoed vs approved mean R). The human signal is kept without starving the book.

**North-star impact**

Card quality is now judged on every layer except the one that actually decides what trades. Measuring the tap tells us whether the operator's judgement adds alpha that should be encoded, or only adds latency and evidence loss. That settles, with data, whether paper should keep opt-in taps or move to auto-entry with an opt-out veto.

**References**

- Fildes, Goodwin, Lawrence & Nikolopoulos (2009), 'Effective forecasting and judgmental adjustments: an empirical evaluation and strategies for improvement in supply-chain planning', International Journal of Forecasting 25(1):3-23, doi:10.1016/j.ijforecast.2008.11.010 — Over more than 60,000 forecasts, human adjustments to system output helped on average in 3 of 4 firms, but upward (optimistic) adjustments were much less likely to help than downward ones. The value of a human override has to be measured per direction, not assumed. Opting in to a card is the 'upward' case. (https://eprints.lancs.ac.uk/id/eprint/45487/)
- Gilliland, 'FVA: A Reality Check on Forecasting Practices', Foresight issue 29 (Forecast Value Added: compare each process step and human touchpoint against the preceding step and a naive baseline) — The stairstep method fits directly onto the card funnel: replay base rate, then scorer, LLM gate, operator tap. Each step must show FVA ≥ 0. (https://forecasters.org/wp-content/uploads/FVA_A-Reality-Check_Foresight29.pdf)

---

## Macro lockout fails open silently on a scraped ForexFactory feed, and only FOMC afternoons ever trigger it

_area: risk · kind: defect · impact: medium · effort: S · confidence: high_

**Claim**

The macro lockout has one source: ForexFactory's unofficial 'this week' JSON (agentic_trader/constants.py:126). The desk fetches it with a browser User-Agent and a 6 s timeout (agentic_trader/agent/calendar.py:186-193). Any failure leaves events=[] and caches that as a successful fetch for 30 minutes (calendar.py:216-221). Failures include a timeout, a non-200 response, or a non-JSON body. Forum users report one such body: an HTML 'Request Denied' page served when a per-IP download cap is exceeded. After that, the scan-start check (agentic_trader/agent/copilot.py:809-830), the evaluator gate (agentic_trader/agent/evaluator.py:526-541), the tap gate and the admission preflight (copilot.py:3962-3987) all see 'no event'. The LLM prompt is then told 'Lockout verified: CLEAR. No Tier-1 US economic releases ... scheduled' (calendar.py:156-160). Nothing journals the failure, alerts, or degrades readiness: there is no calendar component in agentic_trader/diagnostics/readiness.py:17-28. The earnings blackout, by contrast, fails open with an explicit 'Unverified' note (docs/risk-policy.md:119-120). The lockout window is 60 minutes before and 30 after (config/config.yaml:277-278). Only keyword titles count; FF's 'High' impact flag never gates (calendar.py:19-37, :121). Every BLS keyword release (CPI, PPI, payrolls) comes out at 08:30 ET, so its window [07:30, 09:00] never reaches a regular-hours scan or tap. Only two events trigger the lockout during the session: the FOMC statement at 14:00 ET (window 13:00-14:30) and the press conference at 14:30 ET (window 13:30-15:00). The press-conference window contains the 14:35 suggestion scan (config/config.yaml:331). The next two such days are 2026-10-28 and 2026-12-09. So this scraped dependency matters on about 8 sessions a year. The Fed already publishes those dates through 2027, and BLS publishes CPI dates through Nov 2027.

**Evidence**

- agentic_trader/constants.py:126
- agentic_trader/agent/calendar.py:186-193
- agentic_trader/agent/calendar.py:216-221
- agentic_trader/agent/calendar.py:156-160
- agentic_trader/agent/calendar.py:19-37
- agentic_trader/agent/calendar.py:118-125
- agentic_trader/agent/copilot.py:809-830
- agentic_trader/agent/copilot.py:3962-3987
- agentic_trader/agent/evaluator.py:526-541
- agentic_trader/agent/evaluator.py:682
- agentic_trader/diagnostics/readiness.py:17-28
- config/config.yaml:277-278
- config/config.yaml:331
- docs/risk-policy.md:119-120 (earnings fails open with an 'Unverified' note; no equivalent for macro_lockout, docs/risk-policy.md:105)
- Probe 2026-10-21 (holistic-review/macro-sourcing-probes/ff_probe.json): feed served 2026-10-18..2026-10-24 in -04:00, 14 USD events, 0 High: a quiet week and a failed fetch look identical downstream

**Recommendation**

Replace the runtime dependency with a static table pinned by its SHA-256 digest: config/calendar/us-tier1-v1.json. It holds FOMC statement (14:00 ET) and press-conference (14:30 ET) times for 2026-2027 from the Fed page, and 08:30 ET CPI, PPI and Employment Situation dates from the BLS schedule; BEA GDP and PCE dates are optional. Load it through a StaticTier1Calendar(BaseEconomicCalendar) and stamp its digest into every scan journal. Give the table a valid_through date. When fewer than 30 days remain, degrade a new readiness component, macro_calendar. Past that date, call the day 'unverified' in the prompt and apply an operator setting in validated config (risk.macro_calendar_unknown: open|closed). Do not silently pass. Keep ForexFactory (or the BLS iCalendar feed) only as a cross-check: lock out on the union of the table and the live feed, and journal each disagreement as a revision. The 2025 shutdown moved and cancelled BLS releases, so a schedule change must become a new table version. Make fetch_events return a typed status that separates 'fetched, zero events' from 'fetch failed', and never cache a failure as success. Change the prompt line to name the source and digest. Add a calendar sub-block (source, digest, status, events in window) to regime_journal_block.

**North-star impact**

On FOMC afternoons (about 8 a year, the most volatile scheduled hour for US equities) the lockout is meant to skip the 14:35 card batch and refuse taps from 13:00 to 15:00 ET. Today that protection depends on an unofficial endpoint. When the endpoint fails, the LLM is still told the lockout was verified. A static, digest-pinned table makes the one gate that actually triggers deterministic and auditable, and removes a scraped call from every scan.

**References**

- Federal Reserve, Meeting calendars and information (FOMC 2026 incl. Oct 27-28 and Dec 8-9; 2027 schedule published; 'each meeting date is tentative until confirmed at the meeting immediately preceding it') — Official year-ahead source for the only lockout events that fall in regular hours (https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- BLS, Consumer Price Index release schedule (08:30 ET; dates published through Nov 2027; 'updated as needed', usually at least a week before) — Official schedule for the 08:30 tier-1 releases; shows they never overlap a regular-hours window (https://www.bls.gov/schedule/news_release/cpi.htm)
- BLS release iCalendar feed (returned HTTP 403 to an unadorned client in this review's probe) — Official live feed for cross-checking a static table; needs a descriptive User-Agent, like SEC EDGAR (https://www.bls.gov/schedule/news_release/bls.ics)
- ForexFactory programmer forum (community-reported, unverified: since Aug 2024 weekly export files are capped at 2 downloads per 5 minutes per IP; over-limit requests get an HTML 'Request Denied' page; the file updates hourly) — Explains how a daemon plus a CLI dry scan on one host could get the HTML body that this code caches as 'no events' (https://www.forexfactory.com/thread/post/15041460)

---

## Regime inputs: VIX has one unofficial source whose failure aborts the scan without a record, and losing any of ten mandatory macro inputs silently resets sizing to 1.0x

_area: risk · kind: defect · impact: high · effort: M · confidence: high_

**Claim**

Sources and failure handling:
- yfinance history(period='5d') is the only source for VIX, ^TNX, ^IRX, ^FVX, ^TYX and DXY, and returns no observation timestamp (agentic_trader/agent/macro.py:283-293, 383-390; agentic_trader/agent/regime.py:103-113). The repo's own source assessment rates Yahoo 'Unofficial interface ... no SLA' (docs/research-data-sources.md:42).
- The 2y, HY OAS and both breakevens come from scraping the last row of fredgraph.csv, with no API key (macro.py:296-351; constants.py:165-173).
- All ten inputs are mandatory. If any one is missing, the macro report raises (macro.py:411-413). get_regime catches that and continues with risk_multiplier=1.0 and VIX-only breakout and R:R rules (regime.py:146-151, 185-189).
- Five of the ten never affect the stress level: the 5y, 30y, DXY, T10YIE and T5YIE. The inflation argument is accepted and never used (macro.py:207-280). So a breakeven timeout alone can lift a book that should be at 0.25x (EXTREME, critical credit) or 0.50x (HIGH, inverted curve) to 1.0x and re-allow squeeze breakouts. docs/risk-policy.md:59-61 says a multiplier is 'never read as 1.0'.
- The operator toggles credit_oas_enabled and inflation_breakeven_enabled (agentic_trader/config.py:488-489; config/config.yaml:287-288) are read nowhere.

Live evidence: at 2026-10-21 17:04 UTC both fred.stlouisfed.org/graph/fredgraph.csv and api.stlouisfed.org returned HTTP 200 text/html 'Site Maintenance'. raise_for_status passes and the parser finds no data row, so all four FRED inputs come back None. While that page is served, every scan runs with macro_stress null and a 1.0x multiplier.

When the macro report fails, the fallback calls the same yfinance for VIX (regime.py:160-170). If Yahoo throttles (yfinance issues report 429s lasting hours), get_regime raises at an unguarded call (copilot.py:780) and the whole run_scan aborts:
- no cards;
- no SCAN readiness observation; the component only goes stale after scan_max_age_seconds=18000 (config.py:619);
- no pead_decision event, although copilot.py:735 promises one for every decision (the skip journal covers only halt and lockout, copilot.py:772-775 and 826-829);
- for the 14:35 slot, no digest, because publish_scan_digest follows run_scan with no try/finally (agentic_trader/cli/commands/service.py:440-461). The only scheduler listener is for EVENT_JOB_MISSED (service.py:501).

The curve slopes also mix sources and clocks. The code subtracts FRED's DGS2 (a prior-business-day H.15 print) and Yahoo's ^IRX from Yahoo's live ^TNX (macro.py:384-394, 139-156). Those slopes drive the INVERTED and FLAT thresholds, which set the 0.50x multiplier and breakout suppression (macro.py:233-238, 255-259).

**Evidence**

- agentic_trader/agent/regime.py:103-113
- agentic_trader/agent/regime.py:146-170
- agentic_trader/agent/regime.py:185-189
- agentic_trader/agent/macro.py:207-280
- agentic_trader/agent/macro.py:283-351
- agentic_trader/agent/macro.py:383-413
- agentic_trader/constants.py:161-173
- agentic_trader/config.py:479-491
- config/config.yaml:281-290
- agentic_trader/agent/copilot.py:735
- agentic_trader/agent/copilot.py:762-780
- agentic_trader/agent/copilot.py:772-775
- agentic_trader/agent/copilot.py:826-829
- agentic_trader/cli/commands/service.py:440-461
- agentic_trader/cli/commands/service.py:501
- agentic_trader/config.py:619
- agentic_trader/diagnostics/readiness.py:17-28
- docs/risk-policy.md:59-61
- docs/research-data-sources.md:42
- Probe 2026-10-21 17:04 UTC: curl fredgraph.csv?id=VIXCLS -> 200 text/html 'Site Maintenance | St. Louis Fed'; api.stlouisfed.org -> same HTML

**Recommendation**

1. VIX: use Cboe as the primary source. Its VIX_History.csv gives the prior close and was current through 2026-10-20 in this review's probe. It answers with a 307 to cdn-api.cboe.com, so httpx needs follow_redirects=True. Use Cboe's delayed quote for intraday. Keep yfinance as the secondary, and tag every value with its source and as-of time.
2. Curve: take all tenors from one source on one date. Options are Treasury's Daily Par Yield Curve CSV (one row per date, 1 Mo to 30 Yr; verified current through 2026-10-20) or FRED DGS3MO, DGS2 and DGS10 together through the keyed API. Never subtract a prior-day print from a live quote.
3. Make only the decision inputs mandatory: VIX, 3m, 2y, 10y and HY OAS. Treat DXY, 30y, 5y and the breakevens as optional display fields. Delete the dead toggles or wire them up.
4. When decision inputs are missing, set an explicit UNKNOWN stress level with an operator-configured multiplier and breakout policy (validated config, for example 0.5x and breakouts off) instead of 1.0. Optionally allow a last-known-good value within a declared age, such as one business day for daily FRED series.
5. When VIX is unavailable, have run_scan journal a typed regime_unavailable skip, call _journal_skipped_drift, observe SCAN=false with the reason, and queue one outbox notice. Run the digest in a finally block.
6. Add a readiness component, macro_inputs, that reports each input's source, observation date and age, plus a matching doctor check.
7. Add a test pinning that a non-decision input failure leaves the multiplier unchanged.

**North-star impact**

These inputs set the size and breakout permission of every card. Today a hiccup in a feed that does not affect the decision can quadruple risk in a stressed tape. A Yahoo throttle can cost a whole session's cards and its PEAD decision with no signal to the operator. Official sources on one clock, plus typed handling of unknown inputs, keep cards flowing with honest, conservative sizing when a feed fails, and make the cause visible.

**References**

- Cboe, VIX daily price history CSV (1990-01-02 onward; last row 2026-10-20 when probed; served via 307 redirect to cdn-api.cboe.com) — Official, free VIX source to replace or back up yfinance (https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)
- U.S. Treasury, Daily Treasury Par Yield Curve Rates (CSV by year; 1 Mo to 30 Yr per date; current through 2026-10-20 when probed) — One official source for every tenor on one date, removing the ^TNX/DGS2/^IRX clock mix (https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve)
- yfinance GitHub issue #2422 and discussion #2431: 'YFRateLimitError: Too Many Requests' on single-ticker requests in 2025, sometimes lasting more than 24 h — A realistic failure mode that today aborts the suggestion scan through regime.py:168-169 (https://github.com/ranaroussi/yfinance/discussions/2431)
- FRED API documentation (keyed series/observations endpoints with realtime_start/realtime_end) — Documented, keyed alternative to scraping the graph CSV; returns observation dates explicitly (https://fred.stlouisfed.org/docs/api/fred/series_observations.html)

---

## The regime gates change most replayed sessions but have never been replayed, because the desk keeps no macro history or provenance

_area: research_methodology · kind: gap · impact: high · effort: M · confidence: medium_

**Claim**

Replay applies none of the live regime rules:
- Replay builds RiskEvaluator with a no-I/O calendar and never calls the regime detector (agentic_trader/research/setups/replay.py:128-133, 287-291).
- It sizes with calculate_levels_deterministic(candidate) (replay.py:313), whose defaults are macro_risk_multiplier=1.0 and min_reward_risk=None (agentic_trader/agent/evaluator.py:166-174).
- So every replayed population (the bracket counterfactual, the setup-recurrence test) leaves out breakout suppression, the 2.2R/2.5R regime targets, the macro multiplier and the FOMC lockout.

The desk keeps no macro history:
- The macro layer holds only 'latest' values (docs/alpha-data-augmentation-2026-10-18.md §1, Macro row, line 24).
- The scan journal's regime block records no input sources, no per-input observation dates, no macro_unavailable_reason and no calendar status (agentic_trader/agent/regime.py:58-100; docs/card-evidence.md 'Regime and recurrence context').
- /macro does audit observation_dates and the unavailable reason (copilot.py:4432-4446), but scans do not. So the by_macro_stress bucket in 'cards outcomes' cannot tell a feed failure from a calm tape.

The gates are not marginal. In this review's probe (holistic-review/macro-sourcing-probes), official closes (Cboe VIX history and Treasury par curve, 2017-2026) were run through the live thresholds (regime.py:172-189; macro.py:149-156, 250-259). Results for the bracket counterfactual's primary window, 2021-06-01 to 2026-01-29 (config/research/bracket-counterfactual-v1.json 'window', 1,164 sessions):
- the curve was inverted (10y-3m or 10y-2y below zero) on 60.0% of sessions;
- live rules would have suppressed squeeze_breakout on at least 63.4% of sessions (VIX above 30, or inversion, which sets HIGH stress);
- live rules would have required targets of at least 2.2R on at least 71.5% of sessions.
On 2017-01-03 to 2021-04-30 the last two figures are 18.8% and 34.9%. These are lower bounds: they exclude HY OAS and use daily closes rather than live intraday quotes.

So the A0_live arm (planned 2R) and its 1,311 squeeze setups (docs/bracket-counterfactual-2026-10-18.md 'Protocol and run', 'Descriptives') are largely not what live would have carded. The gate thresholds (agentic_trader/constants.py:176-178; agentic_trader/config.py:479-491) have never been tested. Credit stress also cannot be rebuilt later: FRED's ICE BofA series notes say that since April 2026 these series carry only a rolling 3 years of observations.

**Evidence**

- agentic_trader/research/setups/replay.py:128-133
- agentic_trader/research/setups/replay.py:287-291
- agentic_trader/research/setups/replay.py:313
- agentic_trader/agent/evaluator.py:166-174
- agentic_trader/agent/regime.py:58-100
- agentic_trader/agent/regime.py:172-189
- agentic_trader/agent/macro.py:149-156
- agentic_trader/agent/macro.py:250-259
- agentic_trader/risk/rules.py:458-464
- agentic_trader/agent/copilot.py:4432-4446
- agentic_trader/constants.py:176-178
- config/research/bracket-counterfactual-v1.json (window [2021-06-01, 2026-01-29]; strategy_config includes squeeze_breakout)
- docs/alpha-data-augmentation-2026-10-18.md §1 (Macro row, line 24)
- docs/card-evidence.md 'Regime and recurrence context' (journaled regime keys)
- docs/bracket-counterfactual-2026-10-18.md 'Protocol and run' and 'Caveats' (no regime caveat)
- Probe: holistic-review/macro-sourcing-probes/{vix_hist_probe.csv,ust/2017..2026.csv}; inverted 60.0%, breakout suppressed >=63.4%, min_rr>2.0 >=71.5% on 2021-06-01..2026-01-29

**Recommendation**

1. Build a research-only 'alpha macro-history' acquisition into a private cache pinned by digest, shaped like 'alpha corporate-actions'. Sources: Cboe VIX_History.csv (1990 onward); Treasury par yield curve CSVs by year (1990 onward); FOMC meeting dates and BLS/BEA release dates by year; and ALFRED vintages (FRED API realtime_start/realtime_end) for any FRED series used. For HY OAS, take whatever window FRED still serves, plus the journal from now on.
2. Add a pure function regime_at(session, scan_time) that reuses classify_vix_level and compute_compound_macro_stress on inputs visible strictly before the decision: the prior close plus values already published that morning. Declare in writing that this differs from live's intraday VIX.
3. Predeclare a regime-gate replay on the existing setup caches, paired by setup like the bracket counterfactual. Arms: no gates (what today's replays do); full live gates; and VIX-only gates. Pass rules are on R per session net of SPY. Only a pass justifies keeping or retuning the thresholds. If the gates do no better than no gates, that is evidence to retire breakout suppression and the R:R increase, which would raise card count.
4. Extend regime_journal_block with sources, observation_dates, macro_unavailable_reason, the raw decision inputs (VIX, 3m, 2y, 10y, HY OAS) and a calendar sub-block. Each scan then leaves a point-in-time macro record, and the desk owns its own HY OAS history.
5. Until replays apply the gates, label their 'live' arm 'ungated'.

**North-star impact**

The regime layer is the funnel's largest unmeasured filter. By this proxy it would have changed the target or suppressed the setup on most sessions of the windows that every closed lane was judged on. Measuring it either recovers breakout cards that are suppressed today or justifies the suppression. It also makes the live-versus-replay comparisons in 'cards outcomes' and the replay studies honest.

**References**

- Cboe, VIX daily price history CSV (1990 onward) — Free official VIX history for regime_at() and for the gate-coverage figures above (https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)
- U.S. Treasury, Daily Treasury Par Yield Curve Rates (yearly CSV, 1990 onward) — Free official curve history to replay the INVERTED/FLAT rules (https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve)
- FRED ICE BofA series notes: 'Starting in April 2026, this series will only include 3 years of observations' (seen in search snippets; FRED was in maintenance during this review, so not fetched directly) — Credit-stress history is disappearing upstream; the desk must journal HY OAS itself or accept a credit-free replay (https://fred.stlouisfed.org/series/BAMLH0A0HYM2)
- ALFRED (archival FRED) and FRED API real-time periods — Point-in-time vintages for FRED macro inputs, avoiding look-ahead from revised series (https://alfred.stlouisfed.org/)

---

## Price the paid point-in-time pack before building it: Sharadar's full-history bundle ($499/yr) covers most of the planned free EDGAR builds, including the one step that has no free source

_area: data · kind: integration · impact: high · effort: S · confidence: medium_

**Claim**

WHAT IS TRUE NOW. Every pooled cohort uses current membership. cohort-v2.json:4 and cohort-v3.json:4 both say "not point-in-time", and all 64 pooled formula-cohort pairs ran on these cohorts. The free repair plan resolves delisted CIKs through "the SEC's CIK-to-ticker map" (survivorship-spike-2026-10-20.md:15-17, :47-50); the maps' data I5, pooled I10 and history I7 follow the same plan. The map the code actually reads is current-only (insider_filings.py:61 TICKERS_URL; symbol_ciks at :574-596). I measured this read-only on 2026-10-21 against the insider acquisition's cached company_tickers.json (10,443 rows):
- None of the 21 names the spike lists (SIVB, FRC, TWTR, ATVI, SPLK, VMW, PXD, SGEN, HES, JNPR, ANSS, MRO, CTLT, DFS, SQ, SWN, AJRD, NLSN, CTXS, SBNY, WBA) is present.
- FB is absent (it is now META). FISV maps to Fiserv CIK 798354. Q maps to Qnity, CIK 2058873.

So the hardest step of the free route, finding the ticker a dead CIK traded under, has no free authoritative source.

The plan to use "Alpaca's inactive list for 2016–2021 and EDGAR from 2022" (data I5) also splits on the wrong date. Discovery runs 2016-08-01 → 2021-12-31 (campaign-v2.json:11-14). Alpaca's list records only 16–27 non-OTC delistings a year for 2023–2026, against 335–703 a year for 2019–2021 (survivorship-spike:11-13). Names alive in discovery that died after 2022, such as the 2023–25 merger wave, therefore need EDGAR or a vendor in every discovery year. The docs already made membership a precondition:
- alpha-expansion-survey-2026-09-23.md:94-97 says mining beyond about 300 names needs Sharadar or Norgate, yet cohort v3 has 1,000 current names.
- alpha-pooled-mining.md:1372-1374 and alpha-equity-universe.md:162-163 say the same.

The data-augmentation doc defers paid data without pricing it (alpha-data-augmentation-2026-10-18.md:52, :95). Index-inclusion events were skipped as "paid constituent history" (alpha-roadmap.md:576-577; corporate-actions.md:230).

VENDOR FACTS (vendor pages checked 2026-10-21):
- **Sharadar bundle**, personal-use licence, API and bulk download.
  - Price: $29/mo for 5 years of history; $49/mo or $399/yr for 10 years; $69/mo or $499/yr for full history.
  - Coverage: 21,000 active and delisted stock tickers from December 1997, with permaticker as the primary key.
  - Reference fields: SIC code and sector, first and last price date, and actions that include delist reasons and ticker changes.
  - Prices: unadjusted, split-adjusted and fully adjusted end-of-day prices.
  - Fundamentals are point-in-time by filing date and cover about 9,000 delisted companies. Insider data starts in 2005, and 8-K events are included.
  - A 10-year tier bought now starts around October 2016. That is after bars_from 2016-01-01 and the discovery start of 2016-08-01 (campaign-v2.json:8-14), so the full-history tier is the one to buy.
- **Norgate**: Platinum costs $630/yr and Diamond $787.50/yr. Delisted symbols carry a -YYYYMM suffix, and historical index constituents are included. Its updater and Python package require Windows 10/11 or Windows Server, while the desk is a macOS launchd host. Fundamentals and industry classification are latest-only.
- **EODHD All-in-One**: $999.90/yr. Names delisted before 2018 have end-of-day prices only; fundamentals, splits and dividends exist only for later delistings. Data is keyed by ticker.

**Evidence**

- config/research/pooled/cohort-v2.json:4
- config/research/pooled/cohort-v3.json:4
- config/research/pooled/campaign-v2.json:8-14
- agentic_trader/research/insider_filings.py:61
- agentic_trader/research/insider_filings.py:574-596
- measured 2026-10-21 (read-only): ~/agentic-trader-research/insider-filings-cache/company_tickers.json, 10,443 rows; none of the 21 spike-named delisted or renamed tickers present
- docs/survivorship-spike-2026-10-20.md:7-17 (Answer), :11-13 (inactive list by last-bar year), :47-50, :56-64 (Recommendation)
- docs/alpha-data-augmentation-2026-10-18.md:51-52 (§2 rows 10-11), :54-58 (one SDD day per module), :95 (§4 item 6)
- docs/alpha-expansion-survey-2026-09-23.md:94-97
- docs/alpha-pooled-mining.md:1372-1374
- docs/alpha-equity-universe.md:162-163
- docs/alpha-roadmap.md:576-577
- docs/corporate-actions.md:230
- docs/alpha-pooled-cohort3-2026-10-16.md:125-126 (long base rate +0.18R on v2 → +0.08R on v3)

**Recommendation**

Make this a priced decision now rather than "only on evidence". My estimate of the free route, using the doc's figure of one SDD day per acquisition module:
- Form 25/15 acquisition: about 1 day. A Form 25 takes effect 10 days after filing, so the last session must still come from bars.
- Historical CIK→ticker resolution: 1–2 days or more, by heuristic name matching against Alpaca's inactive list and Nasdaq directories, with residual error.
- SIC from EDGAR submissions: about 1 day, and only for CIKs it can resolve.
- XBRL shares and fundamentals: 1–2 days with "messy tags", and data only from 2009–2011.

That is roughly 4–6 SDD-days plus upkeep, against $499/yr. Steps:
1. Buy Sharadar full history. Verify the personal-use and retention terms first. One $69 month is enough to evaluate it.
2. Acquire it with a research-only, digest-pinned `alpha sharadar` command, following the `alpha short-volume` pattern (about 1 SDD day).
3. Run a fail-closed coverage audit: TICKERS must contain all 22 names the spike probed and at least 95% of the 2,147 Alpaca inactive names that have bars.
4. If the audit passes, drop the free Form 25/15, SIC and XBRL builds from the plan. Use:
   - TICKERS first/last price date, category and ACTIONS for a point-in-time membership column keyed by permaticker (data I5, pooled I10);
   - SIC/famaindustry for the cube's risk arrays and for classifying live dynamic names (pooled I1, risk I3);
   - SF1 shares and market cap for size, turnover and the fundamentals conditioning test (data I6; augmentation §4 item 4);
   - SF2 as a cross-check of the EDGAR insider lane.
5. Run one predeclared descriptive read: the long-bracket leg mean over discovery on point-in-time v2 against current v2. This sizes the bias in the base rates behind the leg-mean gates and card_policy without touching the confirmation window.
6. Choose Norgate instead only if a Russell-membership universe or index-inclusion events become a goal, and accept that it needs a Windows VM.

**North-star impact**

Long base rates, the pooled leg-mean gates and card_policy are all read on survivor-conditioned cohorts today, so every card's "honest base rate" is an upper bound. One purchase gives three things for the cost of about one acquisition day: point-in-time membership, authoritative sectors for dynamic-name correlation groups, and point-in-time size and fundamentals for conditioning native cards. It also stops spending further SDD-days on reference plumbing that a vendor already maintains.

**References**

- Sharadar subscription page (plans and prices, personal-use licence) — Bundle full history $69/mo or $499/yr; 10-year $399/yr; bulk download matching the purchased history (https://sharadar.com/subscribe)
- Sharadar stock price data page — 21,000 active and delisted stock tickers from Dec 1997; permaticker key; SIC, sector, first/last price date; actions with delist reasons and ticker changes; unadjusted close (https://sharadar.com/prices)
- Sharadar Core US Equities Bundle data fact sheet (Quandl) — About 9,000 delisted companies; point-in-time fundamentals indexed to filing date; insiders from 2005 (https://resources.quandl.com/a/res-hub/Sharadar_Datasheet_final.pdf)
- Norgate Data US stock packages — Platinum $630/yr, Diamond $787.50/yr; delisted securities and historical index constituents from Platinum (https://norgatedata.com/stockmarketpackages.php)
- Norgate Data package FAQ — Delisted symbols get a -YYYYMM suffix; only the latest classification and fundamentals are provided (https://norgatedata.com/data-package-faq.php)
- norgatedata on PyPI — Requires Windows 10/11 or Windows Server with the Norgate Data Updater running, which does not fit the macOS desk (https://pypi.org/project/norgatedata/)
- EODHD pricing — All-in-One $99.99/mo or $999.90/yr; Fundamentals feed $59.99/mo (https://eodhd.com/pricing)
- EODHD delisted companies data — Names delisted before 2018 have end-of-day prices only; fundamentals, splits and dividends only after 2018; intraday only after 2021 (https://eodhd.com/financial-apis/delisted-stock-companies-data-2)
- SEC Form 25 instructions (Rule 12d2-2) — Delisting takes effect 10 days after filing, so a free Form 25 list gives a notice date, not the last session (https://www.sec.gov/about/forms/form25.pdf)

---

## Whatever the source, the research stack keys securities by today's ticker, never sets Alpaca's asof, and drops trades that run into a delisting as 'immature': a point-in-time cohort would still carry identity and delisting-return bias

_area: research_methodology · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

This is latent today, because current-membership cohorts contain no dead names. It becomes active the moment data I5 or pooled I10 adds them, whether the data is bought or built.

(1) **Identity.**
- Bar requests never pass `asof`. StockBarsRequest at providers.py:245-252 has no such field, and the only `asof` in the codebase is the evidence allowlist at data/evidence.py:24-27. Alpaca therefore applies its default mapping, which returns the company that holds the symbol today.
- Alpaca says this mapping "isn't always perfect": symbol Q merged Quintiles (2016–17) with Qnity (2025) until a December 2025 fix.
- The research cache key is `{symbol}_{timeframe}` (setups/runner.py:172), so whichever entity is fetched first owns the ticker string. The spike found FISV and SBNY reused (survivorship-spike:14-15), and the SEC map now maps FISV to Fiserv after its FI years.
- The event lane states that "the cohort's bars carry today's symbol" (pooled/events.py:8-10).
- The insider lane maps symbols to CIKs through the current ticker map (insider_filings.py:61, :574-596), which held none of the 21 dead tickers I checked. Dead names would therefore be NaN on every insider field. The conditional formulas divide by a 0/1 condition, so those names would silently leave the decile (cohort3-insider-v1.json rationale).
- The Nasdaq earnings history also lacks delisted names (apriori-pead-2026-09-26.md:131-133).

So event and insider lanes would stay survivor-conditioned even on a point-in-time cohort.

(2) **Delisting returns.**
- `label_bracket` returns IMMATURE when the bars end before a stop, a target or the 20-session timeout (research/setups/labels.py:318-327).
- The cube drops IMMATURE cells (research/pooled/cube.py:296-298).
- SIP bars stop at the last exchange session; for example, SIVB ends on 2023-03-09 (survivorship-spike:7-10). Any post-halt or OTC loss is therefore never in the bars.
- As a result, a long bracket opened within 20 sessions of a delisting that the last bar did not stop out is removed. This is the delisting-return bias documented for CRSP (Shumway 1997; Shumway–Warther 1999).

**Evidence**

- agentic_trader/data/providers.py:245-252
- agentic_trader/data/evidence.py:24-27
- agentic_trader/research/setups/runner.py:158-189 (cache key at :172)
- agentic_trader/research/pooled/events.py:8-10
- agentic_trader/research/insider_filings.py:61
- agentic_trader/research/insider_filings.py:574-596
- config/research/pooled/cohort3-insider-v1.json:303 (rationale: conditional formulas, current ticker map)
- agentic_trader/research/setups/labels.py:159-182, :318-327
- agentic_trader/research/pooled/cube.py:288-298
- docs/survivorship-spike-2026-10-20.md:7-17
- docs/apriori-pead-2026-09-26.md:131-136 (Caveats)
- docs/alpha-pooled-cohort3-2026-10-16.md:20-23 (98 identity breaks on v3)

**Recommendation**

Make these prerequisites of any point-in-time cohort. They apply equally to the Sharadar and the EDGAR route.
1. Add a `SecurityId` key: a permaticker, or a CIK plus its listing interval. Use it for the cohort, cube columns, field frames and cache.
2. Fetch bars per (ticker in use, asof = end of the interval). Key the cache as `{security_id}_{ticker}_{asof}`.
3. Check every fetched series session by session against an independent unadjusted close (Sharadar SEP closeunadj, or yfinance with the documented flags), and fail closed above a declared tolerance.
4. Freeze regression fixtures for Q, FISV→FI→FISV, SBNY, SQ→XYZ and FB→META.
5. Map event and insider sources by security id. Through Sharadar ACTIONS ticker changes, this also carries fields across name_change chains instead of blanking them (data I7/G8).
6. Give `label_bracket` an explicit `DELISTED` outcome for series that end inside the hold on a security with a delisting record:
   - cash mergers: the last close (or the deal price);
   - performance delistings: an OTC price where one exists, else a declared proxy. Shumway–Warther's −55% applies to Nasdaq; the −30% often used for NYSE/AMEX is a later secondary convention, so verify it against Shumway 1997 before freezing it.
7. Report IMMATURE and DELISTED counts by year in the existing `unlabelled_by_year` coverage, so the effect of the repair is visible.

**North-star impact**

Without these steps, the survivorship repair produces a cohort that looks point-in-time but still drops dead losers' outcomes and mislabels reused tickers. The next mining round would then report cleaner-looking but still inflated long base rates, and any finalist or card_policy evidence drawn from it would be untrustworthy. With them, absolute leg-mean claims and the native funnel's replayed base rates become defensible.

**References**

- Alpaca Market Data FAQ: symbol mapping with asof — Mapping is on by default; an earlier asof returns the earlier holder of a ticker; asof=- disables mapping (https://docs.alpaca.markets/us/docs/market-data-faq)
- Alpaca forum: historical bars of symbol Q comprised of two different stocks (Nov–Dec 2025) — Alpaca staff confirm the mapping is imperfect; a missing entry merged Quintiles and Qnity (https://forum.alpaca.markets/t/fixing-data-historical-bars-of-symbol-q-are-comprised-of-two-different-stocks/18184)
- Shumway, T. (1997), The Delisting Bias in CRSP Data, Journal of Finance 52(1), 327–340 — Omitted performance-delisting returns are large and negative (https://ideas.repec.org/a/bla/jfinan/v52y1997i1p327-40.html)
- Shumway, T. and Warther, V. (1999), The Delisting Bias in CRSP's Nasdaq Data and Its Implications for the Size Effect, Journal of Finance 54(6), 2361–2379 — A −55% correction for missing Nasdaq performance-delisting returns removes the bias (https://ideas.repec.org/a/bla/jfinan/v54y1999i6p2361-2379.html)

---

## Alpaca Algo Trader Plus ($1,188/yr) fixes neither survivorship nor research parity: decide on it only from a measured admission-quote problem

_area: execution · kind: improvement · impact: medium · effort: S · confidence: medium_

**Claim**

WHAT IS TRUE NOW. Live scans and admission read IEX:
- `load_config` falls back to iex when ALPACA_DATA_FEED is unset (config.py:1043-1050), and `AppConfig.alpaca_data_feed` defaults to "iex" (config.py:925).
- Admission fetches the latest trade and quote on that feed (broker/alpaca.py:576-583).
- It rejects either if older than `entry_quote_max_age_seconds`, default 60 s (config.py:573; capacity.py:233-234).
- It uses the IEX ask as the buying-power and exposure price (capacity.py:267-292).

The free Basic plan gives real-time IEX, SIP history excluding the latest 15 minutes, and 200 calls/min. Algo Trader Plus costs $99/mo for real-time coverage of all exchanges and 10,000 calls/min (Alpaca docs; research-data-sources.md:25-28).

Three of the plan's apparent benefits are already available for free:
- **Bar parity (data G1).** Replay deliberately uses only bars that ended at 10:00 or 14:00 (research/setups/replay.py:17-24). The scans run at 10:35 and 14:35 (config/config.yaml:331), 35 minutes later, so 15-minute-delayed SIP already serves every bar replay uses. The live PEAD path already reads SIP this way (agent/copilot.py:397-401). Real-time SIP would only feed the still-forming bar that replay excludes.
- **Cube build speed.** The plan's rate limit is not what makes builds slow. Cohort v3's hourly download ran sequentially at about 2.3 s per request, roughly 26 requests/min, which is about one eighth of Basic's 200/min (alpha-pooled-cohort3-2026-10-16.md:26-35).
- **Research power.** Research already reads historical SIP, and the intraday fixed set found nothing on it (alpha-data-augmentation-2026-10-18.md:51).

The one consumer the plan genuinely improves is admission's IEX quote and trade (execution G6). No doc measures a problem there. The evidence for measuring it exists:
- every submitted entry persists `broker_context` with the IEX quote and its timestamp (storage/workflow.py:606-612);
- refusals return "Entry capacity refused: … is stale …" (workflow.py:600-602; capacity.py:63-66).

**Evidence**

- agentic_trader/config.py:573
- agentic_trader/config.py:925
- agentic_trader/config.py:1043-1050
- agentic_trader/broker/alpaca.py:576-596
- agentic_trader/execution/capacity.py:63-66, :233-234, :267-292
- agentic_trader/storage/workflow.py:600-612
- agentic_trader/research/setups/replay.py:17-24
- config/config.yaml:331
- agentic_trader/agent/copilot.py:397-401
- docs/research-data-sources.md:9-29
- docs/alpha-pooled-cohort3-2026-10-16.md:26-35 (Building the cube)
- docs/alpha-data-augmentation-2026-10-18.md:51 (§2 row 10)

**Recommendation**

Rank this purchase last, behind the point-in-time reference data, and make it a predeclared measurement call.
1. Move scan bars to delayed SIP on completed bars (data I1). It is free and closes G1.
2. Parallelise the research bar fetch up to Basic's 200/min before blaming the plan for slow cube builds.
3. For two weeks of taps, tabulate from the persisted `broker_context` and fill evidence:
   - the IEX quote and trade age at submission;
   - the IEX spread;
   - fill price minus IEX ask, in basis points;
   - refusals by message, in particular "Market quote/trade is stale".
4. Predeclare the rule, for example: buy Algo Trader Plus only if staleness refusals exceed 5% of taps, or the median fill-minus-IEX-ask exceeds 10 bp, on names in the band the desk would widen to (cohort v3's $5M floor).
5. Record the result next to research-data-sources.md, so the $1,188/yr decision rests on the desk's own evidence.

**North-star impact**

This keeps the data budget pointed at what changes card quality and alpha discovery, which is point-in-time reference data, while still closing the live/replay bar gap for free. The IEX question becomes measured: whether IEX quotes refuse or misprice cards for mid-cap names before the universe widens.

**References**

- Alpaca: About Market Data API (plan table) — Basic is free (IEX real-time, 15-minute SIP restriction, 200 calls/min); Algo Trader Plus is $99/mo (all exchanges, 10,000 calls/min) (https://docs.alpaca.markets/docs/about-market-data-api)
- Alpaca Market Data FAQ — Historical SIP is available without a subscription when the end is at least 15 minutes old (https://docs.alpaca.markets/us/docs/market-data-faq)

---

## No single ordered card-stream plan exists; checked against the journal, the maps' conflicting figures and orders reduce to one sequence, and turning card_policy to preview today adds no information

_area: product · kind: gap · impact: high · effort: M · confidence: high_

**Claim**

NOW (checked against the journal, read-only psql: scan_candidates_ranked candidates (trigger, outcome, signal_id) joined to signals, alpaca:paper). From 2026-09-24 to 2026-10-21 the desk sent 37 cards: 4 pead_long and 33 native. Of the 33 native cards:
- 19 came from scheduled suggestion scans. This includes late 10:35 runs at 10:48 on 10-06 and 11:53 on 10-01.
- 2 came from journaled swing scans: AUGO at 10-15 12:10 and SPY at 10-19 11:46.
- 12 were never journaled. They came from swing, operator /scan or Re-evaluate before PR A.1 (#111, c891f13), which went live between 09:43 and 11:04 on 10-09.
So 14 of 33 native cards (42%) did not come from the slots. Since PR A.1 the figure is 2 of 9.

The docs' "18 of 22 unjournaled" (docs/card-evidence.md:378-380, docs/alpha-roadmap.md:533) is wrong. Of the 22 cards sent 09-24 to 10-08, 11 were unjournaled. Seven more were journaled suggestion-scan cards (TALO, EWJ, CVX x2, FCX, AUGO, STX) whose rows predate #106 (c4a3e9b, 2026-10-05), which added `signal_id`. They do not link by ID, but they match by contract and time. live.md's "14 of 37 outside the slots" is a clock-time count over 09-18 to 10-21. It includes 2 cards from before the journal existed and misses 2 non-suggestion cards that fell inside the 10:35-10:55 window (CRM at 09-24 10:52 and 10:53). Counted by trigger, the two maps agree.

In 7 of the 13 journaled 14:35 scans, every candidate was refused with "per-session budget spent". All 7 fell between 09-25 and 10-08, and in each one a card sent outside the slots had already taken one of the two session slots. This has not happened since 10-09. So live §7.3's estimate that 14:35 supply "roughly doubles" rests on pre-A.1 data. The harm that remains: swing scans spent 2 of 9 native slots. One of those cards was SPY, part of three broad-index cards in two sessions (SPY swing 10-19, VTI 10-20 10:39, VOO dynamic 10-20 14:38).

card_policy ('preview now' in history §7.1 and signals §7.1, versus 'rebuild card_stats first' in live §7.1):
- `decide` already runs in off mode (copilot.py:1245-1250).
- `journal_block` already writes measured_ev, n_mature and n_fetch_failed on every journaled candidate (card_policy.py:71-84). The DHT 10-21 row shows measured_ev +0.535 on n 67.
- Preview adds only `would_withhold`, which is a deterministic function of those fields and the threshold (card_policy.py:47-55), plus a log line (copilot.py:1337-1350). It can be recomputed afterwards for any threshold.
- At threshold 0, preview flags nothing today. Meanwhile the TREND_PULLBACK LONG snapshot moved -0.376 (10-08) -> -0.102 (10-13) -> +0.126 (10-14) -> +0.535 (10-21), driven by censoring and repeats (card_stats.py:91-92,112).

architecture §7.1 suggests running swing scans with ScanBudget.NONE so they send nothing. NONE in fact means no limits and still sends cards (config.py:187-190; card-evidence.md Coverage). It is also excluded from the journal (copilot.py:1591), which is the opposite of what is intended.

**Evidence**

- agentic_trader/agent/copilot.py:1245-1250 (card_policy decide computed in every mode)
- agentic_trader/execution/card_policy.py:47-55,71-84
- agentic_trader/agent/copilot.py:1337-1350 (preview only logs)
- agentic_trader/agent/copilot.py:1591 (journal requires budget != NONE and native_count)
- agentic_trader/agent/copilot.py:1754-1762 (risk rejections only logged)
- agentic_trader/config.py:187-190 (ScanBudget.NONE = no budget)
- agentic_trader/cli/commands/service.py:581-593 (swing scan FULL budget, fires at daemon start)
- agentic_trader/research/setups/card_stats.py:88-117 (immature dropped, raw mean_r_cost, no dedup)
- agentic_trader/research/setups/outcomes.py:187-221 (market_r is a beta-one control in the name's R)
- config/config.yaml:453-455 (card_policy off, min_measured_ev null)
- docs/card-evidence.md:378-380; docs/alpha-roadmap.md:533 ('18 of 22')
- git log -S: signal_id in journal since c4a3e9b (#106, 2026-10-05); all-trigger journaling since c891f13 (#111, 2026-10-09)
- psql read-only: domain_events kind=scan_candidates_ranked x signals (alpaca:paper, >=2026-09-18); card_stats_snapshot keys 2026-10-08..10-21
- agentic_trader/data/market_data.py:119-138; agentic_trader/screeners/coverage.py:12-14
- ~/agentic-trader-research/setup-cache-v1/{XOM,KO,SEIC,ABBV}_1h: SIP hourly bars 04:00-19:00 NY, 4.27/4.24/3.07/3.79 UTC 4h buckets per session
- ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz (A0_live 4h, EDT n 4,770 vs EST n 2,809)
- scratchpad maps: live.md §6.3/§7.1/§7.3, history.md §7.1, signals.md §6.4/§7.1/§7.3, architecture.md §7.1, data.md G1/I1

**Recommendation**

ONE ORDERED PLAN (expected effect on card quality first; dependencies in brackets).

1. [S, no dependency] Add a JOURNAL_ONLY scan budget for swing scans. They then journal with their trigger and send nothing. Remove the immediate swing scan at daemon start. Effect: the cards sent become the population that is measured, and no slot goes to a restart-timed card.
2. [S, none] Stop index-beta clones. Exclude broad-index ETFs from native cards, or refuse a second card whose return correlation with an earlier card that session is above 0.8, using the per-scan Ledoit-Wolf covariance the book sizer already computes.
3. [S, none] Journal zero-candidate scans and per-rule rejection counts (copilot.py:1591, 1754-1762). This makes candidate supply measurable.
4. [M, after 1 and operator decision D3] card_stats v2:
   - Use time-matured cohorts: only decisions at least max_hold_sessions (20) old. This removes the censoring swing exactly, with no survival model.
   - Keep one row per (contract, strategy, timeframe, direction, NY session), from suggestion triggers only.
   - Use a beta-adjusted excess R with an explicit per-name cost.
   - Only then set card_policy to preview, with the threshold on that unit. Preview before this costs nothing and tells nothing.
5. [M, must precede ANY new base-rate replay] Native-v2 bar semantics: delayed SIP, session-anchored 4h bars, indicators on regular-hours bars only (data I1, signals §7.3). Ship it as a new identity with a fresh frozen replay. The ratchet-off test, the native-versus-universe control, a second look at vol_20 and any meta-label all wait for it. Otherwise each would be measured on a bar series the live strategy never sees: the replay cache has 3-4.3 UTC 4h buckets per session including extended hours, while live IEX cards show only 12:00/16:00 UTC candles.
6. [M, after 4-5] The L3 selector (separate finding).
7. [M-L, after decision D6] The forward re-score job (separate finding).

DST on 2026-11-01 is NOT a deadline. On the replay, 4h setups in EDT and EST are indistinguishable: r_cost +0.040R vs +0.020R (session-clustered SE 0.038/0.046), target rate 34.4% vs 34.2%. Live IEX names without extended-hours prints, however, go from 2 to 3 regular-hours buckets per session, so the trigger's 4h EMA20 and RSI14 shrink from about 10 and 7 sessions to about 6.7 and 4.7. Journal `clock_regime` on each candidate and treat 11-02 onward as its own stratum in cards outcomes and in the vol_20 read.

OPERATOR DECISIONS, in date order:
- D1, by 2026-10-28: renew the PEAD probe or let it retire.
- D2: the role of the swing scan (journal-only or sending).
- D3: the card_policy evidence unit (raw or beta-adjusted excess R; this depends on the desk mandate) and the threshold.
- D4: whether index ETFs are excluded from native cards.
- D5: approval to spend a replay window on native-v2.
- D6: the confirmation-window rule, decided before approving the insider campaigns.
- D7: whether the LLM gate stays decisive for native cards or becomes commentary only.

**North-star impact**

The two cards a session become the population that is measured and labelled, so the base rate printed on each card is honest. Slots stop going to restart-timed or index-clone cards: in 3 of the last 9 native cards the bet was broad-index beta, with excess return near zero by construction. card_policy then withholds on an unbiased, de-duplicated estimate instead of one that swung from -0.38R to +0.54R in eight days.

**References**

_none_

---

## The three proposed L3 ranking keys point in opposite directions; on the replay, volatility predicts net R mainly through two 1/stop% terms (fixed-bp cost and the beta-one SPY control), not through hit rate

_area: signals · kind: improvement · impact: medium · effort: M · confidence: medium_

**Claim**

NOW: the live ranking key is -setup_quality (copilot.py:1174-1177). Ranking actually chose between candidates in 13 of the 21 journaled scans that sent a card; 12 of those had a runner-up refused for per-scan budget (psql). The maps propose three different replacements:
- a meta-label P(target before stop) (architecture §7.3);
- a vol_20 + sector_rel_mom_60 composite (signals §7.2, history §7.1, alpha_pipeline I1);
- a -dvol tie-break (portfolio §7.6).

Descriptive check on the bracket-counterfactual primary frame. This is the A0_live 2R arm on 4h setups: 7,579 setups over the development window 2021-06-01 to 2026-01-29, SIP all-adjusted, a window already read. Splitting by 4h ATR% tercile (low/mid/high):
- Target rates are flat: 33.5% / 35.0% / 34.5%.
- Cost in R falls 0.122 -> 0.055 -> 0.032. Cost is 2 x 5bp / stop%.
- The beta-one control market_r falls 0.169 -> 0.146 -> 0.088. It is the SPY return x entry / risk (outcomes.py:187-221).
- Winsorized gross R is 0.041 / 0.119 / 0.142, coming from fewer stops (63.4% -> 58.0%) and more timeouts, not more targets.
- Winsorized excess R is -0.285 / -0.079 / +0.019.
- Spearman correlation of ATR% with gross r is +0.028, but with r_cost it is +0.093.
About 60% of the excess-R spread across terciles comes from the two 1/stop% terms. So vol_20's development rho of +0.049 against R_cost (docs/setup-outcomes-2026-09-23.md H2d) is mostly a cost-efficiency effect, and its size depends on the assumed 5 bp per side.

The -dvol key points the other way. In the replay the $7,500 notional cap (config.yaml:343) binds on 83% of 4h setups at the $250 default risk (config.yaml:337). On a near-empty book a card's dollar volatility is therefore about $7,500 x sigma_i, and -dvol picks the lowest-volatility candidate. That is the worst tercile (net -0.092R).

A meta-label on the shadow features repeats the scorers that already failed out of sample. In the one-shot holdout the frozen Ridge earned -0.293R against -0.126R for setup_quality, a paired CI of [-0.325, -0.016] (setup-outcomes 'Selection (H3)'). Its P(target) target is also blind to the volatility effect, because target rate does not vary with volatility.

The 10-21 -> 11-04 prospective read cannot settle the question. About 3 candidates per journaled scan gives roughly 45 in two weeks, so SE(rho) is about 0.15 against an effect of 0.05.

**Evidence**

- agentic_trader/agent/copilot.py:1174-1177 (sort by -setup_quality)
- agentic_trader/agent/copilot.py:1902 ('ranking_key': 'setup_quality' in journal)
- agentic_trader/research/setups/outcomes.py:187-221 (market_r beta-one in the candidate's R units), :382-383 (excess_r)
- agentic_trader/agent/evaluator.py:208-222 (stop = max(swing, 1.5 x 4h ATR); target = ratio x risk)
- config/config.yaml:337,343 (default_equity_risk_dollars 250; max_trade_notional_cap 7500)
- docs/setup-outcomes-2026-09-23.md 'Hypotheses' (H2d vol_20 rho +0.049 vs R_cost) and 'Selection (H3)' (Ridge holdout failure)
- ~/agentic-trader-research/bracket-counterfactual-v1-20261018/frames/primary.csv.gz and protocol.json (feed alpaca:sip, adjustment all, window 2021-06-01..2026-01-29)
- psql read-only: scan_candidates_ranked outcome counts (31 events, 21 with a sent card, 13 with >=2 candidates)
- scratchpad maps: architecture.md §7.3, signals.md §7.2/§7.10, history.md §6.5/§7.1, portfolio.md §7.6, alpha_pipeline.md I1, data.md I6, pooled.md §7.11

**Recommendation**

Settle L3 by mechanism, not by the underpowered prospective read.
1. Build the L3 `CardSelector` seam with a single key: expected net R per unit of risk. That is a shrunk gross prior per (strategy, timeframe, direction) minus an explicit per-name cost in R, 2 x c_i / stop%_i. Estimate c_i from the daily bars already held with Abdi-Ranaldo (or Corwin-Schultz) instead of a flat 5 bp. This captures the cost part of vol_20 deterministically, with no new test, and journals the key as `ranking_key`.
2. Use dvol only as a correlation-aware tie-break, expressed per unit of R and applied only when the book holds positions. Never use it as the primary key on an empty book.
3. Test the remaining gross effect (about +0.10R across the volatility terciles, from fewer stops), sector_rel_mom_60 and any meta-label as one predeclared comparison:
   - Run it on the native-v2 bar series.
   - Use a window no study has read (post-2026-08, or a declared second use of 2017-21).
   - Compare against setup_quality and random.
4. Make card_stats excess beta-adjusted before any policy or selector reads it. Otherwise both systematically penalise tight-stop names.

**North-star impact**

The one or two cards a session would go to the candidate with the best measured net expectancy at a realistic cost, rather than a key known to be uninformative. It also avoids a plausible regression: a -dvol tie-break would pick the replay's worst tercile. Measured edge comes only from the gross residual, so the work is also honest about how little alpha remains.

**References**

- Novy-Marx, R. & Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. Review of Financial Studies 29(1):104-147. — Cost-aware selection and how assumed costs change which signals look profitable. (https://www.nber.org/papers/w20721)
- Frazzini, A., Israel, R. & Moskowitz, T. (2018). Trading Costs. SSRN 3229719. — Live institutional costs about an order of magnitude below earlier estimates; the flat 5 bp assumption drives most of vol_20's measured edge. (https://papers.ssrn.com/abstract=3229719)
- Abdi, F. & Ranaldo, A. (2017). A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and Low Prices. Review of Financial Studies 30(12):4437-4480. — Per-name spread estimate from bars already held, for the cost term c_i in the selector key. (https://ideas.repec.org/a/oup/rfinst/v30y2017i12p4437-4480..html)

---

## The four 'prospective scorer' proposals are one item, better built as a scheduled post-freeze batch re-score than a daemon worker; it gathers evidence over years, and its window rule must be settled before the insider campaigns and the PEAD renewal are decided

_area: alpha_mining · kind: improvement · impact: medium · effort: M · confidence: medium_

**Claim**

NOW: four maps propose overlapping work.
- architecture §7.8, history §7.2 and alpha_pipeline I5 each propose a daemon worker that scores every frozen fixed set each session.
- data I4 proposes a nightly research warehouse.
The pooled lane already defines what is needed. Its single confirmation window (2024-01-02 to 2026-07-31) is consumed lane-wide by the first campaign that reaches it. After that the lane "needs prospective data after 2026-07-31 (a 12-month window around August 2027)" (docs/alpha-pooled-mining.md:619-622).

All inputs of the 80 frozen formula-cohort pairs (64 already run, 16 insider) can be fetched after the fact, each with its own visibility stamp: SIP bars, FINRA daily files, EDGAR filing dates, corporate actions and the Nasdaq calendar pages. Formulas are frozen by SHA-256. Out-of-sample status therefore comes from the freeze plus the journaled inspected intervals, not from scoring live. A daemon worker would add load to scans that already take 180-250 s under the scan lock. Research and trading I/O already share executor capacity, an open P2 item (docs/architecture-review.md:96; CLAUDE.md:159).

Power:
- A fast formula's per-session paired SD is about 0.17R. shorts_retreat earned +0.020R at t +3.40 over 836 sessions, so SE is 0.0059 (docs/alpha-pooled-short-v1-2026-10-20.md:94).
- The PEAD calibration gives an SD of 0.74R (docs/alpha-pooled-mining-research-2026-09-28.md:85-94).
- Testing about 80 pairs at a familywise one-sided 5% (z about 3.2) with 80% power needs:
  - about 190 sessions for 0.05R at SD 0.17;
  - about 1,190 sessions (4.7 years) for 0.02R, the largest discovery edge seen so far;
  - about 900 sessions (3.6 years) for 0.10R at SD 0.74.
history §7.2's claim of 'about 0.10R in two years' holds only for one a priori formula.

Dated couplings:
- Insider campaigns. Check A is running (replicate 18/100 in ~/agentic-trader-research/insider-v1-checks-20261021.log), and the campaigns need approval. Any insider finalist would spend the lane-wide window first, before the composite that history §7.3 and pooled §7.5 want it reserved for, and before pooled §7.6's prospective interval.
- PEAD probe. The probe term is 30 days (config.py:831) and expires 2026-10-28. After that `probe_block_reason` returns 'probe term expired' (storage/probe_state.py:57-58) and drift cards stop. Renewal is explicit, `alpha apriori-probe ... --renew --days N` with N up to 180 (research/alpha/probe.py:20; cli/commands/alpha.py:540-560). Realized results need at least 10 closed trades (docs/apriori-alphas.md 'Part 2', Measurement). That is unreachable at 1 fill per 4 cards; the counterfactual label over every eligible event (research/apriori/probe_outcomes.py:96-129) does not depend on taps.

**Evidence**

- docs/alpha-pooled-mining.md:616-622 (lane-wide single-use confirmation; prospective data after 2026-07-31)
- docs/alpha-pooled-short-v1-2026-10-20.md:94,101 (shorts_retreat +0.020R, t +3.40, n 836)
- docs/alpha-pooled-mining-research-2026-09-28.md:85-94 (0.74R per-session paired SD; sessions-for-power table)
- agentic_trader/research/pooled/ledger.py:95; agentic_trader/storage/alpha.py:1141 (consume_pooled_confirmation)
- agentic_trader/storage/probe_state.py:45-64 (probe_block_reason, term expiry)
- agentic_trader/config.py:829-831 (probe_risk_dollars 100, probe_term_days 30)
- agentic_trader/research/alpha/probe.py:20 (MAX_PROBE_TERM_DAYS 180)
- agentic_trader/cli/commands/alpha.py:540-560 (apriori-probe --renew)
- agentic_trader/research/apriori/probe_outcomes.py:96-129 (counterfactual label over every eligible event)
- docs/apriori-alphas.md 'Part 2: live paper probe' (Measurement: not comparable until 10 closed trades)
- docs/architecture-review.md:96 (research shares executor capacity with trading I/O); CLAUDE.md:159
- ~/agentic-trader-research/insider-v1-checks-20261021.log (check A at replicate 18/100 on 2026-10-21)
- scratchpad maps: architecture.md §7.8, history.md §6.4/§7.2/§7.3/§7.5, alpha_pipeline.md I5, data.md I4, pooled.md §7.5/§7.6, live.md §6.7/§7.7

**Recommendation**

Write ONE spec, `alpha pooled-forward`, to replace the four proposals. It is a launchd research job outside the daemon, run monthly or quarterly.
- It extends the cohort v2/v3 cubes incrementally past 2026-07-31. That is data I4 scoped to these cohorts plus the FINRA and EDGAR inputs, fetched with multi-symbol requests outside scan windows.
- It re-scores all 80 frozen pairs and their every-eligible controls, and journals picks and label maturity.
- It reads under a rule fixed before the first run. Either one read at about 2027-08 with Holm across the family, or per-formula always-valid confidence sequences (Howard et al. 2021) with an alpha-spending schedule across interim looks (Lan & DeMets 1983).
- The daemon captures only receipt-time evidence for the one input whose historical fetch may differ from what was visible that day (the Nasdaq calendar).
- State plainly that this accumulates evidence over years and is not a card source within months.

Operator decisions, in order:
(a) By 2026-10-28: renew the PEAD probe for 180 days at unchanged risk and judge it on the counterfactual label. Raising risk to parity with native cards or allowing both slots is a separate spec.
(b) Before approving the insider campaigns: pick the confirmation-window rule. The options are first finalist spends it (status quo), reserve it for one predeclared literature-signed composite, or confirm prospectively from 2026-08-01 through this job.
(c) Approve the forward job's read rule before its first run.

**North-star impact**

This turns the operator's 'reasonable cadence' for mining into new, untouched evidence at zero added trial cost, without loading the live scan path. It stops the remaining historical confirmation window from being spent by default on the insider lane, where 64 pairs have produced no finalist. It also keeps the only positive card source, PEAD, alive past 10-28 with evidence that does not depend on operator taps.

**References**

- Howard, S.R., Ramdas, A., McAuliffe, J. & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. Annals of Statistics 49(2):1055-1080. — Always-valid intervals for a forward re-score read repeatedly without inflating the error rate. (https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-2/Time-uniform-nonparametric-nonasymptotic-confidence-sequences/10.1214/20-AOS1991.full)
- Lan, K.K.G. & DeMets, D.L. (1983). Discrete sequential boundaries for clinical trials. Biometrika 70(3):659-663. — Alpha-spending function to budget Type I error across interim reads of the forward window and across lanes competing for one confirmation resource. (https://rdocumentation.org/packages/gsDesign/versions/3.0-1/topics/sfLDOF)
