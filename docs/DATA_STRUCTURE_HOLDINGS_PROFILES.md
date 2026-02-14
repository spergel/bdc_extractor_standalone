# Data Structure: Holdings and Company Profiles

How holdings, companies, and profiles connect so the frontend can show “holdings with profiles” and eventually a company-centric view.

## Canonical ID: `company_id`

- **Format:** `co_<sha256>` (e.g. `co_2bfeef4adb42`).
- **Scope:** One ID per resolved company across all BDCs and periods. Same company in different filings shares the same `company_id`.
- **Set by:** `src/company_resolution/resolve_companies.py` (written into investment CSVs and into `companies_index.json`).

Every link between holdings, profiles, and cross-BDC exposure uses this ID.

---

## Data Sources

| Source | Path | Shape | Role |
|--------|------|--------|------|
| **Holdings (per BDC/period)** | `/data/investments/{TICKER}/{period}.csv` | Rows with `company_id`, `company_name_clean`, ticker, period, fair_value, etc. | One row = one holding line. `company_id` links to company/profile. |
| **Companies index** | `/data/companies_index.json` | `{ generated_at, companies: [ { company_id, canonical_name, name_variants } ] }` | Resolve display name; know all resolved companies. |
| **Company profiles** | `/data/company_profiles.json` | `{ generated_at, profiles: { [company_id]: CompanyProfile } }` | Rich profile per company (description, leadership, website, location, employee_range, etc.). |
| **Company exposures** | `/data/company_exposures.csv` | Rows: company_id, company_name, num_bdcs_invested, bdcs_invested, total_exposure_millions, ... | Cross-BDC view: which BDCs hold this company and aggregate exposure. |

- **Holdings** are loaded per view: one BDC + one period (e.g. via `fetchPeriodSnapshot(ticker, period)` → `investments/{TICKER}/{period}.csv`). Each row already has `company_id`.
- **Profiles** are loaded once (e.g. `loadCompanyProfiles()`) and keyed by `company_id`.
- **Exposures** are loaded once (e.g. `loadCompanyExposures()`) for a company-centric view (e.g. “which BDCs hold this company?”).

**Company profiles: merge, don't delete.** Do not delete `company_profiles.json`. The profile builder loads existing profiles, then adds or updates only the companies it processes (e.g. from `--companies-file`). Other BDCs' profiles stay. Use `--refresh` to re-fetch existing entries.

---

## How It Connects

```
Holding (row in investments CSV)
  └── company_id  ──►  CompanyProfile (profiles[company_id])
                    ──►  CompaniesIndex entry (canonical_name, name_variants)
                    ──►  CompanyExposure row (bdcs_invested, total_exposure_millions, ...)
```

- **Holdings table:** For each row, use `row.company_id` to look up `profiles[row.company_id]` and show description, leadership, website, etc. (tooltip or side panel).
- **Company detail (future):** Given `company_id`, show `profiles[company_id]` and exposures row; “holdings” for that company can be derived by filtering all loaded period data by `company_id`, or by adding a precomputed index (see below).

---

## Frontend Loading Strategy

1. **BDC + period selected**  
   Load holdings for that BDC/period (e.g. `fetchPeriodSnapshot(ticker, period)`). Each item has `company_id`.

2. **Profiles once**  
   Load `company_profiles.json` once (e.g. in `HoldingsTable` or a top-level provider). Key: `Record<string, CompanyProfile>` by `company_id`.

3. **Per row**  
   `profile = companyProfiles[row.company_id]`. Use for tooltip, modal, or inline “profile” block. No extra fetch per row.

4. **Company-centric view (later)**  
   - Load `company_exposures.csv` once.  
   - For “all holdings for this company”: either  
     - (a) Filter already-loaded period data by `company_id`, or  
     - (b) Add a precomputed artifact (e.g. `company_holdings.json`: `company_id → [ { ticker, period, filing_date, fair_value_thousands, ... } ]`) and load that when opening a company page.

---

## Optional: Precomputed company–holdings index

If we want a dedicated “Company” page (profile + list of every holding across BDCs/periods) without loading every BDC’s CSVs:

- **New artifact:** e.g. `/data/company_holdings.json`  
  Shape: `{ company_id: [ { ticker, period, filing_date, fair_value_thousands, investment_type, ... } ] }`.  
  Built by scanning all `investments/{TICKER}/{period}.csv` and grouping by `company_id` (e.g. in `resolve_companies.py` or a small script).

- **Frontend:** For a given `company_id`, load profile from `company_profiles.json`, exposure from `company_exposures.csv`, and holdings list from `company_holdings.json`. Everything stays connected via `company_id`.

---

## Types (frontend)

- **Holding:** has `company_id?: string` (and `company_name_clean`, etc.).
- **CompanyProfile:** has `company_id`, `canonical_name`, and optional `description`, `website`, `location`, `employee_range`, `leadership`, `funding`, `recent_news`, etc.
- **CompanyExposure:** has `company_id`, `company_name`, `bdcs_invested`, `total_exposure_millions`, etc.

For “holding with profile” in the table, use:

- `Holding` + `profile: CompanyProfile | undefined` where `profile = profiles[holding.company_id]`.

No need to duplicate profile data into each holding; keep one profile map keyed by `company_id` and look up by row.

---

## Summary

- **Single key:** `company_id` ties holdings → profile → exposures.
- **Holdings:** per BDC/period CSVs with `company_id` on each row.
- **Profiles:** one JSON keyed by `company_id`; load once, look up per row.
- **Exposures:** one CSV for cross-BDC view by `company_id`.
- **UI:** Holdings table shows profile via `profiles[row.company_id]`; future company page uses same ID for profile + exposures + (optionally) precomputed company holdings list.
