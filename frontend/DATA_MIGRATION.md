# BDC Frontend - Data Structure Update

## Overview

The frontend now uses consolidated, standardized CSV files from `/data/` directory instead of per-ticker JSON files.

## Available Data Files

### 1. Core Data
- **`/data/investments.csv`** - All holdings across all BDCs (31 MB)
- **`/data/income_statements.csv`** - Income statements (2.88 MB)
- **`/data/balance_sheets.csv`** - Balance sheets (0.87 MB)
- **`/data/cash_flows.csv`** - Cash flow statements (2.45 MB)

### 2. Pre-Aggregated Summaries
- **`/data/portfolio_summaries.csv`** - Portfolio metrics by BDC & date (20 KB)
- **`/data/company_exposures.csv`** - Cross-BDC company exposure (1.13 MB)
- **`/data/industry_summaries.csv`** - Industry breakdown (20 KB)

## New Data Fields

### Cleaned/Standardized Fields (prioritize these!)

```typescript
// Investment data now includes:
company_name_clean          // Clean company name
industry_clean              // Standardized industry
investment_type_standardized // Standardized type
asset_class                 // Senior Debt, Equity, Warrants, etc.
seniority                   // Senior Secured, Second Lien, etc.

// Dollar amounts (all in thousands)
fair_value_thousands
principal_amount_thousands
cost_thousands
amortized_cost_thousands

// Interest rates (as decimals, e.g., 0.0625 = 6.25%)
total_interest_rate
spread_clean
reference_rate_clean

// Derived analytics
investment_size_category    // <$1M, $1-5M, etc.
investment_age_years
remaining_term_years
effective_yield_pct
is_floating_rate
is_fixed_rate
is_performing
is_pik
```

### Legacy Fields (backward compatibility)

Original fields like `company_name`, `fair_value`, `interest_rate` are still present but use the cleaned versions when available.

## Component Updates

### `HoldingsTable.tsx` ✅
- Updated to use `company_name_clean` / fallback to `company_name`
- Uses `fair_value_thousands` with $K label
- Shows `total_interest_rate` as percentage
- Added `asset_class` column

### `holdingsDiff.ts` ✅
- Updated to match holdings using cleaned company names
- Compares using `_thousands` fields
- Handles both cleaned and legacy field names

### `adapter.ts` ✅
- New functions: `loadInvestments()`, `loadPortfolioSummaries()`, `loadCompanyExposures()`, `loadIndustrySummaries()`
- `loadBDCInvestments(ticker, date?)` - Get holdings for a specific BDC
- `getBDCList()` - Get all available BDCs and their filing dates
- Typed exports for all data structures

### New: `BDCComparison.tsx` ✅
- Side-by-side portfolio comparison using `portfolio_summaries.csv`
- Select up to 6 BDCs
- Shows size, asset mix, yield, diversification metrics
- Export to CSV

## Migration Guide

### Before (Old JSON structure):
```typescript
const data = await fetchPeriodSnapshot('ARCC', '2024-09-30');
const holdings = data.investments;
```

### After (New CSV structure):
```typescript
import { loadBDCInvestments } from '../data/adapter';

const holdings = await loadBDCInvestments('ARCC', '2024-09-30');
// or for latest:
const holdings = await loadBDCInvestments('ARCC');
```

## Benefits

1. **Faster Loading** - Pre-aggregated summaries load instantly
2. **Cross-BDC Analytics** - Easy to compare and analyze across all BDCs
3. **Standardized Data** - Consistent fields, units, and formats
4. **Better UX** - Show dollar amounts as "$1,234K" instead of raw numbers
5. **Rich Analytics** - Asset class, seniority, yield metrics out of the box

## Example: Portfolio Comparison Page

```typescript
import BDCComparison from '../components/BDCComparison';

// Shows side-by-side metrics for selected BDCs
<BDCComparison tickers={['ARCC', 'MAIN', 'GBDC', 'BXSL']} />
```

## Example: Company Exposure Analysis

```typescript
import { loadCompanyExposures } from '../data/adapter';

const exposures = await loadCompanyExposures();

// Find companies with multiple BDC investors
const multiInvestor = exposures.filter(c => c.num_bdcs_invested > 1);

// Sort by total exposure
exposures.sort((a, b) => b.total_exposure_millions - a.total_exposure_millions);
```

## Performance Notes

- Large files (31 MB investments.csv) load efficiently via streaming
- Use `portfolio_summaries.csv` for dashboard overviews instead of loading full holdings
- Filter by ticker client-side or use the loadBDCInvestments() helper
- All dollar amounts in thousands reduce file size and make calculations easier

## Testing

Run the dev server and test:
```bash
npm run dev
```

Navigate to compare page to see side-by-side BDC metrics using the new data structure.



























