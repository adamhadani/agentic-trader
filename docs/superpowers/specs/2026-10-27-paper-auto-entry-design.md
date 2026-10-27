# Paper auto-entry for native suggestion cards (review item 1.4: CS-R4, CS-R5, CS-R6)

Status: design, 2026-10-27. Operator decision D9 (2026-10-23: "go with all your recommendations"),
confirmed for this item on 2026-10-27 ("meant start with 1.4"). It builds on 1.3 (#132, session-bounded
native entries and `/cancel`), which is its prerequisite.

## 1. Why

Entry is tap-only, so the book stays nearly empty: 3 accepted entries in about 24 sessions
(holistic review, 2026-10-21). With an empty book, book-aware sizing, aggregate stop risk and the
correlation caps never bind, and the desk collects about one execution record a week. Labels say
what a card would have done; only fills say what it did.

Auto-entry makes every native suggestion card an entry on the **paper** account, through the exact
path a tap uses, with every risk rule still applied at admission. The operator keeps a veto (Dismiss
before the entry is placed; `/cancel` or a card button while it rests). The 1.3 lifetime cancels an
unfilled entry at its session close.

## 2. Decisions

### 2.1 Switch and scope

- **Config.** `execution.auto_entry: Literal["off", "paper"]`, code default `off`; `config/config.yaml`
  ships `paper`. It goes through `load_config`, with a shipped-config test (the 2026-10-13 regression
  class).
- **Active only on Alpaca paper, with session-bounded entries.** One resolver,
  `auto_entry_active(config) -> bool`, is true only when `auto_entry == "paper"`,
  `execution_mode == "alpaca"`, `alpaca_paper` is true and `execution.native_entry_lifetime == "session"`.
  Under the simulator (`execution_mode == "paper"`) it is inactive (no error).
- **Misconfiguration refuses to start.** `load_config` raises when `auto_entry == "paper"` and either
  the account is Alpaca live (`execution_mode == "alpaca"` and `alpaca_paper` false) or
  `native_entry_lifetime` is `off` (an auto entry must never rest as an untimed GTC order). A live
  deploy must set `auto_entry: off` explicitly.
- **Defence in depth.** The auto path re-checks `is_paper_scope(workflows.scope)`
  (`research/alpha/probe.py:53`) immediately before it calls the entry path; a non-paper scope refuses
  with reason `not_paper_scope` and journals it.
- **Which cards.** A card is auto-eligible only when all hold:
  - it was sent (`RankedOutcome.SENT`) by a `ScanTrigger.SUGGESTION_SCAN` scan, so neither the swing
    and intraday journal-only jobs nor an operator `/scan` qualify;
  - it is native: not a catalog card (`is_catalog` false, so no PEAD drift and no insider watch card;
    `card_kind != "watch"`), no `alpha_policy`, no `alpha_version`;
  - it carries the 1.3 lifetime marker (`decision_provenance["entry_lifetime"]`), which already implies
    an asset class the broker executes;
  - `auto_entry_active(config)` at record time.
- **Marker.** An eligible card records `decision_provenance["auto_entry"] = {"mode": "paper"}` at
  `record_signal`. The marker is what the card renderer, the notices, the digest and `cards outcomes`
  read. A re-priced card (`db.replace_signal` after `REPRICE`) never carries it: the re-priced card is
  an ordinary tap card.

### 2.2 The entry

- **When: a sweep, not a hand-off.** `TradingCopilot.auto_enter_pending(now) -> list[AutoEntryAttempt]`
  selects, from the database, the signals of the current New York trading day that carry the
  `auto_entry` marker, are still `PENDING` and have no `auto_entry_attempted` event, in rank order
  (scan time, then rank). It does not depend on `run_scan`'s return value, so a scan that raises after
  recording its cards loses nothing. It is called:
  - in the scheduled suggestion job's `finally`, after `run_scan` (whether or not it raised) and before
    the end-of-session digest, outside the scan lock (the normal, immediate path);
  - by an interval job every minute, registered like `register_card_expiry_sweep`
    (`cli/commands/service.py` ~L675) with `next_run_time=now`, so it also runs at daemon start (a
    restart between recording and the attempt). Both jobs are registered only when
    `auto_entry_active(config)`.
  Calls are serialized by one `asyncio.Lock` on the copilot. Selection by `PENDING` plus the existing
  `signal_id` dedup in `enqueue_entry` make it idempotent: a crash after the entry was queued leaves the
  signal `SUBMITTING`, so it is never selected again.
- **Too late to place.** A selected card recorded more than `execution.card_freshness.fresh_seconds`
  (shipped 1,800 s) before `now` is not attempted: it is journaled `abandoned` (reason
  `not_attempted_in_time`) and re-carded as a tap card (2.3), so no card is left saying "no tap needed".
- **How.** For each selected card in order, the sweep calls the existing
  `execute_signal_by_id(signal_id, quantity=None, trigger=EntryTrigger.AUTO)`, the only entry point
  that applies card-freshness assessment. There is no second entry path, and none of the risk rules,
  admission, the FIFO or the preflight token changes.
  - `EntryTrigger` is a new `StrEnum` in `execution/durable.py`: `TAP = "tap"`, `AUTO = "auto"`. The
    default is `TAP`, so the Telegram callback and the CLI are unchanged.
  - The trigger is journaled on the card-tap event (`card_tap_assessed` payload `entry_trigger`) and on
    the `entry_queued` payload (`entry_trigger`), so an auto entry is never counted as a tap.
- **Default size.** `quantity=None`, the card's default tier, which book sizing has already scaled.
- **One attempt.** No retry of any kind, including a retryable refusal (`WAITING`, a tap-check
  timeout).
- **The outcome is read from the signal after the call**, never inferred from the reply alone
  (`ExecutionReply` carries no work status):

  | Signal status after the call | Outcome | Follow-up |
  |---|---|---|
  | `SUBMITTING` or `EXECUTED` (work item `QUEUED`, `CHECKING`, `SUBMITTING`, `ACCEPTED` or `UNKNOWN`) | `entered` | edit the card in place (2.3) |
  | `PENDING` (a queue-time refusal, or a retryable tap-check refusal) | `refused` | the `auto-refused` re-card (2.3) |
  | `FAILED` (a synchronous preflight or broker rejection) | `refused` | none: its existing immediate `entry/{id}/rejected` notice stays |
  | `EXPIRED` (`MISSED`, `EXPIRED`, or `REPRICE`, which also sends the replacement card) | `refused` (reason `expired` or `repriced`) | none |
  | not `PENDING` before the call (dismissed, or a tap won the race) | `skipped_not_pending` | none; the existing `signal_id` dedup already prevents a second entry |

  A rejection that happens later, after an `entered` outcome (the FIFO head belonged to another entry),
  keeps its immediate rejection notice; the digest and `cards outcomes` read the final signal and work
  item state, not the attempt event.
- **Journal.** One `auto_entry_attempted` event (new `EventKind.AUTO_ENTRY_ATTEMPTED`) per selected card
  on stream `card/{signal_id}`, keyed `auto-entry/{signal_id}`, payload: `signal_id`, `scan_id`,
  `outcome` (`entered | refused | skipped_not_pending | abandoned`), `reason`, `work_status` (the
  signal's latest entry work item status, or null), `signal_status`, `attempted_at`. `reason` is the
  desk-authored reply text with markup stripped and cut to 300 characters, or an exception's type name;
  never exception text. A `refused` attempt whose signal is `FAILED` (a synchronous preflight or broker
  rejection) journals the fixed reason `rejected at submission` instead of the reply text, because that
  reply embeds the broker's or an exception's text; the digest therefore shows the fixed phrase. Admission's refusal strings already name the rule; no change to admission or
  `risk/` is made to produce tokens.
- **Failure isolation.** An exception for one card is journaled as `refused` with its type name and
  does not stop the others or the digest. `auto_enter_pending` never raises into the job or startup.

### 2.3 The card

The card is still queued in `record_signal`'s transaction (contract 6), with payload
`auto_entry: "placing"`. The outbox drains every 2 s, so it is almost always delivered while the
signal is still `PENDING`, before the sweep places the entry. Rendering at delivery uses the signal's
status at that moment and the payload:

The auto card's line is the same before and after placement, so the card text never needs editing:
"🤖 Paper auto-entry: entered for you on the paper account; no tap needed. Unfilled entry is
cancelled at the session close." Only the buttons depend on the state:

| State at delivery | Line on the card | Buttons |
|---|---|---|
| payload `auto_entry: "placing"`, status `PENDING` | the auto line | **Dismiss** (a veto before placement) |
| payload `auto_entry: "placing"`, status `SUBMITTING` or `EXECUTED` (a late delivery) | the auto line | **Cancel entry…** |
| re-card (`auto_entry: "refused"` or `"abandoned"`), status `PENDING` | "🤖 Auto-entry refused: {reason}. Tap Execute to enter it yourself." (or "🤖 Auto-entry was not placed in time. Tap Execute to enter it yourself.") | the normal Execute and Dismiss |
| any other status | as today | as today (none, or Re-evaluate) |

- **Buttons swapped in place once placed.** An `entered` outcome queues, in the same transaction as its
  journal event, a new `NotificationKind.CARD_AUTO_ENTERED` notification keyed
  `signal/{id}/auto-entered`. Its delivery replaces the original card's keyboard with **Cancel entry…**
  through `edit_message_reply_markup`, exactly as `strike_expired_card` does (`notifier/telegram_bot.py`
  ~L1636): the message id is read at delivery time; a card never delivered (no `telegram_message_id`)
  is acknowledged without calling Telegram, since its late delivery renders from the status; the same
  `BadRequest` cases are acknowledged; any other failure is retried by the outbox. An auto-entered card
  never receives the expiry strike (expiry applies only to `PENDING` cards), so the two edits cannot
  race.
- **Cancel entry…** has callback `cancelentry_ask:{signal_id}`, which runs the 1.3 `/cancel` preview
  (`cancel_handler(signal_id, False)`) and replies with its **Cancel entry** / **Keep** buttons. No
  one-tap cancel. `/cancel {id}` works throughout.
- **A refusal that leaves the card `PENDING` sends a fresh card.** It queues, in the same transaction as
  the journal event, a `NotificationKind.SIGNAL` notification keyed `signal/{id}/auto-refused` carrying
  the original card payload plus `auto_entry: "refused"` (or `"abandoned"`) and `auto_refusal` (the
  reason, queued raw and escaped once by the renderer). Only when the signal is still `PENDING`; a `FAILED` or `EXPIRED` card gets no
  re-card, and `skipped_not_pending` sends nothing.
- The watch card's existing "tap-only, never auto-entered" line stays true and is pinned by a test.

### 2.4 Notices and the digest

The operator preference of 2026-09-16 stands: **every confirmed trailing-stop change keeps its own
notice** (contract 3). Exits, ratchets, halts, `UNKNOWN` outcomes, partial fills, missed bounds,
`filled_before_cancel` and every refusal keep their immediate notices.

For an entry whose work item carries `entry_trigger: auto`:

- **New immediate fill notice.** When fill sync first records the entry's fill
  (`executed_at` goes from unset to set), queue in the same transaction a `MESSAGE` notice keyed
  `signal/{id}/auto-filled`: "🤖 Auto-entry #{id} {symbol} filled {qty} @ ${price}; stop ${stop},
  target ${target}." Tap entries get no fill notice, as today.
- **Routine notices folded into the digest** (not queued individually; their journal events are
  unchanged):
  - the resolution notice `entry/{work_id}/{status}` when the status is `accepted`;
  - the deadline-cause cancel intent `entry-cancel/{id}/intent`;
  - the deadline-cause cancel result when it is confirmed unfilled.
  An operator-cause cancel keeps both its notices. A notice that resolves an `UNKNOWN` outcome (an
  `accepted` resolution or a cancel result that clears it) is never folded: its halt was announced.
  `signal/{id}/entry-terminal` is **kept** for auto entries: for them it fires only when the broker
  ends the order itself (rejected, expired or an external cancel), which is not routine.
- **Daily auto-entry digest.** One `MESSAGE` notice keyed `auto-entry-digest/{et_date}`, sent at the
  regular session close + 15 minutes (early closes included, from the same calendar the lifetime
  service uses; 16:15 New York on a normal day, inside the host's awake window), only on a session with
  at least one auto-eligible card. A 5-minute interval job (the second job above) sends it on its first
  run at or after that time; the key makes it once per date. One line per card, in rank order: symbol, signal id and outcome:
  - `filled {qty} @ ${price}` (with the current open/closed state);
  - `cancelled unfilled at the close`;
  - `not yet resolved (entry or cancel pending)`;
  - `refused: {reason}`;
  - `dismissed before placement` / `cancelled by you`;
  - `not placed in time` (abandoned);
  and the closing line "Paper fills are optimistic: no queue position or market impact." The digest
  reads only the journal and the signals table; it is idempotent by key; a day with no marked cards queues and
  journals nothing.

### 2.5 Measurement (`cards outcomes`)

- Every card row gains `entry_trigger` (`auto | tap | none`) and `operator_action`
  (`tapped | auto_entered | dismissed | cancelled | expired | auto_refused | none`).
- `summarize_execution` counts auto entries apart from taps (`mean_tap_age_seconds` stays taps-only).
- A new `operator` block in `summarize()`:
  - by slot (`10:35`, `14:35`), trigger and strategy: sent, tapped, auto-entered, dismissed, cancelled,
    expired;
  - tap latency (median and p90, taps only);
  - "kept vs vetoed", over **auto-marked cards only** (recorded with the `auto_entry` marker, so the
    count starts at the deploy): mean beta-matched excess R (the D3 unit, de-duplicated per setup) of
    kept cards (auto-entered and not cancelled by the operator) against vetoed cards (dismissed before
    placement, or cancelled by the operator), with a session-block bootstrap 90% CI and `n` for each
    side. Cards that risk or admission refused (`auto_refused`, including later rejections) and
    `abandoned` cards are on neither side and are counted separately. `status: "insufficient"` below 30
    distinct resolved setups in total or below 10 on either side.
  - Tap-era cards (no marker) keep a separate descriptive "tapped vs untapped" line; it is not part of
    the rule, because before auto-entry "untapped" mostly meant the operator was not looking.
  This is descriptive, never a gate. The predeclared rule (Gilliland, forecast value added): once the
  kept-vs-vetoed comparison is sufficient, keep human selection as a gate only if the kept-minus-vetoed
  CI is above zero. Recorded in the doc, not enforced in code.
- **Pass criteria after 20 sessions**, recorded in the doc and checked by the operator, not code:
  at least one `book_sizing` factor below 1; per-rule admission refusals visible; at least 20 entries
  with entry and exit slippage; labelled R reconciled with realized R card by card.

### 2.6 Visibility

- `/status` gains one line: "Auto-entry: paper (on)", "off", or "inactive (not an Alpaca paper
  account)".
- `scripts/verify_runtime.py` reports `auto_entry` (`off | paper`, active or not).

### 2.7 Bundled follow-ups (small, same PR; no docs-only PRs)

- **`verify_runtime.py` on a 503.** Catch `urllib.error.HTTPError`; on 503 read the JSON body and retry
  up to 3 times, 2 s apart (a GET). If still not ready, fail with a `RuntimeError` naming the not-ready
  checks and their details, never a traceback.
- **`insider_capture` before its first pass is due.** With no capture yet, `freshness` is ready while
  the worker's status is `waiting` or `scan_window` (detail `"not_due; no capture yet"`), and not ready
  after a pass has run and still produced none. This removes the first-deploy incident alert seen on
  2026-10-24.
- **1.3 `not_resting` wording.** Distinct replies for a dismissed or expired card ("Signal #{id} was
  never entered ({status}); there is nothing to cancel.") and for an entry still being submitted
  ("Signal #{id} is still being submitted; try again in a minute."), beside the existing filled,
  closed or failed text.
- **1.3 missed-bound check cost.** A filled native entry's missed-bound verdict is final once its fill
  time has been compared with its deadline, whichever way it went. The lifetime service caches checked
  signal ids per process (both verdicts) and re-checks each at most once after a restart; the
  `entry-lifetime-missed/{id}` dedup is unchanged.

## 3. Out of scope

- Auto-entry for PEAD drift, insider watch, alpha or probe cards, or for any live account.
- A runtime on/off command (the switch is config plus a controlled restart; `/panic` still halts all
  new risk at once).
- Batching ratchet notices (operator preference, contract 3).
- An operator resolve path for an `UNKNOWN` cancel (still a 1.3 follow-up).
- Any change to risk rules, admission, `card_policy`, book sizing or the card budget.

## 4. Contract and doc changes

- **CLAUDE.md contract 1:** "CLI/buttons call `EntryExecutionService`" gains the paper auto path: on an
  Alpaca paper account (`execution.auto_entry: paper`), the scheduled suggestion job calls the same
  `execute_signal_by_id` for its sent native cards, tagged `entry_trigger: auto`; catalog, watch,
  alpha and probe cards stay tap-only; a live account refuses to start with it set.
- **`docs/production.md:43`** (tap-only entry) is amended for paper, with a runbook: turning it off,
  the card states, the digest, vetoes.
- **New `docs/paper-auto-entry.md`:** the contract above, the measurement rules and the 20-session pass
  criteria.
- `docs/card-evidence.md` (the `operator` block, `entry_trigger`, `operator_action`) and
  `docs/durable-execution.md` (the trigger tag, the folded notices, the fill notice and the digest).

## 5. Testing

- **Config:** the shipped config gives `paper`; the code default is `off`; an invalid value is refused;
  `auto_entry_active` truth table (simulator, Alpaca paper, Alpaca live, lifetime `off`); `load_config`
  raises on Alpaca live with `paper` and on `paper` with `native_entry_lifetime: off`.
- **Eligibility:** a native suggestion-scan card gets the marker; none for an operator `/scan`, the
  swing job, a PEAD drift card, a watch card, an alpha card, a futures card, a card without the
  lifetime marker, or when inactive. The watch card still renders "tap-only, never auto-entered".
- **Sweep:** selects only today's marked `PENDING` cards with no attempt event, in rank order; runs in
  the job's `finally` even when `run_scan` raised; runs at startup; a second concurrent call waits on
  the lock and finds nothing; a card older than `fresh_seconds` is `abandoned` and re-carded.
- **Entry:** one `execute_signal_by_id` call per card with `trigger=AUTO`; the tap path still journals
  `tap`; every row of the outcome table in 2.2 (each with its follow-up or its absence); a
  later rejection after `entered` keeps its rejection notice and shows in the digest; an exception in
  one card does not stop the next; a non-paper scope refuses with no entry; no retry.
- **Card rendering:** each row of the table in 2.3; the `CARD_AUTO_ENTERED` edit (including the
  unknown-message fallback); the `cancelentry_ask` callback reaching the preview; a re-priced card
  rendering as a tap card.
- **Notices:** the auto fill notice is queued exactly once in the fill transaction; the three routine
  notices are not queued for an auto entry and still are for a tap entry, and `entry-terminal` is kept; an operator cancel of an auto
  entry keeps its notices; ratchet, exit, `UNKNOWN` and partial-fill notices are unchanged.
- **Digest:** content for each outcome; sent once per date at close + 15 (early close covered); nothing
  sent on a session without auto-eligible cards.
- **Outcomes:** `entry_trigger` and `operator_action` per row; the `operator` block, including
  `insufficient` below 30 setups; `summarize_execution` keeps taps and auto entries apart.
- **Follow-ups:** `verify_runtime` with a fake 503-then-200 server and a persistent 503; insider
  `freshness` truth table; the new `not_resting` texts; the missed-bound cache.
- **PostgreSQL integration** for the trigger payloads, the fill-notice transaction and notice
  suppression; full suite, `--run-postgres` on a `test_*` database, and pre-commit.
