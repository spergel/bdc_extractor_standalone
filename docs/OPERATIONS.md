# Operations: Active Issues and Next Actions

Last reviewed: 2026-03-14

This file replaces older overlapping status docs and is the operational source of truth.

---

## Pipeline Architecture (Current)

Four extraction pipelines, auto-routed by `process_all_bdcs.py`:

| Pipeline | Tickers | Notes |
|----------|---------|-------|
| **XBRL** | ARCC, BBDC, BXSL, CCAP, CGBD, CSWC, FDUS, FSK, GAIN, GBDC, GECC, GLAD, GSBD, HRZN, ICMB, KBDC, LIEN, MAIN, MRCC, MSDL, MSIF, NCDL, NMFC, OCSL, OFS, OXSQ, PFLT, PNNT, PSBD, PSEC, PFX, RAND, SAR, SCM, SLRC, TCPC, TRIN, TSLX, WHF | Best data quality; XBRL rollup filter in post_process drops aggregate rows |
| **HTML/LLM** | CION, HTGC, OBDC, TPVG | html_soi_parser.py; no LLM |
| **DSPy** | EQS, MFIC, RWAY, SSSS | Gemini 2.0 Flash via DSPy; for filings html_soi_parser can't handle |
| **Custom scraper** | BCIC, BCSF | Per-ticker scripts in scripts/ |

Total: 49 tickers, ~6,700 company exposures.

---

## Current Priorities

### High — data quality

1. **MFIC (6 of 9 periods)**: old html_soi_parser data with 83–100% blank `investment_type`.
   - Good periods (0% blank): 2025-02-25, 2025-08-11, 2025-11-06 (DSPy-extracted)
   - Bad periods: 2023-05-02, 2023-08-02, 2023-11-07, 2024-05-07, 2024-11-07, 2025-05-12
   - Fix: `python scripts/run_dspy_scraper.py --ticker MFIC --force --start 2023-01-01`
   - **WARNING**: ~25 min/filing × 9 filings = 4+ hours total

2. **SSSS (3 of 11 periods broken)**:
   - 2023-03-16: $441M total FV (should be ~$60–80M) — Gemini reads wrong valuation table
   - 2025-03-12: 26 rows (should be ~46–160) — same issue
   - 2025-08-07: $133B total FV — negative format `(14297450\t)` misread
   - Root cause: Gemini picks company valuation tables over SSSS's SOI table

3. **BCIC 2024-05-08**: ~350 rows, FV shows $791B (concatenated column values).
   - Pre-existing parsing bug in the Mar 31, 2024 filing table format; other periods are clean.

### Medium — investigation needed

4. **GSBD older filings**: some older filings may still have quality issues.

---

## Recently Completed

| Date | Fix | Result |
|------|-----|--------|
| 2026-03-15 | **BCIC 2025-08-07 duplication fix** | Was 500 rows ($1B); now 174 rows ($408M). Root cause: ex99 exhibit duplicated SOI. Filter added to `_bcic_table_filter`. |
| 2026-03-14 | **PSBD investment_type fix** | Was 93% "Other Equity"; now 77% First Lien + 14% Second Lien + 9% actual equity. 6 XBRL periods (2024-11 to 2026-02). `cash_rate` 90% filled via HTML enrichment. |
| 2026-03-14 | **PFX XBRL migration** | DSPy had $200B–$600B scale errors; XBRL gives correct $586–589M. 3 periods (2025+). Old 10 corrupted DSPy periods deleted. |
| 2026-03-14 | **post_process PFX block removed** | Was dropping 84/90 rows (clean_company_name ran before the block, stripping the prefix the block needed). |
| 2026-03-14 | **GECC + ICMB XBRL migration** | DSPy had encoding errors / $246B impossible totals. XBRL: GECC 13 periods/1124 rows, ICMB 10 periods/596 rows. |
| 2026-03-14 | **FSK 10-Q dedup + unit scale fix** | Cross-period dedup + full-doc unit scale scan. 12 periods, ~9,500 rows. |
| 2026-03-13 | **DSPy unit scale fix** | Was storing native millions not thousands. Fixed in llm_table_scraper + dspy_table_scraper. |
| 2026-03-05 | **RWAY → DSPy** | html_soi_parser produced 77% garbled names; DSPy: 0% garbled, 100% rate coverage. |
| 2026-03-03 | **PFLT/PNNT name extraction** | Fixed ~50% blank rate; broad company name cleanup pass across all frontend CSVs. |

---

## Known-Low-Priority Cases

- Blank fair value on unfunded commitments/CLO equity rows — expected accounting output.
- Negative tiny fair values on some revolvers — fee/marking behavior, not a parsing error.
- SAR quarterly 10-Qs include ~320 CLO holdings alongside ~84 direct portfolio companies. 10-K has only direct. Structural, not a bug.
- EQS: 7–11 rows/filing is correct (tiny BDC, verified).

---

## Standard Recovery Commands

```bash
# Extract/re-extract selected tickers (auto-routes to correct pipeline)
python process_all_bdcs.py --tickers TICKER1 TICKER2 --force --years-back 3

# DSPy re-extract specific ticker
python scripts/run_dspy_scraper.py --ticker MFIC --force --start 2023-01-01

# Re-run post-processing on all output files
python src/processing/post_process_extraction.py --directory output

# Re-consolidate investments → frontend
python src/consolidation/consolidate_investments.py

# Rebuild company resolution artifacts
python src/company_resolution/resolve_companies.py

# Full pipeline: extract + post-process + consolidate + resolve (no profiles)
python process_all_bdcs.py --tickers TICKER --force --skip-profiles
```

---

## Triage Workflow

1. Reproduce on one ticker + one filing period.
2. Determine extraction path (XBRL, HTML parser, DSPy, or custom scraper).
3. Fix path-specific logic or routing in `process_all_bdcs.py`.
4. Re-run post-process + consolidate + company resolution.
5. Verify in `frontend/public/data/investments/` and `company_exposures.csv`.

---

## Scope Rules

- Do not add new one-off "progress" docs for each session.
- Update this file for issue/status changes; keep it concise.
- Deep implementation details go in code comments or dedicated design docs.
