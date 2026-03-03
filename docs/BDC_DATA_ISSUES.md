# BDC Data Issues — Fix Backlog

Status as of 2026-02-19. Go through these one by one.

---

## ✅ Working / Leave Alone

| Ticker | Notes |
|--------|-------|
| BBDC | Okay |
| CCAP | Okay |
| GBDC | Seems okay |
| HRZN | Seems okay |
| HTGC | Seems okay |
| KBDC | Seems okay, minor naming issue |
| MFIC | Seems okay |
| MSDL | Seems okay |
| NCDL | Seems okay |
| SLRC | Okay |
| TCPC | Seems good |
| TRIN | Seems good |

---

## ✅ Custom scraper (use instead of XBRL)

| Ticker | Notes |
|--------|-------|
| BCIC | **Done.** `scripts/run_custom_scraper_bcic.py` → `output/custom_scraper_BCIC_{date}.csv`. We **do not include affiliates** in the report total: CSV has a `schedule` column (Non-Affiliate / Affiliate). Sum only Non-Affiliate for report-aligned total (~$540M); full file includes affiliate holdings (e.g. Series A-Great Lakes). Prior year-end comparative tables skipped for 10-Q. Company names normalized (footnotes, - Warrant, - Term Loan A/B, etc.). |
| BCSF | **Done.** `scripts/run_custom_scraper_bcsf.py` → `output/custom_scraper_BCSF_{date}.csv`. Company names and section rows normalized via custom_table_scraper. |

**Next:** Pick from **🔧 Needs Fixes** below (e.g. ARCC sectors, CGBD, CSWC, FDUS, etc.) or add another BDC from the pipeline list.

---

## 🔧 Needs Fixes (specific issues)

### ARCC
- **In progress:** Most holdings were **Other** sector. Cause: ARCC XBRL contexts don’t include industry axis on line-item dimensions; industry must come from HTML enrichment. Changes made: (1) HTML SOI parser: more industry header patterns (GICS, Sub-Industry, Industry classification), (2) two-row header merge so the industry column is detected when it appears in the second header row. Re-run extraction and consolidate to verify: `python process_all_bdcs.py --tickers ARCC --years-back 1 --skip-financials --skip-profiles --force`, then check industry mix in `frontend/public/data/investments/ARCC.csv`.

### CGBD
- Wrong sectors
- Maturity dates are wrong

### CSWC
- ~50% classified as **Other** sector

### FDUS
- Incorrectly putting things in **Financial Services**
- Company name fix needed

### GECC
- ~50% classified as **unknown** sector

### GLAD
- ~50% classified as **unknown** sector
- No maturity dates
- Has a spurious sector called **"Cash Equivalents"** — should not be a sector

### ICMB
- Company naming issues
- Classifying **loans as Other Equity** — investment type logic is wrong

### MAIN
- ~50% classified as **Other** sector
- No maturity dates

### MRCC
- No maturity dates
- Wrong sector assignments

### MSIF
- ~50% classified as **Other** / unknown sector
- No maturity dates

### NMFC
- ~50% classified as **unknown** sector
- No maturity dates

### OBDC
- ~75% classified as **unknown** sector
- No maturity dates

### OCSL
- Same as OBDC: ~75% unknown, no maturity dates

### OFS
- No maturity dates
- Wrong sector assignments

### PFLT
- ~50% unknown sector
- Company names need fixing

### PNNT
- High proportion of unknown sectors
- Otherwise okay

### PSEC
- ~50% classified as **unknown** sector
- No maturity dates

### RAND
- Unknown sectors

### RWAY
- Company names need fixing

### SAR
- High unknown sectors
- Unknown/bad company names

### SCM
- Lots of unknown sectors

### TSLX
- No maturity dates

### WHF
- ~80% unknown sectors

---

## 🔴 Needs Complete Redo (pipeline wrong or empty)

### BXSL
- Complete redo needed

### EQS
- Gets **nothing** — completely empty output

### GSBD
- Complete redo needed

### LIEN
- Complete redo needed

### NEWT
- Completely wrong output

### OXSQ
- Complete redo needed

### PSBD
- Complete redo needed

### SSSS
- Gets **nothing** — completely empty output

### TPVG
- No maturity dates
- Doesn't get investment types at all

---

## Common Root Causes (reference)

- **50% unknown sector**: HTML enrichment not matching industry sections correctly for that BDC's HTML structure, or XBRL has no industry tags
- **No maturity dates**: XBRL doesn't tag maturity; HTML enrichment not matching company names to pull maturity from HTML table
- **Company name issues**: XBRL dimension strings embed instrument suffixes / legal entity variants that aren't being stripped
- **Wrong investment type**: Investment type detection logic misfiring (e.g. treating revolver/delayed draw as equity)
- **Empty output**: LLM/HTML pipeline failing entirely — table detection not finding SOI tables, or filing structure unexpected
- **Wrong sectors**: HTML industry carry logic misassigning section headers to wrong rows
