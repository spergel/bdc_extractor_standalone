# Corporate Research Section – Improvement Plan

This doc focuses on the **portfolio company** (borrower) research experience—Companies view, CompanyPage, and company profiles—separate from BDC scraping.

---

## Current state

- **Companies view**: Sidebar lists companies from `company_exposures.csv` (search, sort by name/exposure/# BDCs). Clicking a company opens **CompanyPage**.
- **CompanyPage** shows:
  - **Profile**: description, industry, website, location, employees, leadership, funding, recent_news (from `company_profiles.csv` / `company_profiles.json`).
  - **Exposure summary**: total $M, # BDCs, primary industry, avg rate.
  - **Lenders**: which BDCs have exposure (from `company_detail.json` or exposure list).
  - **Maturity breakdown** and **Investment type breakdown** (when `company_detail.json` exists).
- **Profile data**: Built by `src/company_resolution/build_profiles.py`. Many rows are **skeleton** (empty description; `source: skeleton`). With `--with-llm` it uses Tavily + Gemini to fill description, leadership, funding, recent_news.

---

## Improvement directions

### 1. **Data & content**

| Area | Idea |
|------|------|
| **Fill more profiles** | Run `build_profiles.py --with-llm` for more companies; or prioritize by exposure / # BDCs so high-impact names get research first. |
| **Stable schema for “another LLM”** | Keep `company_profiles.csv` (and optional `company_research.json`) as the **single contract**: the other LLM reads companies from the index/exposures and writes only to profiles (or a dedicated research artifact). No changes to BDC extraction. |
| **Research artifact** | Optional `company_research.json`: `{ company_id: { summary, risks, catalysts, source, updated_at } }` so an external research LLM can add structured notes without touching CSV columns. Frontend can show “Research” block when present. |
| **Industry & dedup** | Use `industry_initial` and normalization so Companies filter/sector view is consistent. Consider merging duplicate entities (e.g. CoreWeave variants) in resolution so research isn’t split across rows. |

### 2. **UX**

| Area | Idea |
|------|------|
| **Empty state** | When `!profile?.description`: show “No profile yet” plus a **“Research this company”** link (e.g. Google search, SEC EDGAR, or internal queue) so it’s clear what to do next. |
| **Source & freshness** | Show `source` and `updated_at` on CompanyPage (e.g. “Skeleton”, “Tavily+Gemini”, “Manual”) and “Last updated …” so users know how much to trust the blurb. |
| **Quick links** | Add small links: “Google”, “SEC”, “LinkedIn” (from company name or domain) so power users can jump to external research. |
| **Companies without profile** | In Companies sidebar or a filter: “Show only companies with no description” to drive a research backlog. |
| **Holdings tooltip** | HoldingsTable already uses profile (description, leadership, funding, recent_news) in tooltip; ensure company_id resolution is robust so tooltips show for as many rows as possible. |

### 3. **Contract for another LLM**

To hand off “corporate research” to another LLM without touching BDC scraping:

1. **Inputs** (read-only for research LLM):
   - `frontend/public/data/companies_index.json` (company_id, canonical_name, name_variants).
   - `frontend/public/data/company_exposures.csv` (company_id, company_name, total_exposure_millions, num_bdcs_invested, primary_industry, etc.).
   - Optional: list of company_ids with “no profile” or “skeleton only” (e.g. from `company_profiles.csv` where `description` is empty or `source == 'skeleton'`).

2. **Outputs** (research LLM writes):
   - **Primary**: `frontend/public/data/company_profiles.csv` (append/update rows for company_id with description, industry, website, location, employee_range, leadership, funding, recent_news, source, updated_at). Same schema as today.
   - **Optional**: `frontend/public/data/company_research.json` for extra structured research (summary, risks, catalysts, source, updated_at) if we add a “Research” block on CompanyPage.

3. **Schema stability**:
   - Keep `company_profiles.csv` columns as in `PROFILE_SCHEMA_KEYS` in `build_profiles.py`. Any new field (e.g. `parent_company`, `naics`) can be added as a new column and the frontend/adapter updated once.

4. **No BDC code**:
   - Research LLM does not run or change `llm_table_scraper`, `xbrl_investment_extractor`, `consolidate_investments`, or `resolve_companies` (except possibly triggering resolve for new names). It only enriches profile/research data.

---

## Suggested next steps (priority)

1. **Document the contract**  
   Add a short `docs/company_research_contract.md` (or a section in this file) that lists exact input files, output files, and CSV/JSON schemas so another LLM can implement a “research writer” without guessing.

2. **UX quick wins**  
   - Show `source` and `updated_at` on CompanyPage.  
   - When there’s no description, show “Research this company” with a link (e.g. `https://www.google.com/search?q=${encodeURIComponent(canonical_name)}`).

3. **Research backlog**  
   - Add a filter or sidebar section: “Companies without profile” (description empty or source skeleton) so the research LLM (or human) has a clear queue.

4. **Optional research blob**  
   - If you want structured research beyond the profile blurb, introduce `company_research.json` and a “Research” section on CompanyPage that reads from it; then the other LLM can write only that file.

5. **Prioritized profile build**  
   - In `build_profiles.py`, add an option to build only for companies with exposure above a threshold (e.g. total_exposure_millions > 50) or with at least N BDCs, so the first run of `--with-llm` fills the most important names.

---

## Files to touch (for reference)

| Purpose | Path |
|--------|------|
| Company page UI | `frontend/src/components/CompanyPage.tsx` |
| Profile type & loading | `frontend/src/data/adapter.ts` (CompanyProfile, loadCompanyProfiles) |
| Companies list | `frontend/src/components/CompaniesSidebar.tsx` |
| Profile build script | `src/company_resolution/build_profiles.py` |
| Profile data | `frontend/public/data/company_profiles.csv`, `company_profiles.json` |
| Exposures | `frontend/public/data/company_exposures.csv` |
| Company index | `frontend/public/data/companies_index.json` |
| Detail (by_bdc, maturity, type) | `frontend/public/data/company_detail.json` |

If you tell me which direction you want first (contract doc, UX tweaks, or research backlog filter), I can implement that next.
