# Session-bounded native entries and an operator cancel (review item 1.3, EX-R1)

Status: design, 2026-10-23, approved by the operator ("re item 1.3 - sure go ahead"). This is the
prerequisite for item 1.4, paper auto-entry (decision D9).

## 1. Why

A tapped native card submits an Alpaca limit bracket with no time-in-force. GTC therefore applies
(`agent/copilot.py:3354-3365`, `broker/base.py:98`). An unfilled entry rests for days, and there is
no operator command to cancel it: `/close` answers "No open broker position"
(`execution/closing.py:155-156`).

This causes three problems:
- Research measured GTC limits as adverse-selecting (`docs/alpha-roadmap.md:150-152`).
- Labels assume a one-session entry.
- A stale entry keeps reserving risk and capacity.

Auto-entry (1.4) would multiply such entries.

The desk already has the right machinery for alpha and catalog-probe entries. `TradeLifetimeService`
(`execution/lifetimes.py`) recomputes an entry deadline from the broker's `submitted_at`. It persists one
`entry_cancel` work item before a single DELETE, recovers by exact lookup only, halts new risk on
partial or uncertain evidence, and is covered by readiness `entry_cancellation`. Native entries are
excluded only because they carry no `alpha_policy.lifetime`. This change gives them a lifetime through
a separate marker, and adds an operator cancel on the same path.

## 2. Decisions

- **Keep GTC, set it explicitly, and bound the entry with the lifetime service.**
  - A DAY bracket is not an option. Alpaca applies one time-in-force to the parent and both legs
    (`broker/alpaca.py:214-220`), so a DAY bracket filled intraday would lose its stop and target
    at the close. `docs/alpha-trade-lifetimes.md:25-27` and `docs/alpaca-integration-review.md:129`
    already rule this out.
  - The native `OrderRequest` sets `time_in_force=TimeInForce.GTC` explicitly.
- **The lifetime is `daily_entry_lifetime()`**: 57,600 s from the broker's `submitted_at`, with no
  holding deadline (`execution/lifetime_policy.py:65-71`). This is the semantics-v5 meaning of "one
  regular session".
  - Submissions happen only inside the regular session (`alpaca.py:533-542`). The deadline therefore
    always falls between about 01:30 and 08:00 New York, before the next open, when no fill is
    possible.
  - A card tapped on its next-session validity window gets a fresh one-session deadline from its own
    submission.
- **Separate marker, never `alpha_policy`.** New native signals carry
  `decision_provenance["entry_lifetime"]` (the policy's `asdict`). `alpha_policy` cannot be used
  because it switches on alpha-only behaviour:
  - the alpha trailing formula (`copilot.py:2960-2963`);
  - a policy-locked card, with no re-pricing or next-session validity (`copilot.py:3472, 3879`;
    `execution/freshness.py:110-119`);
  - skipped book sizing (`book_sizing.py:325`);
  - the alpha entry limit in the evaluator.
- **New signals only, and only where the broker can cancel.** The marker is written when the card is
  recorded, and only when both hold:
  - `execution.native_entry_lifetime` is `session`;
  - `config.execution_mode == "alpaca"`. The simulator implements neither `read_entry_group` nor
    `cancel_order`, and its `NotImplementedError` would become a review halt.

  Signals recorded before deploy stay untimed (`docs/alpha-trade-lifetimes.md:39`: historical positions
  keep their contract).
- **Config.** `execution.native_entry_lifetime: Literal["off", "session"]`. The code default is `off`;
  `config/config.yaml` ships `session`. It goes through `load_config` with a shipped-config test (the
  2026-10-13 regression class).
- **One resolver.** `entry_lifetime_of(signal) -> LifetimePolicy | None` returns
  `alpha_policy["lifetime"]` when present, otherwise `decision_provenance["entry_lifetime"]`, otherwise
  `None`. Every place that reads `alpha_policy["lifetime"]` today uses it:
  - `TradeLifetimeService.reconcile`;
  - `resume_blockers`;
  - `LifetimeRepository.begin_cancel`'s policy-equality fence (`storage/lifetimes.py:78-79`).
- **Operator cancel.**
  - Entry points: `/cancel <signal_id>` in Telegram and `copilot cancel <signal_id> [--confirm]` on the
    CLI.
  - Scope: any resting native, alpha or probe entry. That means an EXECUTED signal with no fill
    (`executed_at` unset), a bracket parent the desk submitted, and no active close. It includes
    historical untimed native entries.
  - Without confirmation it previews. Telegram replies with the preview and two inline buttons,
    "Cancel entry" and "Keep" (callbacks `cancelentry_confirm:<id>` and `cancelentry_keep`). The CLI
    previews unless `--confirm`.
  - Confirming runs the existing cancel flow:
    1. `begin_cancel` with `reason="operator"`;
    2. one `broker.cancel_order`, never retried;
    3. `_recover_cancel`.
  - `reason="operator"` skips only the deadline check (`storage/lifetimes.py:97-107`, which requires a
    `CANCEL_ENTRY` assessment) and the policy-equality fence. It keeps every other fence:
    - the durable entry identity and broker order id;
    - zero fills on parent and children;
    - the complete, unchanged bracket group;
    - no active close;
    - the scope lock;
    - one cancel per signal (`dedup_key = signal_id`).
  - The intent notice and the result notice name the cause. Telegram text carries type names only.
  - Refusals are direct replies, and each states its reason: unknown id, not resting (filled, closed,
    failed), a cancel already in progress, a close active, or an incomplete or changed group.
- **Card text.** A native card that will carry the marker adds one line: "Unfilled entry cancels before
  the next open."

## 3. Out of scope

- Resolving an `UNKNOWN` cancel automatically. It still blocks `/resume` and new entries until an
  operator acts (`docs/production.md` "Timed alpha lifecycle operations"); it is listed as a
  follow-up. With overnight deadlines a fill race cannot happen, so `UNKNOWN` comes only from a lost
  reply.
- A holding deadline or 20-session time exit for native positions (RM-R6, EX-R2).
- Auto-entry (1.4).
- Any change to admission, risk rules or `card_policy`.

## 4. Testing

- **Config:** `load_config(config/config.yaml)` gives `session`; the code default is `off`; an invalid
  value is refused.
- **Signal recording:**
  - the marker is present for a native card under `alpaca` and `session`;
  - it is absent under `off`, under `paper` mode, and on alpha or catalog cards, which keep
    `alpha_policy`;
  - the `OrderRequest` has explicit GTC.
- **Resolver:** alpha first, then provenance, then `None`; a malformed marker raises (and then becomes
  a review halt through the existing exception path).
- **Lifetime service**, unit tests and the PostgreSQL integration suite modelled on
  `tests/integration/test_trade_lifetimes.py`:
  - a marked native entry past its deadline is cancelled exactly once, the signal goes FAILED, a notice
    is sent and capacity is released;
  - before the deadline nothing happens;
  - an unmarked historical native entry is never touched (the existing "historical" case stays
    green);
  - a partial fill halts as for alpha entries;
  - `resume_blockers` sees marked signals.
- **Operator cancel:**
  - a preview shows no DELETE;
  - confirming cancels once and produces a notice;
  - each refusal reason is covered;
  - a second confirm is a no-op refusal (already cancelling or cancelled);
  - an operator cancel of an untimed historical entry works;
  - an operator cancel of a filled entry is refused with no DELETE;
  - the Telegram handler and callback buttons, the CLI command, and authorization through the existing
    `_is_authorized`.
- Full suite, the PostgreSQL integration suite on a `test_*` database, and pre-commit.

## 5. Docs

- `docs/alpha-trade-lifetimes.md`: "Known limits" no longer says built-in strategies stay GTC; add a
  "Native entries" section.
- `docs/production.md`: native card validity, the operator `/cancel` runbook, and the lifecycle
  operations section.
- `docs/durable-execution.md`: the `entry_cancel` causes (deadline, operator).
- CLAUDE.md contract 1: one sentence.
- The Telegram help text and command list.
