# Entry-risk policy

**Status:** implemented 2026-10-06, roadmap step
[L2](alpha-roadmap.md#stage-layering-and-attribution-october-5), PR pending.
**Code:** `agentic_trader/risk/`; callers import from `agentic_trader.risk` only.
**Design:** [spec and amendments](superpowers/specs/2026-10-06-risk-policy-module-design.md).

This page owns the rule table. Other documents link here and do not repeat it.

## Purpose

Every rule that can refuse new entry risk has exactly one implementation. It is a pure
function in `agentic_trader/risk/rules.py` that takes typed inputs and returns a
`Rejection` or `None`. All of these call the same functions:

- the scan (the evaluator and position sizing);
- the tap gate, re-pricing, re-evaluate, `/scan`, card validity and the expired-card reply;
- entry admission (the reservation, the preflight, final submission and broker capacity).

Layers differ only in the **book** they pass and in the observed **budget inputs**
(equity, drawdown, regime). No layer re-derives a rule's arithmetic, so the scan and
admission cannot disagree about the same card on the same book.

Rules never read `AppConfig` and never perform I/O. The callers own the economic
calendar, session provider, regime detector, earnings calendar and database, and pass
in what they observed.

## Types

| Type | Module | What it is |
| --- | --- | --- |
| `RiskLimits` | `limits.py` | A frozen snapshot of every limit the rules read, built by `RiskLimits.from_config(config)`. It holds cash, the sizing policy, the per-trade risk/notional/quantity caps, the aggregate stop-risk percentage, the portfolio and asset-class notional caps, the concurrent and correlated position limits, the normalised correlation groups, the minimum reward/risk, the lockout minutes, the earnings blackout days and `enforce_rth`. A class without a configured cap (FX, or a cap set to `None`) has no entry in `asset_class_caps` and is uncapped. |
| `BookPosition`, `Book` | `book.py` | What the desk already holds. `Book.from_signal_rows(rows, reservations=…)` reads `signals` rows (`contract` or `symbol`, `direction`, `asset_class`, `notional_value`, `risk_dollars`, `status`). With `reservations=True`, a `SUBMITTING` row is a reservation. A missing, non-numeric, non-finite or negative `notional_value` or `risk_dollars` makes the position invalid (`Book.invalid`); it is never coerced to zero. |
| `EntryIntent` | `rules.py` | What is about to be risked: symbol, direction, asset class, quantity, entry, stop, target, multiplier, and an optional current price and strategy. The constructor is the validation boundary. Direction must be LONG or SHORT (any case). Quantity, prices, multiplier and a known current price must be finite and positive, otherwise `ValueError`. An inverted bracket is a valid intent; `reward_risk` refuses it. Derived values: the normalised `key`, `risk_distance`, `reward_distance`, `risk_dollars`, `exposure_price` (`max(entry, current_price)`, or the entry when no current price is known) and `notional`. |
| `RiskBudget` | `capital.py` | The per-trade budget: `capital`, `drawdown_pct`, `drawdown_factor`, `macro_factor` and `dollars`. |
| `Rejection`, `RiskRule` | `rules.py` | A refusal: the stable rule id (`RiskRule`, a `StrEnum`) plus the operator-facing reason. `str(rejection)` is the reason. |

`normalize_symbol` drops surrounding whitespace and a leading `/` and upper-cases the
rest, so `/MES`, `mes` and `MES` are one instrument for every rule.

## The per-trade budget

```text
dollars = min(cash, equity) × max_risk_pct_cap × drawdown_factor × clamp(macro_multiplier, 0.10, 1.0)
```

`per_trade_risk_budget(limits, *, equity, drawdown_pct, macro_multiplier=1.0)` is the
only place this product is computed.

- `cash` is `portfolio.cash`. It is a ceiling: observed equity can only lower the
  capital (`risk_capital`). `equity=None` means the configured cash.
- `drawdown_factor` (`drawdown_risk_factor`) is 1 up to
  `sizing.drawdown_haircut_threshold_pct`. It falls linearly to
  `sizing.drawdown_min_risk_multiplier` and is 0 at `sizing.max_drawdown_stop_pct`. It
  is always 1 when `sizing.drawdown_gating_enabled` is false.
- The macro factor (`macro_risk_factor`) is the regime's `risk_multiplier` clamped to
  `[0.10, 1.0]`, so it only ever scales risk down.
- A non-finite multiplier, equity or drawdown raises `ValueError`. It is never read as 1.0.

Each layer passes what it observed:

| Layer | `equity` | `drawdown_pct` | `macro_multiplier` |
| --- | --- | --- | --- |
| Scan: sizing of every tier, and the evaluator's book gates | ledger equity (Alpaca); `None` in the simulator | ledger drawdown (Alpaca); 0.0 in the simulator | the scan's regime |
| Tap-time regime gate and re-pricing | `None` (configured cash) | 0.0 | the cached regime |
| Preflight regime gate, on both broker branches | `None` | 0.0 | the regime refreshed for this dispatch |
| Admission (`admission_gates`) at the reservation, preflight and final submission | observed equity (Alpaca: the reconciled account risk; capacity uses the lowest of it and both fresh account observations); `None` in the simulator | observed drawdown; 0.0 in the simulator | 1.0 (the preflight regime gate applies the macro factor) |

Sizing caps the max tier at `budget.dollars`. A paper probe's max tier is also capped at
`alpha_pipeline.probe_risk_dollars`; the lower cap wins. The base tier scales its own
target budget by the same drawdown and macro factors and is clamped to the max tier; the
half tier is derived from the base tier. The scan's capital and factors are never above
the tap gate's or admission's, so with unchanged equity, drawdown and regime a tier
offered on a card is never refused later for size.

The aggregate stop-risk budget is `capital × portfolio.max_stop_risk_pct ×
drawdown_factor`, with the same capital and drawdown factor and no macro factor.

## Rule table

"Scan" is `RiskEvaluator.evaluate_candidate` and sizing. "Tap/reprice" is the tap-time
gates and the re-priced replacement. "Admission" is `reservation_rejection` →
`admission_gates` at the reservation, preflight and final submission, plus the
preflight's injected macro check. A dash means the layer does not run the rule. Test
paths are under `tests/`.

| Rule id | Inputs | Scan | Tap/reprice | Admission | Reason text | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| `exposure_unknown` | `Book` | `book_gates`, first | — | `admission_gates`, first | `Existing exposure is unknown or invalid; reconcile it before new risk.` | `risk/test_rules.py`, `risk/test_adversarial.py`, `agent/test_evaluator_risk_policy.py` |
| `drawdown_halt` | `RiskBudget.drawdown_factor` | `book_gates` (sizing already blocks a zero factor) | — | yes | `Account drawdown {pct:.1%} reaches the configured sizing halt; new entries blocked.` | `risk/test_rules.py`, `execution/test_drawdown_policy.py` |
| `per_trade_risk` | `EntryIntent.risk_dollars`, `RiskBudget.dollars` | sizing caps every tier at the budget; the post-LLM final-bracket check | regime gate; re-pricing caps the replacement quantity at the same budget | yes, and in the preflight regime gate | `Order exceeds the configured per-trade risk cap.` When the drawdown or macro factor is below 1: `Order exceeds the drawdown- and macro-adjusted per-trade risk cap; request a fresh scan for smaller sizing.` | `risk/test_rules.py`, `risk/test_capital.py`, `agent/test_tap_risk_policy.py`, `execution/test_position_sizing.py` |
| `per_trade_notional` | notional at `max(entry, current_price)`, `max_trade_notional_cap` | sizing caps the quantity | re-pricing caps the replacement quantity | yes; capacity passes `max(limit, ask, last trade)` as the current price | `Order exceeds the configured per-trade notional cap.` | `risk/test_rules.py`, `execution/test_entry_capacity.py`, `workflows/test_entries.py` |
| `quantity_cap` | quantity, asset class, `max_shares_per_trade` / `max_contracts_per_trade` | sizing caps the quantity | re-pricing caps the replacement quantity | yes | `Order exceeds the configured quantity cap.` | `risk/test_rules.py`, `workflows/test_entries.py` |
| `reward_risk` | bracket, `required_reward_risk(limits, regime.min_rr_threshold)` | the deterministic target is built at the required ratio, the LLM's target is clamped to it, and the final-bracket check runs | regime gate at the cached regime's ratio; re-pricing judges the current price at the same ratio | `admission_gates` at the configured minimum; the preflight regime gate at the refreshed regime's ratio | `Reward/risk {rr:.2f} is below the required {required:.2f}.` | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `agent/test_tap_risk_policy.py`, `agent/test_risk_policy_integration.py`, `execution/test_entry_capacity.py` |
| `aggregate_stop_risk` | book planned risk plus the intent's, `RiskBudget`, `max_stop_risk_pct` | `book_gates` | — | yes | `Order would breach the aggregate planned stop-risk budget.` | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `execution/test_entry_capacity.py` |
| `concurrent_positions` | book count, `max_concurrent_positions` | `book_gates` | — | yes | `Maximum concurrent positions ({max}) reached.` | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `agent/test_risk_policy_integration.py` |
| `same_symbol` | normalised symbol, book | — | — | yes | `Symbol already has a position or entry reservation; adding/netting requires a separate reviewed plan.` | `risk/test_rules.py` |
| `portfolio_notional` | book notional plus the intent's, `max_notional_exposure` | `book_gates` | — | yes | `Portfolio notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling.` | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `workflows/test_entries.py` |
| `asset_class_notional` | the class's book notional plus the intent's, the class cap | `book_gates` | — | yes | `{ASSET_CLASS} notional would reach ${total:,.0f}, above the ${cap:,.0f} ceiling.` | `risk/test_rules.py`, `execution/test_risk_budgeting.py`, `execution/test_entry_capacity.py` |
| `correlation_group` | normalised groups, same-direction book positions, `max_correlated_positions` | `book_gates` | — | yes | `Correlation group '{group}' already has {n} {DIRECTION} position(s) ({symbols}); max {max}.` | `risk/test_rules.py`, `risk/test_limits.py`, `risk/test_consistency.py`, `agent/test_evaluator_risk_policy.py`, `agent/test_risk_policy_integration.py` |
| `macro_lockout` | the calendar's tier-1 event (title, timezone-aware time), now, `risk.lockout_pre_event_minutes` / `lockout_post_event_minutes` | evaluator gate | tap gate | preflight macro check, on both broker branches | `Macro event lockout: {title} at {HH:MM} UTC.` An untitled event prints `scheduled release`. | `risk/test_rules.py`, `agent/test_tap_risk_policy.py`, `workflows/test_entries_macro.py`, `agent/test_risk_policy_integration.py` |
| `earnings_blackout` | next earnings date, the New York date, `risk.earnings_blackout_days` (predicate `earnings_days_out`) | evaluator gate, equities | tap gate, equities | — | `Earnings Blackout: {SYMBOL} reports {YYYY-MM-DD} {before open \| after close \| timing unspecified} (in {n} day(s)) — within {days}-day blackout` | `risk/test_rules.py`, `agent/test_earnings.py`, `agent/test_evaluator.py`, `agent/test_card_freshness_tap.py` |
| `session_closed` | the session's `is_open`, the provider's detail | evaluator gate | tap, re-evaluate, `/scan`, card validity, expired-card reply | — | `Market session closed: {detail}.` (`Market session closed.` without detail) | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `agent/test_tap_risk_policy.py` |
| `session_not_rth` | the session's `is_rth`, `session.enforce_rth` | evaluator gate | as `session_closed` | — | `Outside regular trading hours.` | `risk/test_rules.py`, `agent/test_evaluator_risk_policy.py`, `agent/test_tap_risk_policy.py`, `agent/test_risk_policy_integration.py` |
| `regime_breakout` | strategy, `regime.breakout_allowed` | evaluator gate | tap regime gate | preflight regime gate, on both broker branches | `Volatility/macro policy suppresses breakout entries.` | `risk/test_rules.py`, `agent/test_tap_risk_policy.py`, `agent/test_evaluator.py` |

The earnings rule has no `Rejection` function: `earnings_days_out` is the shared
predicate, and `agent/earnings.py:earnings_blackout_reason` formats the text for both
the scan and the tap. A failed earnings lookup fails open at both layers; the card's
earnings note then reads "Unverified — earnings calendar unavailable".

`required_reward_risk(limits, regime_min_rr)` is `max(risk.min_risk_reward_ratio,
regime_min_rr)`, or the configured minimum when the regime threshold is unknown or
non-finite. The `reward_risk` rule and re-pricing's current-price check both compare
with `meets_min_reward_risk`, which rounds the ratio to two decimals like the evaluator's
own approval, so float noise never refuses a card approved at "2.00".

### What the operator sees

Most layers show the rule's text unchanged. Some layers wrap it:

- **Scan.** A send-phase refusal is a runner-up, `rejected: {text}`. A collect-phase
  refusal is logged only (see below).
- **Tap.** A gate refusal turns the card `MISSED` with the rule's text, for example
  "⌛ Reward/risk 2.00 is below the required 2.20.". A session refusal turns it `EXPIRED`
  with "Card expired: its session has ended." plus the next regular open. Re-pricing's
  current-price check reads "Missed: reward:risk at {price} would be {x} < {min}.".
- **Re-evaluate and `/scan`.** A session refusal replies "Market closed; Next regular
  open: … UTC (… NY).".
- **Admission.** The work item's error and the outbox reply carry the rule's text. At
  final submission through broker capacity, the text is wrapped as "Entry capacity
  refused: {text}. No order submitted.".

## Order of evaluation per layer

### Scan

`RiskEvaluator.evaluate_candidate` stops at the first refusal:

1. Sizing (`calculate_dynamic_sizing`). A zero quantity is `Sizing blocked: …` (a sizing
   result, not a rule).
2. The deterministic bracket as an `EntryIntent`. A stop or target at or below zero, or a
   non-positive or non-finite configured multiplier, is refused with admission's text
   `Quantity and bracket prices must be finite and positive.`
3. `book_gates` on the scan's book, in its fixed order: `exposure_unknown`,
   `drawdown_halt`, `aggregate_stop_risk`, `concurrent_positions`,
   `portfolio_notional`, `asset_class_notional`, `correlation_group`. The first
   rejection decides.
4. The statistical return-correlation check, which is not a shared rule (see below).
5. `macro_lockout`. The calendar finds the event on the evaluator's own clock, and the
   rule judges the same instant.
6. The earnings blackout (equities).
7. `regime_breakout`.
8. `entry_session_open` (`session_closed`, `session_not_rth`).
9. The LLM, when enabled, then the final-bracket check.

A refusal by a shared rule sets `LLMTradeEvaluation.rejection_reason` to the rule's text
and `rejection_rule` to its id. Sizing, the bracket check, the statistical check and an
LLM veto carry no rule id, and the evaluator ignores any rule id in the LLM's answer.

`run_scan` evaluates in two phases. The **collect** phase is a deterministic pass
against the book at the start of the scan. The **send** phase re-evaluates the winners,
with the LLM when enabled, against that book plus the cards already sent in this scan.
A collect-phase refusal is logged as `candidate_rejected` with `phase: collect` and the
reason. It does not appear in the runner-up list or the digest. A send-phase refusal is a
runner-up. Separately, a scan that starts inside a lockout window is skipped as a whole
and logged as `macro_lockout_active`.

### Tap and re-pricing

1. Session: `assess_card(session_open=entry_session_open(...) is None)`. A refusal
   expires the card.
2. Price geometry (through the stop or target, too close to the stop). These are
   freshness checks, not risk rules.
3. The gate reason, first refusal wins: `macro_lockout`, then the regime gate
   (`regime_breakout`; `reward_risk` at `required_reward_risk` for the cached regime;
   `per_trade_risk` against the budget on configured cash with the regime multiplier),
   then the earnings blackout for equities. A refusal makes the card `MISSED`.
4. Freshness: `EXECUTE` within the fresh bounds; past them, `REPRICE` when the current
   price still meets the required ratio (`meets_min_reward_risk`), otherwise `MISSED`.
5. The re-priced quantity (`_capped_replacement_quantity`) is the smallest of the tapped
   tier's re-derived quantity, the quantity cap, `max_trade_notional_cap` divided by the
   unit notional, and `budget.dollars` divided by the unit risk. It is in whole units
   except for crypto. A zero quantity is `MISSED`.

A non-finite regime multiplier, request number or configured multiplier raises
`ValueError` in the regime gate. The tap then answers "⚠️ Checks unavailable; try again
shortly.", journals the tap as `unavailable` ("Tap-time checks failed: ValueError.") and
leaves the card `PENDING`.

Re-evaluate and `/scan` refuse while `entry_session_open` refuses. A card records
`valid_until` only while `entry_session_open` allows entries and the provider's close is
timezone-aware and still in the future. Otherwise the card expires at the New York date
change. A tap on an already-expired card adds the next regular open only while
`entry_session_open` refuses entries.

### Admission

`reservation_rejection(request, positions, config, *, current_drawdown_pct,
current_equity, current_price)` runs at three moments:

1. `WorkflowStore.enqueue_entry`, the reservation transaction behind a tap's `EXECUTE`.
   It uses the observed drawdown and equity (Alpaca) and no current price.
2. The preflight (`EntryExecutionService._preflight`):
   1. the Alpaca price-drift check;
   2. the refusal of unsupported broker evidence;
   3. the injected macro check on **both** broker branches: `macro_lockout`, then the
      regime gate with `get_regime(force_refresh=True)`;
   4. on Alpaca, `assess_entry_capacity`, whose broker evidence comes first and which
      then calls `reservation_rejection` with the observed equity, drawdown and exposure
      price; in the simulator, `reservation_rejection` directly.
3. Final submission (`WorkflowStore.begin_submission`, under the trading and ledger
   locks), through capacity or `reservation_rejection` again.

Before the shared rules, `reservation_rejection` refuses these, in order:

- a direction and broker side that disagree: "Order direction and broker side disagree.";
- a futures symbol without a configured multiplier: "Futures instrument multiplier is
  not configured.";
- a configured multiplier that is not finite and positive: "Configured instrument
  multiplier must be finite and positive.";
- invalid request numbers: "Quantity and bracket prices must be finite and positive.",
  or "Current exposure price must be finite and positive." for the current price;
- invalid account inputs: "Account risk inputs invalid: {cause}.".

It then returns `admission_gates`' first rejection, in this fixed order:
`exposure_unknown`, `drawdown_halt`, `reward_risk`, `per_trade_risk`,
`per_trade_notional`, `quantity_cap`, `aggregate_stop_risk`, `concurrent_positions`,
`same_symbol`, `portfolio_notional`, `asset_class_notional`, `correlation_group`.

An exception in the preflight's macro check (for example, VIX unavailable or a NaN
regime multiplier) is reported as "Admission evidence unavailable ({Type}: {message}). No
order submitted."

## Books per layer

| Layer | Book | Built by |
| --- | --- | --- |
| Scan | open positions (`EXECUTED` signals) plus the cards this scan already sent, each with its notional and planned stop risk. `PENDING` cards from earlier scans reserve nothing. | `Book.from_signal_rows(active_positions, reservations=False)` |
| Tap gate, re-pricing | none: only the per-trade rules run. Book rules are admission's job at Execute. | — |
| Admission: the reservation (both modes), and the simulator's preflight and final submission | `EXECUTED` positions plus `SUBMITTING` reservations; the preflight and final submission exclude the entry's own signal | `Book.from_signal_rows(rows, reservations=True)` |
| Admission: Alpaca preflight and final submission, through capacity | the same rows, each matched to its exact broker order and position identity, with the larger recorded/broker-mark exposure | capacity's exposure rows, then `reservation_rejection` |

A position whose `notional_value` or `risk_dollars` is missing, non-numeric, non-finite
or negative makes `exposure_known` refuse at every layer before any numeric rule runs.
`book_gates` then reports that rejection alone.

## Behaviour changes on 2026-10-06

The operator approved decisions 1–7 on 2026-10-06. Items 8–10 are the hardening that
came with them. Everything else that decides an entry is unchanged.

1. **Correlation cap (decision 1, 2026-10-06).** The cap counts only same-direction
   positions at every layer, and an opposite-direction hedge is allowed at the scan and
   at admission alike. Group membership uses `normalize_symbol`: `/MES` ≡ `MES`, in any
   case. An empty `portfolio.correlation_groups` disables the rule everywhere; no layer
   falls back to the default groups. The config loader still fills an omitted key with
   `DEFAULT_CORRELATION_GROUPS` and merges in the universe sectors; only an empty
   loaded mapping disables the rule.
2. **One per-trade budget (decision 2, 2026-10-06).** `min(cash, equity) ×
   max_risk_pct_cap × drawdown_factor × clamp(macro, 0.10, 1)` applies to every sizing
   tier, to admission, to the tap gate and to re-pricing. The max tier shrinks under
   macro stress.
3. **Reward/risk target (decision 3, 2026-10-06).** The deterministic target is built at
   `max(risk.min_risk_reward_ratio, regime.min_rr_threshold)`. Under an elevated or
   extreme regime, cards therefore carry wider targets and pass their own tap gate. The
   target price is computed from the unrounded actual stop distance (`entry − stop` on
   the actual prices) times the ratio, and rounded *away from the entry* to the next
   tick, so `reward_risk` on the actual prices always holds. A target already on a tick
   stays, so on-tick ratio-2.0 targets are unchanged. Research replay
   (`research/setups/replay.py`) and the legacy backtest share this function at the
   configured ratio and may place a target up to one tick farther than before; studies
   completed before 2026-10-06 used the earlier nearest-tick rounding.
4. **Notional and class caps (decision 4, 2026-10-06).** Notional is priced at
   `max(entry, current_price)` when a current price is known. FX and any class without a
   configured cap are explicitly uncapped at every layer.
5. **Scan-time book checks (decision 5, 2026-10-06).** The scan runs
   `concurrent_positions` and `aggregate_stop_risk` against its book before sending a
   card, so a full book or an exhausted stop-risk budget stops the card at scan time. A
   refusal in the collect phase is in the log and in the evaluation's `rejection_rule`,
   not in the Telegram runner-up list (backlog below).
6. **Macro check at admission on both broker branches (decision 6, as implemented,
   2026-10-06).** The preflight's injected macro check (the lockout **and** the regime
   gate) runs on the simulated and the Alpaca branch, after the Alpaca price-drift check
   and the refusal of unsupported broker evidence. Consequences:
   - simulator entries can be refused by the regime rules (breakout suppression, the
     regime's reward/risk, the macro-scaled budget);
   - the regime half fails closed when VIX or macro data is unavailable ("Admission
     evidence unavailable (…)"), while the lockout half fails open when the economic
     calendar is down;
   - the preflight makes provider calls (calendar, regime) inside the 60 s preflight
     lease (`execution.entry_preflight_lease_seconds`);
   - `get_regime(force_refresh=True)` refreshes the shared regime cache on every
     dispatch.
7. **Session (decision 7, 2026-10-06).** One `entry_session_open(is_open, is_rth,
   enforce_rth)` decides at the evaluator, the tap, re-evaluate, `/scan`, card validity
   and the expired-card reply. `session.enforce_rth: false` allows extended hours at
   every entry gate. The production config sets `enforce_rth: true`. `valid_until` is
   recorded only when the provider's close is timezone-aware and still in the future;
   otherwise the card expires at the New York date change. A CME evening session reports
   that day's 17:00 ET halt, which has already passed. Scan scheduling
   (`is_session_active`) is unchanged.
8. **Final-bracket check after the LLM (2026-10-06).** The card as sent must pass
   `reward_risk` at the required ratio and `per_trade_risk` for the card's budget. A
   probe card's final risk must also not exceed `alpha_pipeline.probe_risk_dollars`.
   Otherwise the deterministic stop and target are both restored. An LLM bracket that is
   not a valid `EntryIntent` (NaN, non-positive) is restored the same way, and the LLM's
   verdict stands. `risk_reward_ratio` is reported from the final stop distance. A
   versioned alpha policy's bracket is never changed by the LLM.
9. **Fail-closed inputs (2026-10-06).** A non-positive or non-finite deterministic
   bracket price, quantity or configured multiplier is refused at scan time
   (`EntryIntent` validation) with admission's text "Quantity and bracket prices must be
   finite and positive.". Such a card could never be admitted. A NaN regime multiplier
   raises in sizing and in the tap gate, and is never treated as 1.0. The scan counts it
   as a per-candidate scan error; the tap reports "Checks unavailable" and journals
   "Tap-time checks failed"; the preflight reports "Admission evidence unavailable".
10. **Book validity (2026-10-06).** A position with unknown exposure (`notional_value`
    or `risk_dollars` missing, non-finite or negative) makes `exposure_known` refuse at
    every layer before any numeric rule. The scan's own cards carry `risk_dollars` into
    its book.

Reason texts are unified with these changes: every layer prints the rule's text. For
example, there is one lockout text, `Macro event lockout: {title} at {HH:MM} UTC.`, at
the scan, the tap and the preflight. The tap's per-trade text reuses
`per_trade_risk`'s two texts.

Unchanged by design: the earnings predicate (decision 8: the scan and the tap share
`earnings_days_out`, and admission has no calendar), and the simulator's budget inputs
(decision 9: `equity=None`, `drawdown_pct=0.0`).

## Adversarial and consistency guarantees

- **`tests/risk/test_adversarial.py`** (numpy seed 20261006):
  - The constructors reject NaN, ±inf, negative and zero numbers and an unknown
    direction with `ValueError`. A non-finite macro multiplier raises.
  - 2,000 generated cases with valid intents and hostile books and limits: NaN, ±inf,
    negative or missing exposure; mixed-case, slashed and empty symbols; unknown classes
    and directions; empty and overlapping groups; drawdown past the stop; macro
    multipliers 0 and 2. No rule raises, every rejection carries a non-empty reason, and
    any invalid position makes both `book_gates` and `admission_gates` report
    `exposure_unknown` first.
  - 500 cases: the book-gate result is independent of position order and monotone, so
    adding a position never turns a rejection into a pass.
- **`tests/risk/test_consistency.py`:**
  - On 1,500 seeded books, `book_gates`' first shared-rule rejection equals
    `admission_gates`' whenever admission's first rejection is a shared rule. A case
    where an admission-only rule refuses first (reward/risk, per-trade caps, same
    symbol) is skipped.
  - `reservation_rejection` on an equivalent `OrderRequest` returns exactly
    `admission_gates`' reason, and passes once the held position is opposite-direction.
- **`tests/risk/test_rules.py`, `test_capital.py`, `test_book.py`, `test_limits.py`:** one
  table per rule covering pass, the boundary (equal to the cap), fail, an uncapped class,
  empty groups, normalised symbols, the reservation flag and unknown exposure, plus the
  fixed composite order.
- **`tests/agent/test_risk_policy_integration.py`:** temp database, real `run_scan`, tap,
  enqueue and dispatch paths; only I/O is stubbed. The scenarios:
  - a card built under an elevated regime passes its own tap gate and is accepted;
  - a full book stops the card at scan time (`concurrent_positions`);
  - a book filled by this scan's first card stops the next;
  - empty correlation groups admit a second same-group position at both layers;
  - a slashed group member matches the held root symbol at both layers;
  - simulated admission rejects during a macro lockout;
  - with `enforce_rth` false a tap outside regular hours executes, and with it true the
    card expires.

## What is not a risk rule

These decide or bound entries, but they are not entry-risk rules and stay where they are:

- **Card budgets:** `max_cards_per_scan`, `max_cards_per_session` and
  `max_cards_per_group_per_session`. A `PENDING` card reserves no capacity. The per-group
  cap uses `TradingCopilot.correlation_groups_of`, which also matches root and slashed
  spellings, plus the `dynamic` group.
- **The PEAD open-position cap:** `apriori.max_open_drift_positions`.
- **The statistical return-correlation check:** `portfolio.enable_dynamic_correlation`,
  evaluator only, needs the data fetcher. Its text is "Statistical correlation limit
  exceeded: …", with no rule id.
- **The duplicate-signal rule and live-card checks:** `risk.deduplication_hours`, "A live
  card for SYMBOL already exists".
- **Broker capacity evidence** (`execution/capacity.py`, [entry capacity](entry-capacity.md)):
  funding and buying power, borrow status, the broker clock ("Equity entry session is
  closed; …"), evidence age, price drift, and exact protection and inventory. These refuse
  on broker evidence and call admission for the shared rules.
- **Workflow guards:** the emergency halt, authorization and signal age, and paper-probe
  liveness (`probe_block_reason`).
- **Scan scheduling:** `is_session_active` decides when scans run, not whether a card may
  enter.

## Not done here

- Surface collect-phase book refusals in the scan summary and the end-of-session digest.
  They are only logged today, because changing the runner-up counts was not approved.
- Some test fixtures still carry their own per-desk regime stubs instead of the shared
  `calm_regime`/`calm_macro` fixtures: `tests/agent/conftest.py`,
  `test_account_risk.py`, `test_timeframe_dedup.py`, `test_async_responsiveness.py`, and
  `tap_desk` in `test_card_freshness_tap.py`.
- `missing_fill_evidence` in `cards outcomes` keeps its L1 semantics.
- The per-group card cap's membership function (`correlation_groups_of`) is not shared
  with `RiskLimits.correlation_groups`. Its matching is equivalent; unifying it belongs to
  the card-selector seam (L3).

## Deployment evidence

This is separate from the tests above. Record:

- the controlled restart, `/readyz` and `scripts/verify_runtime.py` on the merged
  revision;
- when the regime is elevated, the first scheduled scan's cards carrying targets at the
  regime-adjusted ratio.
