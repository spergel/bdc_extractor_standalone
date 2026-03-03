# Recent Progress & Next Steps

_Last updated: 2026-03-03_

---

## What We Did Recently

### PFLT/PNNT company name extraction overhaul — DONE ✅

- **Problem**: PFLT had ~50% blank company names across all filings. Root cause: the PFLT/PNNT block
  in `_apply_ticker_specific_company_cleanup` treated ALL `"in Non-Controlled Non-Affiliated Portfolio
  Companies First Lien Secured Debt {Company}"` strings (without "Issuer Name") as section headers
  and cleared them. These are a valid XBRL member format used extensively by PFLT/PNNT.
- **Fixes applied** to `standardization_rules.py` PFLT/PNNT block:
  1. Leading `"` stripped (CSV escaping artifact in some XBRL members).
  2. Critical elif fix: "in Non-Controlled... {DebtType} {Company}" (no "Issuer Name") now correctly
     extracts the company name instead of clearing the row.
  3. Garbled/abbreviated older-format clears: `\ufffd` (Unicode replacement char), `in Non-Control
     Non-Affiliate...`, `n-Controlled...`, `Non-Affiliated Portfolio Companies...`, `FInvestments in
     Non-Controlled...`.
  4. Main regex made more flexible: `Issuer\s+Name\s*` (no required space before company).
  5. Added patterns for garbled "Issuer" (missing "I": `%ssuer Name`) and missing "Name" (`Issuer {Company}`).
  6. Section header regex: `%*` (zero or more %) and optional trailing `Total`.
  7. Current Coupon: `%?` (optional % for older filings), garbled `Current {X}upon`, bare `Current Coupon`.
  8. PFLT-specific industry-category names leaked as company names → cleared.
  9. Trailing `)` without matching `(` stripped.
- **Result**:
  - PFLT: 2,281 rows, **84 blank (3.7%)**, 387 unique companies (was ~50% blank before)
  - PNNT: 2,045 rows, **25 blank (1.2%)**, 351 unique companies
  - Remaining blanks are genuine industry-level XBRL subtotal members with no company name embedded.
- **Pipeline re-run**: post_process → consolidate → restandardize → resolve → 6,539 company exposures.

### CGBD company name prefixes (Task #5) — DONE ✅
- **Problem**: All CGBD named rows had prefixes like `"Investment Non-Affiliated Issuer First Lien Debt {Company}"`.
- **Fix**: Added CGBD block to `_apply_ticker_specific_company_cleanup` in `standardization_rules.py`.
  Strips `Investment Non-Affiliated Issuer`, `Investment Affiliated Issuer`, `Credit Fund` prefixes,
  then strips `First Lien Debt`, `Second Lien Debt`, `Equity Investments`, `Investment Funds` type prefix.
  Section-only entries (no company after stripping) → blank.
- **Result**: 74 rows → 45 unique companies, 0 blank, 0 prefixed.

### GSBD garbled percentage-format names (Task #7) — DONE ✅
- **Problem**: Names like `"216.4% United Kingdom - 2.3% 1st Lien - 2.3% Company Industry Software"` not cleaned.
- **Fix**: Expanded GSBD regex in `standardization_rules.py`:
  - Broadened geography match (any country, not just US/Canada)
  - Added `"Equity Securities - pct% Country - pct% Type - pct% Company"` pattern
  - Added filters for `9.07%` (rate-only), `Initial Acquisition Date`, `Maturity`, `Foreign Currency`,
    `Interest Rate Swaps`, `Total Liabilities`, `Inc.` (section/dimension-only values)
  - Strip trailing `"Industry <X>"` and `"Initial Acquisition Date <D>"` suffixes
- **Result**: 0 percentage-format names in GSBD frontend.

### XBRL member strings in reference_rate — DONE ✅
- **Problem**: Raw XBRL member names leaking into `reference_rate` field.
  Pattern: `YYYYMMDD#MEMBERNAME` or `YYYY#MEMBERNAME`.
  Affected: BCSF, CCAP, GSBD, LIEN, MFIC, MRCC, MSDL, PFLT, SCM, TCPC, TRIN, TSLX, WHF (2,655 rows).
- **Fix**: Added `_XBRL_MEMBER_TO_RATE` lookup in `standardize_reference_rate()` in `standardization_rules.py`.
  Maps all known BDC reference rate XBRL member names to clean strings (SOFR, LIBOR, CORRA, etc.).
  Unknown members → blank (rather than showing raw XBRL).
- Also fixed two BCSF frontend files (2023-02-28, 2023-05-09) directly — these predate the
  custom-scraper transition and aren't re-consolidated automatically.
- **Result**: 0 `#` characters in reference_rate across all frontend files.

### GSBD 2026-02-26 new filing
- New 10-K filing extracted: 492 rows, 49 unique companies in frontend (vs 128 before).
- Older GSBD filings (2025-05-08, 2025-08-07, 2025-11-06) still have high blank rates because
  their HTML SOI documents returned 503 during extraction. Retry when SEC is more accessible.

### Company name cleanup — comprehensive pass (2026-03-03) — DONE ✅

- **Audit → fix → re-audit loop** run multiple times across all frontend CSVs.
- Starting point: ~730 unique bad names. End state: **0 bad names** in all frontend CSVs.
- Fixed tickers: PFLT, PNNT, CCAP, ICMB, MFIC, GSBD, CGBD, TRIN, BCIC, BBDC, NMFC, SLRC + global BOM strip.
- Key patterns fixed:
  - PFLT/PNNT: flexible "Secured Debt [- X%] [Issuer] Issuer Name {Company}" via `[\s\-\d%,.]*` middle group.
    Catch-all for "in [Non-]Controlled ... Issuer Name {Company}"; section headers cleared.
    "Related Party PSLF Cash and Cash Equivalents..." money-market rows cleared.
    "Subordinate" (no d) and "Equity Security" (singular) variants.
  - CCAP: debt-type section header regex fixed (trailing whitespace not required after "Lien").
  - Global: BOM (`\ufeff`) stripped at top of `_apply_ticker_specific_company_cleanup`.
  - GSBD: "Second Lien" added to fragments; BOM now handled globally.
- Re-ran `scripts/restandardize_all.py` and `src/company_resolution/resolve_companies.py`.
  Result: 6543 company exposures, 6106 company details.

### Previous fixes (from prior session)
- TPVG 10-K duplicate fix (2025-03-05): 607→285 rows.
- Blank company_name filter in consolidation (skips rows with empty company_name).
- PSBD company name prefix cleanup.
- LIEN post_process_extraction error fix.

---

## Remaining Issues (Ordered by Priority)

### Task #4 — Blank company names (GSBD, PFLT, PNNT) [mostly fixed]

- **PFLT**: Fixed (see above) — down to 3.7% blank (84/2281). Remaining blanks are structural
  (industry-level XBRL subtotal members). Cannot be eliminated without switching to HTML/DSPy extractor.
- **PNNT**: Fixed similarly — 1.2% blank (25/2045). Same structural cause for remaining blanks.
- **GSBD**: Improved for new 2026-02-26 filing (492 rows / 49 companies). Older 2025 filings
  (Q2/Q3/Q4) still ~75% blank — SEC returned 503 for HTML SOI documents during extraction.

**To fix GSBD older filings when SEC is accessible**:
```bash
python process_all_bdcs.py --tickers GSBD --force
python src/consolidation/consolidate_investments.py
```

### Task #6 — Missing maturity dates (0%) in XBRL tickers

Several tickers report 0% maturity date coverage: BXSL, CGBD, GAIN, GLAD, MAIN, OBDC.
- These all use the XBRL extractor.
- The HTML enrichment step runs but apparently doesn't find maturity dates in the HTML SOI tables.
- **Root cause unknown** — needs investigation by fetching a sample filing and checking what the
  HTML SOI table headers/columns look like for these tickers.

**To investigate**: Fetch a BXSL or MAIN XBRL filing and log what `extract_soi_enrichment` returns
for maturity dates. Check if the HTML table has a "Maturity" column or if maturity dates are encoded
differently.

### Stale BCSF historical data (2017-2023)

The BCSF custom scraper only covers 2024+. The 2017-2023 BCSF data in the frontend comes from old
XBRL extraction (pre custom-scraper switch). These files are not being updated by current consolidation.
The XBRL member reference_rate strings have been manually cleaned in the 2023 files.

**Note**: If BCSF historical data needs refreshing, either:
1. Add `BCSF_investments_*.csv` back to consolidation for BCSF (alongside custom_scraper_BCSF_*)
2. Or re-run the XBRL extractor for BCSF and re-consolidate.

---

## Quick Commands

```bash
# Re-extract with fixes applied (when SEC is accessible)
python process_all_bdcs.py --tickers GSBD PFLT PNNT PSBD --force

# Re-run post-processing for specific tickers
python src/processing/post_process_extraction.py --directory output/

# Re-consolidate all
python src/consolidation/consolidate_investments.py

# Run DSPy scraper for a specific ticker/period
python scripts/run_dspy_scraper.py --ticker SSSS --start 2025-01-01 [--force]

# Check row counts
python scripts/check_extraction_totals.py
```
