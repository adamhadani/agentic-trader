# Entry submission on a refreshed account-risk observation

## Why

**Monday 2026-11-09, 10:35 ET.** The suggestion scan sent one native card, TXN (TREND_PULLBACK, LLM approved). Its paper auto-entry was refused with "Account risk changed after preflight. No order submitted; request a fresh scan." No order went out, and the signal ended `FAILED`.

What happened:
- `EntryExecutionService.dispatch_one` refreshes the account ledger and reads `risk = ledger.current_risk()`.
- It then runs the preflight (broker context, macro check, capacity), which takes about 3–4 s.
- `WorkflowStore.begin_submission` then reads the current checkpoint under the trading → ledger lock and refuses unless `risk.fingerprint == risk_fingerprint`.
- The fingerprint digests the whole `AccountRiskSnapshot`, `observed_at` included. The daemon's periodic ledger import writes a checkpoint about every 60 s. One landed between preflight and submission (checkpoints at 15:40:16.6Z and 15:40:17.5Z; submission check about 15:40:19.9Z).
- Equity had moved by under $1.

At today's timing, any entry whose preflight straddles a periodic import has about a 1-in-15 chance of this refusal. It was the first in three auto-entries. The 14:35 card (UNH) entered normally.

The exact equality protects nothing extra. Under the lock, `begin_submission` already reads the newest fresh checkpoint (`require_risk_checkpoint`, max age enforced). It also re-runs `assess_entry_capacity(request, reservations, broker_context, config, account_risk=risk)` on that newer snapshot, and that call includes `reservation_rejection` with the current drawdown and equity. A materially worse snapshot is therefore refused by the capacity re-check with its real reason, such as a drawdown halt. A benign refresh is refused only because its timestamp changed.

## What changes

In `begin_submission`, a fingerprint mismatch no longer refuses by itself:
- When the checkpoint under the lock differs from the preflight's, the submission proceeds to the existing checks on the newer observation. Those checks are lease, authorization expiry, broker-context presence and `assess_entry_capacity` with `account_risk=` the newer snapshot. Only their refusals apply.
- The parameter `risk_fingerprint` is renamed `preflight_risk_fingerprint`. Callers are `execution/entries.py` and tests, with no alias.
- **Fail-closed guard (kept):** if the store reads a risk snapshot but the preflight passed no fingerprint (`None`), refuse with "Account risk was not observed at preflight. No order submitted." In Alpaca mode the preflight always reads risk, so this only catches an inconsistent caller.
- **Evidence:** `ENTRY_SUBMITTING` and its audit record keep `account_risk` (the snapshot used at submission). `risk_fingerprint` becomes that snapshot's fingerprint, so the two always agree. A new `preflight_risk_fingerprint` holds the preflight's. When they differ, an info log names the refresh with no values, and journal text carries type names only.
- Nothing is resized or replayed. The approved quantity, prices and lease rules are unchanged.

**Unchanged:**
- Admission (`enqueue_entry`), the preflight itself, risk freshness (`accounting.max_age_seconds`) and lock order (trading → ledger).
- The lease and authorization re-checks, capacity rules, halt checks, and the simulator path (no risk).
- POST is never retried. No schema change (head `008`), no new config.

## Tests

1. **Benign refresh (the incident):** a checkpoint refreshed between preflight and submission, with the same material risk, does not refuse. The entry submits once. The `ENTRY_SUBMITTING` evidence has `risk_fingerprint == account_risk` digest (the new snapshot) and `preflight_risk_fingerprint ==` the old one. Test it at the store level, and end to end through `dispatch_one`/`authorize` with a ledger refresh injected during the preflight, giving exactly one POST.
2. **Material refresh still refuses:** a refresh that crosses the drawdown halt (the existing 7% loss fixtures) refuses through the capacity re-check. The reason names the drawdown, the work item stays `CHECKING`, and there are no mutations. This covers both the sequential case and the lock-wait race test (`test_account_risk_race.py`); both now pass a broker context so the refusal comes from the re-check, not from missing evidence.
3. **None guard:** a present risk with a `None` preflight fingerprint refuses with the new reason.
4. Existing tests are updated for the rename. The no-refresh evidence assertion (`risk_fingerprint == risk.fingerprint`) still holds.

## Docs

Update the rule where it is stated, so the text no longer says "pins the exact fingerprint". Instead: final submission re-assesses capacity on the risk observation current under the ledger lock, and a refreshed observation is recorded, not refused.
- `CLAUDE.md` contract 1;
- `AGENTS.md` (Alpaca entry risk line);
- `docs/account-ledger.md` (entry admission paragraph);
- `docs/durable-execution.md` (final submission sentence, plus a one-line note on the 2026-11-09 incident);
- `docs/entry-capacity.md` (fingerprint sentence).

## Rulings (controller)

- Re-assess instead of retrying the whole dispatch. The capacity re-check already runs on the newer snapshot, so a retry adds latency and a second preflight for nothing.
- No monotonic `observed_at` guard. Any fresh checkpoint is valid admission evidence under the existing policy, the same as at admission and preflight.
- Keep the `None` guard as cheap fail-closed protection.
