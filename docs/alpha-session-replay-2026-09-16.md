# A2b session-replay smoke test — 2026-09-16

## Decision and progress

**Ship the replay groundwork; keep intraday promotion blocked.** All four
predeclared diagnostics were retained. Both SIP windows had complete regular-session
minute coverage. Both IEX windows failed the coverage contract. No gaps were filled,
feeds substituted, formulas tuned or failed attempts repeated.

This identifies a concrete constraint before broader intraday research: IEX minute
observations alone did not cover the execution clock even for SPY in these windows.
It does not establish universal SIP completeness, a profitable strategy, or measured
live execution quality. The fixed formula generated **zero simulated orders and zero
simulated trades** in the successful replays. Real-data fill accuracy was therefore
not exercised; deterministic fixtures cover the execution transitions.

The [roadmap](alpha-roadmap.md) remains at A2b. Next, version and align live and
research signal aggregation, feed identity, closed-bar availability and decision
timing, with forward publication/revision observations. Feed choice is part of that
contract: a signal from IEX is not a SIP signal, and inspecting SIP here did not
change the daemon's configuration. Retain the strict coverage failure until an
explicit execution-data contract handles unavailable observations without invented
prices. Broader mining and A3 forecast/strategy work follow these foundations.

## Frozen design

- Source revision: `eade6ca811cc7095afc44ec93a7d9feec1aa2f3b`, committed before data access.
- Protocol: [a2b-replay-v1.json](../config/research/a2b-replay-v1.json), SHA256
  `21932d4684e14824e5e9c84babd6cadb6006bebf23b624a75ff7e67d4a556f0c`.
- SPY, `delta(close,3)`, 15m regular-session signals and one-minute execution,
  default versioned normalization/bracket policy, 60-second assumed decision delay.
- Four attempts: two fixed date windows × explicitly requested IEX/SIP. Dates,
  expected counts and failure policy were frozen before inspection.
- Observed Alpaca calendars, raw historical bars and their private hashes were
  retained. Historical corrected data does not establish point-in-time availability.
- No broker mutations, Telegram messages or new daemon/poller. Each CLI invocation
  reserved one research attempt and excluded its period before provider access.

## Results

| Window | Feed | Expected minutes | Observed | Missing | Excluded outside RTH | Completed 15m bars | Outcome |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2024-03-08–11, DST | IEX | 780 | 767 | 13 | 1 | — | Coverage failure |
| 2024-03-08–11, DST | SIP | 780 | 780 | 0 | 819 | 52 | Replay completed |
| 2024-11-27–29, holiday/early close | IEX | 600 | 572 | 28 | 6 | — | Coverage failure |
| 2024-11-27–29, holiday/early close | SIP | 600 | 600 | 0 | 703 | 40 | Replay completed |

The DST sessions opened at 14:30 UTC on March 8 and 13:30 UTC on March 11.
Thanksgiving contributed no session; November 29 closed at 18:00 UTC. Excluded
minutes remain in the private input artifact but cannot drive signal or fill prices.
The successful windows produced 20 and 8 available normalized scores respectively,
with zero entries. No P&L or alpha-selection conclusion follows from these runs.

Alpaca describes missing bars when no eligible trades establish OHLC. Our coverage
finding alone does not distinguish that condition from a provider/data gap; neither
justifies assuming a known flat return. See [bar construction](https://docs.alpaca.markets/us/docs/market-data-faq).

Lifetime research attempts increased **7,049 → 7,053**, including both failures.
Registry generation remained **4**, with **0 active and 4 historical shadow**
hypotheses. These diagnostics earn no qualification, promotion or shadow credit.

## Reproducibility and review

All four raw-data/calendar/manifest/result hash chains were checked against their
immutable journal diagnostics. Recomputing from retained inputs reproduced both
coverage failures and every calculated field of both successful results exactly,
without new provider calls or new research attempts. Private campaign summary SHA256:
`4da6b08c38370ae2d125e4edb3f9b9d336d6e9eaa1d270dcd8be7864b76b8c26`.

| Diagnostic | Private result SHA256 |
| --- | --- |
| DST / IEX | `2fba44ecb8f1e9de381e0fbbe009ee72fdb6351c3329f334b897067f88405e98` |
| DST / SIP | `172ffc82412433cf66f69e108fe191c27d6d6fc63c93048d01e6ed8bf2d45350` |
| Thanksgiving / IEX | `0274f45b8ddf227d1c34f5370a96bc9949ce86333a5e406b6f2fdffde4478ab5` |
| Thanksgiving / SIP | `5ddc6bed51e9e45c743bb7c06de5fec2a8653e499a5b4dc26ff5bdb764f7eb6d` |

TDD covers calendar/DST/early-close boundaries, future/unknown observations, delayed
eligibility, superseded decisions, pending orders across holidays, flat folds and
trailing protection. Review added a regression rejecting unknown timestamps rather
than silently filtering them. Actual-SDK HTTP tests cover calendar parsing, paginated
bars, empty calendars and missing minutes; PostgreSQL tests cover concurrent immutable
completion and journal rebuild. Eight retained A2a runs preserve every scientific
field after the shared execution-engine refactor.

Local validation: **994 passed, 18 skipped** in the full suite; **66 passed** in the
separate real HTTP/WebSocket/disposable-PostgreSQL integration run; **376 passed,
1 PostgreSQL-only skip** in the final affected suite. All-file pre-commit checks
include lint, formatting, types and impacted tests. CI and deployed runtime evidence
are recorded separately on [PR #38](https://github.com/adamhadani/agentic-trader/pull/38).
See the [replay contract](alpha-session-replay.md) for remaining promotion gates.
