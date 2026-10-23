# Insider watch cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly SEC Form 4 capture for cohort v3 feeds the frozen `insider` v1 cluster rule. The
10:35 suggestion scan sends at most 2 labelled, tap-only insider watch cards. Each one is journaled and
labelled forward.

**Architecture:**
- Introduce a `CatalogCardSource` seam and move PEAD onto it with no behaviour change.
- Add a live EDGAR parser with data-set parity, plus a capture worker modelled on corporate actions.
- Add an insider watch source on the seam, a watch admission check, a card renderer, an outcomes block
  and docs.

**Tech Stack:** Python 3.14, httpx, pydantic v2, SQLAlchemy async, python-telegram-bot, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-insider-watch-cards-design.md`. The branch is stacked on
`feature/insider-apriori-leg` (PR #131).

## Global Constraints

- Contract 1 (risk, admission and FIFO), Contract 6 (delivery) and Contract 8 (scan journal) hold.
- PEAD behaviour is byte-identical after the seam: the same candidates, provenance, payloads, journal
  events and budget decisions.
- The frozen `config/research/apriori/insider-v1.json` and `insider_events.py` behaviour are unchanged.
- No migration. The new event kind is a string enum value.
- `EDGAR_USER_AGENT` is read from the environment at the worker boundary. It never appears in logs,
  journal events, artifacts, Telegram or repo files.
- Watch cards are never auto-entered, never enrolled as probes, and never earn registry, shadow or
  promotion credit.
- Telegram text and journal reasons carry type names only.
- Tests are synthetic, apart from the checked-in EDGAR fixtures. The network guard applies.
  PostgreSQL tests use only a `test_*` database.
- ruff strict (no C408, no PLC0415), mypy clean, pre-commit. Implementers stage only.

## Review Focus

1. **The 10:35 scan runs before the night's capture succeeded.** It must journal `unavailable` and
   send no watch card. It must never decide on a partial history. Test in Task 4.
2. **A cluster whose second filing is a Form 4/A amendment.** v1 counts originals only, so it does not
   fire. Test in Task 2's parser and Task 4.
3. **A cohort name already open as a native or PEAD position.** It is refused by the existing one-per-
   symbol rules with a recorded reason, not silently skipped. Test in Task 4.
4. **The PEAD drift card on a day with an insider watch card.** Both budgets are independent, and
   neither consumes the other's slot or the native slots. Test in Task 4.
5. **A tap on yesterday's watch card.** It is refused as `session_close` validity with a clear reason.
   Test in Task 4.

---

### Task 1: `CatalogCardSource` seam, PEAD moved onto it (no behaviour change)

**Files:**
- Create: `agentic_trader/agent/catalog_sources.py`
- Modify:
  - `agentic_trader/agent/copilot.py`: every `PEAD_STRATEGY_ID` / `pead_event` / `drift_card_facts` /
    `DriftPreparation` / `_journal_drift` special case listed in the spec;
  - `agentic_trader/screeners/earnings_drift.py`;
  - `agentic_trader/notifier/telegram_bot.py` (a generic `card_facts` keyword).
- Test: `tests/agent/test_catalog_sources.py` (the parity suite), plus the existing PEAD tests, unchanged

**Interfaces:**
- Produces `class CatalogCardSource(Protocol)`. Its members:
  - `source_id: str`;
  - `kind: Literal["probe", "watch"]`;
  - `strategy_id: str`;
  - `provenance_key: str`;
  - `journal_kind: EventKind`;
  - `max_cards_per_session(config) -> int`;
  - `max_open_positions(config) -> int`;
  - `async prepare(now, owned) -> Preparation`;
  - `candidates(preparation, data) -> list[ScreenerCandidate]`;
  - `card_facts(candidate) -> dict`;
  - `async journal(store, decision) -> None`.
- `PeadDriftSource` implements it.
- Copilot holds `self.catalog_sources: list[CatalogCardSource]`. The scan loops over the sources
  for:
  - preparation and lateness;
  - the open-position caps;
  - candidates after the native ranking;
  - per-source budgets, counted by `strategy_id`, with native counting excluding every catalog
    source's `strategy_id`;
  - recording (`decision_provenance[source.provenance_key]`, `card_facts`);
  - journaling.
- The notification payload's `drift` key keeps its name for PEAD, and a generic `card_facts` key
  carries the facts for a watch card. `send_signal_alert` accepts both.

- [ ] **Step 1: Write the parity tests first.** Run them against the unchanged code to capture today's
  behaviour, then keep them as the regression suite. They cover:
  - candidates;
  - provenance;
  - payload;
  - the journal event;
  - the budget refusal at the cap;
  - lateness skip;
  - halted, lockout and regime-unavailable skips;
  - unavailable.
- [ ] **Step 2: Refactor.**
- [ ] **Step 3: Run `tests/agent`, `tests/research/apriori`, `tests/notifier`, `tests/cli -k cards`,
  the full suite, and the PostgreSQL integration suite. Stage.**

### Task 2: Live Form 4 parser with data-set parity

**Files:**
- Create: `agentic_trader/data/edgar_form4.py`
- Create: `tests/fixtures/edgar/`, holding the controller-fetched documents and `expected.json`
- Test: `tests/data/test_edgar_form4.py`

**Interfaces:**
- `parse_daily_index(text: str) -> list[IndexRow]`, where `IndexRow(form, company, cik, date_filed,
  path)`.
- `select_form4(rows, ciks: Collection[int]) -> list[IndexRow]`: the form is exactly `"4"`.
- `parse_form4(document: str, *, filing_date: date, cik_to_symbols: Mapping[int, tuple[str, ...]],
  owner_roles: Sequence[str]) -> tuple[list[InsiderTransaction], dict[str, int]]`.
  - It applies the same rules, in the same order, as `insider_filings.read_transactions`, with the
    drop counts keyed by `DROP_RULES`.
  - It reads the `<XML>` block of the `.txt` submission.
  - Malformed XML raises `ValueError` with a type name.

- [ ] **Step 1: Write the failing tests:**
  - `test_index_parse_and_form4_selection`, where `4/A` is excluded;
  - `test_parse_matches_data_set_rows`, every fixture filing against `expected.json`;
  - `test_drop_rules_match_reader` (sale, derivative only, non-officer, zero price, trade after
    filing);
  - `test_multiple_owners_takes_first_qualifying`;
  - `test_share_class_fan_out`;
  - `test_amendment_document_type_dropped`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and see them pass. Stage.**

### Task 3: Capture worker, journal, readiness, seeded history, config

**Files:**
- Create: `agentic_trader/data/insider_capture.py`, with the worker, store and seed
- Modify:
  - `agentic_trader/config.py` (`InsiderWatchConfig` from spec §5; `load_config`);
  - `config/config.yaml`;
  - `agentic_trader/cli/commands/service.py` (start the worker behind `enabled`);
  - `agentic_trader/diagnostics/readiness.py` (`insider_capture`, 4-day limit);
  - `agentic_trader/execution/durable.py` (`EventKind.INSIDER_FORM4_CAPTURE`).
- Test: `tests/data/test_insider_capture.py`, `tests/config/test_shipped_config.py`, readiness tests

**Interfaces:**
- `async capture_filing_dates(client, store, dates, *, ciks, user_agent, pace) -> CaptureResult`.
- `async run_insider_capture_worker(...)`: once per New York date after 06:00 ET, scan windows
  skipped, 30-minute retry, catch-up from the last captured date, and a backfill from
  `backfill_from`.
- `async load_watch_history(config, store, *, cohort, trading_days, raw_bars) -> WatchHistory`.
  - It merges the archive and the journal; the archive wins where both cover a date.
  - It applies plausibility and the identity breaks, keeping the routine-rule input.
  - Its `latest_filing_date` tells the scan whether the history is current.
- Journal events: `insider/form4/<F>` with `{filing_date, index_sha256, form4_rows, cohort_rows,
  transactions: [...], drops, documents}`. Raw documents go through `write_private` to
  `data/insider/<F>/`.

- [ ] **Step 1: Write the failing tests:**
  - once per date;
  - catch-up;
  - backfill;
  - scan-window skip;
  - a failure records a type name and the journal is not partial;
  - the user agent is never logged (`caplog` contains no header value);
  - readiness ages;
  - the seed merge on overlap;
  - plausibility in the seed;
  - the shipped config keys.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and see them pass. Stage.**

### Task 4: Insider watch source, admission, card

**Files:**
- Create: `agentic_trader/agent/insider_watch.py` (`InsiderWatchSource` implementing Task 1's protocol)
- Modify:
  - `agentic_trader/storage/workflow.py` (`_watch_signal_rejection`);
  - `agentic_trader/notifier/telegram_bot.py` (`format_alert_card` watch header and body);
  - `agentic_trader/agent/evaluator.py` (the watch risk cap beside `_probe_risk_cap`);
  - `agentic_trader/execution/durable.py` (`EventKind.INSIDER_WATCH_DECISION`).
- Test: `tests/agent/test_insider_watch.py`, plus admission and rendering tests

**Interfaces:**
- The source's `prepare` loads `WatchHistory`, refuses when `latest_filing_date` is earlier than the
  previous session, and runs `build_insider_events` narrowed to D = today.
- Eligibility comes from SIP daily bars as of D−1.
- The ranking is buyers, then `opp_dollars / dv`, then symbol.
- The policy is `AprioriBracketPolicy` from the pooled protocol's cube spec.
- `decision_provenance["insider_watch"] = {entry_sha256, filing_date, buyers, owners, opp_dollars,
  session}`.
- Risk cap: `insider_watch.max_risk_per_trade_usd`.
- The LLM is commentary only. Book sizing and `card_policy` are exempt; news is attached.

- [ ] **Step 1: Write the failing tests:**
  - Review Focus items 1–5;
  - narrowed window;
  - eligibility reasons;
  - ranking;
  - the per-session and open-position caps;
  - journal statuses (`completed`, `unavailable`, `skipped`);
  - admission (policy equality, a same-session event);
  - every payload key is a `send_signal_alert` keyword;
  - card text contains "INSIDER WATCH — unvalidated" and the study line;
  - never auto-entered: no auto-entry hook exists yet, so assert that the source's `kind == "watch"`
    is the flag item 1.4 must read.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests, the agent suite and the integration suite. Stage.**

### Task 5: Outcomes block, docs

**Files:**
- Modify: `agentic_trader/cli/commands/cards.py`, plus a small labeller module reusing `label_bracket`
- Modify:
  - CLAUDE.md (contract 1: the watch-card sentence; the insider sentence);
  - `docs/apriori-alphas.md`;
  - `docs/card-evidence.md`;
  - `docs/production.md` (worker, readiness, runbook).
- Test: `tests/cli` outcomes tests

- [ ] **Step 1: Write the failing test.** The `insider_watch` block labels sent and unsent events, with
  `n`, `mean_r_cost`, `mean_beta_excess_r`, taps and fills.
- [ ] **Step 2: Implement it, and make the doc edits.**
- [ ] **Step 3: Run the full suite, the PostgreSQL integration suite and `pre-commit run --all-files`.
  Stage.**
