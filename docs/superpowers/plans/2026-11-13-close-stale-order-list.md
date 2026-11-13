# Close Stale Order List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the cancellation of a group has been confirmed by ID, a close is no longer refused because Alpaca's OPEN list still shows one of that group's cancelled orders.

**Architecture:** One change to the post-confirmation re-check loop in `AlpacaBroker.submit_position_close` (`agentic_trader/broker/alpaca.py`, about lines 1040–1053). Listed orders that belong to the cancelled group are re-read by ID; a terminal one is treated as a stale listing.

**Tech Stack:** Python 3.14, the alpaca-py SDK, pytest (offline fake client, plus the loopback HTTP venue in `tests/integration/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-13-close-stale-order-list-design.md`

## Global Constraints

- Existing refusal texts are unchanged, and no refusal text is added. The new audit phase is exactly `stale_open_order_list`, with payload `{"phase": "stale_open_order_list", "order_ids": [<sorted str ids>]}`, emitted at most once per close attempt.
- Terminal states are the existing local `terminal` set in `submit_position_close`.
- No new POST/PATCH/DELETE, no retry of a mutation, no config change, no migration (head `008`), no aliases.
- **Commits:** implementers stage only; the controller commits.

## Review Focus

1. **Unknown order.** An unknown listed ID still refuses immediately, even when stale known IDs are listed in the same pass.
2. **By-ID fill.** A known listed order that reads filled by ID refuses with "An order filled during cancellation", and nothing is submitted.
3. **By-ID non-terminal.** A known listed order that reads non-terminal by ID refuses with "New or remaining orders appeared during cancellation", and nothing is submitted.
4. **Stale list with reserved quantity.** A stale list combined with a still-reserved `qty_available` keeps waiting, then refuses with "Broker quantity remains reserved" at the deadline.
5. **Exactly one submission** when the list is stale for N passes and the quantity is free.

---

### Task 1: Treat a stale listing of a confirmed-cancelled order as stale

**Files:**
- Modify: `agentic_trader/broker/alpaca.py` (`submit_position_close`, the loop after `cancellations_confirmed`).
- Test: `tests/execution/test_position_closing.py`, with a new `FakeTradingClient` knob, for example `stale_list_passes: int`: after an order turns terminal by ID, OPEN `get_orders` keeps listing it, with its pre-cancel status, for that many list calls.
- Test: `tests/integration/test_alpaca_http.py`, with a matching venue knob in `tests/integration/conftest.py`: after a by-ID GET turns `pending_cancel` into `canceled`, the venue's OPEN list keeps returning the order as `new` for N list calls.
- Docs: `docs/production.md`, per the spec's Docs section.

**Interfaces:** `submit_position_close(request, observe)` keeps its signature and `OrderResult` contract.

- [ ] **Step 1: Write the failing tests** (offline, `tests/execution/test_position_closing.py`):
  - `test_stale_open_list_after_confirmed_cancel_still_closes_once`:
    - Setup: `client.cancel_pending_reads = 1`, `client.stale_list_passes = 2`, `client.fill_close = True`.
    - Assert: exactly one submission; the signal closes with `exit_price == 300`; the audit events contain exactly one `stale_open_order_list` phase whose `order_ids` are the cancelled leg IDs that were listed; no halt.
  - `test_unknown_order_listed_after_confirmed_cancel_refuses`:
    - Setup: after confirmation, an extra open order with a new ID appears for the symbol (inject it from the knob's first stale pass).
    - Assert: the response contains `New or remaining orders appeared during cancellation`, there are no submissions, and the signal stays `EXECUTED`.
  - `test_listed_group_order_filled_by_id_refuses`:
    - Setup: a listed known leg reads `filled` with `filled_qty > 0` by ID on the re-check pass.
    - Assert: `filled during cancellation`, and no submissions.
  - `test_listed_group_order_non_terminal_by_id_refuses`:
    - Setup: a listed known leg reads `new` by ID on the re-check pass.
    - Assert: `New or remaining orders appeared during cancellation`, and no submissions.
  - `test_stale_list_with_reserved_quantity_refuses_at_deadline`:
    - Setup: the list stays stale and `qty_available` stays `"0"`.
    - Assert: `Broker quantity remains reserved`, and no submissions.
  - **Loopback** (`tests/integration/test_alpaca_http.py`), `test_close_proceeds_when_open_list_lags_by_id_cancellation`:
    - Setup: the venue list lags 1–2 calls after the leg cancellations.
    - Assert: `close_position_manual(sid)` submits exactly one market close POST, and the signal closes.

- [ ] **Step 2: Run the new tests and confirm they fail.**
  Run: `uv run pytest tests/execution/test_position_closing.py tests/integration/test_alpaca_http.py -q -p no:cacheprovider -k "stale or unknown_order or by_id or lags"`
  Expected: FAIL. The stale and lag cases refuse with "New or remaining orders…".

- [ ] **Step 3: Implement it** per the spec's Change section. Keep the existing loop shape:
  - Compute the known IDs once from `orders`.
  - In each pass, classify the listed orders as unknown, known non-terminal, known filled, or known terminal (stale).
  - Record `stale_open_order_list` once, then fall through to the unchanged `qty_available` check.

- [ ] **Step 4: Run the affected suites and confirm they pass.**
  Run: `uv run pytest tests/execution tests/broker tests/integration/test_alpaca_http.py tests/integration/test_trade_lifetimes.py tests/execution/test_release_exit.py -q -p no:cacheprovider`
  Expected: PASS.

- [ ] **Step 5: Update the docs** per the spec.

- [ ] **Step 6: Run the hooks except pytest.**
  Run: `SKIP=pytest uv run pre-commit run --all-files`
  Expected: PASS.

- [ ] **Step 7: Stage** the files. Do not commit.
