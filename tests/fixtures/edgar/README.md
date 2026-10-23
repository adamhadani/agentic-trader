# EDGAR Form 4 fixtures

Public SEC filings fetched 2026-10-23 for the live Form 4 parser's parity test
(`tests/data/test_edgar_form4.py`).

- `<accession>.txt` are full-submission files (`Archives/edgar/data/<cik>/<accession-no-dashes>/<accession>.txt`) for
  cohort-v3 issuers from 2026q3: 12 with open-market purchases (2 with several reporting owners), 3 sales only, 2
  Form 4/A amendments, 2 purchases by owners who are neither officers nor directors, 1 without non-derivative rows.
  Each is trimmed to the SEC header and its `4` / `4/A` document; exhibits are dropped.
- `expected.json` holds, per accession, the qualifying rows the data-set reader (`insider_filings.read_transactions`,
  Officer/Director, 10b5-1 trades kept) produces from the 2026q3 `form345` archive for that accession alone, plus its
  drop counts.
- `form.20260915.idx` is the daily form index for 2026-09-15, trimmed to its header and a few `4`, `4/A`, `10-K` and
  `8-K` rows.
