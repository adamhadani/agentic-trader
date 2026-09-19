# Alpha mining universe contract

**Updated 2026-09-19.** This page describes the bounded discovery entry point;
it does not turn a current symbol list into point-in-time historical membership.

## Cohorts

`copilot alpha mine` supports four explicit cohorts:

| Cohort | Selection | Provenance |
| --- | --- | --- |
| `explicit` | `--symbol` or comma-separated `--symbols` | Canonical sorted symbol list |
| `etf32` | Frozen ETF benchmark | `ETF_RESEARCH_UNIVERSE.version_id` |
| `snapshot` | `selected` members from an immutable `universe-snapshot` result | Snapshot hash plus applied cap |
| `screen` | `selected` members from a completed source-specific liquidity screen | Screen result hash plus applied cap |

The snapshot cohort is the supported way to widen the funnel toward the
prospective 300-equity screen. It verifies the snapshot hash and observed-at
boundary before reading any prices. It preserves the snapshot's deterministic
selection order and records `snapshot:<id>:cap:<n>` in every dataset manifest.
`--max-symbols` bounds a run to at most 500 names; the cap is part of the
provenance identity. Snapshot membership is current/prospective evidence and
does not establish survivorship-free historical eligibility, borrowability,
capacity or qualification.

The `screen` cohort is the preferred bridge from a broad prospective snapshot
to a bounded discovery run. It accepts only a completed
`equity_liquidity_screen_v1` result whose selection is explicitly research-only,
preserves the screen's deterministic activity ranking, and records
`screen:<result-hash>:cap:<n>` in each dataset manifest. The screen measures the
declared feed (the current 64-name result is IEX activity), not consolidated
capacity, borrowability or point-in-time historical membership.

Example after creating a private snapshot with `alpha universe-snapshot`:

```bash
uv run copilot alpha mine \
  --universe snapshot \
  --universe-file PRIVATE_SNAPSHOT/snapshot.json \
  --max-symbols 64 \
  --feed alpaca --lookback 5y --interval 1d \
  --iterations 25 --method genetic --max-seconds 120
```

To mine the current source-qualified 64-name development cohort instead:

```bash
uv run copilot alpha mine \
  --universe screen \
  --universe-file PRIVATE_SCREEN/result.json \
  --max-symbols 64 --feed alpaca --lookback 5y --interval 1d \
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

Widening the symbol funnel does not weaken statistical or economic gates. A
matched ETF32 versus screened 32–64-name campaign should record transfer,
coverage, turnover, cost and failure rates before increasing to all 300 names.

The per-symbol DSL currently spans price/volume momentum and reversal, causal
overnight/range features, volatility-scaled trend, range location, signed
volume pressure, serial return dependence and standardized slope. Every added
family is compiled against the actually acquired fields and is covered by
prefix-invariance tests; it remains a discovery hypothesis until untouched
holdout and forward execution evidence qualify it.
