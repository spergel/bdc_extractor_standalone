# Data Extraction & Standardization Workflow

## Overview

This document describes the improved data extraction workflow that ensures clean, standardized data from the start.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. LLM Extraction (llm_table_scraper.py)                        │
│    - Prompts now include standard category lists                │
│    - LLM outputs standardized values directly                   │
└─────────────────────┬────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. Post-Processing (post_process_extraction.py)                 │
│    - Catches any LLM mistakes                                   │
│    - Standardizes: industry, investment_type, reference_rate    │
│    - Runs automatically after extraction                        │
└─────────────────────┬────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────┐
│ 3. Consolidation (consolidate_investments.py)                   │
│    - Combines individual CSVs                                   │
│    - Creates ticker rollups                                     │
│    - Moves to frontend/public/data/                            │
└──────────────────────────────────────────────────────────────────┘
```

## Data Standards

### Standard Categories Defined

All standard categories are defined in `data_standards.py`:
- **39 Industry Categories** (e.g., "Software", "Healthcare Services", "Financial Services")
- **16 Investment Types** (e.g., "First Lien", "Revolver", "Common Equity")
- **32 Reference Rates** (e.g., "SOFR", "LIBOR", "Prime")

## Workflow Steps

### 1. Extract New Data

For a single BDC:
```bash
# Extract last 2 years of quarterly filings
python llm_table_scraper.py --ticker ARCC --years-back 2
```

For all BDCs:
```bash
# Extract last year for all BDCs
python process_all_bdcs.py --years-back 1
```

**What Happens:**
- LLM extraction includes standardization guidance in prompts
- LLM attempts to use standard categories directly
- Raw output saved to `output/TICKER_investments_YYYY-MM-DD.csv`

### 2. Post-Process (Automatic)

The `process_all_bdcs.py` script now automatically runs:
```bash
python post_process_extraction.py --directory output
```

**What Happens:**
- Reads all `*_investments_*.csv` files in `output/`
- Applies standardization rules to:
  - `industry` column → 39 standard categories
  - `investment_type` column → 16 standard types
  - `reference_rate` column → 32 standard values
- Overwrites files with cleaned data
- Logs all changes made

You can also run this manually:
```bash
# Process single file
python post_process_extraction.py --file output/ARCC_investments_2025-11-06.csv

# Process directory
python post_process_extraction.py --directory output --pattern "*_investments_*.csv"
```

### 3. Consolidate

Run consolidation to combine and move to frontend:
```bash
python consolidate_investments.py
```

**What Happens:**
- Combines all dated CSVs per ticker into `TICKER.csv`
- Copies to `frontend/public/data/investments/`
- Updates `investments_index.json`

## Updated Scripts

### 1. llm_table_scraper.py (Enhanced)
**Changes:**
- Prompts now include explicit standard value lists
- Column definitions specify which values are allowed
- Provides LLM with examples of standardization
- Encourages LLM to output clean data from the start

**Key Prompt Additions:**
```
investment_type: Use ONLY these standard values: 
"First Lien", "Revolver", "Delayed Draw", ...
Examples: "First lien senior secured loan" → "First Lien"

industry: Use ONLY standard categories: 
"Software", "Healthcare Services", ...
Examples: "Healthcare Providers" → "Healthcare Services"

reference_rate: Use ONLY: 
"SOFR", "LIBOR", "Prime", "N/A", ...
Examples: "SF" → "SOFR", "L" → "LIBOR"
```

### 2. post_process_extraction.py (New)
**Purpose:** Catch-all standardization safety net

**Features:**
- Applies same standardization logic as `standardize_industries.py` and `standardize_columns.py`
- Can process single files or entire directories
- Logs all changes for transparency
- Idempotent - safe to run multiple times

### 3. process_all_bdcs.py (Updated)
**Changes:**
- Now automatically calls `post_process_extraction.py` after extraction
- Ensures all data is standardized before consolidation

**New Workflow:**
```
Extract → Post-Process → Consolidate
```

## Best Practices

### For New Quarterly Data

When a new quarter's 10-Q filings are released:

```bash
# Option 1: Extract specific tickers
python llm_table_scraper.py --ticker ARCC --years-back 0  # Latest only
python post_process_extraction.py --file output/ARCC_investments_2025-11-06.csv
python consolidate_investments.py

# Option 2: Extract all BDCs (recommended)
python process_all_bdcs.py --years-back 0  # Latest only
# Post-processing runs automatically
```

### For Historical Backfills

When adding historical data:

```bash
# Extract multiple years
python process_all_bdcs.py --tickers ARCC BBDC MAIN --years-back 3
# Post-processing runs automatically
```

### Manual Cleanup

If you need to re-standardize existing frontend data:

```bash
# Clean all consolidated files
python standardize_industries.py --apply
python standardize_columns.py --apply

# Or clean output directory before consolidation
python post_process_extraction.py --directory output
python consolidate_investments.py
```

## Validation

After extraction and post-processing, validate the data:

```bash
# Check unique values in extracted data
python -c "
import csv
from pathlib import Path
from collections import Counter

industries = Counter()
inv_types = Counter()

for f in Path('output').glob('*_investments_*.csv'):
    with open(f) as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            industries[row.get('industry', '')] += 1
            inv_types[row.get('investment_type', '')] += 1

print('Industries:', len(industries))
print(industries.most_common(10))
print('\nInvestment Types:', len(inv_types))
print(inv_types.most_common(10))
"
```

Expected Results:
- **Industries:** ~39 unique values (all standard categories)
- **Investment Types:** ~16 unique values (all standard types)
- **Reference Rates:** ~32 unique values (all standard rates)

## Troubleshooting

### LLM Still Producing Non-Standard Values

If the LLM continues to output non-standard categories:

1. The post-processor will catch and fix them
2. Check `debug_tables/` for the actual prompts being sent
3. Consider adjusting temperature (lower = more consistent)
4. Review examples in the prompt for clarity

### Post-Processor Not Catching Everything

If some values slip through:

1. Check if they're edge cases not covered by the mapping
2. Add new mapping rules to `standardize_industries.py` or `standardize_columns.py`
3. Re-run post-processor: `python post_process_extraction.py --directory output`
4. Re-consolidate: `python consolidate_investments.py`

### Validation Showing Too Many Categories

If validation shows >39 industries or >16 investment types:

1. Run post-processor again: `python post_process_extraction.py --directory output`
2. Check for new variations not in the mapping
3. Add them to the standardization scripts
4. Re-run: `python standardize_industries.py --apply && python standardize_columns.py --apply`

## Benefits of This Approach

### 1. Cleaner Extraction
- LLM understands the standard categories upfront
- Reduces post-processing work
- More consistent output across different filings

### 2. Automatic Safety Net
- Post-processor catches any LLM mistakes
- No manual intervention needed
- Runs automatically in the pipeline

### 3. Easy Maintenance
- Standard categories defined in one place (`data_standards.py`)
- Easy to add new categories or adjust mappings
- Standardization logic reusable across scripts

### 4. Better Data Quality
- Reduces "billion industries" problem from 2,319 → 39
- Reduces investment type variations from 3,642 → 16
- Immediate validation after extraction

## Future Improvements

### Potential Enhancements

1. **LLM Few-Shot Learning**
   - Add real examples from past extractions to prompts
   - Show LLM exactly what good output looks like

2. **Confidence Scoring**
   - Post-processor could flag low-confidence standardizations
   - Human review for edge cases

3. **Semantic Matching**
   - Use embeddings to match non-standard values to standard categories
   - Better handling of creative industry descriptions

4. **Validation Hooks**
   - Pre-consolidation validation step
   - Reject data that doesn't meet standards
   - Alert on unexpected categories

## Summary

The new workflow ensures clean data from extraction through to the frontend:

1. **Enhanced LLM Prompts** → Guide LLM to use standard values
2. **Automatic Post-Processing** → Catch and fix any mistakes
3. **Existing Consolidation** → Move clean data to frontend

This two-layer approach (LLM guidance + post-processing) ensures maximum data quality while maintaining flexibility for edge cases.
