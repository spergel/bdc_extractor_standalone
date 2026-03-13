# Affiliation Tracking

BDC Schedule of Investments filings split holdings into three relationship tiers:

| Tier | Voting ownership | Typical share of portfolio |
|------|-----------------|---------------------------|
| **Non-Affiliate** | < 5% | Vast majority |
| **Non-Controlled Affiliate** | 5–25% | Small minority |
| **Controlled** | > 25% | Rare |

This data is not currently captured. Below is what to change when adding it.

---

## What to add

### Column

Add `affiliation` as the 17th column in every extractor output CSV, after `undrawn_commitment`.

Allowed values (use exactly these strings):
- `Non-Affiliate`
- `Non-Controlled Affiliate`
- `Controlled`
- empty string if the section header is ambiguous or absent

---

## Extractor changes

### `src/extraction/llm_table_scraper.py`

**Prompt (`_build_llm_prompt`):**

1. Change `EXACTLY 16 columns` → `EXACTLY 17 columns` everywhere in the prompt.
2. Append `affiliation` to the header line and the column list example.
3. Add column 17 definition:
   > `affiliation`: Relationship tier — fill down from the nearest section header above. Use exactly: `"Non-Affiliate"`, `"Non-Controlled Affiliate"`, `"Controlled"`, or empty if no header found.
4. Extend EXCLUDE THESE ROWS to explicitly exclude the three section header rows themselves (they set the fill-down value but are not data rows):
   > Affiliation section headers: "Non-Controlled/Non-Affiliated Investments", "Non-Controlled Affiliated Investments", "Controlled Investments" — do NOT emit these as data rows; use them only to set the fill-down affiliation value for subsequent rows.
5. Extend FILL-DOWN RULES:
   > DO fill down `affiliation` from the nearest preceding section header. Mapping: `"Non-Controlled/Non-Affiliated Investments"` → `"Non-Affiliate"`, `"Non-Controlled Affiliated Investments"` → `"Non-Controlled Affiliate"`, `"Controlled Investments"` → `"Controlled"`. Continue filling until a new section header appears.
6. Update example rows to show 17 values with `Non-Affiliate` in the last column.

**Parse guards:**

| Location | Change |
|----------|--------|
| `_parse_llm_response` line ~1273 | `expected_columns = 16` → `17` |
| `_parse_llm_response` line ~1303 | `if len(cols) > 16` → `> 17` |
| `_validate_numeric_fields` line ~1408 | `if len(cols) < 16` → `< 17` |
| `_repair_csv_row` docstring | `(16)` → `(17)` |
| `_OLD_deduplicate_csv_rows_DEPRECATED` | both `expected_columns = 16` → `17` |
| Two `while len(cols) < 16` in report/summary methods | → `< 17` |
| `if len(cols) != 16` in `_is_valid_data_row` helper | → `!= 17` |
| Hardcoded `if len(parts) > 16` in debug CSV writer | → `> 17` |

---

### `src/extraction/html_soi_parser.py`

The HTML parser builds rows from parsed table cells. After identifying which table section a row belongs to (Non-Affiliate / Non-Controlled Affiliate / Controlled), write that tier string into an `affiliation` field on each row dict before emitting.

The section header detection already exists in various forms (the parser skips section headers as non-data rows). Extend it to also track the current affiliation tier as state:

```python
current_affiliation = ""
for row in table_rows:
    header_text = row[0].strip().lower()
    if "non-controlled/non-affiliated" in header_text or "non-affiliate" in header_text:
        current_affiliation = "Non-Affiliate"
        continue  # skip header row
    elif "non-controlled affiliated" in header_text or "non-controlled/affiliated" in header_text:
        current_affiliation = "Non-Controlled Affiliate"
        continue
    elif "controlled" in header_text and "non-" not in header_text:
        current_affiliation = "Controlled"
        continue
    row_dict["affiliation"] = current_affiliation
    emit(row_dict)
```

---

### `src/extraction/xbrl_investment_extractor.py`

XBRL filings encode affiliation in the context dimension for each investment fact. The relevant dimension is typically `InvestmentAffiliationAxis` (or similar). Look up the dimension value on each fact and map it:

| XBRL member | Output value |
|-------------|-------------|
| `NonAffiliatedInvestmentsMember` (or similar) | `Non-Affiliate` |
| `NonControlledAffiliatedInvestmentsMember` | `Non-Controlled Affiliate` |
| `ControlledAffiliatedInvestmentsMember` | `Controlled` |

The exact member names vary by filer. Check a few XBRL instances (e.g. ARCC, GSBD) to confirm.

---

### `src/extraction/dspy_table_scraper.py`

Same approach as `llm_table_scraper.py` — add `affiliation` to the DSPy output schema and the prompt fill-down rules. The DSPy schema is defined as a typed dataclass/Signature; add an `affiliation: str` field there.

---

## Deduplicator change

### `src/extraction/data_cleaning/deduplicator.py`

After extracting `company_base` and `investment_type`, also extract `affiliation`:

```python
raw_affiliation = cols[16].strip().lower() if len(cols) > 16 else ""
if raw_affiliation in ("", "non-affiliate", "non-controlled/non-affiliate"):
    affiliation = "non-affiliate"
else:
    affiliation = raw_affiliation
```

Change the dedup key:

```python
# Before:
key = (company_base, inv_type)
# After:
key = (company_base, inv_type, affiliation)
```

Also update `expected_columns = 16` → `17` in both padding locations, and update `Dict[Tuple[str, str], ...]` → `Dict[Tuple[str, str, str], ...]`.

---

## Consolidation

No changes needed. `consolidate_investments.py` uses `csv.DictReader` and passes all fields through by column name, so `affiliation` flows automatically once it appears in the source CSVs.

---

## Frontend

Once the column is present in the per-period CSVs, the frontend can:

- Show the affiliation tier as a badge/tag on each holding row in `HoldingsTable.tsx`
- Add a filter dropdown in `SimpleAnalyticsPanel.tsx` to toggle between tiers
- Break out total fair value and position count by tier in the analytics panel

---

## Verification

After re-running a ticker that has all three tiers (FSK and HTGC both have Controlled positions):

1. Check output CSV has `affiliation` column populated — most rows `Non-Affiliate`, a handful `Controlled`
2. Verify row count is unchanged (no rows added/removed, just tagged)
3. Confirm dedup: a company in both Non-Affiliate and Controlled tiers produces 2 rows (different keys)
4. Run consolidation and confirm `affiliation` appears in `frontend/public/data/investments/FSK/*.csv`
