# Retained forecast controls

`alpha forecast-controls PROTOCOL --parent FORECAST_STUDY_DIR --output NEW_DIR`
compares frozen Ridge forecasts with fixed reversal/volatility controls and checks
endpoint trade-activity evidence. It makes no provider requests, submits no orders,
sends no Telegram messages and cannot qualify or activate an alpha.

The [first protocol](../config/research/screened-equity-controls-iex-v1.json) binds
the complete [screened forecast study](alpha-panel-forecasts-2026-09-17.md) result
hash and parent plan. It declares **90 comparisons**: five strategies × three annual
folds × two endpoint scopes × (one IC evaluation + two cost scenarios). Reusing
previously inspected data remains historical development; it is not fresh validation.

## Decisions and controls

All strategies use the selected equity cohort's original calendar, eligibility and
finite frozen Ridge forecast support. Ridge is not refitted. The other scores are
60-session reversal, 20-session realized volatility, their equally weighted
within-date percentile-rank blend, and an equal-weight long-only context portfolio.
The long-only score is constant, so its rank IC is undefined.

The first four use the same tail construction, tie handling, minimum breadth and
holding horizon. This matches construction, not beta, sector or volatility risk.
The long-only portfolio is context, not a matched market-neutral competitor.

Weights freeze before future outcomes are inspected. Both endpoint scopes retain
identical decisions:

- **Finite source price:** the parent's adjusted next-open to horizon-close proxy.
- **Positive endpoint volume:** additionally requires positive source daily volume
  on entry and exit days. Missing evidence masks outcomes, never eligibility,
  ranking or holdings. Whole-day venue volume does not prove auction liquidity,
  capacity, borrow availability or an executable fill.

Missing labels withhold that entire date's IC. Missing held outcomes withhold the
full compounded return; unknown returns are never replaced with zero. Original
Ridge training used finite-price labels, including zero-volume days. This comparison
does not establish what a retrained strict-endpoint model would achieve.

## Comparison and inference

Paired basket differences are Ridge return minus control return on the same scheduled
basket dates. Reports preserve unknown pairs and the original denominator. They do
not compound return differences into fictitious wealth. Twelve baskets per year
support descriptive observations and dispersion, not reliable economic significance.
Daily cross-sectional IC keeps the parent's explicit horizon, HAC policy and
complete-calendar inference guard. No new promotion threshold is introduced.

## Architecture and evidence

`RetainedPanelSource` supplies the existing `AlphaPanelService`; it does no I/O at
construction. The shared service reserves the whole matrix and excludes every parent
member's full inspected interval before reading the result, calendar or prices. This
includes the parent's ETF controls even though only equities are compared here.

The adapter verifies the bound result, its manifest/input/calendar hashes, the exact
parent contract and each immutable dataset's bytes, content and metadata. It loads
the verified bytes without a second pathname read. Failed reads remain charged and
retain ordinary member checkpoints. Projection replay and qualification rules are
unchanged; there is no separate journal or execution mechanism.

The new manifest labels input origin as retained artifacts. Its receipts timestamp
current artifact reads; the hash-bound parent preserves original provider receipts
and raw evidence references. Current read timestamps must not be presented as new
market observations. Pure forecast preparation, basket accounting and cost curves
are shared with the original forecast study, whose version-1 semantics remain intact.

See the [first completed 90-comparison exercise](alpha-forecast-controls-2026-09-18.md) for results, endpoint gaps and independent verification.
