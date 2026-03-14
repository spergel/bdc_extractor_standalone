# Operations: Active Issues and Next Actions

Last reviewed: 2026-03-14

This file replaces older overlapping status docs and is the operational source of truth.

## Current Priorities

1. **ICMB extraction quality**
   - Symptom: severe under-extraction in recent periods.
   - Next action: route ICMB through DSPy path and re-run with `--force`.

2. **MFIC / PFX investment type quality**
   - Symptom: investment types often blank or misplaced.
   - Next action: prefer DSPy route for these tickers, then re-consolidate.

3. **PSBD investment type mapping**
   - Symptom: overuse of fallback type (`Other Equity`).
   - Next action: enrich/fix type mapping from HTML context or DSPy route.

4. **GSBD older filings**
   - Symptom: older 2025 filings still have high blank-name rates.
   - Next action: re-extract when SEC endpoints are responsive.

5. **Maturity-date gaps in some XBRL tickers**
   - Symptom: near-0% maturity coverage for specific names.
   - Next action: inspect HTML enrichment output for one failing sample ticker.

## Known-Low-Priority Cases

- Blank fair value on some unfunded commitments/CLO equity rows may be expected accounting output.
- Negative tiny fair values on some revolvers can represent fee/marking behavior.
- Treat these as display/data-policy decisions unless extraction evidence shows incorrect parsing.

## Recently Completed (Keep Brief)

- PFLT/PNNT company-name extraction improved significantly.
- CGBD prefix cleanup implemented.
- XBRL member-string leakage into `reference_rate` addressed.
- Broad company-name cleanup pass completed across frontend CSV outputs.

## Standard Recovery Commands

```bash
# Extract/re-extract selected tickers
python process_all_bdcs.py --tickers GSBD PFLT PNNT PSBD --force

# Re-run post-processing
python src/processing/post_process_extraction.py --directory output/

# Re-consolidate investments
python src/consolidation/consolidate_investments.py

# Rebuild company resolution artifacts
python src/company_resolution/resolve_companies.py
```

## Triage Workflow

1. Reproduce issue on one ticker and one filing period.
2. Determine extraction path (XBRL, HTML parser, DSPy, or custom scraper).
3. Fix path-specific logic.
4. Re-run post-process + consolidate + company resolution.
5. Verify in `frontend/public/data/investments/` and `company_exposures.csv`.

## Scope Rules

- Do not add new one-off "progress" docs for each session.
- Update this file for issue/status changes.
- Keep deep implementation details in code comments or dedicated design docs only when needed.
