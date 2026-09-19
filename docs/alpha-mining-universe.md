# Alpha mining universe contract

**Updated 2026-09-19.** This page describes the bounded discovery entry point;
it does not turn a current symbol list into point-in-time historical membership.

## Cohorts

`copilot alpha mine` supports three explicit cohorts:

| Cohort | Selection | Provenance |
| --- | --- | --- |
| `explicit` | `--symbol` or comma-separated `--symbols` | Canonical sorted symbol list |
| `etf32` | Frozen ETF benchmark | `ETF_RESEARCH_UNIVERSE.version_id` |
| `snapshot` | `selected` members from an immutable `universe-snapshot` result | Snapshot hash plus applied cap |

The snapshot cohort is the supported way to widen the funnel toward the
prospective 300-equity screen. It verifies the snapshot hash and observed-at
boundary before reading any prices. It preserves the snapshot's deterministic
selection order and records `snapshot:<id>:cap:<n>` in every dataset manifest.
`--max-symbols` bounds a run to at most 500 names; the cap is part of the
provenance identity. Snapshot membership is current/prospective evidence and
does not establish survivorship-free historical eligibility, borrowability,
capacity or qualification.

Example after creating a private snapshot with `alpha universe-snapshot`:

```bash
uv run copilot alpha mine \
  --universe snapshot \
  --universe-file PRIVATE_SNAPSHOT/snapshot.json \
  --max-symbols 64 \
  --feed alpaca --lookback 5y --interval 1d \
  --iterations 25 --method genetic --max-seconds 120
```

Use the source-correct Alpaca IEX/SIP feed configured for the paper account.
Yahoo evidence remains diagnostic and cannot qualify an Alpaca deployment.
The scheduled launchd job intentionally remains the ETF benchmark until a
fresh, operator-reviewed snapshot and resource budget are declared.

## Charging and failure semantics

Each symbol reserves its full trial budget in the journal **before** provider
I/O. Entitlement failures, timeouts, process interruption and evaluator errors
therefore remain charged failures rather than disappearing from the experiment.
Raw acquisition evidence and completed trial definitions are retained; a
failure never consumes the holdout or promotes an alpha. A bounded campaign
should be reported with its cohort hash, source, attempted symbols, failures,
discovery finalists and qualification results.

Widening the symbol funnel does not weaken statistical or economic gates. The
next campaign should compare ETF32 and a 32–64-name snapshot cohort under a
matched per-symbol budget, then record transfer, coverage, turnover, cost and
failure rates before increasing to all 300 names.
