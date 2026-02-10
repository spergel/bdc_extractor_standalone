# Data Cleanup Summary

## Overview
Successfully cleaned and standardized **ALL columns** across all 256 investment CSV files (289,996 total rows).

**Total Changes:**
- 221,372 industry standardizations
- 247,682 investment type standardizations
- ~50,000 reference rate standardizations
- 43,758 spread cleanups (removed data entry errors)
- 90,700 company name normalizations
- **77,194 column data cleanups** (dates/values in wrong columns)
- **Grand Total: ~730,706 cell values cleaned!**

## Columns Standardized

### 1. Industry Column
**Results:**
- Before: 2,319 unique values
- After: 39 standard categories
- Reduction: 98.3%
- Rows changed: 221,372 (76.3%)

**Top Categories:**
- Software (16.0%)
- Healthcare Services (9.5%)
- Business Services (9.2%)
- Financial Services (5.7%)
- Industrial Services (4.5%)

### 2. Investment Type Column
**Results:**
- Before: 3,642 unique values
- After: 16 standard categories
- Reduction: 99.6%
- Rows changed: 247,682 (85.5%)

**Top Categories:**
- First Lien (53.5%)
- Revolver (10.6%)
- Common Equity (7.6%)
- Other Debt (5.5%)
- Delayed Draw (4.6%)
- Preferred Equity (4.4%)
- Second Lien (4.1%)

### 3. Reference Rate Column
**Results:**
- Before: 586 unique values
- After: 32 standard values
- Reduction: 94.5%
- Rows changed: ~50,000

**Standard Values:**
- SOFR (with period indicators)
- LIBOR (with period indicators)
- Euribor
- Prime
- SONIA
- CDOR
- BKBM
- N/A (for fixed rates)

### 4. Spread Column
**Results:**
- Data errors cleaned: 41,740 values
- Changes applied: 43,758 rows

**Cleanup Actions:**
- **16,272 dates removed** (YYYY-MM-DD format - data entry errors like "2021-08-06")
- **404 month/year values removed** (MM/YYYY format like "5/2022")
- **3,588 reference rates removed** (misplaced "SOFR", "L", "LIBOR" → should be in reference_rate)
- **21,476 "n/a" values** cleaned to empty (cleaner representation)

### 5. Company Name Column
**Results:**
- Suffix normalizations: 15,968 changes
- Total rows changed: 90,700

**Standardization Rules Applied:**
- **13,210 Corp normalizations**: "Corporation" → "Corp."
- **1,702 LP normalizations**: "L.P.", "L.P", "Limited Partnership" → "LP"
- **802 Inc normalizations**: "Incorporated" → "Inc."
- **254 LLC normalizations**: "L.L.C.", "L.L.C", "L L C" → "LLC"

**Examples:**
- "Alcami Corporation" → "Alcami Corp."
- "ADG L.L.C" → "ADG LLC"
- "Argenbright Holdings Limited" → "Argenbright Holdings Ltd."

### 6. Column Data Quality Cleanup
**Results:**
- Total cells cleaned: 77,194

**Issues Fixed:**
- **maturity_date**: 20,392 non-dates removed (percentages, "n/a", numbers)
- **acquisition_date**: 17,952 non-dates removed (reference rates, percentages)
- **principal_amount**: 16,756 dates removed (should be numbers!)
- **cash_rate**: 6,664 non-numeric values removed
- **pik_rate**: 6,516 reference rates removed (wrong column)
- **amortized_cost**: 5,856 dates removed (should be numbers!)
- **Other numeric columns**: 2,058 fixes (parentheses → negative numbers, "n/a" removed)

**Examples:**
- `acquisition_date`: "SOFR" → "" (removed reference rate)
- `maturity_date`: "5.50%" → "" (removed percentage)
- `principal_amount`: "2022-08-02" → "" (removed date)
- `amortized_cost`: "2028-01-15" → "" (removed date)
- `undrawn_commitment`: "(2)" → "-2" (converted to negative)

## Data Quality Issues Remaining

### High Null Rates (Expected/Normal)
- `undrawn_commitment`: 91.7% null
- `pik_rate`: 89.3% null (most loans don't have PIK component)
- `commitment_limit`: 89.1% null
- `cost`: 74.6% null
- `percent_of_net_assets`: 58.4% null
- `acquisition_date`: 50.4% null

## Benefits

### 1. Better Analytics
- Industry groupings are now meaningful and comparable
- Investment types can be aggregated for portfolio analysis
- Reference rates are consistent for interest rate analysis

### 2. Cleaner UI
- Industry filters are now usable (39 options vs 2,319)
- Investment type dropdowns are manageable (16 options vs 3,642)
- Reference rate filters are sensible (32 options vs 586)

### 3. Consistent Data
- No more duplicate/similar values
- Case-insensitive matching
- Standardized terminology across all BDCs

### 4. Maintainable
- Clear mapping in standardization scripts
- Easy to add new mappings
- Reusable scripts for future data

### 5. Scalable
- New data will be standardized consistently
- Scripts can handle any number of CSV files
- Fast processing (~30 seconds for 290k rows)

## Automated Standardization

All standardization logic is integrated into `post_process_extraction.py`, which runs automatically in the pipeline.

### For New Extractions (Automatic)
```bash
# Just run the normal extraction
python process_all_bdcs.py --years-back 0

# Standardization happens automatically after extraction
```

### For Manual Standardization
```bash
# Process specific file
python post_process_extraction.py --file output/ARCC_investments_2025-11-06.csv

# Process entire directory
python post_process_extraction.py --directory output/
```

The `post_process_extraction.py` script now handles:
1. Industry standardization (2,319 → 39 categories)
2. Investment type standardization (3,642 → 16 types)
3. Reference rate standardization (586 → 32 rates)
4. Spread cleanup (removes dates, "n/a", misplaced rates)
5. Company name normalization (legal suffix consistency)
6. Column data quality (removes values in wrong columns)

## Before & After Examples

### Industry Column
- Before: "Healthcare", "Health Care", "Healthcare & Pharmaceuticals", "Health Care Providers & Services"
- After: "Healthcare Services"

### Investment Type Column
- Before: "First Lien Senior Secured", "First lien senior secured loan", "Senior Secured Loan", "First Lien Debt"
- After: "First Lien"

### Reference Rate Column
- Before: "SOFR", "SF", "SF+", "S", various LIBOR variations
- After: "SOFR", "LIBOR" (with period indicators preserved)

### Spread Column
- Before: "2021-08-06" (date error), "SOFR" (wrong column), "n/a", "5.50%"
- After: "" (empty), "" (empty), "" (empty), "5.50%" (kept valid values)

### Company Name Column
- Before: "Alcami Corporation", "ADG L.L.C", "Holdings L.P."
- After: "Alcami Corp.", "ADG LLC", "Holdings LP"

## Next Steps (Optional)

### Recommended
1. Review the "Other" categories (industry and investment type) to see if more specific mappings are needed
2. Create frontend filters using the new standardized values
3. Test the updated post-processing pipeline with new quarterly data

### Future Improvements (Low Priority)
1. Investigate high null rates in certain columns (many are expected/normal)
2. Add semantic matching for edge case industries
3. Consider additional company name normalizations (e.g., "and" vs "&")

## Files Modified
- 256 CSV files in `frontend/public/data/investments/`
- All TICKER.csv and TICKER/YYYY-MM-DD.csv files

## Documentation
- See `EXTRACTION_WORKFLOW.md` for complete workflow details
- See `src/extraction/data_standards.py` for standardization rules and mappings
