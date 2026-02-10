# Financial Statements Extraction & Consolidation

This directory contains tools to scrape financial statements (Balance Sheet, Income Statement, Cash Flow) from SEC filings and prepare them for use in the frontend.

## Overview

The workflow consists of three main scripts:

1. **`financial_statements_extractor.py`** - Scrapes financial statements from SEC filings
2. **`consolidate_financial_statements.py`** - Consolidates individual files into master CSV files
3. **`scrape_and_consolidate_financials.py`** - Complete workflow: scrape + consolidate

## Quick Start

### Option 1: Complete Workflow (Recommended)

Scrape and consolidate in one command:

```bash
# For a single ticker
python scrape_and_consolidate_financials.py --ticker MRCC --years-back 1

# For multiple tickers
python scrape_and_consolidate_financials.py --tickers MRCC ARCC MAIN --years-back 1
```

This will:
1. Scrape financial statements from SEC filings
2. Save individual files to `output/financials/`
3. Consolidate them into master CSV files
4. Place consolidated files in `frontend/public/data/`

### Option 2: Step-by-Step

**Step 1: Scrape financial statements**

```bash
# Single ticker, latest filing
python financial_statements_extractor.py --ticker MRCC --filing-type 10-Q

# Single ticker, specific year
python financial_statements_extractor.py --ticker MRCC --year 2025 --filing-type 10-Q

# Single ticker, historical (past year)
python financial_statements_extractor.py --ticker MRCC --years-back 1 --filing-type 10-Q
```

**Step 2: Consolidate files**

```bash
# Consolidate all files
python consolidate_financial_statements.py

# Consolidate only specific ticker
python consolidate_financial_statements.py --ticker MRCC
```

## File Structure

### Individual Files (output/financials/)

After scraping, individual files are saved as:
- `{TICKER}_{FILING_DATE}_balance_sheet.csv`
- `{TICKER}_{FILING_DATE}_income_statement.csv`
- `{TICKER}_{FILING_DATE}_cash_flow.csv`

Example:
- `MRCC_2025-11-05_balance_sheet.csv`
- `MRCC_2025-11-05_income_statement.csv`
- `MRCC_2025-11-05_cash_flow.csv`

### Consolidated Files (frontend/public/data/)

After consolidation, master files are created:
- `balance_sheets.csv` - All balance sheet data across all tickers and dates
- `income_statements.csv` - All income statement data across all tickers and dates
- `cash_flows.csv` - All cash flow data across all tickers and dates

## CSV Format

The consolidated CSV files have the following columns:

```
ticker,filing_date,statement_label,statement_type,line_item,concept,value,context_key,start_date,end_date,instant_date,duration_days,level,is_abstract,preferred_label,order_index
```

**Key Fields:**
- `ticker` - Company ticker symbol (e.g., "MRCC")
- `filing_date` - SEC filing date (YYYY-MM-DD format)
- `statement_type` - Type of statement ("balance_sheet", "income_statement", "cash_flow")
- `concept` - XBRL concept tag (e.g., "us-gaap_InvestmentOwnedAtFairValue")
- `line_item` - Human-readable line item description
- `value` - Financial value (numeric)
- `context_key` - XBRL context reference
- `start_date`, `end_date`, `instant_date` - Period information
- `duration_days` - Duration of period in days (for period-based items)

## Frontend Integration

The frontend automatically loads these consolidated CSV files from `/data/`:

```typescript
// Frontend code (client-csv.ts) automatically loads:
// - /data/balance_sheets.csv
// - /data/income_statements.csv
// - /data/cash_flows.csv

// Usage in frontend:
import { fetchFinancials } from './api/client-csv';

const financials = await fetchFinancials('MRCC', '2025-11-05');
// Returns PeriodFinancials with:
// - income_statement: Record<string, number | null>
// - balance_sheet: Record<string, number | null>
// - cash_flow_statement: Record<string, number | null>
// - full_income_statement: Record<string, { label, concept, value }>
// - full_balance_sheet: Record<string, { label, concept, value }>
// - full_cash_flow_statement: Record<string, { label, concept, value }>
```

## Examples

### Example 1: Update MRCC Financials for Past Year

```bash
python scrape_and_consolidate_financials.py --ticker MRCC --years-back 1 --filing-type 10-Q
```

### Example 2: Add New Ticker to Existing Data

```bash
# Scrape new ticker
python financial_statements_extractor.py --ticker ARCC --years-back 1 --filing-type 10-Q

# Re-consolidate all files (includes new ticker)
python consolidate_financial_statements.py
```

### Example 3: Just Consolidate Existing Files

If you already have individual files and just want to regenerate the consolidated files:

```bash
python consolidate_financial_statements.py
```

## Data Sources

Financial statements are extracted from:
- **10-Q filings** - Quarterly reports (most common)
- **10-K filings** - Annual reports

The extractor uses XBRL instance documents from SEC filings, which contain structured financial data in XML format.

## Troubleshooting

### No XBRL Documents Found

If you see "No XBRL instance document found", the filing may not have XBRL data available. Try:
- Checking if the filing exists on SEC.gov
- Verifying the filing type (10-Q vs 10-K)
- Checking if the filing date is correct

### CSV Field Size Limit Errors

If you see "field larger than field limit" errors, the script now automatically increases the CSV field size limit. If issues persist, check that the source files aren't corrupted.

### Duplicate Rows

The consolidation script automatically deduplicates rows based on:
- ticker
- filing_date
- concept
- context_key
- value

If you see unexpected duplicates, check the source files for data quality issues.

## Notes

- **Filing Dates**: Files are named using the actual SEC filing date, not the scrape date
- **Deduplication**: The consolidation process automatically removes duplicate rows
- **Data Format**: All values are stored as strings in CSV; the frontend converts them to numbers
- **XBRL Concepts**: The `concept` field uses XBRL taxonomy tags (e.g., `us-gaap_*`) which are standardized across all SEC filings








