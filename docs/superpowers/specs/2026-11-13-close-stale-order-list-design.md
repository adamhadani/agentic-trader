# Close: a stale open-order list no longer refuses after confirmed cancellation

## Incident (BLK, 2026-11-12)

Signal #47 (BLK, PEAD probe, `session_count_v1`, 20 sessions) reached its holding deadline at 15:45 ET. The holding close (`hold-5a92…`) went through `AlpacaBroker.submit_position_close`:

| UTC | Step |
| --- | --- |
| 20:45:13.60 | preflight: cancel IDs are the target leg (`d008…`) and the stop leg (`f3a9…`) |
| 20:45:13.75 | cancellation requested for the target leg (the stop leg was already being cancelled as its OCO sibling) |
| 20:45:13.83 | stop leg `canceled` |
| 20:45:15.94 | target leg `canceled` |
| 20:45:16.63 | `cancellations_confirmed`: both legs were read terminal by ID |
| 20:45:16.94 | `failed`: "New or remaining orders appeared during cancellation; no close submitted." |

The order history has no BLK order other than the entry bracket. The refusal came from Alpaca's OPEN order list, which still listed the target leg about 0.7 s after a by-ID read had shown it `canceled`. The post-confirmation re-check refuses on the first non-empty list. So the close cancelled both protective legs and submitted nothing. The position stayed open with no stop, and `holding_close_requires_review` halted new risk. The same code serves `/close`, `/flatten`, the release exit and panic.

## Change

In `AlpacaBroker.submit_position_close`, after `cancellations_confirmed`, each pass of the re-check loop does the following:

1. List the OPEN orders for the symbol, as today.
2. Any listed order whose ID is **not** in the cancelled group (the expanded `orders` set) refuses at once, with the existing text `New or remaining orders appeared during cancellation; no close submitted.`
3. Every listed order whose ID **is** in the cancelled group is read again by ID in this pass:
   - Filled, or `filled_qty > 0`: refuse with the existing text `An order filled during cancellation; reconcile before closing.`
   - Not terminal (`filled`, `canceled`, `rejected`, `expired`): refuse with the existing "New or remaining orders…" text.
   - Terminal by ID: the list entry is stale. It does not refuse and does not block this pass.
4. The first pass that finds a stale entry records one `close_broker_step` observation, `{"phase": "stale_open_order_list", "order_ids": [...]}`. Later passes do not repeat it.
5. The broker position's `qty_available` check, the deadline and the second session check are unchanged. While Alpaca still reserves the quantity, the loop keeps waiting. When the deadline passes, it refuses with the existing "Broker quantity remains reserved" text.

**Ruling (controller):**
- A by-ID read in the same pass is the authority on an order's state; the list endpoint lags it.
- `qty_available` is the broker's own reservation. A stale listing never also frees the reserved quantity, so it is safe to proceed on these two signals.
- Waiting for the list to empty as well would only add latency.
- **If wrong:** a close is submitted while Alpaca's list still shows a cancelled leg. The broker either rejects it for insufficient quantity, which leads to the existing failure handling, or fills it. Both outcomes are reconciled by exact client ID.

## Constraints

- Every refusal text is unchanged; there is no new refusal text. The one new audit phase is `stale_open_order_list`.
- POST/PATCH/DELETE are never retried. The re-check makes no new mutations, only GETs.
- No config change, no migration (head `008`), no compatibility aliases.
- **Tests:**
  - Offline fakes in `tests/execution/test_position_closing.py`.
  - One loopback SDK test through the local venue in `tests/integration/conftest.py`, giving the venue a "list lags by-ID" knob.
  - Network stays blocked.

## Docs

- **`docs/production.md`:** in the close paragraph that begins "Alpaca OPEN queries omit held bracket stops", add one sentence: after cancellation is confirmed by ID, a listed order from the same group that reads terminal by ID is a stale listing and is ignored; an unknown order ID still refuses.
- **`docs/production.md` incident note:** a short `### November 12 BLK holding close` incident note after the "September 17 close-message investigation" section.
