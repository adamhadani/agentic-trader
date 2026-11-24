# Filing Veto and Release Exit Enforce Prep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the filing veto enforces only NT-filing and adverse-8-K categories and exempts non-native cards. Release-exit gaps reach the morning summary. Both controls ship in `enforce`.

**Architecture:**
- A category subset on `FilingVetoConfig`, applied in the shared evidence classifier, so the evaluator and tap gates change only by an `exempt` flag.
- A storage-level reader feeds a new optional section of the supervisor's quiet-hours summary.
- Config and docs land last.

**Tech Stack:** Python 3.14, pydantic, SQLAlchemy async, pytest.

**Spec:** `docs/superpowers/specs/2026-11-13-enforce-prep-design.md`

## Global Constraints

- Category values are exactly `late_filing`, `adverse_8k` and `offering` (`FilingCategory`). The code default of `enforce_categories` is all three. The shipped value is `["late_filing", "adverse_8k"]`.
- The new block field is exactly `would_refuse`, and the exempt marker exactly `exempt: true`. The enforce card line text is exactly `🗂 Filing (not enforced): {detail} — card unchanged`. The preview line is unchanged.
- `agentic_trader/risk` imports no data or config module (`tests/risk/test_rules.py` pins this).
- No migration (head `008`), no new outbox kind, no compatibility aliases, no retry of any mutation.
- Preview never reads filings at tap time (the `latest.assert_not_awaited()` test stays).
- The supervisor path (`diagnostics/monitor.py`, `storage/operations.py`) imports no broker, trading, earnings or `execution/release_exit.py` module.
- Run commands with `env -u VIRTUAL_ENV uv run …`. Tests keep network blocked and use `test_*` PostgreSQL only.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Masking.** A newer offering plus an older NT filing, under `enforce` with the shipped categories, refuses, and the reason names the NT filing. The label, `category`, shows the offering.
2. **Scope parity.** A PEAD, insider-watch, probe or alpha candidate is exempt at the evaluator, and so is the same card's signal row at the tap gate (`_release_exit_scope` ≠ `native`). Native cards are never exempt.
3. **Old payloads.** An already-queued quiet-hours payload with no `release_exit` key still validates and renders.
4. **Morning trigger.** A quiet period whose previous day is not an alert day (weekend or holiday), or `release_exit.mode == "off"`, adds no section. A period with only release-exit gaps still queues a summary.
5. **Unknown categories.** A filing whose category is missing from `windows` never blocks and never raises.

---

### Task 1: Enforce by category (spec Part A)

**Files:**
- Modify: `agentic_trader/config.py` (`FilingVetoConfig`).
- Modify: `agentic_trader/risk/rules.py` (`blocking_filing`).
- Modify: `agentic_trader/agent/filing_veto.py` (`filing_veto_evidence`, `journal_filings_block`).
- Modify: `agentic_trader/notifier/telegram_bot.py` (`_filing_veto_line`).
- Modify: `agentic_trader/research/setups/outcomes.py` (`_filing_veto_group`).
- Test: `tests/config/test_filing_veto_config.py`, `tests/risk/test_rules.py`, `tests/agent/test_filing_veto_journal.py` (or the existing `filing_veto_evidence` tests), `tests/notifier/test_filing_veto_line.py`, `tests/research/setups/test_filing_veto_summary.py`.

**Interfaces:**
- Produces:
  - `FilingVetoConfig.enforce_categories: tuple[FilingCategory, ...]`;
  - `FilingVetoConfig.enforced_windows() -> dict[str, int]`;
  - `filing_veto_evidence(lookup, symbol, today, policy, *, exempt: bool = False) -> tuple[dict[str, Any], Rejection | None]`.
  - For an ok status, the block carries `would_refuse` (and `exempt: True` when exempt). The returned rejection is None when exempt.
  - `journal_filings_block` returns `{status, would_block, category, would_refuse}`.

- [ ] **Step 1: Write the failing tests:**
  - **Config:**
    - the default holds all three categories;
    - a YAML list of strings loads;
    - empty, duplicate and unknown values are rejected;
    - `enforced_windows()` keeps only the chosen keys.
  - **Rules:**
    - `test_category_without_window_never_blocks`: windows without `offering`, with an offering filed today, gives None and no `KeyError`.
  - **Evidence:**
    - `test_offering_preview_only_does_not_mask_nt_filing`: a 3-day-old 424B5 and a 20-day-old NT 10-K, with `enforce_categories=(late_filing, adverse_8k)`. Asserts:
      - block `category == "offering"`, `would_block is True`, `would_refuse is True`;
      - the rejection reason mentions `NT 10-K`.
    - An offering alone under the same policy gives `would_block` True, `would_refuse` False and rejection None.
    - `exempt=True` gives `exempt` True, `would_refuse` False and rejection None.
    - Default categories keep today's behaviour.
  - **Card line:** under `enforce`, `would_block` True and `would_refuse` False renders `🗂 Filing (not enforced): … — card unchanged`. Under `enforce` with `would_refuse` True there is no line. Preview is unchanged.
  - **Outcomes:** `_filing_veto_group` counts `would_refuse`.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/config/test_filing_veto_config.py tests/risk/test_rules.py tests/agent/test_filing_veto_journal.py tests/notifier/test_filing_veto_line.py tests/research/setups/test_filing_veto_summary.py -q -p no:cacheprovider`
- [ ] **Step 3: Implement it** per spec Part A.
- [ ] **Step 4: Run the affected suites.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/config tests/risk tests/agent tests/notifier tests/research/setups -q -p no:cacheprovider`
  Expected: PASS. The existing `test_card_freshness_tap.py` offering-under-enforce tests pass unchanged, because the code default enforces all three.
- [ ] **Step 5: Run the hooks except pytest.**
  Run: `SKIP=pytest env -u VIRTUAL_ENV uv run pre-commit run --all-files`
- [ ] **Step 6: Stage.** Do not commit.

### Task 2: Exempt non-native cards at both gates (spec Part B)

**Files:**
- Modify: `agentic_trader/agent/evaluator.py` (gate 2d).
- Modify: `agentic_trader/agent/copilot.py` (`_filing_veto_reason`; its caller `_tap_gate_reason` passes what it needs).
- Test: `tests/agent/test_evaluator.py` (filing veto section) and `tests/agent/test_card_freshness_tap.py` (filing veto section).

**Interfaces:**
- Consumes: `filing_veto_evidence(..., exempt=...)` from Task 1, and `TradingCopilot._release_exit_scope(position: Mapping) -> str` (existing, `NATIVE_SCOPE` for native).
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests:**
  - **Evaluator under `enforce`** with an NT 10-K inside its window: PEAD (`catalog_event` set, probe), insider watch (`catalog_event` set) and alpha-versioned candidates are not rejected, and their block has `exempt: true` and `would_refuse: false`. The same native candidate is rejected.
  - **Tap gate under `enforce`:** a signal row whose `_release_exit_scope` is `pead`, `insider_watch`, `probe` or `alpha` gets None from `_filing_veto_reason`, without awaiting the filings lookup. A native row is refused.
  - **Parity:** for each candidate kind, the evaluator's exempt decision equals `_release_exit_scope(row) != NATIVE_SCOPE` on the signal row the scan builds from it. Use the existing scan or test helpers that build rows; if none fits, construct the row with the fields `_release_exit_scope` reads.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement it.**
  - **Evaluator:** `exempt = candidate.catalog_event is not None or candidate.probe or candidate.alpha_version is not None` (verify against `_release_exit_scope`; if the fields differ, follow the scope function and record why in the report).
  - **Tap:** check the scope first.
  - Update the gate 2d comment.
- [ ] **Step 4: Run the affected suites.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/agent tests/execution -q -p no:cacheprovider`
  Expected: PASS.
- [ ] **Step 5: Run the hooks except pytest, then stage.** Do not commit.

### Task 3: Release-exit gaps in the morning summary (spec Part C)

**Files:**
- Modify: `agentic_trader/diagnostics/incidents.py` (`QuietHoursSummary` plus a new `ReleaseExitGaps` model).
- Modify: `agentic_trader/storage/operations.py` (`queue_quiet_summary` and a small reader).
- Modify: `agentic_trader/diagnostics/monitor.py` (`OperationsMonitor` takes the release-exit mode).
- Modify: `agentic_trader/cli/commands/service.py` (`monitor_once` passes `config.release_exit.mode`).
- Modify: `agentic_trader/presentation/operations.py` (`format_quiet_hours_summary`).
- Test: `tests/operations/test_operations_store.py`, `tests/operations/test_quiet_hours.py`, `tests/presentation/test_quiet_hours_summary.py`, `tests/notifier/test_operations_summary_delivery.py`.

**Interfaces:**
- Produces:
  - `ReleaseExitGaps{trading_day: date, missing_pass: bool, unknown: list[GapPosition], unnotified_closes: list[GapClose]}`, with simple pydantic models for `symbol`, `signal_id` and (for closes) `outcome`;
  - `QuietHoursSummary.release_exit: ReleaseExitGaps | None = None`.
- The reader matches events by the literal stream `release-exit`, the kinds `release_exit_pass` and `release_exit_close`, and the key `release-exit/{date}` or prefix `release-exit/{date}/`. It reads inside the summary's session, in the store's scope.

- [ ] **Step 1: Write the failing tests:**
  - **Store, with the trading day = `period_start`'s ET date and mode `enforce`:**
    - no pass event gives `missing_pass` True and queues a summary even with no tallies;
    - a pass with one native `unknown` lists it;
    - a `release_exit_close` with `notified: false` lists it with its outcome;
    - a complete pass with no gaps adds no section, and with no tallies queues nothing (an empty decision is still journaled once, as today).
  - **No section** for mode `off`, or a `period_start` date that is not an alert day (a Saturday-to-Monday period never asks for Saturday's pass).
  - **Model:** a payload without `release_exit` validates (old queued payloads).
  - **Renderer:** each gap kind renders a fixed line naming the symbol and signal ID, and `/close SIGNAL_ID` for an unknown or unnotified position. Nothing renders when the section is None.
  - **Import guard:** `storage/operations.py` and `diagnostics/monitor.py` do not import `agentic_trader.execution.release_exit` or `agentic_trader.agent`. Use an AST or `sys.modules` check in the style of the existing `risk` import test.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement it** per spec Part C.
- [ ] **Step 4: Run the affected suites.**
  Run: `env -u VIRTUAL_ENV uv run pytest tests/operations tests/presentation tests/notifier tests/cli -q -p no:cacheprovider`
  Then, because a storage query is added, run the PostgreSQL integration for operations: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 env -u VIRTUAL_ENV uv run pytest tests/integration/test_postgres.py --run-postgres -q -p no:cacheprovider -k "summary or quiet"`
  Expected: PASS.
- [ ] **Step 5: Run the hooks except pytest, then stage.** Do not commit.

### Task 4: Ship config, docs and the result doc (spec Part D)

**Files:**
- Modify: `config/config.yaml`, `agentic_trader/config.py` (docstring and comments only), `tests/config/test_shipped_config.py`.
- Docs: `docs/production.md`, `docs/risk-policy.md`, `docs/card-evidence.md`, `docs/operational-monitoring.md`, `CLAUDE.md`, `docs/alpha-roadmap.md`.
- Create: `docs/filing-veto-release-exit-preview-2026-11-14.md` (the controller supplies the evidence numbers in the dispatch).

- [ ] **Step 1: Update the shipped-config test** to expect `entry_block == "enforce"`, `enforce_categories == (late_filing, adverse_8k)` and `release_exit.mode == "enforce"`. Run it and confirm it fails.
- [ ] **Step 2: Update `config/config.yaml`** in the corporate_actions comment style: "preview 2026-11-07 → 11-14; enforce since <merge date>". Run the test and confirm it passes.
- [ ] **Step 3: Make the doc edits** listed in spec Part D, and write the result doc.
- [ ] **Step 4: Run the hooks except pytest**, plus `env -u VIRTUAL_ENV uv run pytest tests/config -q -p no:cacheprovider`.
- [ ] **Step 5: Stage.** Do not commit.
