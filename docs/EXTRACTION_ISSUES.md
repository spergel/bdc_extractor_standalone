# Extraction Issues - BDC Schedule of Investments

## Summary
The LLM table scraper extracts investment schedules from SEC 10-Q filings. Several issues cause incorrect row counts, duplicates, and missing data.

---

## 1. Year-End / Prior Period Data Inclusion

**Problem:** 10-Q filings include comparative tables for prior year-end (e.g., December 31, 2024). We extract from BOTH current quarter (e.g., June 30, 2025) AND prior year-end, inflating row counts by ~2–4x.

**Evidence:** MRCC 2025-08-11 had 575 rows instead of ~140. Same companies appear twice (e.g., American Community Homes at 4.46% vs 4.44%) from June vs Dec periods.

**Intended behavior:**
- Skip sections with "December 31, {prior_year}" in the schedule header
- Skip individual tables that have year-end dates in content/context
- Filter `current_quarter_tables` before LLM processing
- Dedup by maturity_date when same company + investment_type + principal match (prefer later maturity)

**Status:** Logic exists in `_is_year_end_table`, section skip, and dedup—but prior runs still produced blended output. Verify on fresh extraction.

---

## 2. Duplicate Holdings (Same Position, Different Periods)

**Problem:** Same company + investment type + principal appears with different maturity dates (e.g., 2028 vs 2029). One is current quarter, one is prior year-end.

**Evidence:** TigerConnect appeared twice: maturity 2029-08-16 (current) and 2028-02-16 (prior).

**Fix implemented:** `_deduplicate_csv_rows` now groups by (company_base, investment_type), and when principals match within 1%, keeps the row with the **later** maturity_date.

---

## 3. Inconsistent Table Collection (Q2 vs Q3)

**Problem:** Q2 2025 extraction collected only 4 tables → 59 rows. Q3 2025 collected 32 tables → 144 rows. Same BDC, same structure; collection is inconsistent.

**Possible causes:**
- Section/header iteration stopping too early
- Table matching (text overlap) failing for some tables
- Year-end section skip affecting adjacent current-quarter sections
- `processed_table_indices` or iteration order differences by filing

**Status:** Root cause not fully diagnosed. Need to compare table discovery and matching between Q2 and Q3 runs.

---

## 4. Equity vs Debt – No Filtering

**Problem:** Some extractions include Common Equity, Preferred Equity, Warrants, Junior Secured. User expects ~140 **debt** investments only (First Lien, Revolver, Delayed Draw, etc.).

**Evidence:** Q2 file had 102+ equity-related rows. Q3 had none.

**Possible causes:**
- Q3 tables happened to be debt-only; Q2 pulled from exhibits/sections that include equity
- No explicit filter on `investment_type`

**Potential fix:** Post-process filter or CLI option to exclude equity types (Common Equity, Preferred Equity, Warrant, etc.).

---

## 5. File Overwrite / Stale Output

**Problem:** Old CSV output not reliably replaced. User saw 575 lines when a newer run should have produced 59 (or ~140 after fixes).

**Fix implemented:** `output_path.unlink()` before writing in `_save_csv_output` to force a clean overwrite.

---

## 6. Multiple Entities (Monroe + MRCC Fund)

**Problem:** MRCC 10-Q has two schedules: Monroe Capital Corporation and MRCC Senior Loan Fund I, LLC. Each has current quarter + prior year-end. Without proper filtering, we get 4x the intended rows.

**Status:** Year-end exclusion and dedup should address this if applied correctly.

---

## Checklist for Correct Extraction

- [ ] Year-end sections skipped (header contains Dec 31 prior year)
- [ ] Year-end tables skipped (table/context contains Dec 31 prior year)
- [ ] No break that stops collection early—only skip, then filter
- [ ] `current_quarter_tables` filter applied before LLM
- [ ] Dedup prefers later maturity when company + type + principal match
- [ ] Output file explicitly deleted before write
- [ ] Same number of tables collected for comparable quarters (Q2 vs Q3)
- [ ] Optional: filter to debt-only if user wants ~140 rows

---

## Relevant Files

- `src/extraction/llm_table_scraper.py` – main extraction, year-end detection, dedup, table collection
- `src/extraction/sec_api_client.py` – filing fetch, quarter selection
