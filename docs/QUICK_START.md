# Quick Start: Clean Data Extraction

## TL;DR - Extract New Quarterly Data

When new 10-Q filings come out:

```bash
# Extract all BDCs (includes automatic post-processing)
python process_all_bdcs.py --years-back 0

# Done! Data is cleaned, standardized, and ready for frontend.
```

## What Changed?

### Before (The Problem)
- 2,319 unique industries → hard to use
- 3,642 investment types → inconsistent
- Had to manually clean after extraction

### After (The Solution)
1. **LLM prompts now include standard categories** 
   - Tells LLM to use "Software" not "High Tech Industries"
   - Tells LLM to use "First Lien" not "First lien senior secured loan"

2. **Automatic post-processing catches mistakes**
   - Runs after extraction
   - Standardizes 5 columns: industry, investment_type, reference_rate, spread, company_name
   - Removes data entry errors
   - Happens automatically in the pipeline

3. **Result: Clean data from the start**
   - 39 industries (down from 2,319)
   - 16 investment types (down from 3,642)
   - 32 reference rates (down from 586)
   - 0 dates in spread column (removed 16,272 errors)
   - 15,968 company name normalizations (consistent suffixes)

## New Workflow

```
┌─────────────────┐
│ 1. Extract      │  LLM scrapes with standardization guidance
└────────┬────────┘
         │
┌────────▼────────┐
│ 2. Post-Process │  Automatic cleanup (catches LLM mistakes)
└────────┬────────┘
         │
┌────────▼────────┐
│ 3. Consolidate  │  Combine and move to frontend
└─────────────────┘
```

## Commands

### Extract Single BDC (Latest Quarter)
```bash
python llm_table_scraper.py --ticker ARCC --years-back 0
python post_process_extraction.py --file output/ARCC_investments_*.csv
```

### Extract All BDCs (Latest Quarter)
```bash
python process_all_bdcs.py --years-back 0
# Post-processing happens automatically
```

### Extract Multiple Years
```bash
python process_all_bdcs.py --years-back 2
# Gets last 2 years, post-processes automatically
```

### Manual Post-Processing
```bash
# If you need to re-clean data
python post_process_extraction.py --directory output
```

### Re-standardize Frontend Data
```bash
# Clean existing consolidated files
python standardize_industries.py --apply
python standardize_columns.py --apply
```

## Standard Categories

### Industries (39 total)
Top categories:
- Software
- Healthcare Services
- Pharmaceuticals & Biotechnology
- Financial Services
- Business Services
- Manufacturing
- Industrial Services

See `data_standards.py` for full list.

### Investment Types (16 total)
- First Lien
- Revolver
- Common Equity
- Delayed Draw
- Preferred Equity
- Second Lien
- (and 10 more)

See `data_standards.py` for full list.

### Reference Rates (32 total)
- SOFR, SOFR (Q), SOFR (M)
- LIBOR, LIBOR (Q), LIBOR (M)
- Prime, Euribor, SONIA, CDOR, BKBM
- N/A (for fixed rates)

## Verification

Check if extraction worked correctly:

```bash
# Count unique values (should be ~39 industries, ~16 types)
python -c "
import csv, glob
from collections import Counter

industries = Counter()
for f in glob.glob('output/*_investments_*.csv'):
    with open(f) as csvfile:
        for row in csv.DictReader(csvfile):
            industries[row.get('industry', '')] += 1

print(f'{len(industries)} unique industries')
print('Top 10:', industries.most_common(10))
"
```

Expected: ~39 industries, all from standard list.

## Files Modified

- `llm_table_scraper.py` - Enhanced prompts with standard categories
- `process_all_bdcs.py` - Added automatic post-processing step
- `post_process_extraction.py` - NEW: automatic data cleaning
- `data_standards.py` - NEW: central definition of standard categories
- `EXTRACTION_WORKFLOW.md` - Full documentation

## Need Help?

See `EXTRACTION_WORKFLOW.md` for:
- Detailed workflow explanation
- Troubleshooting guide
- Validation steps
- Best practices
