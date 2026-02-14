# Design: Company Page, Sectors View, Remove Comparison

## Goals

1. **Company page** – View a single company: profile, which BDCs hold it, exposure totals.
2. **Remove Comparison mode** – Drop the "Individual vs Comparison" toggle; one BDC at a time.
3. **Sectors view** – Browse by industry/sector: see sectors, drill into companies in a sector and which BDCs have exposure.

---

## 1. Remove Comparison Mode

- **Header:** Remove the "Individual" / "Comparison" toggle. Single mode only.
- **App state:** Remove `mode`, `selectedTickers`, `handleModeChange`, `handleTickerToggle`. Sidebar selects one BDC only.
- **Sidebar:** Single selection (current "individual" behavior). Remove any multi-select UI.
- **StatusBar / hooks:** Remove comparison-specific logic; keep `ticker` and `selectedPeriod` for the selected BDC.

**Keep:** The "Changes" tab (compare two *periods* for one BDC) stays. That’s period-over-period diff for the same BDC, not multi-BDC comparison.

---

## 2. Top-Level View: BDC vs Companies vs Sectors

Add a **view mode** in the header (or as a small nav): **BDC** | **Companies** | **Sectors**.

| View     | Sidebar content     | Main content |
|----------|----------------------|--------------|
| **BDC**  | List of BDCs (pick one) | Current tabs: Overview, Financials, Holdings, Analytics, Changes, Disclosure |
| **Companies** | Search/list of companies | Company detail page (profile + exposures + “held by” BDCs) |
| **Sectors**   | List of sectors (industries) | Sector detail: companies in that sector, exposure summary |

- **BDC:** Unchanged flow. Select BDC → tabs for that BDC.
- **Companies:** Sidebar = companies (from `company_exposures.csv` or `companies_index.json`), searchable. Main = company page.
- **Sectors:** Sidebar = list of sectors. Sectors = unique `primary_industry` from `company_exposures.csv` (or industry categories from holdings). Main = sector detail.

**Entry points to Company page**

- From **Companies** view: pick a company in the sidebar.
- From **Holdings** tab (BDC view): company name in the table is clickable → switch to **Companies** view with that company selected and show company page.

---

## 3. Company Page (main content when view = Companies)

**Data**

- Profile: `company_profiles.json[company_id]` (description, leadership, website, location, employee_range, funding, recent_news).
- Exposure: row in `company_exposures.csv` for this `company_id` (company_name, num_bdcs_invested, bdcs_invested, total_exposure_millions, primary_industry, avg_interest_rate, most_common_investment_type).

**Layout**

1. **Profile block** – Description, leadership, website (link), location, employee range, industry, funding, recent news (from profile when present).
2. **Exposure summary** – Total exposure ($M), # of BDCs, primary industry, avg rate, most common investment type.
3. **“Held by”** – List of BDC tickers (from `bdcs_invested`). Each ticker can be a link: switch to **BDC** view with that ticker selected (and optionally later: deep-link to Holdings filtered by this company).
4. **Positions (optional v1)** – “X positions across N BDCs.” Without a `company_holdings.json`, we don’t show a full table of positions; we can add that artifact later and then show a table (ticker, period, type, fair value, etc.).

**Empty state**

- No profile: show exposure row only + “No profile yet” (optional: “Request profile” or similar later).
- No exposure: shouldn’t happen if we only list companies that appear in company_exposures.

---

## 4. Sectors View

**Data**

- **Sector list:** Unique `primary_industry` from `company_exposures.csv`, sorted by total exposure (sum of `total_exposure_millions` for companies in that industry) or by company count.
- **Sector detail:** Companies where `primary_industry === selected sector`; show company name, total_exposure_millions, num_bdcs_invested, bdcs_invested. Company name links to Company page.

**Layout**

- **Sidebar:** List of sectors (e.g. “Software”, “Healthcare”, “Manufacturing”, …). Optional: show company count or total $ next to each.
- **Main:** When a sector is selected, show a table: Company | Total exposure ($M) | # BDCs | BDCs. Rows link to Company page.

**Alternative (per-BDC sector)**

- Keep current **Analytics** tab for one BDC (industry pie chart + drill-down). That’s “sector mix for this BDC.”
- **Sectors** view here is **cross-BDC by sector**: “which companies and BDCs touch this industry,” not “this BDC’s breakdown by industry.” So we use `company_exposures` + `primary_industry` for the sector list and sector detail.

---

## 5. URL / State (no router for v1)

- **State:** `viewMode: 'bdc' | 'companies' | 'sectors'`, `selectedTicker`, `selectedCompanyId`, `selectedSector`.
- **Persistence:** Optional localStorage for last view mode and last selected company/sector.
- **Later:** Add react-router for `/bdc/:ticker`, `/company/:companyId`, `/sector/:industry` for shareable links.

---

## 6. Implementation Order

1. **Remove Comparison** – Header, App state, Sidebar, StatusBar; keep single-BDC selection and period diff (Changes tab).
2. **View mode** – Add BDC | Companies | Sectors to header; state and conditional layout (sidebar + main content by view).
3. **Company page** – Load profile + exposure; render profile block, exposure summary, “Held by” BDCs; company name in Holdings table links to Companies view + company.
4. **Companies sidebar** – Load company_exposures (or companies_index); searchable list; select company → show company page.
5. **Sectors sidebar + detail** – Unique sectors from company_exposures; sector detail table (companies in sector) with links to Company page.

---

## 7. Data Summary

| Need | Source |
|------|--------|
| Company profile | `company_profiles.json` by `company_id` |
| Company exposure (BDCs, $) | `company_exposures.csv` (company_id, company_name, bdcs_invested, total_exposure_millions, primary_industry, …) |
| List of companies | `company_exposures.csv` or `companies_index.json` |
| Sectors list | Unique `primary_industry` from `company_exposures.csv` |
| Companies in sector | Filter `company_exposures.csv` by `primary_industry` |
| Positions per company (later) | Precomputed `company_holdings.json` or filter BDC holdings by `company_id` |

No new backend or APIs required for v1; all from existing JSON/CSV.
