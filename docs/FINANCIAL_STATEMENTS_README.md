# Financial Statements Extraction

Quarterly financial statements (Balance Sheet, Income Statement, Cash Flow)
are extracted from SEC XBRL filings for every BDC and written as lightweight
per-ticker JSON files used directly by the frontend.

## How it works

`src/extraction/financial_statements_extractor.py` uses the SEC **company facts
API** (`data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`) — one JSON per
company containing every XBRL fact ever filed. The extractor:

1. Loads ticker → CIK from `data/company_tickers.json` (plus manual overrides
   for renamed/acquired tickers such as `SSSS` and `MRCC`).
2. Downloads and caches the company facts JSON to `output/financials/companyfacts/`.
3. Keeps 10-Q / 10-K facts with period ends from 2019 onward and builds
   quarterly series for a curated set of us-gaap concepts plus BDC-specific
   concepts matched by label (`net investment income`, `incentive fee`, etc.).
4. Derives `total_debt`, `debt_to_equity`, `asset_coverage`, `nav_per_share`,
   and `nii_per_share`.
5. Writes:

```
app/public/data/financials/{TICKER}_financials.json   per-ticker 3-statement data
app/public/data/financials/financials_index.json      coverage summary
```

### Output JSON shape

```json
{
  "ticker": "ARCC",
  "entity_name": "ARES CAPITAL CORPORATION",
  "periods": [{"end": "2026-09-30", "form": "10-Q", "filed": "2026-10-27"}],
  "statements": {
    "balance_sheet": {
      "Assets": {"concept": "us-gaap:Assets", "unit": "USD",
                 "series": {"2026-09-30": {"val": 30596000000, "filed": "2026-10-27", "form": "10-Q"}}}
    },
    "income_statement": {},
    "cash_flow": {}
  },
  "derived": {
    "total_debt": {}, "debt_to_equity": {}, "asset_coverage": {},
    "nav_per_share": {}, "nii_per_share": {}
  }
}
```

Balance-sheet series are quarter-end snapshots. Income and cash-flow series are
duration rows; the extractor prefers quarter-length rows (10-Q) and keeps
semi-annual / 9-month / annual rows when those are all the filer reports
(`duration_days` tells the UI which is which).

## Commands

```bash
# One ticker
python src/extraction/financial_statements_extractor.py --ticker ARCC

# Specific tickers
python src/extraction/financial_statements_extractor.py --tickers ARCC MAIN BBDC

# All tickers in the investments index
python src/extraction/financial_statements_extractor.py --all
```

`process_all_bdcs.py` runs `--all` automatically after investment
consolidation (use `--skip-financials` to opt out).

## Frontend analytics

`src/processing/build_analytics.py` joins the holdings CSVs with the financial
JSONs and writes:

```
app/public/data/analytics/{TICKER}.json   per-filing-period breakdowns
app/public/data/analytics/peers.json      cross-BDC comparison (latest periods)
```

Breakdowns include investment types, sectors, acquisition-year vintage,
portfolio age buckets, maturity buckets, weighted average cash rate/spread, and
top companies. `process_all_bdcs.py` rebuilds these automatically (use
`--skip-analytics` to opt out).

## Historical note

Earlier versions downloaded each filing's XBRL instance document and wrote raw
per-filing CSVs (`output/financials/{TICKER}_{DATE}_balance_sheet.csv`) which
`consolidate_financial_statements.py` merged into the large
`{TICKER}_balance_sheet.csv` / `{TICKER}_income_statement.csv` files still in
`app/public/data/financials/`. Those raw CSVs remain for reference, but the
frontend now consumes the compact `{TICKER}_financials.json` files above.
