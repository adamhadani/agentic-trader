# A priori alpha catalog

An a priori alpha is a literature-documented anomaly tested as **one frozen hypothesis
per leg**, pooled over many names, before it may produce any card. It sidesteps the
mining funnel's multiple-testing problem (thousands of candidate formulas, few trades per
symbol) by spending one confirmatory test per leg.

Rules:

- Each entry is a JSON file under `config/research/apriori/`, committed before any data
  is read. Its SHA-256 goes into every run manifest. A changed file is a new version
  with a new, non-overlapping window — never an edit.
- Studies are research only: no registry, trial, shadow or promotion credit; no database,
  broker or Telegram access.
- A leg that passes becomes eligible for a live, capped paper probe, which needs its own
  spec (Part 2). A failed leg stays off.

Run: `copilot alpha apriori-study config/research/apriori/<entry>.json --output NEW_DIR [--cache DIR]`.

| Entry | Version | Hypothesis | Long | Short | Result |
| --- | --- | --- | --- | --- | --- |
| `pead` | 1 | Post-earnings drift when EPS surprise and price reaction agree | pending | pending | [spec](superpowers/specs/2026-09-25-apriori-pead-study-design.md) |
