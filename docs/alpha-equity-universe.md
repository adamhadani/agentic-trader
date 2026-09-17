# Prospective individual-equity candidates

`copilot alpha universe-snapshot PROTOCOL.json --output NEW_PRIVATE_DIRECTORY`
retains a dated candidate cohort from **current** Alpaca asset metadata and Nasdaq's
public symbol directories. It neither reads prices nor mines/promotes an alpha.
The current frozen protocol is
[`prospective-equity-300-v2.json`](../config/research/prospective-equity-300-v2.json).
The failed [v1 protocol](../config/research/prospective-equity-300-v1.json) remains
historical evidence and cannot run under the new version.

## What the cohort means

Alpaca `us_equity` includes ETFs. The public Nasdaq directories expose ETF,
NextShares and test flags, but do not establish a reliable common-stock versus
preferred/warrant/ADR/closed-end-fund taxonomy. We do not infer instrument subtype
from a company's name. The selected class is **listed non-ETF equity candidate**;
`instrument_subtype=unknown` remains explicit. Descriptive development research
can use these candidates with those limitations; the snapshot cannot qualify an
alpha or establish historical membership, borrow availability or market capacity.

The v2 protocol selects up to **300** current active/tradable NASDAQ, NYSE or
AMEX assets with an exact published Nasdaq/CQS symbol match, ETF/test/NextShares
flags clear, matching listing exchange, and normal Nasdaq financial status.
Selection is the lowest SHA256 of frozen seed plus **Alpaca asset UUID**, with
UUID tie-breaking. This is reproducible sampling, **not liquidity ranking**.
No alphabetical cap, security-name heuristic, symbol punctuation conversion,
price winner selection or substitution for excluded/unknown observations occurs.
Exactly one currently active/tradable UUID may resolve a symbol. Inactive or
nontradable historical identities remain separate rows; multiple current eligible
UUIDs exclude all ambiguous candidates. Both symbol directories are mandatory.
All broker members—including inactive, nontradable and excluded assets—remain in
the snapshot with exact IDs, metadata and reasons. Target shortfalls are explicit.

Official references:

- [Alpaca assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1): current
  assets and status/class/exchange filters; omitting status retains all statuses.
- [Nasdaq symbol lookup](https://www.nasdaqtrader.com/Trader.aspx?id=symbollookup):
  information is current trading-day metadata.
- [Directory fields](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs) and
  [ETF/NextShares fields](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2016-36).
- [Nasdaq Daily List specification](https://www.nasdaqtrader.com/content/technicalSupport/specifications/dataproducts/dlcompletespec.pdf):
  issue/subissue types, effective dates and delisting events are a different product;
  the public-directory adapter does not claim these fields or that entitlement.

## Evidence and layering

The CLI injects dedicated bounded SDK/HTTP clients and the existing
`AlphaRepository`. The application service freezes the exact protocol/identity and
charges **one metadata-selection attempt** before any external request, preserving
failures with no refunds. This is not one alpha comparison per listed security.
There are no price reads or price holdout exclusions. A later liquidity or forecast
study must declare/charge its own full matrix and exclude every inspected member
and warmup interval **before** acquisition.

The bounded official SDK response observer saves the full `/v2/assets` JSON before
SDK parsing. Directory response bytes are base64-preserved with SHA256 before
parsing, including malformed input. Request/receipt timestamps, source hashes,
protocol, environment and typed failures are immutable private artifacts. Raw bounded
asset JSON is retained even when empty or malformed. Both the application acquisition
clock and raw transport timestamps must be ordered: requests cannot precede the
frozen manifest or prior receipts, and raw receipts must lie inside their actual
request/receipt interval. Rollback fails the charged attempt with receipts preserved.
Directory
generation dates must be no more than seven calendar days old and cannot be future
dated; their date is freshness evidence, not an inferred receipt time. Each source
has a 32 MiB parsed-response capture ceiling, with 100,000 rows and 500 candidate
hard limits. Requests are sequential with configured socket deadlines; acquisition
and computation run off the asyncio loop. The SDK asset endpoint is not paginated;
its decoded response byte budget is checked before retention, and bounded responses
are retained before structural validation.

The result is projected through the existing diagnostic journal. No migration,
second journal, daemon, order queue or notification path is introduced. Failed and
partial raw evidence remains available. An interrupted manifest/reservation cannot
be called a completed cohort. Existing output directories cannot be overwritten.

`--previous PREVIOUS/snapshot.json` verifies that snapshot's identity and retains
added/removed asset IDs and changed full rows, including renames and status changes.
It does not overwrite older membership or infer unobserved changes between captures.
Missing assets remain removal evidence in the new report, not deleted history.

## Consuming a frozen cohort

```python
from agentic_trader.research.alpha.equity_universe import candidate_symbols

symbols = candidate_symbols(snapshot, at=aware_current_timestamp)
```

The helper verifies the immutable identity and refuses a timestamp before the
maximum actual acquisition receipt. This is a dated observation, **not a guarantee
that membership stays valid later**. The consumer must bind its protocol to the
`snapshot_id` and raw evidence hashes, refresh eligibility when required, and keep
the `common_stock_classification=False` and `liquidity_screened=False` limitations.
Do not convert today's sample into survivorship-free historical constituents.
Prospective return/label collection starts strictly after observation; preceding
prices may support separately declared development features or a liquidity screen.

## Daily liquidity and coverage screen

```bash
uv run copilot alpha liquidity-study PROTOCOL.json --universe SNAPSHOT.json --output NEW_PRIVATE_DIRECTORY
```

The source-specific daily screen now consumes the **entire** selected snapshot
through the existing daily-study acquisition/journal service. Its frozen protocol
binds the snapshot hash, feed, raw native-daily semantics, observed exchange
calendar, acquisition interval, trailing lookback and selection rules. A consumer
cannot supply a preferred subset or replace asset UUIDs/symbols. The snapshot's
receipt must precede the screen's actual observation time, and every inspected
daily date must be strictly before the current New York date. These fences do not
backdate current membership or historical bar availability.

The first declared study will inspect August 1–September 16, 2026 on **IEX**, then
select up to **64** candidates using the last **20 observed exchange sessions**.
Each eligible member needs all 20 daily bars, positive volume on all 20, and a
latest completed close of at least **$5**. Ranking uses median daily `close × volume`
over those same sessions, descending, with exact asset UUID tie-breaking. The
minimum median dollar-volume policy is zero: selection ranks measured IEX activity
without pretending an arbitrary cutoff measures consolidated market capacity.
The actual source run and resulting cohort remain pending.

All snapshot members retain their UUID, symbol, unknown subtype, full acquired
coverage, trailing coverage, available metrics and exclusion reasons. Missing dates
before the required trailing window remain evidence without changing trailing
eligibility. A successful zero-row source response is known missing coverage;
zeros are not filled, and fewer eligible names produce an explicit shortfall.
Transport failures, unaccounted missing frames, malformed OHLCV or incompatible
feed/adjustment/clock evidence withhold the **entire** final selection and mark the
attempt failed. Valid members' diagnostics remain available; failed attempts do not
refund the charged trial or authorize selecting around unknown evidence.

The screen charges one declared selection attempt, excludes every inspected
member/interval before source access, and preserves acquisition receipts and
artifacts through the shared research journal. It evaluates at most 500 metadata
candidates, no more than 90 calendar days, and selects at most 64. These are
acquisition/screen bounds; existing forecast panels still require their own frozen
universe and complete coverage. No daemon enrollment, registry promotion, order,
notification or automatic live threshold update follows from a screen.

IEX volume measures one venue. A resulting cohort has evidence of IEX activity,
not consolidated liquidity, borrow availability or a participation/capacity limit.
Raw prices require corporate-action review before return experiments. Actual-run
findings, independent numerical verification and deployed health must be recorded
separately from unit/SDK/database test results.

## Next stages and qualification limits

1. Run and independently verify the frozen daily liquidity/coverage screen over
   this complete sampled cohort; preserve missing/ineligible members and all
   read/exclusion history. Implementation alone does not establish a usable cohort.
2. Freeze a bounded economic-hypothesis/Ridge-control experiment over the screened
   cohort, with purged chronological labels, declared costs and cohort transfer.
   Retrospective tests selected using today's membership/liquidity are explicitly
   survivor-conditioned development evidence, not historical tradable universes.
3. Acquire authoritative instrument subtypes/sectors, historical eligibility/delistings,
   corporate-action and actual feed/execution evidence before qualification.

Comparing feeds requires a separately frozen source-specific mapping/calibration.
Nothing here relaxes the existing panel member limit, complete-coverage contract
or qualification gates. Prospective labels begin only after the cohort and policy
were actually observed and frozen; historical development dates stay consumed.

Tests cover deterministic sampling, unknown subtypes, exact metadata joins,
failures, stale/future directory dates, tampered/backdated consumption,
removals/renames, journal ordering, immutable capture and projection replay. SDK
asset integration uses the real client and loopback HTTP; directory integration
uses real HTTPX streaming against a separate loopback HTTP server. PostgreSQL tests require the usual disposable `test_*` DB.
Actual source capture and deployment verification are recorded separately below.


## Actual metadata capture — September 17, 2026

The frozen v1 attempt retained 33,509 broker assets plus 5,619 Nasdaq-listed and
7,634 other-listed rows, both directories dated September 17. It failed because
the master asset list contains repeated symbols across distinct historical IDs:
504 rows cover 254 duplicate symbol occurrences (274 inactive, 230 active).
Requiring every broker symbol to be globally unique was an adapter assumption,
not evidence of corrupt source data. The failed attempt and raw evidence remain
unchanged and charged. No prices were read.

Three red-first parameterized regressions then established the v2 policy: retain
all UUIDs, allow one active/tradable identity, exclude multiple current candidates.
There were **zero** multiple-active/tradable symbols in the first actual capture.
V2 froze that resolution rule and repeated metadata acquisition with a new manifest
and another charged attempt; no other sampling parameter changed.

V2 completed at **2026-09-17 17:32:02 UTC**: **6,721 eligible candidates, 300 selected**,
from all 33,509 retained broker rows. Selected listings are NASDAQ 165, NYSE 118 and
AMEX 17. Subtypes, sector membership and liquidity remain unclassified/unscreened.
Two metadata attempts were charged in total: one failed, one completed; zero alpha
comparisons, promotions, price reads, broker mutations or Telegram sends.

- Protocol identity: `59cc99d1c43d4cb3e753d10240c5ec73182c2b96b93264a6e62d2a6dc26cf645`.
- Snapshot identity: `bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f`.
- Private artifact root: `~/.local/state/agentic-trader/research/equity-universe-20260917/`.

An independent standard-library calculation rebuilt eligibility and stable-hash
sampling directly from the captured source bytes: all 300 identities matched.
It verified source/artifact hashes and both journal reservations preceding provider
access. The private `independent-audit.json` binds that script and both attempts.
An additional root-agent audit in `v2/independent-audit.json` independently matched
all UUIDs, eligible counts, all 300 identities in order, listing counts and three
source hashes without importing application code. A separate `clock-audit.json`
verified both retained captures against the final receipt fences without recapture.

Focused verification: **36 tests passed**, including real SDK/TCP and HTTPX streams
on SQLite and disposable PostgreSQL. The disposable database was dropped after
verification. All-file pre-commit checks passed before commits.

This is metadata selection evidence, not evidence that the cohort contains alpha
or that the daemon has begun collecting these symbols.
