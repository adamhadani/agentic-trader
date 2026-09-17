# Alpaca bar acquisition evidence

Routine `AlpacaDataProvider` bar reads now retain decoded JSON pages **before** SDK pagination
aggregation, typed bar parsing and OHLCV cleaning. This applies to native live
scans, research downloads, session replay, panels and the forward collectors.
The [SPY-minute postmortem](alpha-spy-minute-postmortem-2026-09-17.md) motivated
this boundary: saved cleaned frames alone could not identify where rows disappeared.

## Architecture and evidence chain

- `transport/alpaca.py` exposes a scoped response observer on the existing bounded
  SDK transport. A per-client `ContextVar` isolates overlapping calls; scope cleanup
  runs on success and failure. SDK pagination, parsing and mutation retry policy
  remain authoritative. No second HTTP implementation or broker-mutation replay.
- `data/evidence.py` owns an injected `BarEvidenceStore`. Application composition
  supplies its path/policy; test doubles can omit it. Production composition always
  supplies it. No new database schema, queue or event projection is introduced.
- `storage/artifacts.py` owns the existing immutable private JSON publisher, moved
  out of the research layer and used directly by all callers, without an alias.
- Request bounds/feed/adjustment, actual page request/receipt times, SDK/Pandas
  versions, every decoded raw field/null entry, and normalization outcomes survive.
  Dropped OHLCV rows record original position, timestamp and missing columns.
  Incomplete mandatory schemas fail; empty responses retain declared feed/clock
  metadata and an empty aware index. No prices are filled or inferred.
- A successful frame contains an `evidence` reference (absolute artifact path and
  SHA-256). Session chunks put each reference in `attrs.acquisition`; a combined
  frame does not misleadingly expose only its first chunk. Saved NPZ metadata and
  existing research receipts retain these references. Acquisition failures retain
  earlier pages, typed failure/status and their artifact reference. Observer,
  decision, panel, replay and mining failure journals preserve the relevant links.

Private layout under `~/.local/state/agentic-trader/market-data/YYYY-MM-DD/UUID/`:

| File | Meaning |
| --- | --- |
| `manifest.json` | Intent and policy, written before network access |
| `page-NNNN.json` | One successful decoded response and sanitized request parameters |
| `result.json` | Page hashes/counts, normalization fingerprint or typed failure |

Directories are created with mode 0700 and files with mode 0600. Tests use
`COPILOT_TEST_ROOT/market-data`; test mode without that root refuses artifact access.
Raw pages stay out of Git. Logs emit the capture
UUID, artifact hash and status using the existing structured logger; they do not
emit bar payloads, auth headers, credentials or raw exception messages.

A `complete` capture proves that this acquisition and normalization completed. It
**does not** prove complete market coverage, fresh data, executable prices, or alpha
qualification. Existing coverage, clock, feed and promotion gates still apply.

## Failure and capacity semantics

Validated `market_data.evidence` defaults are 100 pages and 128 MiB per acquisition,
with a 1 GiB free-space reserve checked before requests and page/result writes.
Crossing a bound rejects that Alpaca acquisition; it never returns a partial frame
as complete evidence. The page which exceeds a limit may already have been received
but is not retained. Small failure manifests/receipts may exceed these budgets so
that refusals remain diagnosable. Normal production fallback policy remains in place;
research paths never silently change feed.

There is **no automatic deletion**. Retain failed/incomplete captures with their
journal references. Plan private archival/backups and monitor volume/free space.
The reserve check is not an atomic filesystem quota: concurrent writers and other
programs can consume space after it. A full/unwritable disk can prevent even the
failure receipt; the error then points to the earlier manifest as `incomplete`.
After a process crash, a directory without a result is unfinished evidence, never
replayed or reclassified as successful. Limits bound retained acquisition data,
not the size of a server response before the SDK decodes it or total lifetime storage.

Capture is lossless for decoded valid JSON pages, not byte-for-byte HTTP packets.
HTTP error bodies, invalid JSON, and SDK GET retry attempts are not retained as raw
pages; the final typed failure and already received pages remain. Requests that
fail before receiving any page have a manifest and failure result. Latest quotes,
calendars, active doctor access probes, broker account/order responses and Yahoo Finance are outside this bar
capture scope. The normalization fingerprint is versioned with Pandas; original
JSON files and SHA-256 hashes are the portable retained evidence.

All SDK, normalization, hashing and filesystem work remains at the existing blocking
worker boundary. A completed capture after a caller timeout does not retroactively
make that data available to the original decision.

## Verification

Real SDK/TCP tests cover raw null/unknown fields, malformed bars, cleaning loss,
empty responses, interrupted pagination, concurrent calls, page/byte/free-space
limits and failed completion writes. Research integration tests follow references
through SQLite/PostgreSQL journal replay and preserve trial/registry semantics.
Deployment verification and the bounded actual-SIP acquisition check are recorded
separately in the delivery PR; source tests do not establish live feed entitlement.

Actual historical SIP verification retained one narrow page and two full-chunk pages
with 16,654 bars and zero normalization differences; see the [frozen verification
evidence](alpha-spy-minute-postmortem-2026-09-17.md#automatic-capture-follow-up).
This historical request does not resolve recent-SIP entitlement.

Native scan signal records do not yet pin all input-frame references individually;
capture UUIDs, timestamps and request manifests support investigation. Carrying exact
source/calibration references into executable target/signal identity remains part
of the roadmap execution gate.

Valid empty bar maps and requested-symbol empty lists retain a zero-row normalized
receipt and missing coverage. Invalid envelopes, unexpected symbols, non-list bar
values and invalid pagination tokens fail as `BarResponseError`; raw response
evidence is saved before validation. Typed row parsing remains owned by the SDK.
