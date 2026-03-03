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

## 7. Industry Misclassification (e.g. Advanced Aircrew → Other)

**Problem:** Some companies are aviation/aerospace but appear under a generic section in the filing (e.g. BCSF "Services: Business Advanced Aircrew..."). They then get industry "Other" or "Business Services" instead of "Aerospace & Defense".

**Evidence:** Advanced Aircrew (aviation training) showed as **Other**; same schedule lists ATS, BTX Precision, Forward Slope, GSP Holdings as **Aerospace & Defense**.

**Fix implemented:**
- **standardization_rules.py**: Added "aircrew" / "air crew" to Aerospace & Defense keywords in `_INDUSTRY_RULES`. In `clean_company_name`, when the cleaned company name contains "aircrew", set `extracted_industry = "Aerospace & Defense"`.
- **restandardize / post_process**: When applying `extracted_industry`, overwrite industry if it is empty **or** "Other" (so existing rows with Other get corrected).
- **BCSF "Non-controlled/Non-Affiliated Investments {Industry} "**: Many BCSF rows had the full prefix in `company_name` and empty/Other `industry`. BCSF ticker cleanup now strips "Non-controlled/Non-Affiliated Investments " plus any of the filing’s industry phrases (Aerospace & Defense, Automotive, Beverage, Capital Equipment, Chemicals, Construction & Building, Consumer Goods: *, FIRE: Finance/Insurance, Healthcare & Pharmaceuticals, Services: Business, etc.) and sets `extracted_industry` to the matching canonical industry. So all such BCSF rows get both a cleaned company name and the correct industry when restandardize or post_process runs.

**Similar patterns (other tickers):**
- **TCPC**: "Equity Securities {sector} {Company}" (e.g. "Equity Securities Internet Software and Services Domo") was not stripped; industry stayed empty/Other. Cleanup now strips "Equity Securities " + sector and sets `extracted_industry` (Internet Software and Services → Software & Technology, Professional Services → Business Services, Healthcare Providers and Services → Healthcare, Software → Software & Technology).
- **TRIN**: "Portfolio Company Warrant Investments United {Company}" (no "States") and "Portfolio Company Equity Investments Canada {Company}" were not fully stripped. Cleanup now strips these variants and "United States " after "Portfolio Company Debt Securities- " so company name and sector parsing work correctly.

**Inferring industry from company name keywords:** When industry is still empty/Other after prefix cleanup, restandardize and post_process now try `normalize_industry(cleaned_company_name)`. If the company name contains sector keywords (e.g. "Technologies", "Software", "Healthcare", "Pharma") from the same rules used for industry strings, that canonical category is applied. So e.g. "Forescout Technologies Inc." → Software & Technology. Only used when the result is a canonical category (not "Other").

**Propagation by company_id:** After restandardize, `restandardize_all.py` runs `propagate_industries()`: for each row with industry Other/empty, if the same `company_id` has a non-Other industry in another file, that industry is filled in. This fixes the bulk of remaining Other when the same company appears in multiple BDCs or periods with industry set in at least one.

---

---

## 8. XBRL Tickers: Missing Industry and Maturity Date

**Problem:** Many XBRL-extracted tickers (GBDC, ARCC, MAIN, etc.) had 100% missing `industry` and `maturity_date` because those fields are not tagged in their XBRL instance documents — they exist only in the HTML filing table.

**Root cause:** Confirmed via XBRL debug files. For example, GBDC's XBRL dimensions only contain company name, investment type, and affiliation category; `InvestmentMaturityDate` and industry are never tagged. The values exist solely in the rendered HTML Schedule of Investments table.

**Solution implemented: HTML enrichment layer** (`src/extraction/html_soi_parser.py`)

After XBRL rows are extracted, `_html_enrich_rows` in `xbrl_investment_extractor.py`:
1. Retrieves the main HTML filing from `text_map` (no extra HTTP request — already fetched by `fetch_filing_by_index_url`)
2. Calls `extract_soi_enrichment(html_content, ticker, period_end)` which parses the HTML SOI table(s) using `HTMLSOIParser._parse_table` logic
3. Builds lookup dicts: `industry_map[company_key]`, `maturity_by_principal[(company_key, principal_key)]`, `maturity_default_map[company_key]`
4. Fills any missing `industry` and `maturity_date` in XBRL rows by normalized company name + principal amount

**HTML table parsing fixes required:**

*Fix 1 — Missing "Portfolio Company" column header:* Some BDCs (e.g. GBDC) omit the column header label for the company name column entirely. `_find_header_row` now detects this case: if ≥3 financial columns are identified but no `company_name` column, the first visible non-hidden unmatched column is inferred as `company_name`.

*Fix 2 — Cross-table industry context carry:* GBDC splits the SOI into ~30 separate HTML tables (one per industry section), and continuation tables have no section header. `_parse_table` now reads `_carry_industry` / `_carry_investment_type` attributes from the parser instance, and saves them back after each table. `extract_soi_enrichment` initializes these carry attrs so industry context flows from each table into the next.

*Fix 3 — Investment type embedded in XBRL company name:* Older GBDC filings embedded the investment type in the XBRL dimension string (e.g. `"AAH TOPCO LLC One stop 1"`). `_company_key` now strips trailing investment-type keywords (`one stop`, `first lien`, `second lien`, `senior secured`, `term loan`, `revolver`, `preferred`, etc.) and trailing numeric indices from the end of normalized company names, so XBRL and HTML keys match.

**Results (tested GBDC and ARCC, last 4 quarters each):**

| Ticker | Industry fill | Maturity fill | Notes |
|--------|-------------|--------------|-------|
| GBDC   | ~87–88%      | ~63–67%       | Was 5–25% before fixes |
| ARCC   | ~95–97%      | ~69–79%       | Was ~72% missing before |

Remaining gap for industry: equity investments (warrants, LP interests, common stock) that appear under equity-only section headers and may have slightly different company name formatting. Remaining gap for maturity: equity investments don't have maturity dates; floating rate precision differences affect principal_key matching.

**Relevant files:**
- `src/extraction/html_soi_parser.py` — `_find_header_row`, `_parse_table`, `_company_key`, `extract_soi_enrichment`
- `src/extraction/xbrl_investment_extractor.py` — `_get_main_html_content`, `_html_enrich_rows`, `process_filing`

---

## Relevant Files

- `src/extraction/llm_table_scraper.py` – main extraction (HTML/LLM path), year-end detection, dedup, table collection
- `src/extraction/xbrl_investment_extractor.py` – XBRL extraction + HTML enrichment
- `src/extraction/html_soi_parser.py` – HTML SOI table parser, cross-source company key normalization
- `src/extraction/sec_api_client.py` – filing fetch, quarter selection
