# Card freshness: revalidate on tap — design

**Approved:** September 23, operator ("record this work to our roadmap and take on it next").
**North star:** one or two reasonable Telegram suggestions per session that the operator can
still act on sensibly when they read them late.

## Problem

The first live card (signal #16, XOM LONG, 10:38 NY) was read more than an hour later.
Today a tap is judged only by entry admission:

- **Under 4h and within 1% price drift:** submit the *original* bracket limit.
- **Otherwise:** refuse with "request a fresh scan".

That leaves five gaps:

1. **The refusal is a dead end.** `is_duplicate_recent` counts every recorded signal
   regardless of status, for 12h on 4h setups, so no fresh card can be issued.
2. **The card shows no validity window,** and its buttons stay live after expiry.
3. **The setup's gates are not rechecked at tap time.** The macro check runs, but the
   earnings blackout, session and bracket geometry at the current price do not.
4. **Drift is measured in percent, not in R.** It is symmetric even though a move toward
   the target erodes reward:risk while a move toward the stop improves it.
5. **There is no session boundary.** A 14:35 card keeps the same entry across the
   overnight gap until the 4h cap.

## Decision

A card is an offer valid until **the close of the regular session it was issued in**.
A tap re-assesses the card with the current price, session and gates, and returns one of
four outcomes. The approved order is never silently changed. Any change of price or size
becomes a *new* card, which needs a fresh tap.

| Outcome | When | Behaviour |
| --- | --- | --- |
| `EXECUTE` | Same session; age ≤ `fresh_seconds` (1800); `abs(r_consumed)` ≤ `fresh_max_r` (0.25) | Unchanged path: `EntryExecutionService.authorize` with the original bracket. Admission still checks drift, macro, capacity and deadlines. |
| `REPRICE` | Same session, open, and past the fresh bounds. The price is strictly between stop and target. Remaining risk ≥ `reprice_min_risk_fraction` (0.5) of the original. Reward:risk at the current price ≥ `risk.min_risk_reward_ratio`. Gates pass (halt, RTH session, macro lockout, regime, earnings blackout). | Atomically expire the old signal and record a replacement PENDING signal: entry = current price, same stop and target, quantity re-sized to the original signal's risk dollars (capped by the original notional and existing sizing caps). The replacement card is delivered through the outbox with an "updated from #N" header. The tap reply says a re-priced card was sent. |
| `MISSED` | Same session, but the geometry fails: at or through the stop or target, too close to the stop, or reward:risk below the minimum. | Expire the old signal. Reply with the reason (current price, R consumed) and offer **[🔄 Re-evaluate]**. |
| `EXPIRED` | The issuing session has ended (now ≥ `valid_until`), or the market is not in RTH | Expire the old signal. Reply with the reason and the next regular open, and offer **[🔄 Re-evaluate]**. |

A failing gate (earnings, macro, halt) in the same session is reported as `MISSED`,
with that gate's reason.

`r_consumed = sign × (price − entry) / |entry − stop|`, where `price` is the latest trade
from the data fetcher (off the event loop). If the price can't be fetched, the tap is
refused with "price unavailable, try again", and the card stays PENDING (fail closed,
retryable).

**[🔄 Re-evaluate]** (callback `reval_<signal_id>`) runs a single-symbol scan:

- `run_scan(symbols=[contract], budget=ScanBudget.NONE)`, with the duplicate rule
  exempted for that contract.
- It is refused with the next open when the market is not in RTH.
- It either issues a fresh card through the normal path or replies "no valid setup now".
- It never reuses the old signal's levels.

**Card rendering.** Every new card records `valid_until` (the session's `next_close`) in
`decision_provenance` and shows "⏳ Valid until HH:MM NY". Replacement cards show
"🔄 Updated card (re-priced from #N, first issued HH:MM NY)".

**Evidence.** Every tap appends one `card_tap_assessed` domain event: signal id, outcome,
tap latency in seconds, price, r_consumed and reason. The replacement signal's provenance
records `reprices`, `tap_latency_seconds` and `r_consumed`. This measures how late the
operator acts and whether late cards still work.

**Budget.** A replacement card replaces the original rather than adding one: session
budget counting excludes signals whose provenance has `reprices`. A re-evaluation card
uses the NONE budget; it is an explicit operator request. `EXPIRED` signals still count
toward the session budget, so the budget remains "cards sent today".

## Constraints preserved (CLAUDE.md, durable execution)

- The original bracket and client ID stay immutable; re-pricing creates a new signal.
- A trade handler is never replayed. Every Telegram callback is single-use (the keyboard
  is cleared) and safe under at-least-once delivery.
  - `expire` is a conditional PENDING→EXPIRED update.
  - The replacement signal and the old signal's expiry commit in **one transaction**,
    together with the outbox notification.
  - A second tap on the old card finds it EXPIRED and is refused.
- FIFO and fenced admission are unchanged. `EXECUTE` goes through `authorize`; admission
  still enforces `signal_max_age_seconds`, drift, macro, capacity, session and
  deadlines. The freshness decision never authorizes on its own.
- The outbox carries every card and every asynchronous message.
- `EXPIRED` becomes a live status. Every status-gated query is audited: enqueue and
  dismiss accept PENDING only; the duplicate rule ignores status (intended, apart from the
  re-evaluate exemption); `/perf` and positions ignore non-executed signals.
- No migration. `SignalStatus.EXPIRED` already exists, and `domain_events.kind` is a
  string.

## Config

`execution.card_freshness`:

- `enabled: true`. When false, taps behave exactly as today.
- `fresh_seconds: 1800`
- `fresh_max_r: 0.25`
- `reprice_min_risk_fraction: 0.5`

`execution.signal_max_age_seconds` is unchanged; it bounds authorization of an
`EXECUTE`.

## Out of scope (follow-ups)

- Proactively striking buttons at session close. This needs an outbox "edit message"
  kind; tap-time handling already covers correctness.
- Delayed-entry sensitivity in the setup-outcome study: label entries 1–3h after the
  decision to quantify decay against tap latency.
- A `/scan SYM` Telegram command. Re-evaluate covers the carded contract.

## Testing

- Pure assessment table:
  - boundaries of `fresh_seconds`, `fresh_max_r`, `reprice_min_risk_fraction` and
    minimum reward:risk;
  - through stop or target;
  - long and short;
  - session ended;
  - missing `valid_until` on legacy cards (NY date of the signal ≠ today's NY date, or
    the market is not in RTH, gives `EXPIRED`).
- Re-sizing: same risk dollars, notional cap, integer shares for equities.
- Storage:
  - conditional expire;
  - atomic replace, with the outbox row in the same transaction and rollback leaving the
    old signal PENDING;
  - budget excludes replacements.
- Copilot tap path:
  - `EXECUTE` calls `authorize` with the original bracket;
  - `REPRICE` records the replacement and does not authorize;
  - `MISSED` and `EXPIRED` expire the old signal and offer re-evaluate;
  - a price failure keeps the signal PENDING;
  - `enabled: false` gives legacy behaviour;
  - a double tap is refused.
- Telegram:
  - `reval_` parsing;
  - the re-evaluate button is rendered only for `MISSED`/`EXPIRED`;
  - the "valid until" and "updated card" lines render.
- Re-evaluate:
  - duplicate exemption only for that contract;
  - NONE budget;
  - refused outside RTH.
