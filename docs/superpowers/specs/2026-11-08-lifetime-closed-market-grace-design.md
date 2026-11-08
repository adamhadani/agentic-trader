# Lifetime evidence: no halt for a closed-market outage on a filled position

Operator request, 2026-11-08 ("yes", to the fix proposed after the 2026-11-07 halt).

## Why

The lifetime service reads each timed position's broker order group once per reconciliation pass. A failed read is retried, and after `execution.lifetime_evidence_review_after_failures` (5) consecutive failed passes it opens a `lifecycle_evidence_unavailable:{Type}` review. That review persists the trading halt until the operator sends `/resume` ([#136](../../alpha-trade-lifetimes.md)).

**Incident, Saturday 2026-11-07.**
- Alpaca reads hit `ReadTimeout` on and off from 07:33 to 08:38 ET. Telegram stayed healthy, so the host network was up.
- The first burst lasted about 8 minutes, more than the 5-pass grace. At 07:41 ET the review opened on signal #47 (BLK, the PEAD catalog probe: filled, with a 20-session holding exit) and halted the desk.
- Unless the operator resumes, Monday's scans are skipped. This is the second halt from the same probe; the first was the 2026-10-26 wake-up `ConnectionError`.

**The gap.** A *filled* timed position has exactly one lifetime action left: the holding close. That close is submitted only while the broker says the regular session is open. While the regular session is closed:
- nothing at the broker can fill;
- a filled entry cannot become unfilled;
- the bracket's stop keeps protecting the position;
- and the lifetime service could not act even with perfect evidence.

So a failed read in that window carries no risk that a halt would mitigate.

**Resting entries are different.** A native entry's cancel deadline is the submission session's close + 5 minutes, which falls while the market is closed. Its missed cancel matters before the next open. Resting entries keep today's rule.

## The rule

On a failed evidence read (`EvidenceReadFailed`) for a signal, the pass does **not** count toward the review threshold when both of these hold:

1. **The regular session is certainly closed now**, by the service's injected predicate. Its default is a new pure function, `regular_session_certainly_closed(now)`, on the static exchange calendar (`MarketHolidayCalendar`), with no broker or network call. It returns true when the New York date:
   - is a Saturday or Sunday;
   - is a statutory equity holiday;
   - or falls outside [09:30, 16:00) New York time.

   Early closes are deliberately ignored: 13:00–16:00 on an early-close day reads as open, which is the conservative side. A naive datetime returns false.
2. **The journal shows the entry fully filled.** The latest journaled projection of the signal's `broker_order_id` (`LifetimeRepository.order`, a local database read) has status `filled` and `filled_quantity == quantity > 0`.

**When the pass is not counted:**
- The signal's consecutive-failure count keeps its prior value. It neither increments nor resets.
- The failed type name is still returned by `reconcile()`, so `reconciliation` readiness still degrades and the monitor still sees it (quiet hours apply as usual).
- A warning is logged with type names only.

**When the market reopens** and reads still fail, counting resumes from the prior value. The review fires as it does today.

**Any failure inside the grace check counts the pass**, as today. That covers the predicate raising, a missing projection, or the projection read raising. Every doubt falls back to the existing behaviour.

## Unchanged

- **Deterministic findings still halt on the first pass:** `REVIEW` decisions, missing durable identity, partial exits, post-read failures and policy-parse failures.
- `resume_blockers` is untouched.
- The threshold, its config and the review reason string are unchanged.
- No new config, no migration, no Telegram change.
- The strict broker calendar stays the only source of deadlines. The static calendar only decides whether to *defer counting*. It never closes, cancels or computes a deadline.

## Non-goals

- Changing how a resting entry's failed reads are counted.
- Auto-clearing an existing halt. The operator still `/resume`s; the monitor and Claude never change the halt.
- Making the count durable across restarts. It stays in memory, as in #136.

## Tests (what proves it)

- **The predicate, at fixed instants:**
  - weekend → closed;
  - Thanksgiving 2026-11-26 → closed;
  - 09:29:59 / 16:00 on a Monday → closed;
  - 09:30 / 15:59:59 → open;
  - Black Friday 14:00 → open (early close ignored);
  - Columbus Day 2026-10-12 noon → open;
  - a UTC-aware input converts;
  - naive → false.
- **A filled position whose reads fail while closed:** 10 passes, no review, no halt. Once open, 5 more failed passes review.
- **The count carries across the closed window:** 3 failed passes open, 10 closed, then the review on the 2nd open failure.
- **A resting entry, market closed:** still reviews at 5.
- **No journaled fill:** a filled-at-broker entry whose reads have failed from the start (no journaled projection) still reviews at 5 while closed.
- **A failed broker clock read** at a passed holding deadline, while closed, is not counted, and no close is submitted.
- **A raising projection read** counts the pass.
- **Default wiring:** the service built without the argument uses `regular_session_certainly_closed`.
- **Existing tests are pinned to an open market** (`market_closed=lambda now: False` in the fixture), so the suite never depends on the weekday it runs.
