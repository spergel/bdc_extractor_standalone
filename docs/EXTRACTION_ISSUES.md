# Extraction Issues - BDC Schedule of Investments

## Summary
The pipeline has three extraction paths: XBRL (`xbrl_investment_extractor.py`), HTML (`html_soi_parser.py` — routed as LLM_TICKERS), and DSPy (`dspy_table_scraper.py`). Issues below are organized by ticker, then by systemic problem.

---

## Per-Ticker Issues (Active)

### ICMB — critically under-extracted
- **Router:** LLM_TICKERS → html_soi_parser
- **Symptom:** 1–2 rows in 2025 periods. Should be ~30–50 portfolio companies.
- **Root cause:** html_soi_parser completely fails ICMB's table format. 2023–2024 historical periods from html_soi_parser backfill are also suspect for same reason.
- **Fix:** Move ICMB to DSPy_TICKERS in `process_all_bdcs.py` and re-run with `--force`.

---

### LIEN — company name garbage in some periods
- **Router:** XBRL_TICKERS
- **Symptom:** "Non Qualifying Assets" appearing as a company name (should be excluded). Some periods may have XBRL dimension strings leaking into company_name.
- **Root cause:** `_apply_ticker_specific_company_cleanup` for LIEN doesn't catch all junk dimension values. Fixed `return ('', None)` bug existed previously; may still have residual dirty names.
- **Fix:** Audit all 11 LIEN periods for bad company names. Add any remaining junk patterns to LIEN block in `standardization_rules.py`. "Non Qualifying Assets" should be in `_NON_COMPANY_PATTERNS` or cleared in LIEN cleanup.

---

### MFIC — investment_type embedded in company_name, 100% blank type
- **Router:** LLM_TICKERS → html_soi_parser
- **Symptom:** 254/254 rows have blank `investment_type`. Company names contain `,Common Stock` / `,Term Loan` / `,Revolver` suffixes — e.g. `"FC2 LLC,Term Loan"`, `"Surf Opco LLC,Revolver"`.
- **Root cause:** MFIC's HTML table doesn't have a separate investment_type column — the instrument type is appended to the company name in the HTML. html_soi_parser reads the full string into `company_name` and finds no type column.
- **Fix:** Add MFIC to the ticker-specific cleanup in `standardization_rules.py` to split on `,` and extract the trailing type token as `investment_type`. Alternatively move MFIC to DSPy_TICKERS (DSPy handles this correctly).

---

### NEWT — remove from pipeline
- **Router:** LLM_TICKERS → html_soi_parser
- **Symptom:** Only 6 rows per period, all JV interests and equity (`NCL JV`, `TSO JV`, `EMCAP Loan Holdings`). Not a meaningful BDC portfolio view.
- **Root cause:** Newtek Business Services converted to a bank (Newtek Bank) in 2023. Its BDC investment portfolio is now tiny — just a handful of legacy JV positions. The data extracted is technically correct but not useful.
- **Fix:** Remove NEWT from LLM_TICKERS in `process_all_bdcs.py`. Optionally delete existing NEWT frontend data files or leave as historical artifact.

---

### OFS — generally OK, some blanks
- **Router:** XBRL_TICKERS
- **Symptom:** 106 rows, 6 blank fair_value, 29 blank principal. Negative fair values appear on undrawn revolvers/delayed draws (e.g. `-3`, `-8`).
- **Root cause:** Blank principals are equity positions (correct). Blank fair_values may be unfunded commitments or zero-value positions. Negative fair values on revolvers are upfront fees — technically correct per BDC accounting.
- **Fix:** Minor — confirm the 6 blank fair_value rows are genuinely $0 (check SEC filing). If yes, no code change needed. Optionally normalize negative fv to `0` for display. No structural fix required.

---

### OXSQ — blank fair_value on CLO equity tranches
- **Router:** DSPY_TICKERS
- **Symptom:** 122 rows, 16 with blank fair_value. Blank rows include CLO equity tranches (`Telos CLO 2014-5 Ltd.`, `Venture XX Ltd.`) and some first lien positions.
- **Root cause:** CLO equity fair values are sometimes zero or omitted in the filing table. DSPy may also be dropping values when the table layout is ambiguous. Naming itself is OK (CLO tranche names are verbatim from the filing).
- **Fix:** For CLO equity blanks — these may be genuinely $0 or unlisted. For first lien blanks — re-run with `--force` and check the raw table text. If DSPy is dropping numeric cells, adjust the chunking so these rows have more context. Low priority.

---

### PFX — 100% blank investment_type, possible unit scale issue
- **Router:** LLM_TICKERS → html_soi_parser
- **Symptom:** 73/73 rows have blank `investment_type`. Fair values appear in raw dollars (e.g. 4,143,180) while principals are also raw dollars but different magnitudes. One position (Adamas Trust) shows fv=4,143,180 vs principal=167,876 — implying FV is 25× principal, which is wrong; likely a column mapping error.
- **Root cause:** html_soi_parser cannot find the investment_type column in PFX's HTML table. PFX's table format is non-standard (Phoenix Senior Secured Debt, Inc. / PennantPark Floating Rate Capital). Column mapping failure is causing fair_value and principal to be read from wrong columns in some rows.
- **Fix:** Move PFX to DSPy_TICKERS. DSPy reads column headers contextually and handles non-standard layouts far better than the rule-based parser.

---

### PNNT — IDK, possibly OK
- **Router:** XBRL_TICKERS
- **Symptom:** 217 rows, only 2 blank fair_value, 0 blank type. Some negative fair values (`-10` for Arcfield). Companies appear 2–3 times (multiple tranches, unfunded commitments at $0) which is correct BDC behavior.
- **Root cause:** Unknown — data may be mostly correct. Possible issues: (1) some company names have XBRL artifact prefixes not yet caught by PNNT cleanup; (2) earlier periods (2023–2024) may have higher blank rate from PFLT/PNNT extraction bugs that were fixed in the March 2026 overhaul.
- **Fix:** Audit older PNNT periods (2023–2024) for blank company_name rates. If blank rate is still high, re-run pipeline on those periods. For negative fv: acceptable (unfunded revolver fees).

---

### PSBD — 100% wrong investment_type (all "Other Equity")
- **Router:** XBRL_TICKERS
- **Symptom:** 80/80 rows have `investment_type = "Other Equity"`. PSBD (Palmer Square BDC) holds mostly first lien senior secured loans. Zero fair_value blanks otherwise.
- **Root cause:** PSBD's XBRL instance uses a non-standard `InvestmentTypeAxis` dimension where all positions share a single member that maps to "Other Equity" in the XBRL extractor's member-to-type mapping. The actual investment types (First Lien, Second Lien, etc.) are only in the HTML table.
- **Fix:** Either (a) add PSBD to the HTML enrichment path that reads investment_type from the HTML SOI table (extend `_html_enrich_rows` to fill `investment_type` in addition to `industry` and `maturity_date`), or (b) move PSBD to DSPy_TICKERS. Option (a) is preferred since PSBD's XBRL numbers are correct — only the type is wrong.

---

### RAND — blank fair_values on equity positions (DSPy extraction gaps)
- **Router:** DSPY_TICKERS
- **Symptom:** 52 rows, 14 blank fair_value (27%), 43 blank principal (83%). The blank principals are correct (equity has no principal). The 14 blank fair_values are the problem — some are equity/preferred positions that have a dollar fair value in the filing but DSPy left blank.
- **Root cause:** RAND (Rand Capital) uses a table layout where preferred equity and warrant values appear in a column that DSPy misidentifies or skips. Some positions genuinely have $0 fair value; others are extraction failures.
- **Fix:** Re-run with `--force` and inspect the raw table text for blank-fv positions. If DSPy is reading the wrong column, the fix is in the `section_context` or the chunk boundary — try smaller chunk sizes. Also only 4 periods available (2025 only) — add historical backfill with `--years-back 3`.

---

### SSSS — blank fair_values and duplicate rows on multi-tranche equity
- **Router:** DSPY_TICKERS
- **Symptom:** 99 rows, 28 blank fair_value (28%), 8 duplicate `(company, type, fv)` keys. Orchard Technologies appears 3+ times under Preferred Equity — some with FV, some blank.
- **Root cause:** SSSS (SuRo Capital) holds many distinct tranches of the same company (Series A, Series B, Series C preferred — all tagged as "Preferred Equity"). When FV is blank for some tranches, dedup can't distinguish them (same key). SSSS filings also have complex table layouts with cross-page continuation.
- **Fix:** (1) For dedup: Preferred Equity dedup should also key on `principal_amount` or `cost` when available. (2) For blank fv: these may be genuinely $0 (liquidation/write-off). Re-run and check the raw table. (3) Only 4 periods — add historical backfill.

---

### WHF — blank fair_values on unfunded commitments
- **Router:** XBRL_TICKERS
- **Symptom:** 246 rows, 14 blank fair_value (6%), 82 blank principal (33%). Data otherwise looks correct.
- **Root cause:** Blank principals are equity/warrant positions (correct). Blank fair_values are mostly unfunded revolvers and delayed draws that have $0 or immaterial fair value per BDC accounting — this is standard.
- **Fix:** Low priority. Optionally normalize blank fv to `0` for unfunded commitments for cleaner display. No structural fix needed. Monitor for regression.

---

## Systemic Issues

### A. Year-End / Prior Period Data Inclusion
**Problem:** 10-Q filings include comparative tables for prior year-end. Without filtering, row counts inflate ~2×.
**Fix:** Logic exists in `is_year_end_table` (table_detection.py). Confirmed fixed for TPVG (Dec 31 case). Monitor other tickers with Dec 31 period-end.

### B. Duplicate Holdings (Same Position, Different Periods)
**Problem:** Same company + investment type + principal appears with different maturity dates.
**Fix:** `deduplicate_csv_rows` in `data_cleaning/deduplicator.py` groups by (company_base, investment_type) and keeps the row with the later maturity_date. Working.

### C. html_soi_parser Fails Non-Standard Tables
**Problem:** html_soi_parser uses rule-based column detection. When a BDC's table doesn't have standard column headers or embeds multiple fields in one cell, it produces 0 rows, blank types, or wrong values.
**Affected tickers:** ICMB, MFIC, PFX (blank investment_type), FSK (83% blank type, share-count principal values).
**Fix:** Move affected tickers to DSPy_TICKERS. DSPy's LLM-based extraction handles non-standard layouts correctly.

### D. XBRL investment_type Dimension Mapping Failures
**Problem:** Some BDC XBRL filers use non-standard `InvestmentTypeAxis` members. The extractor maps unknown members to "Other Equity" as a fallback.
**Affected tickers:** PSBD (100% Other Equity).
**Fix:** Extend `_html_enrich_rows` to fill `investment_type` from the HTML SOI table when XBRL provides only a fallback value.

### E. Missing Fair Values on Legitimate $0 Positions
**Problem:** Many equity, warrant, CLO tranche, and unfunded commitment positions genuinely have $0 or unlisted fair value. These appear as blank in the output but are not extraction errors.
**Affected tickers:** SSSS, RAND, OXSQ (CLO equity), OFS, WHF (revolvers).
**Fix:** Document as expected behavior. Optionally display as `$0` instead of blank in the frontend for these investment types.

---

## Priority Queue

| Priority | Ticker | Issue | Effort |
|----------|--------|-------|--------|
| High | ICMB | 1–2 rows, complete failure | Low — move to DSPy |
| High | MFIC | 100% blank type | Low — move to DSPy or add cleanup rule |
| High | PFX | 100% blank type + column mapping wrong | Low — move to DSPy |
| High | PSBD | 100% wrong type (Other Equity) | Medium — HTML enrich investment_type |
| High | NEWT | Useless data (bank, not BDC) | Trivial — remove from pipeline |
| Medium | FSK | 83% blank type + share-count principal | In progress — DSPy run (see task bkqu4pcnt) |
| Medium | RAND | 27% blank fair_value, only 4 periods | Medium — re-run + backfill |
| Medium | SSSS | 28% blank fv, dedup gaps | Medium — dedup fix + backfill |
| Low | LIEN | Junk names in some periods | Low — add cleanup rules |
| Low | OXSQ | 16 blank fv (CLO equity, likely correct) | None needed |
| Low | OFS | 6 blank fv (unfunded, correct) | None needed |
| Low | WHF | 14 blank fv (unfunded, correct) | None needed |
| Low | PNNT | Possibly OK, audit older periods | Low |

---

## Historical Issues (Fixed)

### 1. TPVG 10-K double-counting
Table detection wrongly included comparative Dec 31 SOI from 10-K. Fixed in `table_detection.py` by using "as of" anchored patterns for Dec 31 period-end detection.

### 2. PFLT/PNNT ~50% blank company_name
XBRL `elif` block was clearing all "in Non-Controlled ... First Lien Secured Debt {Company}" strings. Fixed with `no_issuer_m` pattern. Result: PFLT 3.7% blank, PNNT 1.2% blank.

### 3. CGBD company name prefixes
"Investment Non-Affiliated Issuer {Type} {Company}" strings stripped. Section-only strings cleared.

### 4. GSBD garbled names
Expanded GSBD regex to handle any geography. Added dimension-value filters for rate-only strings, maturity dates, etc.

### 5. XBRL member strings in reference_rate
`_XBRL_MEMBER_TO_RATE` mapping added. Handles `YYYYMMDD#MEMBERNAME` format for SOFR, LIBOR, CORRA, CDOR, BBSW, etc.

### 6. RWAY garbled company names (69% garbled from html_soi_parser)
Moved to DSPY_TICKERS. DSPy result: 0% garbled, ~100% rate coverage.

### 7. OXSQ 0 rows from html_soi_parser
"COMPANY/INVESTMENT" header format not recognized. Moved to DSPY_TICKERS. Now 108–150 rows per filing.

### 8. RAND 12–19 rows from html_soi_parser
Moved to DSPY_TICKERS. Now 40–65 rows per filing.

### 9. FSK bad period-end files (2025-03-31, 2025-06-30, 2025-09-30)
Old DSPy run used period-end dates instead of filing dates. Deleted; replaced with correct filing-date-named files.

### 10. LIEN post_process_extraction error
`return ''` → `return ('', None)` at lines 838/840/842 in `_apply_ticker_specific_company_cleanup`.

---

## Relevant Files

- `process_all_bdcs.py` — ticker routing (XBRL / LLM / DSPy / Custom)
- `src/extraction/dspy_table_scraper.py` — DSPy LLM extractor
- `src/extraction/html_soi_parser.py` — HTML rule-based parser (LLM_TICKERS)
- `src/extraction/xbrl_investment_extractor.py` — XBRL extractor + HTML enrichment
- `src/extraction/table_detection/detection.py` — year-end table filter
- `src/extraction/data_cleaning/deduplicator.py` — cross-period dedup
- `src/processing/standardization_rules.py` — company name + industry cleanup
- `src/processing/post_process_extraction.py` — cleaning orchestration
