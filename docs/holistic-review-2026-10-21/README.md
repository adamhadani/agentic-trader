# Holistic system review — 2026-10-21

A whole-system expert review of signals, alpha mining, risk, positioning, portfolio optimisation,
data, execution, architecture and the roadmap. The operator asked for it on 2026-10-21:

> "take a minute to step back and do a more holistic high-level expert overview of the system and
> roadmap as a whole to identify any gaps, areas with potential for improvement across data
> gathering/processing/alpha mining, outside papers or work we can or should integrate"

It is a point-in-time snapshot. The code was read at main `db82817`, identical to production on that
day. Live figures come from read-only production queries covering 2026-09-14 to 2026-10-21. Line
numbers in citations drift as the code changes; the file and symbol names are the stable reference.

## Start here

1. [report.md](report.md): §0 has the executive summary and the top ten, and §5 the ordered roadmap
   (Phase 0 → Phase 3, operator decisions D1–D9, and what to stop).
2. [findings-index.md](findings-index.md): every canonical finding C01–C57 by priority, linked to its
   full entry.
3. The registers hold each finding's evidence, fact check and value review:
   [P0–P1](findings-p0-p1.md), [P2](findings-p2.md), [P3](findings-p3.md).

The report uses section IDs (CS1, SG-R1, MI-R1, RK-R1, …). The registers use the canonical IDs C01–C57.
The report integrates both, along with the gap findings.

## How it was produced

The review ran as one multi-agent workflow of 143 agents, with no failed agents.

| Stage | What it did | Output |
| --- | --- | --- |
| Map | 10 read-only readers: data, signals, alpha pipeline, pooled lane, risk, portfolio, execution, architecture, history, and live production (read-only SQL) | [maps/](maps/) |
| Critique | 7 expert lenses: quant methodology, data, ML/alpha mining, portfolio and risk, architecture, product/north star, and literature (citations checked on the web) | 91 findings, [lens-findings.md](lens-findings.md) |
| Consolidate | Merged duplicates into canonical findings | 57 findings, C01–C57 |
| Verify | Per finding: a fact checker told to refute it against the code, then a value critic assigning priority and horizon | 56 partially confirmed (corrections applied), 1 confirmed, 0 refuted. Priorities: 3 P0, 19 P1, 23 P2, 12 P3 |
| Synthesize | First report draft | [synthesis-draft.md](synthesis-draft.md) (superseded) |
| Critic | Completeness critic: missing topics and errors | [completeness-critic.md](completeness-critic.md) |
| Gap fill | 8 agents on the critic's missing topics | 23 findings, [gap-findings.md](gap-findings.md) |
| Revise | Built the final report from the maps, applying every correction and gap finding | [report.md](report.md), revision 2 |

## Files

| File | Contents |
| --- | --- |
| [report.md](report.md) | The final report (revision 2). Appendix A lists every correction; Appendix B is the evidence index |
| [findings-index.md](findings-index.md) | One row per canonical finding: priority, horizon, area, title |
| [findings-p0-p1.md](findings-p0-p1.md), [findings-p2.md](findings-p2.md), [findings-p3.md](findings-p3.md) | Each canonical finding in full: claim, evidence, recommendation, references, fact check (corrected claim, what was checked, bad references), value review (reasoning, risks, prerequisites, better alternative) |
| [gap-findings.md](gap-findings.md) | The 23 completeness-round findings. They are integrated in the report but were not separately fact-checked or value-rated |
| [lens-findings.md](lens-findings.md) | The 91 raw lens findings, kept for provenance |
| [completeness-critic.md](completeness-critic.md) | The critic's missing topics and the errors it found |
| [synthesis-draft.md](synthesis-draft.md) | The superseded first draft, which framed C01, C09 and C18 as the P0 actions |
| [maps/](maps/) | The ten subsystem maps: how each part works today, with `file:line` citations, gaps and ideas |

## Caveats

- Figures marked "measured in review" came from read-only scripts over frozen research frames, caches
  and the production journal. The scripts, the raw workflow journal (every agent's full output) and the
  workflow script are archived locally, not in Git, at `~/agentic-trader-research/holistic-review-20261021/`.
- The beta adjustment of the SPY control uses in-sample betas, so it is descriptive.
- The auto-entry book simulation used Yahoo 1h bars, whose buckets start at :30.
- Gap findings carry the gap agents' confidence only.

## Decisions and status

Recorded as they are made. The roadmap pointer is in
[alpha-roadmap.md](../alpha-roadmap.md#holistic-review-queue-october-21).

| Item | Decision (2026-10-21) | Status |
| --- | --- | --- |
| D1 PEAD probe | Renew for 180 days at unchanged risk; judge it on the counterfactual label over every eligible event, not on taps | Done 2026-10-21 14:46 ET: `apriori:pead:v2:long:fc8a5b3ec14845a2` renewed, registry generation 17 → 18, expires 2027-04-19, $100 per-trade cap unchanged |
| Phase 0 (report §5): static tier-1 calendar and typed fetch status; `UNKNOWN` regime state instead of 1.0×; journal-only swing and intraday scans with no card on daemon start (D2); broad-index ETFs excluded from native cards (D4); journaling of zero-candidate scans and per-rule rejections | Approved | Built on `feature/phase0-fail-safes`; merge # and deploy date recorded after merge. Due before the 2026-10-28 FOMC |
| D6 confirmation-window rule | Keep the frozen rule: the first finalist spends the window | Default for any future pooled campaign |
| Insider campaigns (C01) | The pooled `insider-v1` and `cohort3-insider-v1` campaigns are **not run**. Insider becomes one a priori event leg, shaped like PEAD: opportunistic or cluster officer/director purchases on cohort v3's band, entry once on the session after filing, an event-matched control and P1–P4. It does not touch the confirmation window | Decided. Next mining item after Phase 0; freeze its entry before reading any insider outcome |
| D3, D5, D7, D8, D9 | Open | — |
