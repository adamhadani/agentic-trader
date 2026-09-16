# Prospective session-clock observations

This A2b increment measures the live data boundary before migrating strategy
versions to session bars. It shares `data/sessions.py:AlpacaSessionSource` and
`market/bars.py:session_bar_windows` with [session replay](alpha-session-replay.md).
It does not change trading scans, score formulas, bracket policy, alpha identities,
qualification thresholds or the unconditional intraday promotion gate.

## What runs

The existing daemon owns one independent, read-only observation loop. The checked-in
configuration enables SPY, 15m, using the configured Alpaca stock feed. It does not
start a broker stream, Telegram poller, execution queue or discovery search.
Dependencies are injected; SDK reads and artifact/aggregation work run off the
asyncio loop. Shutdown waits for an in-flight observation before closing its owned
SDK clients. This bounded workload still shares the default executor; the broader
research-executor separation remains in the architecture review.

- Request the actual exchange calendar for the current exchange date, refreshing
  every five minutes. A successful empty response means no observed session; a
  failure is not a weekday approximation or a silent reuse of stale boundaries.
- Anchor signal windows at that session's open and clip at its observed close.
  This handles DST and early closes using the same clock as replay.
- Poll every 30 seconds, aligned to wall-clock boundaries plus a five-second offset.
  Only capture within 180 seconds after a completed signal window. Ordinarily this
  gives six observations, approximately +5/+35/+65/+95/+125/+155 seconds. Startup,
  provider latency and missed polls can change actual counts; receipts are retained.
- Read raw one-minute bars from session open through the target close. Apply the
  replay contract: all expected RTH minutes must be present, ordered and valid.
  Earlier missing minutes make the session prefix unavailable, even if the final
  15-minute bucket is complete. This deliberately measures compatibility with replay.
- Keep unavailable results and successful snapshots. No interpolation, feed fallback,
  synthetic bars, scoring, orders, or synthetic Telegram messages.

Alpaca documents late-trade bar revisions and distinguishes original from updated
bars. REST polling is the interface currently used by our scanner; these observations
measure when **our REST process** obtained a usable snapshot. They do not establish
exact exchange publication time, capture every WebSocket update, or prove that a
bar will never change again. See [Alpaca's bar and updated-bar contract](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

## Retained evidence and meaning

The policy identity includes `forward_session_observation_v1`, layout `rth_open_v1`,
feed, universe, timeframe and sampling configuration. Each capture has a unique ID,
source revision/run identity, session window and immutable private artifacts:

- `manifest.json` published before price I/O;
- raw minute NPZ, observed `calendar.json`, and `inputs.json` with dataset hash;
- `result.json` with request/receipt/completion timestamps, coverage or failure,
  target OHLCV, input/calendar/manifest hashes and content identity.

The journal retains the result path/hash. Price reads are preceded by a durable
`capturing` event and a holdout exclusion for the entire inspected UTC date, including
native daily start labels before RTH. Repeated captures of one policy/date do not
repeatedly consume trials. This is data observation with **zero formula trials**,
no shadow sessions/decisions, and no promotion credential. Any subsequent hypothesis
search must reserve its own trials and fresh holdout.

`observation/<id>` retains each capture; `observation/latest` and per-symbol latest
projections expose progress. `observed-bar/<identity>` retains earliest complete
receipt, latest receipt and content versions. `revision_count` is the number of
**distinct observed minute contents beyond the first**, not every provider update
or every transition back to a previously seen value. Out-of-order completions cannot
replace a later snapshot or erase the earliest receipt. Journal replay restores these
projections without provider access or delivery.

`availability_upper_bound_seconds` is receipt time minus signal close. It includes
sampling/HTTP delay and is only an upper bound on availability. Failed reads retain
request/receipt timestamps without claiming usable data. A crash may leave a
`capturing` record or an artifact not yet journaled; no automatic replay or backdated
forward credit follows a restart. Later captures are new observations with real times.

Private artifacts live under `~/.local/state/agentic-trader/research/forward-observations`
with 0600 files and private directories. Never commit them. Retain failures and hashes
alongside successes. Existing capacity/backup work applies to these accumulating data.

## Operations and configuration

`alpha_pipeline.observations` has validated fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | false in model; true in desk YAML | Daemon starts the observation loop |
| `symbols` | `[SPY]` | 1–5 explicit unique stock symbols; independent of permission to trade |
| `timeframe` | `15m` | `15m`, `1h`, `4h`, or `1d` session windows |
| `poll_seconds` | 30 | Bounded sampling cadence, 10–60 seconds |
| `poll_offset_seconds` | 5 | Wall-clock offset, less than ten seconds |
| `window_seconds` | 180 | Observe for 60–600 seconds after a close |
| `calendar_refresh_seconds` | 300 | Refresh observed boundaries, 30–3600 seconds |
| `max_age_seconds` | 180 | Collector readiness freshness, 120–3600 seconds |

```bash
uv run copilot alpha status
uv run copilot doctor --readiness
```

`alpha status` includes `latest_observation`, separate from `latest_research`.
An idle collector may have no capture yet, or a previous session's capture.
`/readyz`'s `alpha_observer` check measures current-run collector progress, including
waiting outside sampling windows. It does **not** assert complete market data or a
qualified alpha. Coverage and provider failures are visible in retained results;
calendar/persistence failures degrade collector readiness.

Prometheus exports `alpha_observation_complete`, `alpha_observation_timestamp_seconds`
and (for complete samples) `alpha_observation_availability_upper_bound_seconds`,
labelled by symbol/timeframe/feed. Check completeness and timestamp before reading
an old availability gauge. Logs correlate `alpha_session_observation` with capture ID,
status and artifact hash. There are no routine observation Telegram notices.

## Remaining A2b work

Collect prospective quality/revision/latency evidence over real sessions. Use it to
choose and version the live acquisition, availability/freshness and decision-clock
contract for **new** alpha definitions. Existing fixed-duration versions remain
unchanged. Add versioned research/live score parity and missed/repeated-decision
fixtures before routing session forecasts toward the existing execution boundaries.
Actual operator/broker acknowledgment, partial fills, protection and corporate-action
semantics remain separate required evidence. No intraday qualification follows from
this collector alone. Continue the [ordered roadmap](alpha-roadmap.md).
