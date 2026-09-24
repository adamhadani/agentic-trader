---
layout: default
title: Production Operations — Agentic Trader
---

# Production operations

Current local baseline: **Alpaca paper**, PostgreSQL, one macOS launchd daemon and
one Telegram poller. `EXECUTION_MODE=alpaca` plus `ALPACA_PAPER=true` selects the
broker's paper account. `EXECUTION_MODE=paper` selects the local simulator.

See [development notes](development-notes.md), [CLI reference](cli-reference.md),
[incident remediation](incident-2026-09-15.md), and [architecture review](architecture-review.md).

## Ownership and scheduling

`com.agentictrader.copilot` runs `uv run copilot daemon` from this checkout after
its launchd shell sources `.envrc`. `com.agentictrader.watchdog` checks the PID and runs the external readiness monitor every
60 seconds; `com.agentictrader.alphaminer` runs weekly. Do not start a second daemon,
`listen`, or Compose service while the installed poller owns the bot.

- Swing scans: every four hours from startup, immediate first run, all timeframes,
  the whole `universe:` when the equity session is open.
- Suggestion scans: cron on the New York clock, weekdays, at
  `scheduler.suggestion_scan_times_et` (see below). They also add the dynamic
  suggestion universe.
- Intraday scans: every 15 minutes from startup, session gated, `15m` filter,
  restricted to the contracts configured explicitly under `contracts:`
  (`AppConfig.non_universe_contracts`) — never the universe.
- Position monitor: every minute, with additional broker stream wakeups.
- Macro briefing: weekdays 12:30. Automatic legacy retuning is removed. Cron schedules
  inherit scheduler/system timezone. Intervals are not candle-close aligned.
- Alpha miner: Saturday 03:00 local launchd time; ETF32, 9 genetic + 7 catalog trials/symbol,
  5y daily Alpaca data, 120s compute/symbol; no automatic qualification or promotion.
  Reviewed prospective equity cohorts are available only through the manual,
  capped `alpha mine --universe snapshot --universe-file ...` path; the scheduled
  job remains ETF32 until a snapshot campaign is explicitly enrolled.

Scans stage suggestions; an operator approves entry orders. Configuration loads at construction. The journal-backed alpha registry reloads
atomically between scans; external config edits still require restart.

### Suggestion scans

Two APScheduler cron jobs (`suggestion_scan_0`, `suggestion_scan_1`) run
`run_scan(asset_class="equity")` at `scheduler.suggestion_scan_times_et`, default
`["10:35", "14:35"]` New York, Monday to Friday, with `coalesce`, `max_instances=1`
and a 600-second misfire grace. Both times follow a completed hourly bar and leave a
card's four-hour validity inside the regular session. A closed equity session is
logged (`suggestion_scan_skipped`) and skipped. The last configured time publishes an
end-of-session **digest** through the durable outbox under the key
`scan-digest/{et_date}`; the digest runs even when the session was closed, so a quiet
day still reports.

The digest reports: number of scans, instruments actually scanned, names with
insufficient data, candidates found, cards sent, runners-up with their
`setup_quality` scores (top five), fetch failures (count and up to eight symbols),
the count of names excluded by the coverage gate, and each scan's duration. When a
scan attempted the dynamic suggestion universe, it also reports "N dynamic names
scanned (M excluded)" (see below).
It aggregates **universe suggestion scans only**: a run carrying a timeframe filter
(the 15-minute intraday job) or a symbol restriction (`copilot scan --symbols`,
Telegram `/scan` of named contracts) is excluded, and a session in which only those
ran reports that no suggestion scans ran. It is assembled from in-memory per-run
statistics trimmed to the current New York date, so a mid-session daemon restart
truncates the digest — the card budget itself is unaffected because it is derived
from the signals table, not from a counter.

The gate counts **regular-session buckets only**. SPY also prints the 08:00
pre-market and 16:00 post-market buckets that most names never do; counting them put
every seven-bucket name at about 0.79–0.80 of the reference, so the result flipped
with the time of day (roughly 100 liquid names, VTI and XLI among them, excluded at
10:35 and after the close, September 22).

Every scan also skips contracts whose asset class the configured execution mode
cannot admit (`skipped_not_executable` in the scan summary). Alpaca entry admission
accepts equities only, so on the Alpaca paper desk futures are not scanned: a futures
card could never be accepted. The simulator (`EXECUTION_MODE=paper`, including dry
scans) still scans every configured class.

**Dynamic suggestion universe (WS3, September 23).** Each scheduled suggestion scan
(and only that scan: `run_scan(shadow_evidence=True)` with no symbols and no
timeframe) adds up to `universe.dynamic.max_symbols` in-play US equities to the static
universe. The swing scan, the intraday `non_universe_contracts` job, and manual or
Telegram scans (restricted or not) never do; the one exception is Telegram
`/scan SYMBOL` of an unconfigured equity, which scans that single name as a dynamic
name (see below). The design and the filter order are in
[the WS3 spec](superpowers/specs/2026-09-23-dynamic-universe-design.md); the code is
[`screeners/dynamic_universe.py`](../agentic_trader/screeners/dynamic_universe.py).

- **Sources.** Alpaca's screener: most actives by trade count, plus market movers.
  These are read-only GETs, followed by the Alpaca asset list, which is cached per
  New York date. Both clients are bounded (`BoundedScreenerClient` and
  `BoundedTradingClient`, with a per-request socket timeout of
  `market_data.timeout_seconds`). All of it runs off the event loop under one
  60-second bound (`DYNAMIC_UNIVERSE_TIMEOUT_SECONDS`). Any failure, including missing credentials,
  is logged once as `dynamic_universe_unavailable`, and the scan continues with the
  static universe alone.
- **Filters.** A deterministic, reason-counted filter keeps:
  - plain `^[A-Z]{1,5}$` symbols not already in `contracts:`;
  - no crypto-prefixed symbols (`BTC`, `ETH`, `SOL`, `DOGE`: the static universe
    validator's rule, since the session router treats them as crypto), reason
    `crypto_prefix`;
  - active, tradable `us_equity` assets on NYSE, NASDAQ, ARCA, AMEX or BATS;
  - no warrants, rights or units, and no volatility or option-income funds (a fund
    whose name has the whole word "VIX" or "volatility", "YieldMax", "option income"
    or "covered call"), reason `instrument`;
  - no leveraged or inverse funds;
  - a price of at least `min_price`.

  At most `max_candidates` survivors are fetched.
- **Liquidity gate (relative, self-calibrating).** After the scan's normal fetch and
  the coverage gate, a dynamic name's median `Close × Volume` over its last 20
  completed, finite daily sessions must reach the threshold. The threshold is the
  `min_dollar_volume_static_percentile` (default 0.25) percentile of the same median
  for every static universe equity, computed in the same scan from the same bars.
  - Same bars means the same feed and the same completed-rows rule. The desk's
    `market_data.alpaca_feed` is IEX, whose volume is a few percent of consolidated
    volume. An absolute dollar floor would be miscalibrated: the first real dry run
    excluded SHOP, SOFI, IONQ, CRWV and RKLB at $50M.
  - `min_median_dollar_volume` is an optional absolute floor in the feed's own units.
    It defaults to 0; when set, the larger of the two applies.
  - Fewer than 20 static equities with a full window means there is no reference.
    Every dynamic name is then excluded as `no_reference` (fail closed), and
    `dynamic_liquidity_no_reference` is logged.
  - Today's partial bar never counts on either side. The window ends at the last
    session completed before the scan's New York date, the date the shadow
    cross-section uses.
  - A fetch failure, a coverage exclusion or a failed gate drops the name before
    strategy scanning, with a reason. Such a name never counts as a scan fetch
    failure or consumes the `max_symbols` cap.
- **Treatment.** Each dynamic name gets a synthetic contract for this scan only:
  equity, multiplier 1, tick 0.01, and the symbol as ticker. `config.contracts` is
  never mutated. Every existing gate applies unchanged, including dedup, earnings,
  macro, sizing, session, LLM, card budget and card freshness. Admission, the
  tap-time checks and trailing stops use the default equity policy (multiplier 1) for
  an unconfigured equity. Dynamic names run the native strategies only: the scan
  drops every registry alpha (active or paper probe) for them, whatever the alpha's
  declared eligibility. Alpha shadow observation also skips them, so alpha evidence
  keeps its declared population.
- **One correlation group.** All dynamic names share the group `dynamic` for the
  scan's `max_cards_per_group_per_session` cap. A card recorded today whose
  provenance has `dynamic: true` counts toward it, so the paper desk issues at most
  one dynamic card per session. Admission's `max_correlated_positions` still sees
  only configured groups.
- **Evidence.** A dynamic card's provenance carries `dynamic: true` and
  `dynamic_source` (`most_actives` or `movers`). So does its `scan_candidates_ranked`
  candidate; the keys are absent for static names. The shadow cross-section stays
  `universe.groups` only, so a dynamic candidate's cross-sectional features are null.
- **Audit.** Every non-dry scan that attempts dynamic selection appends one
  `dynamic_universe_built` event under stream `scan/{et_date}` (key
  `dynamic_universe_built/{scan_id}`). It is written in its own transaction under the
  scope lock and never blocks the scan. The event carries `available`, `error`, the
  sources' `raw_counts`, the per-reason filter counts, the members with source, rank,
  price and percent change, the `scanned` names and the `excluded` names with their
  reasons. It also records the liquidity `threshold`, the `feed` it is measured in,
  and the `reference` (percentile, number of static names, value, floor). The scan
  summary holds the same under `dynamic`. The end-of-session digest adds "N dynamic
  names scanned (M excluded)", counting unique names across the session's scans, plus
  how many scans found the dynamic universe unavailable. A coverage-excluded dynamic
  name is counted there, not again under "coverage excluded".
- **Rollback.** Set `universe.dynamic.enabled: false`; suggestion scans are then
  unchanged. Like any config change, this needs a restart.
- **Deploy note: trailing stops for unconfigured equities.** The trailing-stop
  monitor now trails **any** open EQUITY position that has no `contracts:` entry,
  with the default equity policy (multiplier 1, tick 0.01, the symbol as ticker).
  This covers a dynamic name, but also any older equity position whose contract was
  removed from config. Before this change such a position was skipped with "No
  instrument policy for trailing stop". Unconfigured futures, and positions with no
  asset class, are still never touched.

  Before restarting onto this revision, list the tracked open positions and check
  which ones are unconfigured:

  ```bash
  uv run copilot positions
  uv run python - <<'PY'
  import asyncio
  from agentic_trader.config import load_config
  from agentic_trader.storage.db import SignalDatabase

  async def main():
      config = load_config()
      db = SignalDatabase(db_url=config.resolved_db_url, config=config)
      for p in await db.get_active_positions():
          if p["contract"] not in config.contracts:
              print(p["id"], p["contract"], p["asset_class"], "stop", p["stop_loss"])

  asyncio.run(main())
  PY
  ```

  Every EQUITY row printed will start trailing on the next monitor cycle once its
  price clears the breakeven or trail trigger. A confirmed replacement sends the
  usual trailing-stop notice. Resolve any row that must not trail (for example, by
  closing it) before the restart.

**Earnings blackout.** The risk evaluator's deterministic gates now include an
earnings-announcement blackout for equity candidates, right after the macro lockout
check. It looks ahead from the unofficial, keyless `api.nasdaq.com` earnings
calendar (same style as the ForexFactory macro feed: short timeout, per-date cache,
logged warning on failure) and rejects a candidate whose next report lands within
`risk.earnings_blackout_days` calendar days (default 7; 0 disables the gate and the
lookup entirely). A card that clears the gate carries a one-line `Earnings:` note
(e.g. "No report within 7 days"); when the calendar could not be verified for every
date in the window the note reads "Unverified — earnings calendar unavailable" and
the gate fails open (never blocks on unverifiable data). Held positions are not yet
warned ahead of an earnings date — that is a follow-up, not covered by this gate.

**Card freshness (September 23).** A card is an offer valid only until the close of
the regular session it was issued in, not until the operator happens to read it. A
tap (Telegram button or CLI `execute`) re-assesses the card against the current
price, session and gates instead of blindly submitting the original bracket. The
approved bracket itself is never silently changed; any change of price or size
becomes a *new* card that needs its own fresh tap. Config lives under
`execution.card_freshness`:

| Key | Default | Meaning |
| --- | ---: | --- |
| `enabled` | `true` | `false` restores the legacy tap (admission's own drift/age checks only, no re-assessment). |
| `fresh_seconds` | 1800 | Age below which a same-session tap is still `EXECUTE`. |
| `fresh_max_r` | 0.25 | `abs(r_consumed)` bound for `EXECUTE`; `r_consumed = sign × (price − entry) / |entry − stop|`. |
| `reprice_min_risk_fraction` | 0.5 | Minimum fraction of the original stop distance the current price must still have as remaining risk for `REPRICE`. |

A tap resolves to exactly one of four outcomes:

| Outcome | Behaviour |
| --- | --- |
| `EXECUTE` | Same session, within `fresh_seconds` and `fresh_max_r`. Unchanged path: `EntryExecutionService.authorize` with the original bracket. The freshness decision never authorizes by itself — admission still enforces drift, macro, capacity, session and deadlines on its own terms. |
| `REPRICE` | Same session, open, past the fresh bounds, price strictly between stop and target, remaining risk and reward:risk still acceptable, and every gate (halt, RTH, macro, regime, earnings) passes. The old signal is atomically expired and a replacement `PENDING` signal is recorded — entry at the current price rounded to the instrument's tick, same stop/target, quantity re-derived from the risk dollars of the tapped tier (else the card's size) and then capped by admission's per-trade caps (`sizing.max_shares_per_trade`/`max_contracts_per_trade`, `max_trade_notional_cap`, `max_risk_pct_cap` on configured cash, scaled down by the regime's risk multiplier); a size that rounds or caps to zero is `MISSED` instead — in the *same* transaction as its outbox notification, so a crash never leaves a replacement without its card or a card without its signal. The tap reply says a re-priced card was sent; the new card needs its own tap. A versioned alpha card (`alpha_version`/`alpha_policy`) is never re-priced: its immutable execution policy owns the entry limit and bracket, so this outcome executes the original bracket instead and admission's age/drift/policy checks decide. |
| `MISSED` | Same session, but the geometry or a gate fails (through the stop/target, too close to the stop, reward:risk below the larger of `risk.min_risk_reward_ratio` and the cached regime's threshold, or a failing gate). The old signal is expired and the reply offers **[🔄 Re-evaluate]**. |
| `EXPIRED` | The issuing session has ended (`now ≥ valid_until`) or the market is not in RTH. The old signal is expired; the reply gives the reason and the broker's next regular open, and offers **[🔄 Re-evaluate]**. `EXPIRED` is a live signal status, not a terminal-only label: every status-gated query (duplicate rule, `/perf`, positions) already treats it as non-executed. |

A price fetch failure, or a tap-time session/gate read failure (regime, macro,
earnings), refuses the tap with a retryable message and leaves the card `PENDING`
exactly as it was — safe to tap again. All tap-time reads share one 15-second bound
(`TAP_CHECK_TIMEOUT_SECONDS`); a timeout is the same retryable refusal. Telegram restores the original keyboard
(the tapped execute button plus dismiss) on the card so the operator can retry
without a fresh scan; a multi-tier card only gets back the tier that was actually
tapped, since the others are not reconstructable from the reply alone.

**Session-close card sweep (September 24).** Correctness never depended on the buttons
themselves, only on tap-time re-assessment — but an untapped card's Execute/Dismiss kept
looking live in Telegram long after its session ended. A `card_expiry_sweep` job (every 5
minutes, also at startup, regardless of halt state — expiring a card releases no risk and
adds none) calls `TradingCopilot.expire_stale_cards()`, which runs
`SignalDatabase.expire_stale_signals(now, configured_contracts=...)`. That method applies
the exact same rule as a tap (`execution/freshness.py::card_is_stale`, sharing
`card_session_over`/`valid_until_from_provenance` with `assess_card` so the two paths
cannot drift) to every `PENDING` card in scope, atomically flips each stale one to
`EXPIRED` and enqueues one `CARD_EXPIRED` outbox notification per row changed in the same
transaction. Delivery (`NotificationDispatcher` → `TelegramNotifier.strike_expired_card`)
reads the card's `telegram_message_id` at send time and edits its keyboard: a single
**[🔄 Re-evaluate]** button when the contract is still configured, or no buttons at all for
a dynamic suggestion-universe name. A card that was never delivered to Telegram, or a
Telegram edit that fails because the message is already gone, unchanged, or names an id
Telegram no longer recognizes, is acknowledged without retry; an unconfigured/unbuilt
Telegram client instead fails like every sibling sender, so the outbox retries and
eventually dead-letters rather than reporting a strike that never happened. A tap that
still lands on an `EXPIRED` card — swept before its strike was delivered, an earlier
duplicate message, or a card a previous tap expired or re-priced — never gets the generic
"not PENDING" refusal. It gets "⌛ Card #N is no longer live (expired).", plus the next
regular open when the contract's session is closed (omitted if the session read fails,
15 s bound). It offers **[🔄 Re-evaluate]** only for a configured contract. If another
card for the contract is live, for example a re-priced replacement, the reply names it
("Card #M for SYMBOL is live.") and offers no Re-evaluate. A SIGNAL notification that
retries after the sweep already expired its card is also covered: `send_signal_alert`
checks the signal's current status at delivery time and sends the expired keyboard instead
of Execute/Dismiss (Re-evaluate only if the status is `EXPIRED`).

**Duplicate rule and failed cards.** Scans skip a setup that was already carded for
the same contract, strategy and timeframe within `risk.deduplication_hours` (capped at
4h for 1h setups and 2h for 15m ones). A `FAILED` card does not count: the system, not
the operator, failed to place it, for example through a preflight refusal. The next
scan may therefore card the setup again if it is still valid. Pending, dismissed,
expired and executed cards still suppress repeats, and a failed card still spends
that session's card budget.

**Re-evaluate** (`reval_<signal_id>`, `copilot.reevaluate_signal`) runs a
single-symbol scan (`run_scan(symbols=[contract], budget=ScanBudget.NONE)`) and never
reuses the old signal's levels. The duplicate-signal rule is exempted only for the
expired card's exact setup — `(contract, strategy, timeframe, alpha_version)` — so
the contract's other setups stay deduplicated. It validates synchronously and answers
with a direct reply, not through the outbox:

- only an `EXPIRED` card can be re-evaluated ("Signal #N is STATUS; nothing to re-evaluate.");
- a `PENDING` or `SUBMITTING` card for the same contract refuses it ("A live card for
  SYMBOL already exists (#M).");
- outside the regular session it is refused with the next regular open ("Market closed; …"),
  and an unavailable session read with "⚠️ Market session unavailable; try again shortly.";
- once daemon shutdown has begun it is refused ("Daemon is shutting down; try again after
  restart.") before the claim, so the card can still be re-evaluated after the restart.

The last three refusals take no claim and are marked retryable. The Telegram handler
clears the card's keyboard while it handles the tap, then restores the **[🔄 Re-evaluate]**
button for them. A card the sweep struck at the close therefore keeps its button for a
tap after the next open. A claimed request, "already requested", a live card or a
non-`EXPIRED` status leave the keyboard cleared.

It then **claims** the card's single re-evaluation: under the workflow scope lock, in
one transaction, it checks for and inserts the `card_reevaluate/{signal_id}` domain
event (kind `card_reevaluate_requested`). A second request for the same card — a
Telegram callback redelivered at least once, a fast double tap or the CLI — finds the
claim and replies "Re-evaluation of #N already requested." without scheduling
anything. The claim, not the cleared button, is what makes at-least-once callback
delivery safe. The claim is permanent: a re-evaluation that found no setup, found the
scanner busy, or failed is not retried from the same card.

The scan runs as a background task, so the serialized Telegram handler replies at once
("🔄 Re-evaluating SYMBOL… a fresh card or a result message will follow."). It waits
at most 120 s (`REEVALUATE_SCAN_WAIT_SECONDS`) for a running scan. A fresh card is its
own result. "No valid setup … right now" (with the first runner-up reason), "No new
card for SYMBOL: a matching setup was already carded within the duplicate window." (a
setup the recent-duplicate rule suppressed, counted in the scan summary's `duplicates`),
"a scan is running" and a failure are queued as durable outbox messages, never sent
directly.
Daemon shutdown cancels and awaits every in-flight background operator scan —
re-evaluations and `/scan SYMBOL` share one task set
(`copilot.cancel_background_scans()`) — right after the shutdown event, before the
trade stream, Telegram and SDK clients close; a cancelled scan reports nothing.
The Telegram poller stops last, so a request that arrives after the shutdown event
would start a task nothing cancels or awaits. Re-evaluate and `/scan SYMBOL` therefore
refuse once the event is set ("Daemon is shutting down; try again after restart.").

**`/scan SYMBOL` (September 24).** Telegram `/scan` with no argument is unchanged (the
full-universe summary). With exactly one argument (upper-cased; more than one replies
`Usage: /scan or /scan SYMBOL`) it calls `copilot.request_symbol_scan`. Like Re-evaluate,
the serialized Telegram handler runs only cheap checks and replies at once ("🔍 Scanning
SYMBOL… a card or a result message will follow."); everything else, including the
unconfigured-symbol validation below, runs in one background task with the same task
set, 120 s scan lock wait and durable outbox result messages. It logs
`operator_symbol_scan` with `symbol` and `configured`. Direct-reply refusals:

- daemon shutdown already begun ("Daemon is shutting down; try again after restart.");
- a scan of the same name already in flight ("A scan of SYMBOL is already running.");
- a `PENDING`/`SUBMITTING` card for the name ("A live card for SYMBOL already exists (#N).");
- an unconfigured symbol while `universe.dynamic.enabled` is false ("Cannot scan
  SYMBOL: unconfigured symbols need the dynamic universe, which is disabled.");
- the name's regular session closed ("Market closed; " plus the next regular open), or
  the session read unavailable/timed out (15 s, `TAP_CHECK_TIMEOUT_SECONDS`).

A **configured contract** matches by its `contracts:` key, with or without a futures
`/` (`MES` finds `/MES`). It runs `run_scan(symbols=[contract], budget=NONE)` with **no**
duplicate exemption, so a setup carded within the duplicate window is not re-carded
(the result says "No new card for SYMBOL: a matching setup was already carded within
the duplicate window.");
the NONE budget skips the per-scan, per-session and per-group card caps.

An **unconfigured symbol** is validated in the background task with the
dynamic-universe machinery, never a second rule set. A refusal arrives as an outbox
message ("Cannot scan SYMBOL: …"):

- **Asset filters.** One `ScreenerEntry(source="operator")` runs through
  `select_dynamic` against the Alpaca asset list (the per-date cache; 60 s bound).
  A rejected name is refused with its reason ("…leveraged/inverse fund.", "…not an
  active Alpaca US equity.", "…warrant/right/unit or volatility/option-income
  product.", "…not listed on a major US exchange.", "…not a plain US equity symbol
  (1-5 letters).", and so on). A missing dynamic source or a failed or timed-out asset
  read refuses "asset lookup unavailable; try again shortly."
- **Liquidity reference.** The scan fetches only this one name, so it cannot measure
  the static percentile itself. It uses the newest `dynamic_universe_built` event whose
  `threshold` is non-null, whose `feed` equals `market_data.alpaca_feed` and whose
  `built_at` is within the last 7 days (the age check binds; the reader looks in the
  `scan/<date>` streams of today and the seven previous New York dates, the only ones
  that can hold such an event). Its `reference` block is rebuilt as the
  `StaticReference` for the unchanged `liquidity_gate` (the current
  `min_median_dollar_volume` floor still applies). Without one the request is refused:
  "no recent liquidity reference (the scheduled suggestion scan records one); try after
  the next suggestion scan." A failing journal read reports "Scan of SYMBOL failed; see logs."
- **Scan.** `run_scan(symbols=[SYMBOL], budget=NONE, operator_dynamic=…)` adds the
  synthetic contract as a dynamic name: the fetch and liquidity gating of
  `_gate_dynamic_members` with the journaled reference (median dollar volume over at
  least 20 completed daily bars), native strategies only, no alpha shadow observation,
  `dynamic: true` and `dynamic_source: "operator"` in provenance. It writes no
  `dynamic_universe_built` event. A gated name reports "SYMBOL was not scanned: below the
  liquidity threshold." (or "fewer than 20 completed daily bars", "market data
  unavailable", "below the minimum price").
- **No bar-coverage gate.** A single-name scan fetches no `coverage_reference_symbol`,
  so, exactly as for Re-evaluate, the hourly bar-coverage gate is skipped (the summary's
  coverage note says so and `coverage_gate_skipped` is logged). A scheduled suggestion
  scan would still apply it to the same name.
- **Dynamic cap.** Unlike a configured contract, the operator name still shares the
  one-dynamic-card-per-session cap: under NONE only the `dynamic` group is counted
  (from today's recorded signals) and enforced, so a dynamic card already sent today
  leaves "correlation group already has a card this session" as the result.

Halt, macro lockout, the earnings blackout and every evaluator gate apply unchanged
inside `run_scan`. The scan itself is not limited by the per-scan or per-session
budget, but a card it records counts toward today's derived session budget, which
later scheduled suggestion scans see, and, for an unconfigured name, takes the day's
dynamic slot — exactly like a Re-evaluate card.

**Card rendering.** Every card that carries a `valid_until` (recorded in
`decision_provenance` when the contract is in RTH at scan/tap time) shows
"• **Valid until:** HH:MM NY" under the Earnings line, in both the Telegram and
terminal cards. A `REPRICE` replacement additionally prefixes its title with
"🔄 UPDATED CARD (re-priced from #N, first issued HH:MM NY)"; `first_issued_at` is
carried across a whole chain of re-prices, so a twice-repriced card still cites the
original issue time. A legacy card without `valid_until` expires on the New York
date change instead.

**Evidence.** Every assessed tap appends one `card_tap_assessed` domain event to the
`card/{signal_id}` stream, written after the outcome's state transition: outcome,
tap latency in seconds, price, `r_consumed`, reason, `applied` (whether the transition
took effect: handed to authorization, replacement recorded, or card expired; false
when a conditional update lost a race), `new_signal_id` for a replacement and
`policy_locked` for a versioned alpha card. Retryable refusals are journaled too, with
outcome `unavailable` and the failure reason (price unavailable, checks timed out,
or the failing read's exception type), so read-failure rates are measurable. Taps
refused before any assessment — trading halt, unknown signal, a card that is no
longer `PENDING`, or an invalid tier quantity — are not journaled. A replacement signal's `decision_provenance` additionally records
`reprices` (the old signal id), `first_issued_at`, `tap_latency_seconds` and
`r_consumed`. This is the evidence for how late the operator actually acts on a
card, and whether a late-read card still works; it is a `domain_events` row
(stream `card/{signal_id}`, kind `card_tap_assessed`) like other workflow evidence,
with no dedicated CLI view yet.

**Deploy note.** Outbox `SIGNAL` rows written by this release carry `valid_until` and,
for replacements, `reprices` and `first_issued_at`. An older daemon's
`send_signal_alert` rejects those arguments, so rolling the daemon back below this
release dead-letters such rows. `CARD_EXPIRED` rows (kind `card_expired`, the
session-close sweep's strikes, including the first sweep right after deploy) are unknown
to earlier revisions: `NotificationKind("card_expired")` raises, so the row retries to
DEAD, `delivery` readiness turns false and `doctor --monitor` raises an incident. The card
itself is already `EXPIRED` and taps fail closed. Drain the outbox (`copilot db outbox`
shows nothing queued) before a rollback.

**Budget.** A `REPRICE` replacement does not spend a fresh session-card slot: the
budget counts signals whose provenance lacks `reprices`. Re-evaluate and `/scan SYMBOL`
use the `NONE` budget, since they are explicit operator requests: they need no budget
to issue a card, but the card they record still counts toward that session's budget
for later scheduled scans. `EXPIRED` signals still
count toward the session budget — the budget remains "cards sent today", not
"cards still valid".

**Disabling the cron scans** is a config edit: `scheduler.suggestion_scan_times_et: []`
registers no cron job, so neither an automatic suggestion scan nor the end-of-session
digest runs (`Suggestion scans disabled (no times configured)` at startup). Manual
`copilot scan` and Telegram `/scan` are unaffected. Like every config change it takes
effect only on daemon restart.

A scan whose selection was non-empty but which scanned nothing (every name failed to
fetch, came back with empty frames, or was excluded by the coverage gate) records
**degraded** `scan` readiness with a `0 of N instruments scanned` detail rather than a
healthy observation — a silent whole-universe data failure must be visible in
`/readyz` and `doctor`, not only in the digest.

`scan:` keys and their shipped defaults:

| Key | Default | Meaning |
| --- | ---: | --- |
| `max_cards_per_scan` | 1 | Cards one scheduled scan may send. |
| `max_cards_per_session` | 2 | Cards per New York trading day, derived from recorded signals since New York midnight (this environment and execution mode, excluding quarantined rows). |
| `max_cards_per_group_per_session` | 1 | Cards per correlation group per session; universe sectors are merged into `portfolio.correlation_groups` at load. |
| `max_llm_evaluations_per_scan` | 4 | LLM re-evaluations per scan; spent only when the LLM is in use, so a lower-ranked candidate can replace an LLM rejection. |
| `min_bar_coverage` | 0.8 | Fraction of the reference's active regular-session hourly bars (New York 09:00–15:00 buckets) a name needs to be scanned by strategies. |
| `coverage_sessions` | 10 | Sessions of hourly bars the coverage gate counts. |
| `coverage_reference_symbol` | `SPY` | Reference name; if it is unavailable the gate is skipped and the summary says so. |
| `shadow_ranker_artifact` | unset | Path to a frozen setup-study `ranker.json`, scored in shadow only. A relative path is resolved against the repository root at config load, regardless of the process's working directory. Every scheduled suggestion scan's ranked candidates get a `setup_features_v2` vector, recorded in sent-card provenance (`shadow_ranker`) and in one `scan_candidates_ranked` journal event per scan (`scope=universe`, `trigger=suggestion_scan`); the score is recorded only when the artifact's sha256 and `features_version` verify. It never changes ranking, the budget or any card. |

### Shadow card ranker

`scan.shadow_ranker_artifact` scores every ranked candidate in shadow only, whether or
not it is configured. Live ranking, the per-scan/per-session/per-group card budgets
and every card sent are governed by `setup_quality` alone; the shadow block never
reorders a scan, blocks or replaces a card, and earns no shadow, holdout or promotion
credit by existing.

Computing and journaling shadow evidence is gated by an explicit `shadow_evidence`
keyword on `TradingCopilot.run_scan`, set to `True` only by the scheduled
suggestion-scan job (`make_suggestion_scan`) -- never inferred from a scan's shape.
The daemon's 4-hourly swing scan, the intraday `non_universe_contracts` job, and an
unrestricted or symbol-restricted `copilot scan`/Telegram `/scan` all leave it `False`
and so neither compute nor journal shadow evidence, even though the swing scan and an
unrestricted manual scan otherwise share the suggestion scan's shape (no symbols, no
timeframe). A symbol- or timeframe-restricted scan is additionally guarded even if a
caller mistakenly passes `shadow_evidence=True`, since only the unrestricted universe
matches the setup study's own population.

What is recorded, and where:

- every sent card from a suggestion scan carries a `shadow_ranker` provenance block
  (`features_version` `setup_features_v2`, the feature vector, `score` when a verified
  artifact is configured, and the artifact's `ranker_sha`);
- one `scan_candidates_ranked` domain event per suggestion scan (`EventKind` in
  `agentic_trader/execution/durable.py`), journaled under stream `scan/{et_date}`
  with `"scope": "universe"` and `"trigger": "suggestion_scan"`, readable with
  `copilot db events --stream scan/<et-date>`. It carries every ranked candidate that
  scan considered, sent or runner-up, with its entry/stop/target, `setup_quality`,
  `rank`, `outcome` (`"sent"` or the reason it was skipped) and shadow block.

A `live_cross_section` failure because the universe has no completed daily session yet
(every symbol's daily frame empty or too short) is an anticipated, recoverable gap: it
is logged once at WARNING (`shadow_ranker_skipped`) with no traceback, distinct from an
unexpected exception (`shadow_ranker_failed`, ERROR with traceback). Either way every
candidate's shadow block is recorded as `None` and the scan itself is unaffected.

`copilot cards outcomes [--days 30]` is a read-only report over those events (filtered
to `scope=universe`; no other scope is ever journaled today). For each journaled
candidate it fetches 1-hour bars from the decision time to now (raw adjustment, the
configured live feed, paced with the same sliding-window `RequestPacer` the setup
runner uses) and labels the realized bracket outcome with the same `label_bracket` the
setup-outcome study uses (`DEFAULT_MAX_HOLD_SESSIONS=20`,
`DEFAULT_COST_BPS_PER_SIDE=5.0` in `agentic_trader/research/setups/outcomes.py`,
mirroring `config/research/setup-outcomes-v1.json`; the labeler itself is
`agentic_trader/research/setups/labels.py`). It prints counts by outcome (sent versus
runner-up), with rows the labeler cannot yet resolve counted separately: IMMATURE (not
enough elapsed bars yet) and, distinctly, `FETCH_FAILED` (the bar fetch itself raised,
with its reason recorded and summarized by count); each group's base rates and mean
cost-adjusted R; and, per scan, the top-1/top-2 mean cost-adjusted R a picker following
`setup_quality`, the shadow `score` (when recorded) and a random pick would each have
realized, so an operator can see whether either scorer would have out-selected chance.
It sends no orders or Telegram messages and writes nothing back to the database.

Switching live ranking away from `setup_quality` to a shadow score is an operator
decision, not something this report or the shadow block can do by itself. It needs a
frozen study holdout result plus enough measured evidence from this report and `alpha
forward`, applied through an explicit config change and the documented restart
procedure — exactly like promoting any other alpha.

Fetch scaling lives under `market_data:`: `scan_concurrency` (8) bounds the copilot's
read pool and `max_requests_per_minute` (150) paces provider reads below the 200/minute
IEX limit.

Paper desk caps that ship with this universe: `portfolio.max_concurrent_positions: 8`,
`portfolio.max_correlated_positions: 2` and `sizing.max_trade_notional_cap: 7500.0`.
The total notional ceiling, asset-class caps and the 2% aggregate stop-risk budget are
unchanged. A `PENDING` card reserves no capacity; these caps are enforced at Execute.

**Sector correlation groups now bound the book per sector.** Every universe entry
carries a sector, and those sectors are merged into `portfolio.correlation_groups` at
load, so each group is a sector rather than a handful of index proxies. Entry
admission counts *any* open position in the group (direction-agnostic) against
`max_correlated_positions`, so the paper cap of 2 means at most two open positions per
sector; with `max_concurrent_positions: 8` that spreads a full book across at least
four sectors. The evaluator's own correlation check, which gates the suggestion card,
counts only same-direction positions (an opposite-direction hedge is allowed there by
design and by test), so a card can still be approved and then refused at Execute with
`Configured correlated-position limit reached` — the cap of 2 is what keeps that rare.

**Rollback** is a config edit: `universe: {}` restores the 13 explicitly configured
contracts, and `non_universe_contracts` then equals the whole contract set.

**Config changes take effect only on daemon restart**, through the documented
[controlled maintenance and restart](#controlled-maintenance-and-restart) procedure.

**Watch list for the first two sessions after deployment.** Read the digest (and
`copilot db outbox`) and check, before changing `max_cards_per_session` or the
universe:

- scan duration — expect roughly two to three minutes per scan for ~160 names at
  two reads each; a much longer run means the pacer or the read pool is the bottleneck;
- fetch failures — expect a handful of thin research-cohort names; a broad failure
  set means entitlement or throttling, not data quality;
- coverage exclusions — thin names correctly dropped from strategy scanning;
  alpha-shadow observation still records them;
- cards sent versus runners-up — whether the budget or the gates are binding;
- instruments scanned versus selected — the digest's `N scanned` and the `scan`
  readiness detail; `0 of N instruments scanned` is a data failure, not a quiet market.

`market_data.max_requests_per_minute` paces **the scan fetcher only**. Other workers
share the same provider feed — the prospective daily-panel worker, the forward
observers and the one-minute position monitor — so the real per-feed request rate is
the sum of all of them, and the 150/minute setting is headroom under the 200/minute
IEX limit rather than a process-wide ceiling. A process-wide per-feed pacer is a
[roadmap follow-up](alpha-roadmap.md); until then, treat a burst of provider
throttling during a scan as evidence about the *combined* rate.

## Configuration and state

Production `load_config()` merges YAML, `.envrc` values and environment overrides,
without modifying the process environment. Explicit environment values (even empty
ones) win. `COPILOT_ENV_FILE=''` disables dotenv. `COPILOT_CONFIG` selects a YAML.

Database precedence: `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML →
production default. `--db-path` accepts a URL or SQLite path; `--db-name` explicitly
selects a SQLite sandbox. There is no automatic SQLite failover. `data/signals.db`
is historical and must not be mistaken for the current PostgreSQL database.

Head migration is `008_alpha_pipeline`. `signals`, `system_state` and
`audit_events`, `close_requests`, `work_items`, `domain_events`, `order_projections`,
`workflow_locks`, `activity_projections`, `ledger_checkpoints`, `incident_projections` and `alpha_projections` hold trading and
operational state. Construction currently checks
migrations, even for informational copilot commands. Back up PostgreSQL before
schema or historical repairs. Do not use `db clear` to fix contamination: quarantine
preserves original evidence and removes rows from operational queries.

## Broker accounting and reports

`copilot positions` and `/positions` use one Alpaca account snapshot for quantity,
average entry, current price and unrealized P&L. Source/retrieval time and tracking
mismatches are shown and audited. Broker-only positions are displayed once. A
failed snapshot reports an error; another feed or zero is not substituted.

`/perf` and `copilot perf` separate reconciled account performance from tracked
full-close statistics. The account section includes partial/external fills and
signed fees/income, uses broker cost basis and withholds realized totals on
cash/inventory mismatch, unsupported activity or stale/failed imports. Telegram
uses the timestamped worker cache; CLI `perf` refreshes it read-only first. The
60-second accounting worker has a separate current-run readiness check. This is
all available history, not account-day return or tax accounting. See the
[ledger contracts and recovery commands](account-ledger.md).

Tracked metrics still require exact entry/exit IDs, actual full fill prices and
chronological order; quarantined/unverified signals are excluded. Account-level
partial realization does not assign partial exits to a signal by symbol.

Alpaca manual close submits an order and waits for confirmed fill accounting.
A requested `exit_price` cannot become brokerage profit. Panic persists the halt
first and requests closes; inspect broker positions for pending or failed exits.

## Safe verification

```bash
launchctl list | rg 'com\.agentictrader\.'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/healthz
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/metrics
uv run copilot db current
uv run copilot db audit --limit 20
uv run copilot positions
```

`/healthz` proves liveness; `/readyz` and `copilot doctor --readiness` assess passive
current-run freshness. `copilot doctor` and `launchd.sh health` perform active CLI
diagnostics, including migrations and an LLM request. HTTP `/healthcheck` is removed. A separate
`copilot metrics` instance does not contain the running daemon's metrics.

A completed session or macro policy check records scan progress even when it
pauses entry alerts. Read the scan detail to distinguish a policy-gated check from
instrument evaluation; this does not establish price/calendar freshness or waive
entry restrictions. Failed macro checks and dry scans cannot record healthy scan
progress. Reconciliation, broker stream and Telegram freshness remain independent.

```bash
uv run copilot scan --dry-run --no-llm --symbols IWM
uv run copilot test-alert
```

A dry scan has an empty temporary database and simulated portfolio, no Telegram,
no broker connection/execution and no monitoring. Market-data calls still occur.
`test-alert` previews locally. Explicit `test-alert --send` requires a different
`TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` and sends a labeled,
non-actionable message without persistence. Never test order buttons using real
production signals merely to check connectivity.

## Controlled maintenance and restart

1. Finish isolated tests, type/lint checks and `uv run pre-commit run --all-files`.
   Commit/merge in an isolated worktree so the daemon's startup revision is identifiable.
   Supervisor scripts execute directly from the installed checkout every minute;
   pause services before pulling the merged code into that checkout.
2. For a brief ordinary restart use `./scripts/launchd.sh restart`. For a maintenance
   pause, unload the watchdog first, then the daemon. A plain `stop` is not durable
   while the watchdog is loaded. Preserve the installed plist files.
3. Update the installed checkout to the reviewed merged revision and back up PostgreSQL.
   Apply migrations/validated data repair. Keep original rows/snapshots; run the
   incident repair tool in preview mode before `--apply`.
4. Reload the daemon and then watchdog. The installed launch agents reside under
   `~/Library/LaunchAgents/`; use `launchctl bootout/bootstrap gui/<uid>` with their
   exact labels/plist paths. Editing `scripts/launchd.sh` does not regenerate plists.
5. Verify the new PID and `runtime_started` audit event match the committed revision;
   verify `/healthz`, recent reconciliation, Alpaca paper account access, Telegram
   bot identity/command registration, and the shared position report.

Notifications enqueued by this revision carry a `probe_risk_cap` argument, so
rolling BACK to an earlier revision with such a notice still queued would
dead-letter it; drain or inspect `copilot db outbox` before a rollback.

Deploying the native-daily entry-expiry change (`--entry-policy` on `alpha mine`)
is a case of step 4's plist rule: editing `scripts/launchd.sh` does not regenerate
the installed plist. After updating the installed checkout, run
`./scripts/launchd.sh install-miner` and verify that
`~/Library/LaunchAgents/com.agentictrader.alphaminer.plist` contains
`--entry-policy gtc`; otherwise the scheduled ETF32 benchmark would silently adopt
the new `session` default for daily mining instead of keeping its pinned GTC
identity. See [trade lifetimes](alpha-trade-lifetimes.md#native-daily-one-session-entries-semantics-version-5).

Do not restore old code against a newer schema without checking compatibility.
Quarantined records can be reviewed/restored with an audited migration; they were
not deleted. Keep broker-held protective orders in place during a daemon restart.

## Logs, audit and monitoring

- `data/copilot.err.log`: JSON application logs with UTC time and process run ID.
- `data/copilot.log`: printed cards; `data/watchdog.log`: process checks and readiness reports; `.err.log`: probe errors.
- `data/alphaminer.log` and `.err.log`: scheduled research output.
- `audit_events`: signal creation, exact fill changes, stream and REST evidence,
  valuations, close submissions/completions, notification message IDs/results,
  quarantine/restoration, and source revision at daemon startup.
- Prometheus includes active positions, fills, entry-fill synchronization and
  existing scan/data metrics. Scrape the daemon at `:9108/metrics`.

Alembic preserves logging configuration; Telegram token URLs are redacted and
HTTP transport info logging is suppressed. Never commit credentials, raw logs,
DB files or private incident snapshots. Operational audit is complemented by the schema 005 event journal and transactional
notification outbox. See [durable workflows](durable-execution.md) for recovery,
readiness, dead-letter requeue and deployment checks.

## Alternative deployment

Docker Compose defines PostgreSQL and copilot services with persistent PostgreSQL
storage and healthchecks. Configure credentials and `DATABASE_URL` explicitly;
review port exposure and default example passwords before using it on a server.
Do not start Compose on this desk alongside launchd.

```bash
docker compose up -d --build
docker compose logs -f copilot
docker compose exec copilot copilot db current
```

For systemd or another supervisor, run the same `copilot daemon` command with one
process, an explicit working directory/environment, persistent DB storage and restart
policy. Review data/Telegram freshness in addition to process health.

## Polling freshness and event-loop stalls

Run `uv run python scripts/verify_runtime.py` after restart. Besides broker/report
agreement, it checks the running daemon's successful-poll timestamp and poll-health
gauge; a separate `getMe` request alone cannot establish poller health.

Telegram request retries and timeouts live under `telegram` in YAML. Poll errors
are recovered by the SDK and audited with recovery/periodic success. Every handler
records update ID and lifecycle; request audits retain delivery outcome/message ID.
No handler or trade action is automatically replayed. A lost Telegram HTTP response
may produce a duplicate message on retry. Slow interactive commands still queue
later commands because handler execution remains serialized.

The generic event-loop monitor exports lag and records `event_loop_stall` above
`telemetry.event_loop_warning_seconds` (default 2s), sampled every
`telemetry.event_loop_sample_seconds` (default 1s). Consult those records alongside
poll/command audits when a command appears delayed. Market-data, simulation quote,
correlation and research computation boundaries now offload blocking work.

## Operator report checks after deployment

After restart, verify the registered menu contains `/macro` and no `/regime`.
`/macro` includes combined volatility/macro filters and published-data dates;
`/explain_macro` is educational. `/gex` discloses missing-chain fields and requires
a real spot quote. `/positions`, `/perf` and conversational position queries share
broker-backed valuation. Backtest defaults come from loaded configuration.

`scripts/verify_runtime.py` checks the clean source revision against the running
startup audit, Alpaca paper access and exact position parity, Telegram identity,
menu and actual daemon poll freshness. Account reconciliation is read through
`AccountLedgerService.current()` using the daemon's fresh persisted report; the
verifier never starts a competing activity import. Missing, stale, failed or
unreconciled evidence fails verification. It sends no messages or orders. `/healthz`
is liveness; successful read checks do not exercise order submission.

### Scheduler startup timing

Telegram/metrics initialize before immediate jobs receive their first deadline.
`scheduler.misfire_grace_seconds` defaults to 60; jobs coalesce delayed executions
and allow one concurrent instance per job. This prevents initialization latency
from silently skipping the first scan/monitor run. The deployment log review
reproduced a 1.5-second delay exceeding APScheduler's former one-second default.

Old Telegram messages can retain removed callback names. Use `/help` or the current
command menu for `/macro`; historical `/regime` buttons are no longer active.

## Coordinated close and flatten

`/close <id>` / `copilot close <id>` close one tracked position; the CLI no longer
requires a simulated price. `/flatten` / `copilot flatten` preview all open Alpaca
positions. Submit with `/flatten confirm` or `copilot flatten --confirm`.
`copilot flatten --dry-run` and `copilot close <id> --dry-run` are read-only, including
when `--confirm` is also supplied. Preview is informational; confirmation reads a
fresh snapshot. No Telegram test message or order is needed to validate registration.

Both operations preserve the existing trading halt. Flatten operates on positions
present in the account snapshot, including broker-only positions; it cancels orders
on those symbols only. Unfilled entries in other symbols remain active. An account
snapshot can change during execution: inspect each result and `/positions` afterward.
Tracking ambiguity, mismatched cost basis/quantity/direction, partial entries/exits,
working market orders and unconfirmed cancellations block a new close. A failure
on one symbol does not prevent attempts on the other symbols. Untracked closes do
not invent tracked entries or trade statistics. Their actual executions are included
in account-level `/perf` reconciliation.

For ordinary equity close/flatten requests the broker clock must indicate regular
trading hours before any cancellation; after-hours requests retain protection.
Direct replies and durable result notices include the same refusal reason, the
broker's next regular open in UTC, and a prompt to retry during regular hours.
No close is queued by an ordinary closed-session refusal; the trading halt is unchanged.
Panic explicitly retains its emergency policy: cancel orders, permit market exits
queued for the next session, and persist the halt. A queued order is not a confirmed
liquidation.

Alpaca OPEN queries omit held bracket stops, even with nested results. The adapter
expands exact entry/order groups and confirms every active leg. For broker-only
brackets it resolves the working exit ID against bounded nested history; missing
or ambiguous group identity refuses the close before cancellation. The
workflow checks the clock again before submission; if the market closes or the
broker fails after cancellation, protective orders may already be removed. The
response explicitly reports that condition: inspect the account and restore
protection or close through the broker as appropriate. No automatic restoration
or blind close retry is attempted after an uncertain mutation.
Cancellation attempts are audited before DELETE, so even a lost cancellation
acknowledgement carries this protection warning. Only a pre-mutation refusal may
say existing protective orders were left unchanged by this request. External SDK
errors expose their type/HTTP status, not raw broker response text.

`close_requests` persists a UUID client order ID before broker mutations. A partial
unique index allows only one active request per environment/account mode/symbol,
including requests from another CLI process. Statuses are `claimed`, `submitted`,
`unknown`, `completed`, `failed`; terminal history is retained. Minute monitoring
and subsequent commands recover exact client IDs and attach broker exit IDs to
tracked signals. Confirmed full fills drive normal trade accounting/notifications.
Confirmed unfilled cancellation/rejection permits a later explicit request;
partial or ambiguous outcomes retain exclusivity. A crash before submission can
leave a `claimed` request without a matching order: it is intentionally not expired
or replayed automatically. Review broker orders and the audit trail before an
operator repairs that request; elapsed time alone does not prove no order exists.

Audit events `close_request`, `close_broker_step`, and `flatten` record intent,
request/order IDs, cancellation confirmation, submission, recovery and per-symbol
failures. They complement `exit_order_submitted`, reconciliation and Telegram
handler/delivery audits. `execution.close_cancel_timeout_seconds` (default 10) and
`execution.close_cancel_poll_seconds` (default 0.25) control cancellation polling;
SDK calls run off the event loop. These are not guarantees of broker HTTP latency.

### September 17 close-message investigation

The requests at 13:06:55 and 13:07:17 UTC were refused before any cancellation:
Alpaca's clock reported the next regular open at 13:30 UTC. Both Telegram handlers
completed and their direct replies and outbox messages received HTTP 200 responses.
The durable result notice had omitted the useful market-closed explanation and
showed only a request ID and `failed`; it now includes the symbol and full safe
explanation. Private audit evidence is retained outside Git. Loopback SDK and
isolated database regressions cover both commands, post-cancellation session closure,
lost cancellation acknowledgements and broker-error redaction; these test results
are separate from subsequent deployment verification.

Deployment checks must verify `/flatten` in default, private-chat and operator-chat
command scopes, the chat menu button, current daemon revision and poll freshness.
`scripts/verify_runtime.py` performs these checks without messages or orders.


### Broker transport and stop replacement

Broker HTTP calls have a per-attempt timeout (`execution.broker_request_timeout_seconds`,
default 10 seconds). GET retains the SDK retry policy; POST/PATCH/DELETE are not
replayed automatically. An uncertain entry records `entry_submission_unknown`,
leaves its signal `SUBMITTING`, and halts new entries. Inspect `entry_submission`
audit for its client ID, retrieve that exact Alpaca order and reconcile its state
before resuming. The entry worker and monitoring perform exact client-ID lookup recovery; neither
404 nor elapsed time permits resubmission. Do not reset a claim after a timeout.

Stop changes resolve the exact bracket and replacement chain, then read back the
working price before updating storage or sending a ratchet alert. A pending/failed
replacement preserves the previous local stop and original thesis/risk. Review
`stop_replacement` request/result events and `stop_updated` for acknowledged changes.
`execution.stop_replace_timeout_seconds` defaults to 10 seconds. Native bracket
fills and minute reconciliation continue independently of Telegram response delivery.

See [Alpaca contract review and integration coverage](alpaca-integration-review.md).

### Repeated trailing-stop notices

One position may generate many valid notices as its stop tightens. The monitor
runs every minute and on broker-stream wakeups; `trail_step_ticks` controls the
minimum proposed stop improvement. A short stop ratchets downward, a long stop
upward. The current non-alpha implementation uses recorded initial risk distance
for its trigger/trail calculation; the named ATR/high-water policy mismatch remains
in the [architecture review](architecture-review.md#remaining-findings-ranked).

The operator explicitly prefers **every confirmed stop-change notice**. Keep that
cadence; do not raise the broker ratchet threshold merely to quiet Telegram.
For suspected duplicates, correlate `stop_replacement` request/result, `stop_updated`,
the notification work item and `telegram_request.outbox_id`. An outbox attempt alone
does not establish one Telegram HTTP attempt; inspect transport retries/message IDs.

The September 16 IWM investigation found 12 strictly tightening, broker-confirmed
changes and 12 successful first-attempt Telegram sends, each with a distinct message
ID. Exact Alpaca bracket retrieval agreed with the current stored stop. No duplicate
delivery or reset loop was found, and no protective order or notification policy was
changed for the investigation. Raw snapshots remain private. A stop price is a trigger,
not a guaranteed realized gain; broker fills remain authoritative.

## Current schema and monitoring verification

Apply head migration `008_alpha_pipeline` after the backup and controlled
pause. Verify `/readyz` includes fresh successful accounting, reconciliation,
worker/delivery and Telegram observations. `db ledger` should show no issues and
zero quantity differences. The runtime verifier must match the clean source
revision and current run; keep its output private.

The watchdog now consumes passive readiness and persists debounced incidents,
including dead-letter failures, through the shared journal/outbox. Inspect
`copilot db incidents`, `copilot db outbox` and recent watchdog output. A running
unready process is alerted, not blindly restarted. Default startup/failure grace
is 120 seconds; recovery is 60 seconds. See [monitoring policy and recovery](operational-monitoring.md).

Only redundant successful blank-detail health observations older than 30 days are
compacted, in hourly batches of at most 1000. Failures, recovery boundaries, latest
observations, financial/order/incident events, audit traces, work records and dead
letters are retained. Preview with `db retention`; `--apply` compacts one batch.
Local files and durable financial/audit history still need capacity planning,
backups and a tested archive/log-rotation policy.


## Alpha pipeline deployment

Back up PostgreSQL, apply schema `008_alpha_pipeline`, and explicitly import
`config/promoted_alphas.yaml` using `copilot alpha import` once. This retains four
historical hypotheses in shadow, discarding their unverified promotion claims.
Existing positions remain monitored with their original protection. No new alpha
is activated by the migration/import. Confirm `alpha list`, `alpha status` and the
`alpha_registry` readiness component after the first scan. `/alphas` uses the same
registry. See [pipeline contracts](alpha-pipeline.md) before any qualification.

Regenerate only the miner plist with `./scripts/launchd.sh install-miner` to apply
its staggered schedule. This does not start another daemon or Telegram poller.
Private research artifacts live in `~/.local/state/agentic-trader/research` with
atomic writes and mode 0600. Retain them with the journal for reproducibility.

`alpha replay` is a read-only Alpaca data diagnostic, but intentionally writes its
attempt and inspected-period exclusion to the configured research journal before
provider access. It starts no daemon/poller and submits no orders or notifications.
Retain failed results as well as successful ones; no automatic retry, feed fallback
or promotion is performed. See [session replay](alpha-session-replay.md) for its
private artifact contract and remaining live-clock limitations.


### Prospective session data observer

The existing daemon now owns the [forward data observer](alpha-forward-observations.md).
Desk YAML enables SPY/15m on the configured stock feed, sampling around observed
session closes. It is independent of trading scans and does not emit signals or
routine Telegram messages. `alpha status` exposes capture results; `/readyz` includes
current-run `alpha_observer` progress. A healthy idle collector is not evidence of
complete prices. Check capture coverage/status and metrics separately. After a
post-close deployment, retain that no forward sample exists yet; do not backdate a
historical fetch to pass verification. No schema change or alpha activation is needed.

### Sleep and wake

launchd supervises process exits; it does not keep this Mac awake. During sleep,
scans, reconciliation, notifications and collection pause; dark wakes can generate
transport failures and scheduler misfires. After a full wake, inspect current-run
`/readyz`, watchdog progress and `scripts/verify_runtime.py`. The same healthy process
may resume and reconnect without a restart. Restart only if recovery fails, preserving
evidence and the single-poller rule. Broker-held protection remains at Alpaca while
the host sleeps, but local trailing/monitoring cannot run. Continuous paper operation
requires an awake host or an always-on deployment.

New session alpha versions use [explicit decision windows](alpha-session-decisions.md)
but remain blocked from activation and entry admission. The native scan schedule and
forward collector are unchanged; a healthy collector is not evidence of deployed
session strategies or of complete forward samples.

### Forecast research operations

`alpha benchmark` runs explicit-horizon forecast diagnostics against saved discovery
data. It charges trials and retains private artifacts through the shared journal;
it sends no orders or notifications and does not change the weekly miner cadence.
See [forecast benchmarks](alpha-forecast-benchmarks.md). Run research outside the
daemon; broader worker-resource isolation remains a roadmap item.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](alpha-forecast-policy.md).


### Durable candidate decisions

Use `uv run copilot alpha forward --days 7` (JSON) or Telegram `/alphas` for the
[read-only cohort report](alpha-forward-evidence.md). Missing/truncated history is
explicit; measured receipt lag includes configured delay and polling. `/readyz`
continues to measure worker freshness separately from successful data/scoring.

The daemon also owns the [receipt-aware diagnostic evaluator](alpha-session-decisions.md#durable-diagnostic-worker).
`alpha_pipeline.decisions` bounds its universe/history/work; `alpha_decisions` readiness
is separate from capture quality. Immutable claims/cursors/results use the existing
journal and private `forward-decisions` artifacts. A healthy worker with no eligible
version-3 candidates is idle, not evidence of a forward score. No promotion, orders,
qualified shadow credit or synthetic Telegram messages follow from these diagnostics.


The frozen ETF campaign designates six SPY/QQQ session hypotheses for forward diagnostics
alongside four historical shadows. Enrollment uses immutable registration and registry
CAS during controlled deployment; restart installs the new generation before readiness
is verified. All remain unqualified, and active-alpha count remains zero. Inspect the
[results and limits](alpha-session-campaign-2026-09-17.md); positive historical marks
are not Alpaca paper-account P&L.

### Timed alpha lifecycle operations

Only explicitly timed alpha positions participate in [trade lifetimes](alpha-trade-lifetimes.md); historical positions receive no inferred limits. Resting deadlines request one exact entry cancellation, then reconcile by GET. `entry_cancellation` readiness and `trader_entry_cancellations_unresolved` expose unresolved work. Entry admission remains blocked during cancellation even before an uncertainty halt. Partial fills, replacements or ambiguous outcomes require protection review and retain journal/outbox evidence; never manually replay the DELETE. `/resume` rechecks unresolved cancellation and timed-position evidence.

Holding expiry waits for a current eligible broker session and uses the existing close service with a stable ID. A failed attempt is retained for operator review, not retried every monitor cycle. General close claims/results now also create durable journal/outbox notifications; confirmed financial accounting remains separate. Monitor cadence and host availability bound response time: an elapsed lifetime is not a guaranteed fill time. Session-alpha promotion remains disabled.

Inspect durable cancellation evidence with `copilot db queue --kind entry_cancel` and `copilot db events --stream entry-cancel/COMMAND_ID`. These are read-only; unresolved intents have no resend operation.

The [continuous timed ETF campaign](alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The [persistent-book follow-up](alpha-persistent-book-2026-09-17.md) is also complete;
use the [current roadmap](alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities) for next work. [Automatic Alpaca bar evidence](market-data-evidence.md) now retains raw pages and normalization outcomes.

## Provider read and forecast hardening

See [forecast contract hardening](forecast-contract-hardening.md). Active `doctor` includes
a recent request against the exact configured Alpaca feed; HTTP permission and actual
bar freshness are distinct. Read fallbacks share bounded executor capacity and Alpaca
socket timeouts. Unfinished reads are never replayed by the fallback wrapper.

## Raw bar evidence operations

The daemon and research compositions now retain [Alpaca bar evidence](market-data-evidence.md)
in the private state directory. Budget failures reject the Alpaca acquisition and
retain a failure receipt; no evidence is automatically deleted. Monitor disk growth
and back up referenced pages with research journals. Capture success, worker readiness
and recent-feed entitlement remain separate checks.

### Market-data entitlement finding

The September 17 read-only [access probes](research-data-sources.md) confirmed
recent SIP is forbidden by subscription, with 199/200 requests still available.
Delayed SIP and current IEX succeeded. Historical research can proceed; do not
backdate delayed observations or silently switch a qualified alpha's feed.

Source-specific [volume calibration](alpha-volume-calibration.md) uses the shared
daily study harness with frozen training/forward intervals and exact feed identity.
`alpha volume-study` is research-only. `/alphas` now provides a compact explanation
and status summary; use `alpha forward` and `alpha list` for full evidence/definitions.

### Separate prospective IEX feed

Desk `alpha_pipeline.observations.feed` and `alpha_pipeline.decisions.feed` select
`alpaca:iex` for SPY/QQQ independently from the SIP trading data setting. The
[fixed IEX controls](alpha-iex-forward.md) use new identities; SIP failures remain
retained and are reported as an uncollected feed. Import is explicit after deployment.
Verify actual coverage and scores separately from worker readiness.

### Equity universe research command

`copilot alpha universe-snapshot config/research/prospective-equity-300-v2.json
--output NEW_PRIVATE_DIRECTORY` is an explicit metadata-only research operation. It
charges one attempt and retains failures; it does not add the selected symbols to
trading scans, launch a poller, send messages or fetch prices. The [September 17
capture](alpha-equity-universe.md#actual-metadata-capture--september-17-2026) selected
300 candidates. Keep source/artifact files private and bind later studies to the hash.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

`copilot alpha forecast-study PROTOCOL --selection COMPLETED_LIQUIDITY_DIRECTORY --output NEW_PRIVATE_DIRECTORY` runs the [screened-cohort development study](alpha-panel-forecasts.md). The frozen matrix is charged and every acquired member/date excluded before reads. Keep artifacts private; this command neither promotes alphas nor sends messages/orders. It does not start a daemon or poller. Existing output directories cannot be overwritten.

### Retained forecast comparisons

`alpha forecast-controls PROTOCOL --parent FORECAST_DIR --output NEW_DIR` is an explicit operator research command, not a daemon/poller. It reads verified local artifacts after durable trial reservation, excludes all inspected parent members, and writes private diagnostic evidence. It has no provider, broker or Telegram composition and grants no promotion authority. Keep the output directory outside Git; see the [research contract](alpha-forecast-controls.md).

## Account risk on deployment

The first reconciled checkpoint after enabling [account risk](account-ledger.md#cash-flow-adjusted-drawdown-and-entry-admission) establishes its dated baseline; historical peaks are not inferred. Verify `db ledger` risk availability and the passive runtime verifier after restart. Accounting readiness includes risk validity. Derived drawdown can block new entries without setting the emergency halt; `/resume` cannot waive it. Broker-held protection, closes and reconciliation continue. Preserve the journal when investigating corrections or transfer-classification failures.

### Broker capacity admission

[Entry capacity](entry-capacity.md) adds no migration or daemon. Configured planned
stop risk defaults to 2% of the lesser of mandate and observed equity, with the
drawdown multiplier; evidence defaults to a 30-second age from the first read.
Missing funding, current borrow status or exact working protection refuses new risk.
An unrelated order or partially reconciled fill requires review, not a blind retry.
Use retained entry result/submission evidence and the existing outbox to diagnose it.

Deployment verification checks passive readiness, current revision, broker positions,
protection and Telegram polling/menu registration. It does not submit a test trade.
A closed regular session prevents live entry preflight; loopback SDK and disposable
PostgreSQL tests separately exercise success, refusal and race behavior. Record these
limits independently of a healthy service or current market-data connection.


## Offline research isolation

`alpha power-study` is synthetic and artifact-only: it constructs no runtime DB,
provider, broker or notifier. `alpha factor-controls` reads verified retained data
and deliberately records its real research attempt in the configured alpha journal.
It reserves trials/exclusions before reading observations, while issuing no provider
requests, broker orders or Telegram messages. Neither command starts a daemon or
poller, qualifies an alpha or grants shadow credit. Run substantial studies in a
separate process with bounded numerical threads and monitor the existing daemon.

## Prospective native-daily research

The [daily panel collector](alpha-daily-panel.md) is an independently configured
worker in the existing daemon. `alpha_daily_panel` readiness tracks completed
worker checks (including idle); usable forecast/outcome counts are separate in
`alpha status` and `alpha forward`. It neither starts a Telegram poller nor submits
orders. Its protocol path and campaign identity are explicit and immutable after
enrollment. Day-close timing, expiry and private-artifact recovery are in the guide.

`comparison_protocol_paths` predeclares independent daily baseline portfolios using
that same worker and data capture. Each companion enrollment charges its declared
hypotheses once and becomes immutable; removing its file from configuration does
not withdraw the enrollment or abandon pending outcomes. Primary and baseline
results have separate counts in `alpha forward` and `/alphas`. Keep the parent
protocol fixed until a supported campaign retirement/draining workflow exists.
