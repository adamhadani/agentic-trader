# Pooled short-volume lane: FINRA Reg SHO daily short-sale fields on the cached cubes

Date: 2026-10-20. Status: design, written under the operator's standing design trust
(2026-10-09) after the operator approved the entry-side order on 2026-10-20 ("go ahead with next
items in order/priority you suggest"); the data-augmentation assessment of 2026-10-18 ranks this
source first. Research only. Nothing here touches the registry, broker, Telegram or promotion.

## 1. Goal and non-goals

Test whether daily short-sale flow carries a pooled edge on the existing long-bracket label
cubes, using the same frozen cohorts, cubes, windows, gates and checks as the events lane. The
lane adds one new, free, point-in-time source and changes only what a formula may read: four
dimensionless per-symbol, per-session **short-volume fields** derived from FINRA's Daily Short
Sale Volume Files (Consolidated NMS, `CNMSshvolYYYYMMDD.txt`), acquired once into a digest-pinned
private cache by a new research-only command.

Why this source first (assessment §2): it is free, published same day (FINRA posts each file by
18:00 ET on its trade date), available from 2018-08-01, covers 430 of cohort v2's 452 names on
2021-06-01 (the rest are later listings or renames), and has a direct literature prior: daily
short-sale flow predicts negative near-term returns (Boehmer, Jones and Zhang 2008; Diether, Lee
and Werner 2009), and the lane is long-only, so the hypotheses select names shorts are *not*
pressing.

Non-goals (rulings, recorded for the PR):

- **Off-exchange volume only.** The FINRA files cover trades reported to the TRFs/ADF, not
  exchange prints; `TotalVolume` in a file is therefore off-exchange total volume, not the day's
  consolidated volume. Every field is built from the file's own columns except `offex_share`, which
  divides the file's total by the raw bar volume and is a measure of off-exchange (largely
  retail/internalised) share, not a short-interest figure. No exchange short data is sought.
- **As served now.** FINRA occasionally re-posts a corrected file on a later day ("Updated"); the
  acquisition takes the file as served at acquisition time and records that. No bi-monthly short
  interest (source #7) in v1: a different cadence and a one-week lag need their own visibility rule.
- **No label, bracket, cohort, window, gate, bootstrap or stage change.** Both cubes (cohort v2,
  spec identity `f1c835a3d3364d2b…`; cohort v3) must be cache hits.
- **No live contract.** Live scans have no short-volume frames; a finalist (if any) would need its
  own feed contract (a daily fetch of yesterday's file) before any registry step.

## 2. Acquisition: `alpha short-volume`

`agentic_trader/research/short_volume.py` (new) and `cli/commands/alpha_short_volume.py` (new):

```
copilot alpha short-volume --from 2018-08-01 --through 2026-09-01 --cache DIR --output DIR
```

- One `GET https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt` per **NYSE
  session** in `[from, through]`, via a
  bounded `httpx.Client` (timeout 20 s, 8 MiB byte budget per file, streamed like
  `EquityMetadataSource.directory`), paced at most 4 requests per second, idempotent GETs only,
  one retry on a transport error. Weekends and holidays are never requested. Sessions come from
  Alpaca's exchange calendar (`AlpacaCalendarProvider` over a `BoundedTradingClient`, the desk's
  credentials from `load_config()`), the calendar the cube build reads, so the acquisition and the
  build agree on the session set and ad-hoc closures (the 2018-12-05 and 2025-01-09 days of mourning)
  are simply not sessions. The offline `DeterministicCalendarProvider` is not used because it lacks
  such closures (amended 2026-10-20 in the final review; the offline calendar's Juneteenth rule was
  also corrected to 2022 onward). The manifest records `calendar: "alpaca"`.
- Cache layout, `DIR/{YYYY-MM-DD}.txt` plus `DIR/{YYYY-MM-DD}.meta.json`
  (`requested_at`, `received_at`, `status`, `bytes`, `sha256`, `rows`), mirroring the Nasdaq
  page store, written directly into `--cache DIR` (the same `DIR` the pooled commands take as
  `--short-volume`); a cached session is never refetched. A session whose file answers 403/404 is recorded
  as `absent` (`status: "absent"`, no `.txt`); the files begin on 2018-08-01, and FINRA has posted
  every exchange session since, so an `absent` session **inside** the window fails the acquisition
  closed unless it is listed in `--allow-absent`, which is only for a session FINRA genuinely did not
  post (a documented exception list recorded in the manifest as given; a date that is not a session
  inside `[from, through]` is refused). The v1 window has 2,032 sessions and none absent.
- Parse on acquisition only to validate: header
  `Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`, every row's `Date` equal to the
  file date, volumes non-negative decimals (the 2026 files carry fractional shares), at least 1,000
  rows, and the final integer row-count trailer, which must be present and equal the data rows (a
  missing trailer is a truncated download). A file failing validation is kept on disk for evidence
  and fails the acquisition.
- `output/manifest.json`: window, sessions requested/present/absent, per-file digest lines, the
  **files digest** = SHA-256 of the newline-joined sorted lines `"<date>:<sha256>"` for the present
  sessions and `"<date>:absent"` for the absent ones (amended 2026-10-20 in the final review: an
  extra allowed absence changes the digest, so a protocol pins exactly which sessions may be absent),
  byte totals, `allow_absent`, `environment` (`research_environment()`), `command` (the argv). The
  loader and the campaign's pre-ledger check apply the same digest rule, and the pre-ledger check
  reads every present body against its meta file. Research only: no database, broker or Telegram.

Expected scale: about 2,040 sessions, 250–550 KB each, under 1 GB on disk, under 10 minutes.

## 3. Fields (`short_fields_v1`)

All fields are per symbol, indexed by NYSE trading day `T` (the cube build's full `trading_days`
calendar), and hold the state **known at T's regular close plus the file's same-day publication**
(by 18:00 ET), so `evaluate_prepared`'s read of row `D-1` for decision session `D` (10:35 ET) is
causal with at least 16 hours of slack. The builder reads the cached files from disk only.

Symbol mapping: FINRA writes share classes with `/` (`BF/B`, `BRK/B`) and preferreds with `p`
(`BFHpA`); Alpaca writes `.` (`BF.B`). A file row maps to a cohort symbol by replacing `/` with `.`;
preferreds and warrants (`/WS`, `/U`, `p`) never match a cohort name. Identity breaks are the events
lane's rule, verbatim: the protocol pins the cohort's corporate-actions acquisition
(`CorporateActionInputs`), `identity_breaks` (shared from `events.py`, imported, not copied) gives
each symbol's latest `name_change` break, and every field is `NaN` on `T < break_on`; file rows keyed
by the symbol before the break are discarded (META was FB, ELV was ANTM, XYZ was SQ).

Per symbol, let `S_T`, `V_T` be the file's `ShortVolume`, `TotalVolume` on T (missing when the
symbol has no row that day), and `B_T` the **raw** (unadjusted) bar volume on T.

| Field | Definition at T | Unit |
| --- | --- | --- |
| `short_ratio` | `S_T / V_T`; `NaN` when the row is missing or `V_T < min_total_volume` (protocol, 10,000 shares: thin days are noise) | dimensionless, in [0, 1] |
| `short_ratio_z` | `(short_ratio_T − mean) / std` over the baseline: the most recent `z_window` (20) **finite prior** values of `short_ratio` (strictly before T), all within the last `2 × z_window` (40) sessions `T-40 … T-1`; `NaN` when fewer than `z_window` finite values lie in that span, or when `std = 0` (sample std) | dimensionless |
| `short_volume_rel` | `S_T / mean(S over the same kind of baseline of the short-volume series)` (`NaN` with fewer than `z_window` finite prior values in `T-40 … T-1`, or a zero mean); a forward or reverse split is a baseline break for this series (below) | dimensionless |
| `offex_share` | `V_T / B_T`, `NaN` when either is missing or `B_T = 0`; values above 1.5 (a corrected file against a stale bar) set to `NaN` and counted | dimensionless |

Rulings: the trailing statistics exclude T so a spike is measured against its own baseline;
`z_window` is 20 sessions (one month, BJZ's horizon is days to weeks); a baseline is the last 20
finite prior values within 40 sessions, so one missing or thin day is skipped rather than blanking
the next 20 sessions, while a sparsely reported name stays `NaN` (amended 2026-10-20 during
execution); fields are not carried forward across missing days (a missing row is `NaN`, not stale),
unlike the sparse earnings fields, because the source is daily and dense. Splits (amended 2026-10-20 in
the final review): a `forward_split` or `reverse_split` corporate action on the symbol is a baseline
break for the share-count series only, so the `short_volume_rel` baseline never mixes sessions before
the split's `effective_on` with sessions on or after it (it is computed per segment between split
dates; the split session and the 19 after it are `NaN`); `short_ratio` and `short_ratio_z` are
ratios of same-day share counts and span splits unchanged. First finite session is 2018-08-01 for `short_ratio` and
`offex_share` and about 2018-08-29 for the two windowed fields; the discovery window
(2016-08-01 → 2021-12-31) therefore holds about 840 scored sessions, above the 400-session gate.
Coverage (`ShortCoverage`): symbols without any row, `unscored_rows` (rows whose symbol is not among
the scored names with an eligible cell; the loader keeps only the cohort's rows and counts the rest),
per-symbol first finite session per field, thin-day count, `offex_share` clips, identity breaks.

## 4. Protocol: `pooled-short-volume-campaign`

`ShortVolumeCampaignProtocol(CampaignProtocol)` in `research/pooled/campaign.py`, dispatched by
`load_campaign_protocol` on `id`, mirroring `EventsCampaignProtocol`:

```json
"id": "pooled-short-volume-campaign",
"score_inputs": "daily",
"short_inputs": {
  "fields_version": "short_fields_v1",
  "short_volume": {
    "source": "finra-regsho-cnms",
    "files_sha256": "<the acquisition manifest's files digest>",
    "files_from": "2018-08-01", "files_through": "2026-09-01",
    "z_window": 20, "min_total_volume": 10000, "max_offex_share": 1.5
  },
  "corporate_actions": { "source": "alpaca", "content_sha256": "<cohort acquisition>", "window": [...], "trailing_sessions": 252 }
}
```

`search_mode: fixed_set` is required (a genetic search on these fields needs check B and its own
spec). `score_inputs` is `"daily"` and included in the dump for this subclass; base-class, intraday
and events hashes are unchanged. The frozen formula document and every manifest gain
`short_inputs` next to `score_inputs`, so a short-volume formula never shares an identity with a
daily, hourly or events one. The runner fails closed when either digest does not match the cache
on disk, before any bar read or evaluation; the campaign command verifies both before the ledger
reserves anything. `excluded_families` is `cohort3-events-v1`'s list plus the eight `events-v1`
families (every earlier fixed set stays excluded).

Two frozen files, one per cached cube, everything else copied from the matching events protocol:

- `config/research/pooled/short-v1.json`: cohort v2, 5 bp, static-percentile universe,
  `events-v1`'s windows, gates, bootstrap, power and null seeds.
- `config/research/pooled/cohort3-short-v1.json`: cohort v3, 15 bp, the $5M floor,
  `cohort3-events-v1`'s values.

CLI: `alpha pooled power|null-check|campaign PROTOCOL --cache DIR --short-volume DIR
--corporate-actions ACTIONS_JSON` (`--short-volume` is the acquisition's cache directory; the two
options are required for a short-volume protocol and refused for any other; `--earnings-pages` is
refused for it). Manifests record the digests, not the paths.

## 5. Frozen fixed set `short-v1` (eight formulas, dimensionless)

The lane buys the top decile of the score each session, so each expression is signed to select
the names the hypothesis says will **outperform**. Windowed formulas divide by their condition
(`x / (cond)` is `x` in the window and `NaN` out of it, the events-lane ruling), so an out-of-window
cell is ineligible and the decile is taken among in-window names only.

| Family | Expression | Hypothesis (literature) |
| --- | --- | --- |
| `light_shorting` | `-short_ratio` | names with the lowest share of short-sale volume outperform the heavily shorted (BJZ 2008, DLW 2009; long leg of the daily short-flow spread) |
| `light_shorting_week` | `-ts_mean(short_ratio, 5)` | the same over one week of flow (BJZ's five-day aggregation) |
| `shorts_retreat` | `-short_ratio_z` | shorting below its own recent norm: informed sellers stepping back |
| `shorts_retreat_week` | `-ts_mean(short_ratio_z, 5)` | the same, smoothed over a week |
| `shorts_retreat_volume` | `-short_volume_rel` | short volume below its recent mean, independent of the day's total |
| `unopposed_strength` | `-short_ratio_z / (ts_sum(returns, 5) > 0)` | DLW: shorts are contrarian and step in after price rises; a rise they do not oppose continues |
| `squeeze` | `short_ratio_z / (ts_sum(returns, 5) < 0)` | the opposite sign, declared: abnormal shorting into a falling week reverses (a crowded-short bounce) |
| `quiet_tape` | `-offex_share` | a low off-exchange (retail/internalised) share: names outside the retail-attention tape (Boehmer, Jones, Zhang and Zhang 2021: retail herding precedes short-horizon reversals) |

`squeeze` is the pre-registered opposite of `shorts_retreat`'s mechanism; both are charged. Every
row is dimensionless (`short_*` fields and `offex_share` join `FIELD_UNITS` as `(0.0, 0.0)`, and
`returns` is dimensionless); the pin test verifies each under `require_dimensionless`. The two frozen
files' SHA-256 prefixes are pinned in `tests/research/pooled/test_short_v1.py` and recorded in the
results doc.

## 6. Implementation shape

- `research/short_volume.py` (new): `ShortVolumeStore(directory)` (`fetch_session`, `load(date)
  → rows | None`, `meta(date)`), `acquire_short_volume(client, sessions, directory, *, pace,
  allow_absent) → manifest`, `files_sha256(directory, sessions)`, `parse_file(text, expected_date)
  → tuple[ShortVolumeRow, ...]` (`symbol` mapped to the Alpaca convention, decimals as floats).
- `research/pooled/short_fields.py` (new): `SHORT_FIELDS = ("short_ratio", "short_ratio_z",
  "short_volume_rel", "offex_share")`, `load_short_volume(directory, inputs, sessions, symbols) → LoadedShortVolume
  (by_symbol, files_sha256, sessions, absent, rows, unscored_rows)` (disk only, digest verified against
  `.meta.json`), `build_short_frames(inputs, loaded, actions, raw, trading_days, symbols) →
  ShortFrames(frames, coverage)`; imports `identity_breaks` from `events.py` (formerly private
  `_identity_breaks`, the events module updated in the same change).
- `research/alpha/dsl.py`: the four names join `FIELD_UNITS` as dimensionless; a frame without
  them is unchanged (live frames).
- `research/pooled/scoring.py`: `ScoreBook.with_events` generalised to
  `ScoreBook.with_fields(adjusted, frames, field_names, trading_days, sessions, symbols)`; the
  events call site passes `EVENT_FIELDS`, the new one `SHORT_FIELDS`; `score_book` dispatches on
  `ShortVolumeCampaignProtocol`; `short_inputs_document(protocol)` beside `event_inputs_document`.
- `research/pooled/runner.py`: `ShortSources(short_volume_dir, actions_file)`,
  `load_short_sources`, `CubeBuild.short` built after the bars (cache-hit path included), only
  for cohort symbols with an eligible cell; raw bars are kept (as for events) for `offex_share`.
- `campaign_run.py`, `power.py`, `null_check.py`: `short_inputs` in the frozen document and
  manifests; every `result.json` records `short_coverage` (`null` for other protocols); the campaign
  result records `first_finite_session` per formula.
- `cli/commands/alpha.py`: `_pooled_field_sources` replaces `_pooled_event_sources` and returns the
  one source set a protocol needs, refusing the others' options.
- Acquisitions first, outside scan windows: `alpha short-volume --from 2018-08-01 --through
  2026-09-01` once (shared by both protocols); the cohort-v2 and cohort-v3 corporate-actions
  acquisitions already exist (digests `776d4df7…` and the cohort3-events-v1 pin) and are reused.

## 7. Checks, campaigns and closure (pre-registered)

Fixed set ⇒ checks **A** (power) and **C** (real-formula false acceptance) only, per protocol, on
the cached cube from a clean worktree at the merged revision, outside scan windows. A is
formula-independent and must replay the matching events protocol's result on the same cube; C
must show `errors_total: 0`. Each campaign runs only with the operator's explicit approval
(`--journal-scope production/alpaca:paper`), charges each formula to the pooled ledger before
scoring, and consumes the lane-wide confirmation window only for finalists. `no_finalists` on both
cubes closes the short-volume fixed set on this window; nothing promotes. Results doc:
`docs/alpha-pooled-short-v1-<date>.md`, with the coverage block and first-finite sessions.

## 8. Tests (plan must cover)

Acquisition: sessions only (no weekend/holiday request), cache hit never refetches, 403/404 →
`absent` and fails closed unless allowed, header/date/row validation failures keep the file and
fail, byte budget, files digest stable under row order (digest is over file bytes), manifest
contents, no network outside the FINRA host. Parser: `/`→`.` mapping, preferred `p` rows unmatched,
fractional volumes, a row dated another day fails. Fields: `short_ratio` on a thin day `NaN`;
`short_ratio_z` uses only prior sessions (a spike on T does not move its own baseline), `NaN` with
fewer than 20 finite priors, `NaN` at zero std; `short_volume_rel` likewise; `offex_share` `NaN`
without a raw bar and clipped above 1.5 (counted); a missing row is `NaN`, not carried forward;
identity break ⇒ `NaN` before the break and pre-break rows discarded. Windowed formulas `NaN` out of
window through `evaluate_prepared`. Digest mismatch (files or actions) fails closed before
evaluation and before the campaign's ledger opens. Base-class, intraday and events hashes unchanged
(pin test). Both frozen files parse, are dimensionless, freeze to identities distinct from the same
text under an events protocol, and refuse `search_mode: genetic`. `--short-volume` refused for a
non-short protocol; `--earnings-pages` refused for a short protocol.
