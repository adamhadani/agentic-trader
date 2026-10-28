# Lifetime evidence reads: retry before a review halt

Status: design, 2026-10-28. Incident-driven fix; operator design trust ("go with your recommended
approaches").

## 1. Incident

- Mon 2026-10-26 21:45 ET the host woke from sleep with the network not yet up. The 60 s position
  sweep (`_monitor_positions` → `TradeLifetimeService.reconcile`) read the broker order group of the
  one timed position, signal #47 (BLK, a PEAD catalog probe, `session_count_v1`, 20-session holding
  exit due about 2026-11-13). The GET and its one connection-reset retry both raised
  `ConnectionError`.
- `reconcile()` sends **any** per-signal exception straight to `repository.review(...,
  "lifecycle_evidence_unavailable:ConnectionError")`, which persists the trading halt.
- The next sweep read the evidence fine, but the halt is sticky until `/resume`. Every scan since
  was skipped (Tue 10:35 and 14:35 suggestion scans; Wed's would follow). That is two sessions of
  zero cards from one failed read.

## 2. Why the halt is wrong here, and when it is right

- A failed read is absence of evidence, not inconsistent evidence. It is retried by the next sweep
  60 s later. While the broker is unreachable, admission cannot submit new risk anyway.
- A review halt is right when evidence was read and found wrong: a `REVIEW` decision, a missing
  durable identity, a partial exit, or a failed holding close. It is also right when the read keeps
  failing, for example a code or data defect specific to one signal, because then a timed position
  can overstay its exit unseen.

## 3. Decision

- `TradeLifetimeService.reconcile()` counts **consecutive failed sweeps per signal** in memory
  (`self._evidence_failures: dict[int, int]`). Only an exception raised while **reading the
  evidence** adds one. The read phase is the durable entry identity, the broker order-group read,
  `observe_orders`, the parent lookup and the session calendar read.
  - An exception **after** the evidence is read reviews on the first sweep, as today, with the same
    `lifecycle_evidence_unavailable:{TypeName}` reason. This covers the decision and the action:
    `begin_cancel`, including its "Bracket evidence changed before cancellation claim" integrity
    refusal; the cancel submission; the holding close; and journal/outbox writes. Such an exception
    means the evidence was read and found changed, or an action failed partway. That is the
    review-worthy case in §2. A partial fill seen during a deadline cancel must keep blocking new
    risk at once.
  - `entry_lifetime_of` failing to parse a signal's own policy is deterministic, not a read. It also
    reviews on the first sweep, as today.
  - `_missed_native_bound` already swallows its own errors.
  - Below the threshold, it logs a warning with the type name only and does not review.
  - When the count reaches `execution.lifetime_evidence_review_after_failures`, it reviews with the
    same reason string as today (`lifecycle_evidence_unavailable:{TypeName}`). The halt and the
    journal event are unchanged.
- **Counted in sweeps, not seconds.** A host asleep between two failures must not turn two failed
  reads into a halt. The default is **5** (about 5 minutes at the 60 s sweep), with `ge=1, le=60`.
  `1` restores today's behaviour.
- A sweep where the signal reconciles without an exception resets its count. Ids that leave the
  active set are dropped. A restart starts from zero, so a persistent failure halts within N sweeps
  of a new run.
- **Visibility.** `reconcile()` always finishes the loop for every signal. It returns
  `tuple[str, ...]`: the exception type name of each signal whose evidence read failed this sweep,
  whether or not it reached the threshold. The result is empty when every read succeeded.
  - `TradingCopilot._monitor_positions` adds `trade_lifetimes:{TypeName}` to
    `_reconciliation_errors` for each name. This is the string a raised `reconcile()` already
    produces.
  - So any failed sweep marks `reconciliation` readiness not ready, with type names only, and
    `doctor --monitor` opens its debounced incident if the failure persists. A one-sweep blip is a
    single not-ready observation, which the debounce absorbs.
  - No exception is used for control flow. A `reconcile()` that raises (for example from
    `recover()`) keeps today's handling.
- **Unchanged:**
  - `resume_blockers` (a failed fresh read still blocks `/resume`);
  - every deterministic review;
  - cancellation recovery;
  - unknown-outcome halts in the entry FIFO;
  - native entry cancels (a sweep that cannot read simply tries again next minute, as today).

## 4. Out of scope

- Auto-clearing an existing halt. The halt only changes on an operator `/resume`.
- Transport retry changes. On wake the network was down, so a fresh socket would not have helped.

## 5. Tests

- 4 failing sweeps then a success: no review and no halt, and the count resets.
- N consecutive failures: exactly one review, with today's reason, and the halt is set.
- N = 1 reviews on the first failure (today's behaviour).
- Two signals: one failing, one healthy. The healthy one is still reconciled in the same sweep,
  and only the failing one counts.
- `reconcile()` returns `("ConnectionError",)` on a failed sweep and `()` on a clean one.
- `_monitor_positions` puts `trade_lifetimes:ConnectionError` into the reconciliation readiness
  detail. A planted exception message never appears in that detail, the log `extra`, or the review
  event.
- A deterministic `REVIEW` decision still halts on the first sweep.
- `missing_durable_entry_identity` still halts on the first sweep. It is a read that succeeded and
  found something wrong, not a failure.
- `resume_blockers` still blocks on a failed read.
- Shipped config and `load_config` round-trip the new key.
- Update the existing integration tests that expect a review after one failed calendar read
  (`tests/integration/test_trade_lifetimes.py`, both `...calendar...failure_reviews_and_never_closes`).
  They should assert no review after N−1 sweeps and exactly one review at N. They keep asserting
  "never closes" and the `resume_blockers` block.

## 6. Docs

- **CLAUDE.md, "Timed alpha execution":** after "Partial/uncertain evidence halts new risk;
  `/resume` cannot waive it", add one sentence: a failed evidence read is retried each sweep and
  becomes a review only after `execution.lifetime_evidence_review_after_failures` (5) consecutive
  failed sweeps, and every failed sweep degrades `reconciliation` readiness.
- **`docs/alpha-trade-lifetimes.md`:** the session-calendar bullet. A calendar read that raises is a
  failed evidence read under the same threshold. A `REVIEW` decision still halts immediately.
- **`docs/production.md`, "Timed alpha lifecycle operations":** the retry threshold; what a
  persistent read failure looks like (reconciliation not ready, then a
  `lifecycle_evidence_unavailable:{Type}` review halt); and the recovery (`/resume` once reads
  succeed). Include a one-line incident note for 2026-10-26.
