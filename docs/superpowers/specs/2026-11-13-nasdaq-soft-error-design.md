# Nasdaq calendar: a soft-error page is never a verified day

## Why

The earnings blackout (scan and tap, one `resolve_earnings`) asks the live lookup
`NasdaqEarningsCalendar.next_earnings` first. Its `_request_date` accepts any HTTP 200 JSON body, and
`parse_calendar_payload` reads a missing or null `data`/`rows` as an empty day. So an HTTP 200 soft-error
page (`status.rCode != 200`, or `data: null` with Nasdaq's `bCodeMessage` code 1002 "No record found")
counts as a verified "no reports" day. The blackout then clears the candidate without ever reading the
capture fallback that #149 added. This is the last fail-open path left after the 11-10..12 Nasdaq 403.
PEAD's `reported_rows` and the capture's `_rejection` already require `rCode == 200`, but they too read
`data: null` as an empty day.

## Evidence (2026-11-13)

- **Live probe** (three plain GETs, one per second):

  | Date | rCode | `data` | Rows |
  | --- | --- | --- | --- |
  | 2026-11-13 (Fri) | 200 | object | 180 |
  | 2026-11-14 (Sat) | 200 | object, `rows: null` | 0 |
  | 2026-11-26 (Thanksgiving) | 200 | object | 1 |

- **Journaled captures.** In all five captures from 11-06 to 11-13 (28-day horizon), every zero-row
  weekday page (2026-11-26 in the first three) carried `as_of`, so its `data` was an object.
- **PEAD research cache** (2,724 pages, 2016–2026):
  - 2,612 pages have rows;
  - 98 pages are `data: {rows: null}`;
  - 14 pages are `data: null`. All 14 fall on June 2016 trading weekdays and carry bCode 1002 "No record
    found", so they are archive gaps, not empty days.

A legitimately empty day keeps the `data` object. `data: null` marks missing data.

**Ruling (controller):** a page is a verified day only with HTTP 200, a JSON object body,
`status.rCode == 200` and a JSON-object `data`.
- **Evidence against the old claim.** The parser docstring's claim that the endpoint "serves `data: null`
  on some non-trading dates" has no observed page behind it. The capture test that accepts `data: null`
  encoded that claim.
- **If wrong:** a day Nasdaq legitimately serves as `data: null` makes the live lookup unverified, and the
  blackout falls back to the capture. Because the capture is all-or-nothing, that page would also fail the
  capture pass. If it is a weekday inside the capture's 28-day horizon, then:
  - under `closed`, equity entries are refused until the page changes;
  - rollback is `risk.earnings_calendar_unknown: open`;
  - the log names the reason.

## Change

1. **Shared acceptance predicate.** `agentic_trader/agent/earnings.py` gets one public predicate,
   `calendar_page_rejection(response: httpx.Response) -> tuple[dict[str, Any] | None, str | None]`. It
   returns either the accepted payload or a reason. In order:

   | Condition | Reason |
   | --- | --- |
   | status code != 200 | `HTTP {code}` |
   | body not JSON | `body is not JSON` |
   | body not a JSON object | `body is not a JSON object` |
   | `status.rCode != 200` (a missing status or rCode counts) | `status.rCode {code!r}` |
   | `data` not a JSON object | `data is not an object` (the only new reason) |

   The first four reasons are the existing strings, unchanged.
2. **Live lookup.** `NasdaqEarningsCalendar._request_date` uses the predicate. A rejected page:
   - logs one warning carrying the date and the reason, and nothing from the body;
   - returns `None`, so the date is unverified and goes through the existing failure backoff.

   `resolve_earnings` then falls back to the capture, and with neither source,
   `risk.earnings_calendar_unknown` applies (shipped `closed`).
3. **PEAD.** `NasdaqEarningsCalendar.reported_rows` uses the predicate. A rejected page is
   `ReportedPage(day, None, <HTTP status>, <reason>, <digest>)`. Request exceptions are unchanged.
4. **Capture.** `agentic_trader/data/earnings_capture.py` drops its private `_rejection` and calls the
   predicate. A `data: null` page now fails the pass (`CaptureIncomplete`, the existing all-or-nothing
   path, no retry), exactly as an `rCode 400` page does today.
5. **Unchanged:**
   - `parse_calendar_payload` and `parse_forward_rows`, because the frozen research acquisition and the
     pooled events lane parse with them;
   - `research/apriori/earnings_history._accepted`.

   Their docstrings change only to drop the unsupported "non-trading dates" claim. Parsing still tolerates
   `data: null` for research, and the live paths now reject it before parsing.

## Constraints

- GET only. Soft errors get no new retry.
- No config change, no migration (head `008`), no new risk refusal text.
- Logs and readiness carry reasons and type names only, never body content.

## Tests

- **`tests/agent/test_earnings.py`:**
  - `data: {rows: null}` is a verified empty day. The existing `test_null_rows_is_a_verified_empty_day`
    moves to the object shape, and `_response` builds `{"rows": None}`.
  - `data: null` with rCode 200 is unverified.
  - An rCode 400 page with a `data` object is unverified.
  - A soft-error date is not refetched within the failure backoff.
  - `reported_rows` on `data: null` returns `rows is None` with detail `data is not an object`.
- **`resolve_earnings` with a real `NasdaqEarningsCalendar` on a `MockTransport`:** a soft-error page
  plus a capture that holds a report in the window gives a blackout from the capture (`source == "capture"`).
  Without a capture, the lookup is unverified.
- **`tests/data/test_earnings_capture.py`:**
  - `test_null_data_is_accepted_empty_day` becomes a `data: null` page failing the pass with reason
    `data is not an object`, with nothing journaled.
  - A `data: {asOf, rows: null}` page is accepted as an empty day with `as_of` set.
- **Predicate table test:** one case per reason.

## Docs

`docs/production.md`, earnings blackout paragraph and capture "Targets" bullet: one sentence each saying
that a page counts only with HTTP 200, `rCode` 200 and a `data` object, so a soft-error page is
unverified (live lookup) or unaccepted (capture), never an empty day. The 2026-11-10 incident note gets
one closing sentence.
