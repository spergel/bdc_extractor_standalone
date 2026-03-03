# Ticker-specific company name cleanup

After switching to XBRL (and table) scrapers, the **company_name** column often contained type/industry/value text that belongs in other columns. This doc lists BDCs with custom cleanup so **company_name** holds only the business name.

## Summary

| Ticker | Issue | Fix |
|--------|--------|-----|
| **RAND** | Value or instrument in name (e.g. " - $3 000", " - Warrant for 1% Membership Interest", " - 37") | Strip trailing " - $N NNN", " - N", " - Warrant for...", " - First Lien", " - Common Equity", " - Other" |
| **OXSQ** | "Senior Secured Notes - Industry - CompanyName" | Use last segment as company; "Senior Secured Notes - Industry" only → empty (subtotal) |
| **SAR** | "Non-control/Non-affiliate ... - X% - CompanyName"; "Ta TT Buyer LLC-Media: Broadcasting & Subscription-Term Loan..." → take segment before "-Industry:"; sector-only rows | Strip prefix; when "Company-Industry: SubIndustry-..." take first segment; take first segment before " - "; clear sector-only |
| **MFIC** | "Affiliated Investments Golden Bear 2016-R LLC"; "Advertising Printing & Publishing CompanyName ..." | Strip "Affiliated Investments "; strip " Investment Type ..." and leading industry; strip trailing instrument text; dedupe repeated first word |
| **ICMB** | "Non-Controlled/Non-Affiliated Investments Senior Secured First Lien Debt Investments {Industry} {Company}" | Strip long prefix; strip leading industry (GICS-style) |
| **LIEN** | Category rows; "US Corporate Debt Senior Secured U.S. Notes {Industry} {Company} Facility Type..."; "Ascend Wellness Senior Secured Note All in Rate..." | Return empty for category-only; strip US Corporate Debt... prefix, Facility Type/Initial Acquisition Date suffix, leading industry (Cannabis, Finance and Insurance); strip " Senior Secured Note All in Rate..." |
| **CCAP** | "VetStrategy Investment Type ..."; "United States Debt Investments Materials Online Labels Group LLC"; "& Components Auveco Holdings"; "Automobiles & Components Sun Acquirer Corp." | Strip "United States/Equity Debt/Investments "; strip leading "& Components "; strip leading industry (Automobiles & Components, Materials, etc.); strip trailing " Investment Type ..."; section-only → empty |
| **GECC** | "Universal Fiber Systems Industry Chemicals Security 1st Lien" or "... Common Equity Initial Acquisition Date ..." | Strip trailing " Industry ..." (sector/security/date) so company name only |
| **PNNT** / **PFLT** | "in Non-Controlled ... - X% First Lien ... CompanyName"; "in ... Common Equity/Warrants CompanyName - Common Equity Acquisition ... Industry X"; "- Unfunded Term Loan Acquisition 08/15/2025" / "- Common Equity Acquisition ... Industry Insurance" = no company | Strip long prefix; strip " Common Equity/Warrants "; strip trailing " - Common Equity Acquisition M/D/YYYY Industry X"; return '' for "- Unfunded/Common Equity Acquisition..." only; "Current Coupon..." → empty |
| **PFX** | "Non-Controlled/Non-Affiliated ... - CompanyName"; "Affiliated Investments - Advocates for Disabled Vets"; "Altisource S.A.R.L. - Services: Business - Equity" | Strip prefix; strip "Affiliated Investments - "; strip " - Services: Business - ..."; strip " - Business" / " - Real Estate" etc. |
| **RWAY** | "Non-Control/Non-Affiliate Investments Debt/Equity Investments {Industry} {Company}", "Company Investment Type Senior Secured..." | Strip prefix; strip leading industry; strip trailing " Investment Type ..."; industry-only → clear |
| **TCPC** | "Debt Investments {Industry} {CompanyName}"; "Equity Securities Internet Software and Services Domo"; "Professional Service JobandTalent USA" | Strip "Debt Investments "/"Equity Securities {sector} "; strip leading industry; set industry from sector (Internet Software and Services → Software & Technology, etc.) |
| **TSLX** | "Spread SOFR + X%..." = rate-only; "Other Investments Ares CLO Ltd."; "Equity and Other Investments Business Services ReliaQuest" | Return empty for rate-only; strip "Other Investments "/"Equity and Other Investments "; strip leading sector |
| **WHF** | "CompanyName First Lien Secured Term Loan" / "Term Loan Two", etc. | Strip trailing " First Lien Secured Term Loan", " First Lien Secured Revolver" |
| **KBDC** (KCDC) | "Aerospace & defense - ..."; "Professional services - DISA Holdings Corp." | Strip "Industry - " and "Professional services - "; subtotal rows → empty |
| **PFLT** (extra) | ": Diversified and Production Current Coupon ...", "Morse Defense Maturity 06/23/2028 Industry ...", bare "LLC" | Clear rate-only lines; strip " Maturity DD/MM/YYYY Industry ..."; clear fragment "LLC" only |
| **PNNT / PFLT** (no-% variant) | "in Non-Controlled ... First Lien Secured Debt Issuer Name Route 66 Development Acquisition 01/28/2025" | Strip prefix (no " - X%"); strip trailing " M/D/YYYY" → "Route 66 Development Acquisition" |
| **PSBD** | "Packaging Interest Rate 10.26% ... Maturity Date 3/31/2028 One" (rate/maturity dimension) | Return '' for ".+ Interest Rate .+% ... Maturity Date" (no company) |
| **TRIN** | "Affiliate Investments ..."; "Portfolio Company Debt Securities- United States ..." / "Healthcare Technology Unmind Ltd."; "Portfolio Company Warrant Investments United Impulse Space Inc."; "Portfolio Company Equity Investments Canada Construction Technology" | Strip "Portfolio Company Debt Securities- ", "United States ", "Portfolio Company Warrant Investments United " (or "... United States "), "Portfolio Company Equity Investments Canada "; strip leading sector; clear sector-only |
| **GSBD** | "216.4% United States - 205.6% 1st Lien/Senior Secured Debt - 195.3% CompanyName" (or Canada variant) | Strip leading percentage + geography + debt type + final % so company name only |
| **GBDC** | "Armstrong Bidco Limited One stop 1", "Accelya Lux Finco S.A.R.L. One stop"; "Arnott LLCBaduhenna Bidco Limited One stop 1" (two names concatenated) | Strip trailing " One stop" / " One stop N"; when "LLC" is immediately followed by capital letter (no space), take the part after LLC as company |
| **SLRC** | "Common Equity/Equity Interests/Warrants", "Equipment Financing" (investment type/category leaked as company) | Treated as non-company (cleared to empty). Re-extraction from XBRL may be needed to recover true issuer names for affected rows. |
| **BCIC** (generic) | ": Broadcasting & Subscription", ": Cargo", ": Consumer"; ") Industry X", ") Insurance" | Section headers ": Category" → empty; fragment ") Industry X" / ") Insurance" → empty for any ticker |
| **FDUS** | "Affiliate Investments Pfanstiehl Inc." | Strip "Affiliate Investments " |
| **OCSL** | "Alvotech Holdings S.A. Biotechnology" | Strip trailing " Biotechnology" (sector leak) |
| **MAIN / MSIF** | "Amounts related to investments transferred to or from other 1940 Act classification during the period Affiliate Investments" (section header) | Return '' for this and "... Control Investments"; optional "Other " prefix (MSIF) |
| **MFIC** | "Automobile/Paper & Forest/Personal Care Components ..."; "Summer Fridays Summer Fridays" | Strip industry prefixes; dedupe repeated phrase at end; return '' for industry-only |
| **CCAP** | "Biotechnology & Life Sciences ..."; "Pharmaceuticals Biotechnology & Life Sciences LSCS Holdings" | Strip leading industry (incl. "Pharmaceuticals Biotechnology & Life Sciences ") |
| **LIEN** | "Canadian Warrants Information Tulip.io Inc." | Strip "Canadian Warrants Information " |
| **BCSF** | "Ansett Aviation Training Equity Interest", "Ansett Aviation Training First Lien Senior Secured Loan", "Non-Controlled/Affiliate Investments Aerospace & Defense ...", "Australian Dollar Aerospace & Defense ...", "... BBSY Spread X% Interest Rate Y% Maturity Date ...", " Acquisition Date 3/24/2022" | Strip section/industry prefix; strip long rate/maturity suffix; " Equity Interest" / " First Lien Senior Secured Loan" via generic instrument suffix |
| **HRZN** (concatenated) | No-space XBRL names: "NonaffiliateDebtInvestments...OnkosSurgicalInc...TermLoanOneMember"; "NonControlledAffiliateDebtInvestmentsShengrowIncOtherSustainabilityRevolverMember"; "Technology Supply Network Visibility Holdings LLCSoftware" | Strip prefix Nonaffiliate(d)DebtInvestments or NonControlledAffiliateDebtInvestments; strip leading sector (LifeScience, Sustainability); strip trailing industry + TermLoan.../RevolverMember; split CamelCase; strip trailing concatenated sector after LLC/Inc (e.g. LLCSoftware → LLC); set industry from stripped sector |

## HRZN concatenated format (no spaces)

HRZN XBRL dimension labels sometimes appear as one token with no spaces, e.g.:

- `NonaffiliateDebtInvestmentsLifeScienceOnkosSurgicalIncMedicalDeviceTermLoanOneMember` → **Onkos Surgical Inc.** (industry: Medical Devices)
- `NonaffiliateDebtInvestmentsLifeScienceCastleCreekBiosciencesBiotechnologyTermLoanOneMember` → **Castle Creek Biosciences** (industry: Biotechnology)
- `NonaffiliateDebtInvestmentsSustainabilitySparkChargeIncAlternativeEnergyTermLoanOneMember` → **Spark Charge Inc.** (industry: Energy)

Pattern: prefix (`NonaffiliateDebtInvestments`, `NonaffiliatedDebtInvestments`, or `NonControlledAffiliateDebtInvestments`) + optional leading sector (LifeScience, Sustainability) + company name in CamelCase + optional trailing sector + `TermLoan[One|Two|...]Member` or `RevolverMember`. Parser strips prefix/sectors/suffix and converts CamelCase to words. Industry is set from the stripped sector (e.g. OtherSustainability → Environmental Services, MedicalDevice → Medical Devices). A second HRZN rule strips a trailing sector word concatenated after the legal suffix with no space (e.g. `Technology Supply Network Visibility Holdings LLCSoftware` → **Technology Supply Network Visibility Holdings LLC**, industry **Software & Technology**).

## Exceptions (value in name)

Some RAND (and similar) rows have the **principal/commitment value** in the company name, e.g.:

- `Mountain Regional Equipment Solutions - $3 000` → **Mountain Regional Equipment Solutions**
- `HDI Acquisition LLC. - $1 245` → **HDI Acquisition LLC**
- `Lumious - $850` → **Lumious**

Cleanup strips " - $N NNN" and " - N" (line item number) so the display name is the business only; value stays in principal/cost/FV columns.

## Generic cleanup (all tickers)

- **Warrant Investments and {Industry} and {Company}** (e.g. HTGC XBRL): strip to company only — e.g. "Warrant Investments and Electronics & Computer Hardware and Skydio Inc." → **Skydio Inc.** "Warrant Investments and {Industry}" only (no company) → empty (section header).
- **Warrant Acquisition Date M/D/YYYY**: trailing suffix stripped so "Strive Health Holdings LLC. Warrant Acquisition Date 9/28/2023" → **Strive Health Holdings LLC.**
- **Equity Interest / First Lien Senior Secured Loan**: trailing suffix (no dash) stripped so "Ansett Aviation Training Equity Interest" → **Ansett Aviation Training**, "Ansett Aviation Training First Lien Senior Secured Loan" → **Ansett Aviation Training**.

## Where it runs

- **Post-process** (`post_process_extraction.py`): when running on `*_investments_*.csv` files, ticker is taken from the row’s `ticker` column or inferred from the filename (e.g. `ARCC_investments_2025-11-06.csv` → ARCC).
- **Re-standardize** (`standardization_rules.restandardize_csv`): uses each row’s `ticker` column when present.

## Adding a new ticker

1. Add the rule in `src/processing/standardization_rules.py` inside `_apply_ticker_specific_company_cleanup()`.
2. Document the pattern and fix in this file.
3. Re-run post-process (or restandardize) on the affected CSVs.

## Tickers with no custom rule (yet)

- **PSBD** – Now has rule: rate/maturity dimension rows → return ''.
- **BXSL** – Generic instrument suffix now strips " - Common Equity", " Common Equity", " - Series A Preferred Shares", " Series A Preferred Shares" so e.g. "Zoro", "Zoro - Common Equity", "Zoro Series A Preferred Shares" all normalize to **Zoro** (one company_id after resolve).
- **BCSF, …** – Use generic `clean_company_name()` only unless specific patterns show up.
