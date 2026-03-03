# Handoff: Company name cleanup (for second agent)

Use this doc to continue fixing **company_name** in the BDC investments data. The goal is for `company_name` to contain **only the business name** (e.g. "Pfanstiehl Inc.", "Senior Credit Corp."), with no XBRL/filing artifacts like "Affiliate Investments ", "Investment Type Common Stock", industry labels, or geography/debt-type prefixes.

---

## 1. Where everything lives

| What | Path |
|------|------|
| **Cleanup logic** | `src/processing/standardization_rules.py` |
| **Function to extend** | `_apply_ticker_specific_company_cleanup(name, ticker)` — add `if ticker_upper == 'TICKER':` blocks |
| **Generic cleanup** | Same file: `clean_company_name()` (calls ticker-specific first, then `_strip_instrument_suffix`, etc.) |
| **Full list of existing rules** | `docs/TICKER_COMPANY_NAME_FIXES.md` |
| **Data** | `frontend/public/data/investments/*.csv` and `frontend/public/data/investments/<TICKER>/*.csv` |
| **Company resolution** | `src/company_resolution/resolve_companies.py` (assigns `company_id` from cleaned names) |

---

## 2. How to add a new fix

1. **Find bad names**  
   Search CSVs or use the frontend; note the **ticker** and exact **company_name** string.

2. **Add a rule in** `standardization_rules.py`  
   In `_apply_ticker_specific_company_cleanup()`, either:
   - Add a new block: `if ticker_upper == 'TICKER':` then strip prefix/suffix / return `''` for section headers, or  
   - Extend an existing block for that ticker.

3. **Document**  
   Update `docs/TICKER_COMPANY_NAME_FIXES.md` (summary table + any new ticker).

4. **Apply and resolve**  
   ```bash
   python scripts/restandardize_all.py
   python src/company_resolution/resolve_companies.py --investments-dir frontend/public/data/investments --data-dir frontend/public/data
   ```

5. **Quick test** (optional)  
   ```python
   from src.processing.standardization_rules import clean_company_name
   clean_company_name("Bad Name Here", "TICKER")  # expect "Good Name"
   ```

---

## 3. Industry from stripped title

When ticker-specific cleanup strips an **industry prefix** from `company_name` (e.g. "Biotechnology & Life Sciences ", "Automobile Components ", "Aerospace & Defense "), that phrase is returned as **extracted_industry**. In `restandardize_csv` and `post_process_extraction`, if the row’s **industry** column is empty, it is set to `standardize_industry(extracted_industry)` (e.g. Healthcare, Automotive). So companies that don’t currently have industry but had it in the title will get it when the name is cleaned. This only applies when the **raw** name still contains the prefix (e.g. during post-process of new extractions or before restandardize has been run).

---

## 4. Known / possible remaining work

- **PSBD**  
  No ticker-specific rule yet. If you see industry/type in `company_name` for PSBD, add a rule using sample rows from `frontend/public/data/investments/PSBD.csv`.

- **Display concatenation**  
  Sometimes the UI shows e.g. "Other EquityAffiliate Investments Senior Credit Corp." — that can be **investment_type** ("Other Equity") glued to **company_name** ("Affiliate Investments Senior Credit Corp."). Fix is usually in the cleanup (e.g. strip "Affiliate Investments ") so **company_name** is correct; no change needed if the display is just concatenating two columns.

- **Other tickers**  
  BCSF, CCAP, and others are in the doc; any new BDC or new filing format may introduce new patterns. Grep `frontend/public/data/investments/*.csv` for suspicious prefixes (e.g. "Investment Type", "Industry ", "Non-Controlled", "Debt Investments ") to find candidates.

- **Section headers / industry-only rows**  
  Rows that are only a category (e.g. "Non-Controlled/Non-Affiliated Investments Materials") should be cleared to `''` so they don’t appear as companies. Check the "section-only" / "return ''" logic for the ticker when adding new prefixes.

- **Trailing sector words**  
  OCSL had " Biotechnology" stripped; other tickers might have similar trailing sector words (e.g. " Software", " Insurance"). Add per-ticker or generic strip if you see them.

---

## 5. Patterns that are already handled

So a second agent doesn’t duplicate work, these are already in place (see `TICKER_COMPANY_NAME_FIXES.md` for full list):

- **Warrant Investments and {Industry} and {Company}** → company only (generic).  
- **Warrant Acquisition Date M/D/YYYY** → stripped (generic).  
- **Affiliate / Affiliated Investments** → stripped for FDUS, TRIN, PFX, MFIC.  
- **United States Debt Investments {Industry} {Company}** → CCAP.  
- **216.4% United States/Canada - ... 1st Lien... - X%** → GSBD.  
- **": Category"** (e.g. ": Cargo", ": Consumer") and **") Industry X"** / **") Insurance"** → cleared (BCIC/generic).  
- **Company-Industry: SubIndustry-Instrument** (SAR) → first segment is company.  
- PFLT/PNNT, LIEN, KBDC, GECC, RWAY, TCPC, TSLX, WHF, and others have ticker-specific rules.

---

## 6. Commands reference

```bash
# Re-apply all standardization rules to investment CSVs
python scripts/restandardize_all.py

# Rebuild company_id and exposure files from cleaned names
python src/company_resolution/resolve_companies.py --investments-dir frontend/public/data/investments --data-dir frontend/public/data
```

Run from repo root. After changing `standardization_rules.py`, run both so the frontend and company pages show updated names and a single company per resolved entity.
