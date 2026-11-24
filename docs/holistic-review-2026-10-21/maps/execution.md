# Map: execution, broker adapters, durable workflows and accounting

Code at `db82817` (worktree `agentic-trader-intraday`). Paths are relative to the repo root. "Now" means what the code does today; "Recommend" marks proposals.

## 1. Purpose and scope

This layer turns an operator-approved card into exactly one broker order. It keeps the order's lifecycle (fills, stop ratchets, timed cancellations, closes) consistent with the broker, and it derives account-level P&L and drawdown that gate new risk. Its design goal is safety under ambiguity: no replayed mutation, no symbol-based ownership, and no inferred fill. It succeeds at that goal. It is weaker at being a measurement instrument for alpha research and at keeping the desk live when ownership becomes ambiguous.

## 2. Components and contracts

**Entry application service.** `EntryExecutionService` (`agentic_trader/execution/entries.py:35`):
- `authorize` enqueues the request and drains the queue inline (`entries.py:50-57`).
- `dispatch_one` claims the FIFO head, refreshes the account ledger, runs `_preflight`, commits `SUBMITTING` and then POSTs once (`entries.py:112-164`).
- `recover` is lookup-only by client ID (`entries.py:166-175`).
- In preflight, the price-drift check (±1% of the approved limit) runs first (`entries.py:85-93`). The macro and regime check follows (`entries.py:99`), then `assess_entry_capacity` (`entries.py:101-107`).
- Adapters without an admission contract fail closed (`entries.py:94-96`).
- An acknowledgement with no order ID becomes `submission_uncertain` (`entries.py:159-162`).

**Durable vocabulary.** `agentic_trader/execution/durable.py`:
- `WorkKind` covers ENTRY, ENTRY_CANCEL and NOTIFICATION (`:11-14`), and `WorkStatus` has 8 states (`:17-25`).
- `ENTRY_BLOCKING` (`:28`) and `EventKind` (`:32-65`) define the blocking statuses and the journal event kinds.
- `OrderObservation` (`:114-140`) holds cumulative broker state as wire strings and is never treated as an individual execution.

**Repository.** `WorkflowStore` (`agentic_trader/storage/workflow.py:111`):
- **Locking:** one lock row per scope (`:116-131`), keyed `environment/execution_mode`.
- **Idempotent journal append:** writes are keyed by event key (`:142-159`).
- **`enqueue_entry` (`:334-446`):** deduplicates per signal (`:338-346`) and refuses entries during a halt, an unresolved cancellation or an active close (`:347-363`). It applies the alpha registry gate (`:369`, `:478-525`) and requires fresh ledger risk (`:372`, `:320-332`). It then reserves risk through `reservation_rejection` (`:392`). It mints the client ID `entry-{uuid}` (`:412-413`), flips the signal to `SUBMITTING` and journals `entry_queued` (`:429-441`).
- **`claim_entry` (`:448-476`):** gives a fenced lease on the FIFO head. A `SUBMITTING` or `UNKNOWN` head blocks the whole queue (`:462-463`).
- **`begin_submission` (`:527-616`):** under the locks it rechecks the token and lease, that the signal is unchanged, the alpha gate, the risk fingerprint and capacity (`:543-602`). It journals the full admission evidence, including the broker quote (`:606-614`).
- **`resolve_entry` (`:618-677`):** marks the signal `EXECUTED` at broker acceptance, not at fill (`:645-649`). An `UNKNOWN` result persists a global halt (`:650-651`).
- **`observe_orders` and `_project_order` (`:810-881`):** keep the order projection monotonic. A zero-fill terminal entry is moved to `FAILED`, which releases its reservation (`:827-845`).

**Admission policy.**
- `authorization_expiry` checks queue age (120 s) and signal age (4 h) (`agentic_trader/execution/admission.py:16-27`).
- `reservation_rejection` delegates every rule to `risk.admission_gates` (`admission.py:30-91`).
- `assess_entry_capacity` (`agentic_trader/execution/capacity.py:220-312`):
  - **Freshness:** evidence and quote ages are checked (`:231-234`).
  - **Contract envelope:** only whole-share equity limit brackets with DAY/GTC are allowed (`:237-244`).
  - **Shorts:** require easy-to-borrow evidence (`:270-279`).
  - **Funding:** uses the lower of `buying_power` and `regt_buying_power` (`:266`, `:280-289`).
- `_exposure` (`capacity.py:79-217`) reconstructs the book from exact order lineage. It refuses all new entries when it finds:
  - a partial entry (`:103-104`) or any exit fill (`:111-112`);
  - a pending bracket that no longer matches its approved levels (`:126-139`);
  - a filled position without exactly one live stop (`:162-193`), or with a mark already through its stop (`:194-196`);
  - untracked holdings (`:213-214`) or unrelated working orders (`:215-216`).

**Closing.** `PositionCloseService` (`agentic_trader/execution/closing.py:20`):
- It persists an exclusive close claim (`:104-118`), submits through the adapter, maps results (`:43-75`) and recovers by client ID (`:77-93`).
- `close_signal` requires exactly one tracked match (`:143-160`). For an accepted but unfilled entry it answers "No open broker position" (`:155-156`).
- `submit_position_close` (`agentic_trader/broker/alpaca.py:898-1093`) works in this order:
  1. Verify the position (`:921-933`), and for a tracked entry the fill quantity and cost basis (`:969-994`).
  2. Expand bracket groups by exact identity, including held legs (`:825-896`).
  3. Cancel, then poll until each order is terminal (`:1010-1037`).
  4. Recheck that no orders remain, that the quantity is available and that the session is open (`:1039-1051`).
  5. Submit one **market DAY** order (`:1053-1065`).

**Lifetimes.**
- `TradeLifetimePolicy` (elapsed UTC) and `SessionLifetimePolicy` (session-counted, exits at 15:45 New York) are in `agentic_trader/execution/lifetime_policy.py:33-62`, `:95-141`. `DAILY_ENTRY_LIFETIME_SECONDS = 57_600` (`:24`).
- `assess_lifetime` (`agentic_trader/execution/lifetimes.py:86-134`) returns NONE, CANCEL_ENTRY, CLOSE_POSITION or REVIEW. A partial fill always returns REVIEW (`:121-122`).
- `TradeLifetimeService.reconcile` acts only on signals whose `alpha_policy` carries a `lifetime` (`lifetimes.py:209-213`).
- A cancellation persists its intent before one DELETE (`:293-302`). Holding expiry reuses the close service with a deterministic `hold-` ID (`:247-249`, `:303-333`).
- `resolve_cancel` and `review` set the **global** halt when evidence is incomplete (`agentic_trader/storage/lifetimes.py:237-240`, `:260`).

**Broker seam.** `BaseBroker` (`agentic_trader/broker/base.py:247-407`) declares the admission, recovery, lifecycle and close contracts as optional methods that raise `NotImplementedError` (`:303-344`).
- `OrderRequest` carries futures-era defaults: `asset_class=FUTURES`, `time_in_force=GTC`, `order_class=BRACKET` (`base.py:92-98`).
- Typed, frozen `extra=forbid` evidence models exist for the account, asset, quote and `BrokerEntryContext` (`base.py:31-80`, `:213-233`).
- **Alpaca** is the only adapter that implements every contract:
  - entry evidence is read twice to bracket the read interval (`alpaca.py:562-571`), and quotes and trades come from `config.alpaca_data_feed` (`:576-583`, default `"iex"` at `agentic_trader/config.py:925`);
  - `find_entry_order` (`alpaca.py:605-642`), `observe_orders` (7-day history plus open orders plus replacement chains, `:644-709`) and `read_entry_group` (`:711-732`);
  - `modify_order_stop` confirms the replacement by read-back (`:1434-1507`);
  - stream events become evidence plus a REST wake-up (`:1321-1366`).
- `PaperBroker`, `TradovateBroker` and `RedundantBroker` lack the admission contract.

**Accounting.**
- `reconcile` (`agentic_trader/accounting/ledger.py:92-169`) recognises only FILL, CSD/CSW/JNLC, FEE/CFEE/DIVFEE and DIV/INT (`:20-23`). Any other activity type is an issue, and an issue sets `gross_realized=None` (`:137-138`, `:156`).
- `advance_risk_checkpoint` and `require_risk_checkpoint` (`agentic_trader/accounting/risk.py:148-244`) derive cash-flow-adjusted drawdown. Both refuse when the report is not `ready` (`:92-93`).
- `AccountLedgerService.refresh` imports the **full** activity history on every refresh (`agentic_trader/accounting/service.py:54-86`; `alpaca.py:309-336`, capped at `max_pages`).
- `LedgerStore.commit` journals revisions and retractions under the ledger lock (`agentic_trader/storage/ledger.py:58-105`).

**Seams.**
- *Consumes:* the card (`signals` row: entry, stop and target from `agent/evaluator.py:203-250`), the operator tap (`agent/copilot.py:2912-2988`), risk rules (`agentic_trader/risk`), the macro check and broker evidence.
- *Produces:* `domain_events` streams (`entry/*`, `order/*`, `entry-cancel/*`, `activity/*`, `account/ledger`), the signal status and fill sync, outbox notifications and readiness.
- *Read by:* `research/setups/execution_evidence.py` (cards outcomes) and `/perf`.

## 3. Data flow

The flow covers a tap, admission and submission; then fills and stop ratchets; then exits and timed lifetimes; then the account ledger and drawdown gate; and finally the measured feedback.

1. **Tap and freshness.** The operator taps a card, and freshness classifies it as EXECUTE, REPRICE, MISSED or EXPIRED (`execution/freshness.py`). Only EXECUTE builds an `OrderRequest` with the card's original entry, stop and target. It sets **no time-in-force**, so the GTC default applies (`copilot.py:2967-2978`).
2. **Enqueue and preflight.** `enqueue_entry` reserves risk and queues the request, then `dispatch_one` runs:
   1. a full ledger import (`entries.py:120-124`);
   2. broker evidence (`alpaca.py:518-603`);
   3. drift, macro and capacity checks;
   4. under locks, `begin_submission` rechecks everything and journals `entry_submitting` with the quote, account and book (`workflow.py:606-614`);
   5. one POST of a limit bracket at the approved price (`alpaca.py:179-269`).
3. **Fills.** The signal is `EXECUTED` at acceptance. Monitoring (`copilot.py:2453-2488`) recovers unknown entries and runs lifetimes, refreshes the order journal, recovers closes and then calls `sync_entry_executions`. That call **overwrites** `entry_price` and `risk_dollars` with the fill (`copilot.py:2366-2390`).
4. **Stop ratchets.** `manage_trailing_stops` ratchets the stops (`copilot.py:2528-2629`).
5. **Exits.** Exits are matched only through exact legs or recorded manual exit IDs (`alpaca.py:1212-1298`). Their P&L comes from prices and ignores fees (`alpaca.py:1279`).
6. **Timed lifetimes.** Only alpha and probe signals carrying a `lifetime` get a timed cancel or close (`lifetimes.py:209-213`).
7. **Account ledger and drawdown.** A separate 60-second worker imports account activities and derives drawdown. Scans also require that risk (`copilot.py:1726-1740`).
8. **Feedback.** `collect_execution` reads `order_observed` fills and tap assessments and reports `fill_slip_r` and tap age (`research/setups/execution_evidence.py:68-80`, `:207-280`). Everything else in cards outcomes comes from **bar-replayed labels** (`research/setups/outcomes.py:529-627`), not fills.

## 4. Evidence: what was tried

- **GTC entries suppress conversion.** "GTC pending orders suppress about 90% of later opportunities" (docs/alpha-roadmap.md:46-49; docs/alpha-power-diagnosis-2026-09-18.md, "Execution conversion is a concrete bottleneck").
- **One-session resting entries restore conversion.** Bounding the resting entry to one session (P1) "restores trade conversion". A one-session holding expiry (P2) is "strongly harmful" (docs/alpha-lifetime-attribution-2026-09-21.md, "Result and decision"). This led to semantics v5 and `DAILY_ENTRY_LIFETIME_SECONDS` (docs/alpha-trade-lifetimes.md, "Native-daily one-session entries").
- **GTC fills are adverse.** "GTC limit entries were measured to fill mainly when the forecast is wrong" (docs/alpha-roadmap.md:150-152). Built-in native strategies "still submit GTC limit entries at the observed price" (docs/alpha-trade-lifetimes.md, "Known limits").
- **Bracket counterfactual (2026-10-18).** It found no exit arm that beats the live 2R bracket. The **live ratchet arm is reliably worse**, by −0.013R with a 90% CI of [−0.020, −0.006], and every arm trails SPY by 0.10–0.15R (docs/bracket-counterfactual-2026-10-18.md, Rules table and "What this says" §2). A test of "ratchet off" was explicitly left as an operator decision.
- **Stage review.** "fill sync overwrites the planned entry", and slippage and exposure were not decomposed (docs/alpha-roadmap.md:478-483). L1 added `fill_slip_r` and tap age only.
- **Known gaps on record.** Corporate actions leave reconciliation unsupported (docs/architecture-review.md:90). Live trailing uses the recorded risk distance and is not at parity with research (docs/architecture-review.md:92). The probe risk cap is not an admission invariant (docs/alpha-roadmap.md:160-163).

## 5. Strengths worth preserving

- **Single-submission discipline.** Only a valid fenced lease commits `SUBMITTING`. POST, PATCH and DELETE are never retried. Recovery is lookup-only, even after a 404 (`entries.py:166-175`; `alpaca.py:605-642`; docs/durable-execution.md, "Entry state machine").
- **Two-phase admission.** The remote preflight runs outside any transaction, and a locked final transaction revalidates the token, the unchanged signal, the risk fingerprint, capacity and deadlines (`workflow.py:527-616`). This is the right pattern; keep it.
- **Exact identity everywhere.** Exposure, closes and stop ratchets are resolved only through order lineage, never by symbol (`capacity.py:69-76`; `alpaca.py:825-896`, `:1403-1432`).
- **Evidence-first journal.** `entry_submitting` stores the arrival quote, account and book. Projections rebuild without broker calls (`workflow.py:892-905`; `storage/ledger.py:132-180`). This already makes an implementation-shortfall analysis possible (see §7).
- **Broker-authoritative Decimal accounting** that refuses to invent FIFO lots (`accounting/ledger.py:1-6`).
- **Shared lifetime functions for research and live**, versioned into the alpha identity (`lifetime_policy.py`; docs/alpha-trade-lifetimes.md).

## 6. Gaps, weaknesses, risks, inconsistencies

**G1. Native cards run the execution policy research found adverse, with no expiry.**
- Native entries are GTC limits at the scan-time price (`evaluator.py:203-207`; `copilot.py:2967-2978`; `base.py:98`). Lifetimes skip any signal without `alpha_policy.lifetime` (`lifetimes.py:210-213`).
- There is no operator cancel for a resting native entry. `/close` reports "No open broker position" (`closing.py:155-156`), and the only bulk cancel is `/panic` (`copilot.py:4085`).
- An accepted, unfilled entry stays `EXECUTED` and counts in `reservations()` (`workflow.py:262-269`) and in capacity (`capacity.py:113-140`).
- The book is small: 8 slots, $2,000 of aggregate stop risk and $250 per trade (`config/config.yaml:3-8`). So a zombie entry consumes about 1/8 of the desk's capacity.
- A Little's-law check makes the risk concrete. At about 1.5 fills per day and the replayed mean hold of 5.6 sessions (docs/bracket-counterfactual-2026-10-18.md, Descriptives), steady state is already about 8 positions. Capacity, not ideas, can cap card throughput.

**G2. Research labels do not describe the live execution policy.**
- Every native measurement labels an entry at the **next regular hourly open** (a market-like fill that always fills), with a 20-session timeout and 5 bp per side (`research/setups/labels.py:180-187`, `:252-261`; `outcomes.py:64-65`). The pooled cube uses the same labeller from 10:35 (`research/pooled/cube.py:5-7`).
- Live differs on every axis. It uses a limit at the decision price, so it fills conditionally. It has no holding timeout. It runs a ratchet from one-minute prices rather than hourly closes.
- `card_stats` and `card_policy`, the one outcome-derived gate, are therefore computed on a policy the desk does not trade (`research/setups/card_stats.py:1-5`).

**G3. Execution evidence feeds back into research only thinly.**
- `summarize_execution` reports counts, mean `fill_slip_r` and tap age (`execution_evidence.py:264-280`).
- It does **not** measure:
  - fill rate or time to fill;
  - the conditional-fill bias: label R of filled versus unfilled sent cards, though both are available;
  - shortfall against the arrival mid (the quote is journaled at `workflow.py:606-614` but never read);
  - exit slippage: stop fill versus stop price, and gap-through;
  - realized live R versus label R per closed card;
  - per-signal fees (`alpaca.py:1279` excludes them).
- Admission refusals are free-text `ValueError` strings (`capacity.py:63-301`), so refusal causes cannot be aggregated.
- Research cost assumptions (5 bp per side, 15 bp decisive cost on cohort v3) are not calibrated from any of this.

**G4. One unmodelled event stops the whole desk.**
- **Unsupported account activity.** Any activity type outside the eight supported ones, anywhere in the account's history, makes `report.ready` false (`accounting/ledger.py:137-138`, `:156`). Risk then raises (`accounting/risk.py:92-93`), and every scan candidate is dropped as "account risk unavailable" (`copilot.py:1726-1740`) and every entry is refused (`workflow.py:372-382`).
  - This is not an emergency halt, so the `/resume` described in production docs as the way to lift a halt does nothing for it. Recovery requires code that supports the activity type, because the full-history import re-reads the same activity on every refresh.
  - Other Alpaca activity types (capital-gain and return-of-capital distributions on ETFs, splits, mergers, name changes) are plausible triggers in a universe with 32 ETFs. Whether they occur in a paper account is unverified (§8).
  - docs/account-ledger.md says unsupported risk evidence "blocks new entries". It does not say that scans stop too, nor that no recovery path exists short of code.
- **Partial fills, untracked positions and ambiguous orders.** A partial entry, an exit partial, an untracked position or an unrelated order refuses **all** symbols (`capacity.py:103-112`, `:213-216`). A partial fill on a timed entry, or an uncertain cancel, sets the **global** halt (`lifetimes.py:121-122`; `storage/lifetimes.py:237-240`, `:260`). There is no automated or symbol-scoped recovery.

**G5. The live trailing ratchet is measured as harmful and mislabelled.**
- `trailing_stop.enabled: true` (`config/config.yaml:419-425`) despite the −0.013R result.
- The code trails at `current − 1.5 × initial R`, not ATR (`copilot.py:2569-2572`). `trailing_stop.mode: chandelier_atr` is never read (`config.py:671`).
- Each ratchet also lengthens the replacement chains that admission and close must follow (`alpaca.py:474-482`, `:1403-1422`).

**G6. The tradeable envelope is narrower than the research lanes assume.**
- Admission accepts only:
  - whole-share equity **limit** brackets, DAY or GTC (`capacity.py:237-244`);
  - regular-session entries (`alpaca.py:542-543`);
  - one position per symbol;
  - full closes only, as a market DAY order (`alpaca.py:1053-1061`).
- It has none of the following:
  - marketable-limit, opening-auction (OPG) or closing-auction (CLS) entries;
  - two-leg or pairs orders (the spread lane notes that no probe or two-leg execution contract exists);
  - scaling in or out, or partial take-profit;
  - target-portfolio rebalancing orders.
- A pooled-lane finalist (next-hour-open entry, 2-ATR stop and 3R target, 20 sessions) cannot be executed as it was labelled. The 15:45 market close for session lifetimes also differs from the label's close price (`labels.py:260`).
- The quote and trade freshness limit is 60 s on the configured feed (`capacity.py:233-234`), and the code default is IEX. That limit will refuse more mid-cap names (cohort v3's band) if the desk widens its universe.

**G7. Legacy components contradict the contracts.**
- `RedundantBroker` fails a POST over to another broker when the error text contains "timeout" (`broker/redundant.py:121-161`). That is the exact case where the contract demands UNKNOWN plus a halt. It is disabled by default (`config.py:502-507`), but it is still constructible.
- `SlicedExecutionEngine` infers simulated fills from the limit price (`execution/engine.py:128`). It also computes collars that the immediate path never applies (`execution/slicer.py:100-127`; `engine.py:46-53`).
- `PaperBroker` fills at the live price and ignores the limit (`broker/paper.py:62-64`). So `EXECUTION_MODE=paper` evidence has a different fill model from Alpaca's.
- Drift units differ across layers: admission uses 1% of price (`entries.py:89`), while the tap gate uses 0.25R (`config/config.yaml:364-365`). This also refuses favourable moves.

**G8. Scaling and latency fuses.**
- Every entry preflight runs a full activity import (`entries.py:120-124`). Imports fail closed above 100 pages × 100 activities (`alpaca.py:316-336`); docs/account-ledger.md, "Import, concurrency and recovery", acknowledges this.
- Every monitor cycle re-journals 7 days of order history with one existence query per observation. This happens under the single scope lock, which entry admission and card taps share (`alpaca.py:656-682`; `workflow.py:810-823`).

## 7. Improvement opportunities (Recommend)

1. **Give native cards a versioned execution policy.** Attach `daily_entry_lifetime()` (`lifetime_policy.py:65-71`) or a `SessionLifetimePolicy` to native signals, so `TradeLifetimeService` cancels unfilled entries after one session. Make `time_in_force` explicit in card construction, not a default.
   - *Effect:* fewer adverse fills and released capacity, so more cards can be executed. Research measured a large conversion gain (§4).
   - Add an operator "cancel resting entry" command that reuses the existing `entry_cancel` work item. The machinery exists; only the trigger is missing.
2. **Measure policy parity before tuning.** Use the existing label infrastructure:
   - (a) In `cards outcomes`, add the mean label R of filled versus unfilled sent cards. This directly measures the adverse selection in G1/G2.
   - (b) Choose the entry type deliberately. A marketable limit (ask plus k·spread, capped by drift) matches the labels. A passive limit needs its own labelled fill model.
   - (c) Give native positions the label's 20-session timeout through `SessionLifetimePolicy`, or drop the timeout from the labeller. Either way `card_policy` then gates the traded policy. This is roadmap L5 ("one bracket-label kernel"), and it should precede further gate work.
3. **Implementation-shortfall decomposition from the journal.** Decompose shortfall in the style of Perold (1988):
   - **Delay:** decision price to tap price (`CardAssessment.price`, `freshness.py:128`).
   - **Admission latency:** tap to arrival mid (`entry_submitting.broker_context.quote`).
   - **Execution:** arrival to fill (`order_observed`).
   - **Exit slippage:** stop or target price versus exit fill.
   - **Opportunity:** label R of unfilled cards.

   Report each in R and feed the measured distributions into research cost parameters instead of fixed 5/15 bp. Treat paper fills as optimistic: Alpaca paper simulates fills against quotes without queue position. For limit-order adverse selection and time to fill, Handa and Schwartz (1996, *J. Finance*), Harris and Hasbrouck (1996, *JFQA*) and Lo, MacKinlay and Zhang (2002, *JFE*) give the standard framing.
4. **Turn the ratchet off for native cards**, as the predeclared construction change the bracket study names, or rebuild it on ATR with high-water mark and hourly-close parity. Either way, delete the unused `mode` field.
   - *Effect:* about +0.013R per affected setup, fewer broker mutations and simpler lineage.
5. **Scope blast radius.**
   - (a) Ledger: classify the full Alpaca dividend family and pass-through fees, and quarantine the *symbol* for structural actions. Decouple the drawdown gate, which needs equity and external flows, from realized-P&L readiness, which needs every activity classified. Treat an unknown cash-bearing activity conservatively as an external inflow, which overstates drawdown, rather than blocking.
   - (b) For a partial fill, reserve the worst-case risk and block only that symbol until it is reviewed.
   - *Effect:* the north-star card stream survives routine corporate actions and partial fills.
6. **Typed refusal codes** for capacity and admission, like the risk module's `Rejection`, plus a refusal histogram in cards outcomes. This shows whether quote staleness (IEX), drift or capacity is what drops tapped cards.
7. **Widen the envelope only where research needs it, through the same FIFO and lineage contracts.**
   - Marketable-limit entries.
   - A CLS (market-on-close) time-in-force for session-count exits, which matches the labels' close.
   - Partial closes with protection resize; this is a prerequisite for allocator-driven "reduce" decisions and the advisory exit cards (docs/alpha-roadmap.md item 7).
   - Defer pairs until a lane passes.
8. **Delete or quarantine `RedundantBroker`, the slicer engine and the paper fill model,** or make `PaperBroker` use the same limit-fill model as the labeller. A parity simulator in the spirit of NautilusTrader's single engine for backtest and live is a design to borrow, not a framework to adopt.
9. **Incremental ledger import** (cursor plus a periodic full audit) and batched order-observation deduplication, before history growth lengthens tap latency or holds the scope lock longer.

## 8. Open questions

- Which `ALPACA_DATA_FEED` is deployed? The code default is IEX, and the environment was not read. What fraction of tapped native cards were refused, and why?
- How many accepted native entries never filled, and how long did they rest? This needs `db queue`/`db orders`, which were not run.
- Does the Alpaca paper account emit capital-gain or return-of-capital dividend, split or merger activities, and has any unsupported type appeared yet?
- How often does Alpaca paper simulate partial fills? Each one halts or blocks the desk.
- Is the probe's quantity override at tap bounded anywhere by the probe risk cap (docs/alpha-roadmap.md:160-163)?
