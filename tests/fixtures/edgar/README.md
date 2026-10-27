# EDGAR responses (trimmed)

Real responses saved on 2026-10-27, trimmed so tests of `quantproof/ingest/edgar.py` run offline.

- `submissions_CIK0001665650.json`: `https://data.sec.gov/submissions/CIK0001665650.json`
  (JPMorgan Chase Financial Co. LLC); `filings.recent` cut to its first 6 entries, `files` to 2.
- `efts_search_lesser_performing.json`: EDGAR full-text search,
  `https://efts.sec.gov/LATEST/search-index?q="Lesser Performing"&forms=424B2&startdt=2025-11-01&enddt=2025-11-30`;
  `hits.hits` cut to 3, `aggregations` dropped (643 hits in total).
